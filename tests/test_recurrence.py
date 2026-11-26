import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from migration_support import drop_later_task_tables

from ritm.application.tasks import TaskService
from ritm.domain.recurrence import scheduled_day
from ritm.domain.task_list import is_overdue
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore

DAY = date(2026, 11, 26)


def create(store, *, first=DAY, interval=3):
    task = Task(
        title="Читать", subtasks=(Subtask(title="Глава", items=(ChecklistItem(title="Конспект"),)),)
    )
    store.save_regular_task(task, None, interval=interval, first=first, today=DAY)
    return task


def test_fixed_calendar_schedule_and_validation():
    assert scheduled_day(DAY, 3, DAY + timedelta(days=8)) == DAY + timedelta(days=6)
    assert scheduled_day(DAY, 3, DAY - timedelta(days=1)) == DAY
    for value in (0, -1, True, 1.5, 36501):
        with pytest.raises(ValueError):
            scheduled_day(DAY, value, DAY)


def test_missed_replaced_without_backlog_overdue_or_trash_and_restart(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        task = create(store)
        store.refresh_recurrences(DAY + timedelta(days=100))
        rows = store.list_tasks()
        assert len(rows) == 1 and rows[0].id != task.id
        new_id = rows[0].id
        assert not is_overdue(rows[0], datetime(2027, 1, 1, tzinfo=UTC))
        assert store.recurrence_views()[str(new_id)]["repeatDay"] == "2027-03-05"
        assert len(store.list_trash()) == 0
        # Kept locally so focus/file references are never silently deleted.
        assert store.get_task(task.id) is not None
    with TaskStore(path) as store:
        store.refresh_recurrences(DAY + timedelta(days=100))
        assert [t.id for t in store.list_tasks()] == [new_id]


def test_completed_history_kept_new_tree_unchecked_and_schedule_not_shifted(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        old = create(store)
        TaskService(store).set_completed(old.id, old.id, True)
        store.refresh_recurrences(DAY + timedelta(days=2))
        assert len(store.list_tasks()) == 1
        store.refresh_recurrences(DAY + timedelta(days=3))
        history, new = store.list_tasks()
        assert history.completed and history.id == old.id
        assert (
            not new.completed
            and not new.subtasks[0].completed
            and not new.subtasks[0].items[0].completed
        )
        assert new.subtasks[0].id != old.subtasks[0].id
        assert new.subtasks[0].items[0].id != old.subtasks[0].items[0].id
        assert old.id in store.list_completion_dates()
        assert new.id not in store.list_completion_dates()
        assert len(store.list_tasks()) == 2


def test_future_start_management_disable_and_deadline_guard(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        task = create(store, first=DAY + timedelta(days=5))
        assert store.list_tasks() == ()
        assert len(store.list_recurrences()) == 1
        store.refresh_recurrences(DAY + timedelta(days=4))
        assert store.list_tasks() == ()
        store.refresh_recurrences(DAY + timedelta(days=5))
        assert store.list_tasks() == (task,)
        with pytest.raises(ValueError):
            store.update_task(
                task.id, lambda t: replace(t, deadline=datetime(2026, 12, 1, tzinfo=UTC))
            )
        store.save_regular_task(
            replace(task, deadline=datetime(2026, 12, 1, tzinfo=UTC)),
            None,
            interval=None,
            first=None,
            today=DAY,
        )
        assert store.list_recurrences() == ()
        store.refresh_recurrences(DAY + timedelta(days=90))
        assert len(store.list_tasks()) == 1


def test_delete_stops_restore_does_not_restart_schedule(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        task = create(store)
        receipt = TaskService(store).delete_node(task.id, task.id)
        store.refresh_recurrences(DAY + timedelta(days=9))
        assert not store.list_tasks() and not store.list_recurrences()
        TaskService(store).restore(__import__("uuid").UUID(receipt))
        store.refresh_recurrences(DAY + timedelta(days=10))
        assert len(store.list_tasks()) == 1 and not store.list_recurrences()


def test_v13_migration_keeps_garden_identity_and_task(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        task = Task(title="Старая задача")
        store.create_task(task)
        source = store._db.execute("SELECT identity FROM garden_source").fetchone()[0]
    with sqlite3.connect(path) as db:
        drop_later_task_tables(db, 14)
        db.execute("DROP TABLE recurrence_occurrences")
        db.execute("DROP TABLE recurrence_rules")
        db.execute("PRAGMA user_version=13")
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task
        assert store._db.execute("SELECT identity FROM garden_source").fetchone()[0] == source
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert not store.list_recurrences()


def test_refresh_transaction_rolls_back_on_failure(tmp_path, monkeypatch):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        task = create(store)

        def fail(*args, **kwargs):
            raise OSError("test")

        monkeypatch.setattr(store, "_write_task", fail)
        with pytest.raises(OSError):
            store.refresh_recurrences(DAY + timedelta(days=3))
        assert store.list_tasks() == (task,)
        assert store.list_recurrences()[0]["task_id"] == str(task.id)


@pytest.mark.parametrize("capture", [False, True])
def test_regular_tasks_gui_and_restart(tmp_path, capture):
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{key}_HOME": str(tmp_path / key) for key in ("DATA", "CONFIG", "CACHE")},
    }
    for mode in ("run", "reopen"):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("ui_recurrence_scenario.py")),
                str(tmp_path),
                mode,
            ]
            + (["--capture"] if capture else []),
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in (
            "TypeError",
            "ReferenceError",
            "Binding loop",
            "Cannot assign",
            "failed to load",
        ):
            assert warning not in result.stderr, result.stderr


def test_backup_roundtrip_keeps_schedules_and_old_v13_supported(tmp_path):
    from ritm.storage.backup import create_backup, inspect_backup, restore_backup

    profile = tmp_path / "profile"
    profile.mkdir()
    path = profile / "tasks.sqlite3"
    with TaskStore(path) as store:
        task = create(store, first=DAY + timedelta(days=10))
    archive = create_backup(profile, tmp_path / "copies", datetime(2026, 11, 26, tzinfo=UTC))
    preview = inspect_backup(archive)
    with TaskStore(path) as store:
        store.save_regular_task(task, None, interval=None, first=None, today=DAY)
    restore_backup(profile, archive, preview["token"])
    with TaskStore(path) as store:
        assert store.list_tasks() == ()
        assert store.list_recurrences()[0]["task_id"] == str(task.id)
        drop_later_task_tables(store._db, 14)
        store._db.execute("DROP TABLE recurrence_occurrences")
        store._db.execute("DROP TABLE recurrence_rules")
        store._db.execute("PRAGMA user_version=13")
        store._db.commit()
    legacy = create_backup(profile, tmp_path / "copies2", datetime(2026, 11, 27, tzinfo=UTC))
    assert inspect_backup(legacy)["token"]


def test_deleted_future_restores_as_visible_ordinary_task(tmp_path):
    from uuid import UUID

    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        task = create(store, first=DAY + timedelta(days=10))
        service = TaskService(store)
        receipt = service.delete_node(task.id, task.id)
        service.restore(UUID(receipt))
        assert store.list_tasks() == (task,)
        assert not store.list_recurrences()
