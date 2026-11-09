import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.storage import attachments
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore


def test_attachments_follow_all_levels_and_survive_root_trash(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    leaf = ChecklistItem(title="Пункт")
    step = Subtask(title="Подзадача", items=(leaf,))
    task = Task(title="Задача", subtasks=(step,))
    other = Task(title="Другая")
    expected = []
    with TaskStore(path) as store:
        service = TaskService(store)
        service.create_task(task)
        service.create_task(other)
        for index, owner in enumerate((task, step, leaf)):
            folder = tmp_path / str(index)
            folder.mkdir()
            source = folder / "Конспект.txt"
            payload = f"Файл {index}".encode()
            source.write_bytes(payload)
            entry = attachments.copy_attachment(store, tmp_path, task.id, owner.id, source)
            assert entry.size == len(payload) and entry.name == source.name
            assert source.read_bytes() == payload
            source.rename(folder / "перемещён.txt")
            expected.append((entry, payload))
        with pytest.raises(KeyError):
            attachments.copy_attachment(
                store, tmp_path, other.id, leaf.id, folder / "перемещён.txt"
            )
        assert len(list((tmp_path / "attachments").iterdir())) == 3
        entry = service.delete_node(task.id, task.id)
        assert store.list_attachments() == ()
        assert all(attachments.saved_attachment_path(tmp_path, e).is_file() for e, _ in expected)
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(entry))
        assert store.list_attachments() == tuple(e for e, _ in expected)
        for attachment, payload in expected:
            assert attachments.saved_attachment_path(tmp_path, attachment).read_bytes() == payload


def test_copy_or_database_failure_rolls_back_managed_copy(tmp_path, monkeypatch):
    path = tmp_path / "tasks.sqlite3"
    source = tmp_path / "source.txt"
    source.write_text("Оригинал")
    task = Task(title="Задача")
    with TaskStore(path) as store:
        store.create_task(task)

        def interrupted(original, copy, length):
            copy.write(original.read(2))
            raise OSError("disk full")

        with monkeypatch.context() as patch:
            patch.setattr(attachments.shutil, "copyfileobj", interrupted)
            with pytest.raises(OSError, match="disk full"):
                attachments.copy_attachment(store, tmp_path, task.id, task.id, source)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TRIGGER fail_attachment BEFORE INSERT ON attachments "
                "BEGIN SELECT RAISE(ABORT,'cannot write metadata'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot write metadata"):
            attachments.copy_attachment(store, tmp_path, task.id, task.id, source)
        assert store.list_attachments() == ()
        assert not list((tmp_path / "attachments").iterdir())
        assert source.read_text() == "Оригинал"


def test_v6_profile_migrates_without_rewriting_task_or_focus(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    task = Task(title="Старая задача", completed=True)
    with TaskStore(path) as store:
        store.create_task(task)
        completed = store.list_completion_dates()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE attachments")
        db.execute("PRAGMA user_version=6")
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task
        assert store.list_completion_dates() == completed
        assert store.list_attachments() == ()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_attachment_dialog_selects_file_and_opens_saved_copy(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    script = Path(__file__).with_name("ui_attachments_scenario.py")
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=35,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
        assert warning not in result.stderr, result.stderr
