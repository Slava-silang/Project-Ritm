import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage import backup
from ritm.storage.attachments import (
    clean_deleted_attachments,
    copy_attachment,
    saved_attachment_path,
)
from ritm.storage.sqlite import TaskStore

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)


def test_snapshot_and_restore_includes_wal_trash_and_shared_files(tmp_path):
    db_path = tmp_path / backup.DATABASE
    source = tmp_path / "Учебник.txt"
    source.write_text("Материал из снимка")
    task = Task(title="Снимок")
    archived = Task(title="В корзине")
    with TaskStore(db_path) as store:
        store.create_task(task)
        store.create_task(archived)
        direct = copy_attachment(store, tmp_path, archived.id, archived.id, source)
        material = copy_attachment(store, tmp_path, None, None, source)
        store.link_material(task.id, task.id, material.id)
        TaskService(store).delete_node(archived.id, archived.id, NOW)
    (tmp_path / "api-key.txt").write_text("test secret")
    with closing(sqlite3.connect(db_path)) as wal:
        assert wal.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        wal.execute("UPDATE nodes SET title=? WHERE id=?", ("Из WAL", str(task.id)))
        wal.commit()
        assert db_path.with_name(db_path.name + "-wal").exists()
        file = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    with ZipFile(file) as archive:
        assert len(archive.namelist()) == 4
        assert "api-key.txt" not in archive.namelist()
    source.unlink()
    saved_attachment_path(tmp_path, material).write_text("Поздняя правка")
    with TaskStore(db_path) as store:
        store.create_task(Task(title="После копии"))
    preview = backup.inspect_backup(file)
    backup.restore_backup(tmp_path, file, preview["token"])
    with TaskStore(db_path) as store:
        assert [row.title for row in store.list_tasks()] == ["Из WAL"]
        assert store.list_trash()[0]["title"] == "В корзине"
        (link,) = store.list_attachments()
        (restored,) = store.list_materials()
        assert link.id == restored.id
        assert saved_attachment_path(tmp_path, restored).read_text() == "Материал из снимка"
        assert {key for key, _ in store.pending_attachment_cleanup()} == {direct.id, material.id}
        clean_deleted_attachments(store, tmp_path)
        assert not store.pending_attachment_cleanup()
        assert len(list((tmp_path / "attachments").iterdir())) == 2
    assert (tmp_path / "api-key.txt").read_text() == "test secret"


def test_daily_rotation_preserves_manual_and_unrelated_files(tmp_path):
    with TaskStore(tmp_path / backup.DATABASE):
        pass
    folder = tmp_path / "Копии с пробелом"
    folder.mkdir()
    backup.set_backup_folder(tmp_path, folder)
    assert backup.backup_folder(tmp_path) == folder
    manual = backup.create_backup(tmp_path, folder, NOW)
    unrelated = folder / "ritm-auto-2024-01-01.zip"
    unrelated.write_text("Чужой файл")
    for offset in range(9):
        backup.daily_backup(tmp_path, folder, NOW + timedelta(days=offset))
    identifier = (tmp_path / "backup-profile-id").read_text()
    latest = folder / f"ritm-auto-2026-11-18-{identifier}.zip"
    saved_stamp = latest.stat().st_mtime_ns
    backup.daily_backup(tmp_path, folder, NOW + timedelta(days=8))
    assert latest.stat().st_mtime_ns == saved_stamp
    assert len(list(folder.glob("ritm-auto-*.zip"))) == 8
    assert not (folder / f"ritm-auto-2026-11-11-{identifier}.zip").exists()
    assert manual.is_file() and unrelated.read_text() == "Чужой файл"
    other_profile = tmp_path / "other-profile"
    with TaskStore(other_profile / backup.DATABASE):
        pass
    backup.daily_backup(other_profile, folder, NOW + timedelta(days=8))
    assert len(list(folder.glob("ritm-auto-2026-11-18-*.zip"))) == 2
    assert len(list(folder.glob(f"ritm-auto-*-{identifier}.zip"))) == 7
    backup.daily_backup(tmp_path, tmp_path / "different-folder", NOW + timedelta(days=8))
    assert not (tmp_path / "different-folder").exists()


def test_corruption_and_failed_publish_preserve_previous_profile(tmp_path, monkeypatch):
    db_path = tmp_path / backup.DATABASE
    source = tmp_path / "Файл.txt"
    source.write_text("Исходный")
    task = Task(title="В архиве")
    with TaskStore(db_path) as store:
        store.create_task(task)
        direct = copy_attachment(store, tmp_path, task.id, task.id, source)
    old_copy = saved_attachment_path(tmp_path, direct)
    original = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    token = backup.inspect_backup(original)["token"]
    with TaskStore(db_path) as store:
        store.create_task(Task(title="Текущая"))
    new_archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW + timedelta(minutes=1))
    with pytest.raises(ValueError, match="изменился"):
        backup.restore_backup(tmp_path, new_archive, token)
    corrupt = tmp_path / "corrupt.zip"
    with ZipFile(original) as src, ZipFile(corrupt, "w") as dest:
        for name in src.namelist():
            dest.writestr(
                name, src.read(name) if name in (backup.DATABASE, backup.MANIFEST) else b"bad"
            )
    with pytest.raises(ValueError, match="Контрольная сумма"):
        backup.inspect_backup(corrupt)
    with pytest.raises(ValueError, match="Контрольная сумма"):
        backup.restore_backup(tmp_path, corrupt, token)
    with monkeypatch.context() as patch:

        def fail_publish(*args, **kwargs):
            raise OSError("нет места")

        patch.setattr(backup, "_copy_database", fail_publish)
        with pytest.raises(OSError, match="нет места"):
            backup.restore_backup(tmp_path, original, token)
    with TaskStore(db_path) as store:
        assert {row.title for row in store.list_tasks()} == {"В архиве", "Текущая"}
        assert store.list_attachments() == (direct,)
        assert not store.pending_attachment_cleanup()
    assert list((tmp_path / "attachments").iterdir()) == [old_copy.parent]
    assert old_copy.read_text() == "Исходный"


def test_backup_gui_confirms_replacement(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    script = Path(__file__).with_name("ui_backup_scenario.py")
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=35,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
        assert warning not in result.stderr, result.stderr
