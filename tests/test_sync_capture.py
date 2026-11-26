"""Opt-in capture on disposable profiles, without a transport or real account."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from migration_support import drop_later_task_tables

from ritm.domain.tasks import Subtask, Task
from ritm.storage import backup
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore
from ritm.storage.sync_intents import SyncBinding

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
BINDING = SyncBinding(owner_id=uuid4(), device_id=uuid4(), server_id=uuid4())


def enable(store, known=None):
    store.initialize_sync_binding(BINDING)
    store.configure_sync_task_capture(True, {} if known is None else known)


def actions(store):
    return [row.intent.action for row in store.list_sync_intents()]


def test_opt_in_and_persistence(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        assert not store.is_sync_task_capture_enabled()
        with pytest.raises(ValueError):
            store.configure_sync_task_capture(True, {})
        store.initialize_sync_binding(BINDING)
        with pytest.raises(ValueError):
            store.configure_sync_task_capture(True)
        task = Task(title="Local before consent")
        store.create_task(task)
        assert not actions(store)
        enable(store, {task.id: "live"})
        store.update_task(task.id, lambda t: replace(t, title="Changed"))
        assert actions(store) == ["put"]
    with TaskStore(path) as store:
        assert store.is_sync_task_capture_enabled()
        store.configure_sync_task_capture(False)
        store.update_task(task.id, lambda t: replace(t, title="Local again"))
        assert actions(store) == ["put"]


def test_tree_completion_prune_and_dependency(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        enable(store)
        child = Subtask(title="Step")
        task = Task(title="Task", subtasks=(child,))
        store.create_task(task)
        store.update_task(task.id, lambda t: t)
        store.update_task(task.id, lambda t: replace(t, title="Edited"))
        store.archive_node(task.id, child.id, lambda t: replace(t, subtasks=()), NOW)
        store.update_task(task.id, lambda t: replace(t, completed=True))
        rows = store.list_sync_intents()
        assert actions(store) == ["create", "put", "prune", "put"]
        assert rows[0].intent.depends_on is None
        assert [r.intent.depends_on for r in rows[1:]] == [r.intent.id for r in rows[:-1]]
        assert rows[-1].intent.payload.completed_at == NOW
        assert rows[-1].intent.payload.task == store.get_task(task.id)


def test_delete_restore_and_purge_metadata(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        enable(store)
        task = Task(title="Delete me")
        store.create_task(task)
        entry = store.archive_node(task.id, task.id, lambda t: None, NOW)
        assert actions(store) == ["create", "delete"]
        assert store.list_sync_intents()[-1].intent.payload is None
        assert store.get_sync_task_presence(task.id) == "deleted"
        store.restore_entry(UUID(entry), lambda current, before, after, node: before)
        assert actions(store) == ["create", "delete", "restore"]
        entry = store.archive_node(task.id, task.id, lambda t: None, NOW)
        store.purge_trash({UUID(entry)})
        assert store.get_sync_task_presence(task.id) == "deleted"
        with pytest.raises(ValueError):
            store.create_task(task)
        assert store.get_task(task.id) is None


@pytest.mark.parametrize("operation", ["edit", "complete", "archive", "restore"])
def test_enqueue_failure_rolls_back_whole_mutation(tmp_path, monkeypatch, operation):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        task = Task(title="Atomic")
        store.create_task(task)
        enable(store, {task.id: "live"})
        entry = None
        if operation == "restore":
            entry = store.archive_node(task.id, task.id, lambda t: None, NOW)
        store.begin_garden_source(NOW)
        before = store.get_task(task.id)
        dates = store.list_completion_dates()
        trash = store.list_trash()
        queue = store.list_sync_intents()
        presence = store.get_sync_task_presence(task.id)
        events = store._db.execute("SELECT count(*) FROM garden_events").fetchone()[0]

        def fail(*args):
            raise RuntimeError("Synthetic queue failure")

        monkeypatch.setattr(store, "_enqueue_sync_intent", fail)
        with pytest.raises(RuntimeError, match="Synthetic"):
            if operation == "archive":
                store.archive_node(task.id, task.id, lambda t: None, NOW)
            elif operation == "restore":
                store.restore_entry(UUID(entry), lambda current, before, after, node: before)
            else:
                store.update_task(
                    task.id,
                    lambda t: replace(t, title="Changed", completed=operation == "complete"),
                )
        assert store.get_task(task.id) == before
        assert store.list_completion_dates() == dates
        assert store.list_trash() == trash
        assert store.list_sync_intents() == queue
        assert store.get_sync_task_presence(task.id) == presence
        assert store._db.execute("SELECT count(*) FROM garden_events").fetchone()[0] == events


@pytest.mark.parametrize("state, expected", [("retained", "restore"), (None, "create")])
def test_history_edit_does_not_resurrect_but_reopening_does(tmp_path, state, expected):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        task = Task(title="Old completion", completed=True)
        store.create_task(task)
        enable(store, {task.id: state} if state else {})
        store.update_task(task.id, lambda t: replace(t, title="Local history edit"))
        assert actions(store) == []
        store.update_task(task.id, lambda t: replace(t, completed=False))
        assert actions(store) == [expected]


def test_suppression_is_nested_exception_safe_and_connection_local(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        enable(store)
        with pytest.raises(RuntimeError):
            with store.suppress_sync_capture("incoming"):
                with store.suppress_sync_capture("bootstrap"):
                    store.create_task(Task(title="Received"))
                with TaskStore(path) as other:
                    other.create_task(Task(title="Other connection"))
                raise RuntimeError("Stop")
        store.create_task(Task(title="Local"))
        assert actions(store) == ["create", "create"]
        with pytest.raises(ValueError):
            with store.suppress_sync_capture("unknown"):
                pass


def test_regular_roots_are_deferred_even_after_stop(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        enable(store)
        task = Task(title="Regular")
        day = date(2026, 11, 27)
        store.save_regular_task(task, None, interval=1, first=day, today=day)
        store.refresh_recurrences(day + timedelta(days=1))
        current = store.list_tasks()[0]
        store.update_task(current.id, lambda t: replace(t, title="Regular edit"))
        store.save_regular_task(
            store.get_task(current.id),
            None,
            interval=None,
            first=None,
            today=day + timedelta(days=1),
        )
        store.update_task(current.id, lambda t: replace(t, title="Stopped"))
        assert actions(store) == []
        assert store.get_sync_task_presence(task.id) == "deferred"
        assert store.get_sync_task_presence(current.id) == "deferred"


def test_frozen_request_unchanged_after_next_local_edit(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        enable(store)
        task = Task(title="Initial")
        store.create_task(task)
        first = store.list_sync_intents()[0].intent
        frozen = store.freeze_sync_intent(first.id)
        store.update_task(task.id, lambda t: replace(t, title="Next version"))
        assert store.get_frozen_sync_request(first.id) == frozen
        assert store.list_sync_intents()[1].intent.depends_on == first.id


def test_v16_migration_default_off_and_ddl_rollback(tmp_path, monkeypatch):
    from ritm.storage import sqlite as module

    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        task = Task(title="Preserved")
        store.create_task(task)
        store.initialize_sync_binding(BINDING)
    with sqlite3.connect(path) as db:
        drop_later_task_tables(db, 16)
        db.execute("PRAGMA user_version=16")
    original = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(module, "CAPTURE_SCHEMA", (*module.CAPTURE_SCHEMA, "INVALID DDL"))
        with pytest.raises(sqlite3.OperationalError):
            TaskStore(path)
    assert path.read_bytes() == original
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task
        assert store.get_sync_binding() == BINDING
        assert not store.is_sync_task_capture_enabled()
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_backup_strips_capture_and_queue_only_in_copy(tmp_path):
    profile = tmp_path / "profile"
    with TaskStore(profile / backup.DATABASE) as store:
        enable(store)
        task = Task(title="Backup data")
        store.create_task(task)
    archive = backup.create_backup(profile, tmp_path / "copies", NOW)
    with ZipFile(archive) as zipped:
        copied = tmp_path / "copy.sqlite3"
        copied.write_bytes(zipped.read(backup.DATABASE))
    with TaskStore(copied) as store:
        assert store.get_task(task.id) == task
        assert not store.is_sync_task_capture_enabled()
        assert store.get_sync_binding() is None
        assert store.get_sync_task_presence(task.id) is None
        assert actions(store) == []
    with TaskStore(profile / backup.DATABASE) as store:
        assert store.is_sync_task_capture_enabled()
        assert store.get_sync_task_presence(task.id) == "live"
        assert actions(store) == ["create"]


def test_service_changes_and_local_trash_restore_are_captured(tmp_path):
    from ritm.application.tasks import TaskService

    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        enable(store)
        service = TaskService(store)
        task = service.create_task(Task(title="Through application"))
        child = Subtask(title="Child")
        service.add_subtask(task.id, child)
        service.set_completed(task.id, child.id, True)
        service.delete_node(task.id, child.id, NOW)
        service.restore(UUID(store.list_trash()[0]["id"]))
        assert actions(store) == ["create", "put", "put", "prune", "restore"]
        assert store.list_sync_intents()[-1].intent.payload.task == store.get_task(task.id)
        with pytest.raises(ValueError, match="автоматической"):
            store.update_task_and_enqueue(task.id, lambda t: t, lambda t: None)
        assert len(actions(store)) == 5


def test_invalid_initial_state_rolls_back_opt_in(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.initialize_sync_binding(BINDING)
        identifier = uuid4()
        with pytest.raises(ValueError):
            store.configure_sync_task_capture(True, {identifier: "live", uuid4(): "invalid"})
        assert not store.is_sync_task_capture_enabled()
        assert store.get_sync_task_presence(identifier) is None
