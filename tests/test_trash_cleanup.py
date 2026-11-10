import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.storage.attachments import (
    clean_deleted_attachments,
    copy_attachment,
    saved_attachment_path,
)
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)


def test_root_restores_then_purges_only_selected_related_data(tmp_path):
    source = tmp_path / "Материал.txt"
    source.write_text("Оригинал")
    path = tmp_path / "tasks.sqlite3"
    leaf = ChecklistItem(title="Пункт", completed=True)
    step = Subtask(title="Шаг", items=(leaf,), completed=True)
    task = Task(title="Готовая", subtasks=(step,), completed=True)
    other = Task(title="Другая")
    with TaskStore(path, clock=lambda: NOW) as store:
        service = TaskService(store)
        expected = service.create_task(task)
        service.create_task(other)
        direct = [
            copy_attachment(store, tmp_path, task.id, owner, source)
            for owner in (task.id, step.id, leaf.id)
        ]
        unrelated = copy_attachment(store, tmp_path, other.id, other.id, source)
        material = copy_attachment(store, tmp_path, None, None, source)
        store.link_material(task.id, leaf.id, material.id)
        store.link_material(other.id, other.id, material.id)
        work = WorkBlock(task_id=task.id, starts_at=NOW, ends_at=NOW + timedelta(hours=1))
        event = WorkBlock(title="Событие", starts_at=work.starts_at, ends_at=work.ends_at)
        store.create_work_block(work)
        store.create_work_block(event)
        store.add_focus_record(task.id, NOW - timedelta(hours=1), NOW, NOW)
        root_id = UUID(service.delete_node(task.id, task.id))
        unrelated_id = UUID(service.delete_node(other.id, other.id))
        assert store.list_attachments() == ()
        assert store.list_work_blocks() == (event,)
    with TaskStore(path, clock=lambda: NOW) as store:
        service = TaskService(store)
        assert service.restore(root_id) == expected
        assert set(store.list_work_blocks()) == {work, event}
        assert len(store.list_attachments()) == 4
        assert store.list_focus_sessions()[0].worked_us == 3_600_000_000
        assert store.list_completion_dates() == {task.id: NOW}
        root_id = UUID(service.delete_node(task.id, task.id))
        store.purge_trash({root_id})
        clean_deleted_attachments(store, tmp_path)
        assert not store.pending_attachment_cleanup()
        assert {row["id"] for row in store.list_trash()} == {str(unrelated_id)}
        assert store.list_work_blocks() == (event,)
        assert store.list_focus_sessions() == ()
        assert store.list_completion_dates() == {}
        assert store.list_materials() == (material,)
        for entry in direct:
            with pytest.raises(ValueError, match="отсутствует"):
                saved_attachment_path(tmp_path, entry)
        assert saved_attachment_path(tmp_path, unrelated).read_text() == "Оригинал"
        assert saved_attachment_path(tmp_path, material).read_text() == "Оригинал"
        assert source.read_text() == "Оригинал"
        service.restore(unrelated_id)
        assert len(store.list_attachments()) == 2
    with sqlite3.connect(path) as db:
        assert not db.execute("SELECT * FROM focus_intervals").fetchall()
        assert not db.execute(
            "SELECT 1 FROM task_identities WHERE id=?", (str(task.id),)
        ).fetchone()
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_selected_parent_requires_separately_archived_child(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    leaf = ChecklistItem(title="Отдельный пункт")
    step = Subtask(title="Шаг", items=(leaf,))
    task = Task(title="Задача", subtasks=(step,))
    source = tmp_path / "file.txt"
    source.write_text("Файл")
    with TaskStore(path) as store:
        service = TaskService(store)
        service.create_task(task)
        entry = copy_attachment(store, tmp_path, task.id, leaf.id, source)
        root_file = copy_attachment(store, tmp_path, task.id, task.id, source)
        leaf_id = UUID(service.delete_node(task.id, leaf.id))
        step_id = UUID(service.delete_node(task.id, step.id))
        root_id = UUID(service.delete_node(task.id, task.id))
        with pytest.raises(ValueError, match="отдельно"):
            store.purge_trash({root_id, step_id})
        with pytest.raises(ValueError, match="отдельно"):
            store.purge_trash({step_id})
        assert len(store.list_trash()) == 3 and not store.pending_attachment_cleanup()
        store.purge_trash({leaf_id, step_id})
        clean_deleted_attachments(store, tmp_path)
        assert not (tmp_path / "attachments" / entry.id.hex).exists()
        assert saved_attachment_path(tmp_path, root_file).is_file()
        assert service.restore(root_id).subtasks == ()


def test_cleanup_queue_survives_failure_and_migrates_v8(tmp_path, monkeypatch):
    path = tmp_path / "tasks.sqlite3"
    source = tmp_path / "file.txt"
    source.write_text("Файл")
    task = Task(title="Задача")
    with TaskStore(path) as store:
        store.create_task(task)
        entry = copy_attachment(store, tmp_path, task.id, task.id, source)
        saved = saved_attachment_path(tmp_path, entry)
        archive = UUID(TaskService(store).delete_node(task.id, task.id))
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE attachment_cleanup")
        db.execute("PRAGMA user_version=8")
    with TaskStore(path) as store:
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TRIGGER fail_purge BEFORE DELETE ON trash "
                "BEGIN SELECT RAISE(ABORT,'fail'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            store.purge_trash({archive})
        assert store.list_trash() and not store.pending_attachment_cleanup() and saved.exists()
        with sqlite3.connect(path) as db:
            db.execute("DROP TRIGGER fail_purge")
        store.purge_trash({archive})
        original_unlink = Path.unlink
        with monkeypatch.context() as patch:

            def denied(path, *args, **kwargs):
                if path == saved:
                    raise PermissionError("test failure")
                return original_unlink(path, *args, **kwargs)

            patch.setattr(Path, "unlink", denied)
            clean_deleted_attachments(store, tmp_path)
        assert not store.list_trash() and store.pending_attachment_cleanup() and saved.exists()
        with pytest.raises(ValueError, match="Корзина изменилась"):
            store.purge_trash({archive})
    with TaskStore(path) as store:
        clean_deleted_attachments(store, tmp_path)
        assert not store.pending_attachment_cleanup() and not saved.exists()
        assert source.exists()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_active_focus_blocks_purging_root(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        task = Task(title="Активная")
        store.create_task(task)
        store.change_focus("start", NOW, None, task_id=task.id)
        archive = UUID(TaskService(store).delete_node(task.id, task.id))
        with pytest.raises(ValueError, match="фокус"):
            store.purge_trash({archive})
        assert store.list_trash()
        (session,) = store.list_focus_sessions()
        store.change_focus("finish", NOW, (str(session.id), session.revision))
        store.purge_trash({archive})
        assert not store.list_focus_sessions()


def test_selective_confirmation_in_gui(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    script = Path(__file__).with_name("ui_trash_cleanup_scenario.py")
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
        assert warning not in result.stderr, result.stderr
