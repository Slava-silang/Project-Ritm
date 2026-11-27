"""Past repeats detach without losing identities, dates or their local trash."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from migration_support import drop_later_task_tables

from ritm.application.tasks import TaskService
from ritm.domain.tasks import Subtask, Task
from ritm.integrations.sync_codec import OccurrenceRecord, TaskRecord, history_id
from ritm.storage import backup
from ritm.storage.sqlite import TaskStore
from ritm.storage.sync_intents import SyncBinding

DAY = date(2026, 11, 27)
NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
BINDING = SyncBinding(owner_id=uuid4(), device_id=uuid4(), server_id=uuid4())


def seed(store):
    store.initialize_sync_binding(BINDING)
    store.configure_sync_task_capture(True, {})
    task = Task(title="Read yesterday")
    store.save_regular_task(task, None, interval=1, first=DAY, today=DAY)
    TaskService(store).set_completed(task.id, task.id, True)
    store.refresh_recurrences(DAY + timedelta(days=1))
    return store.get_task(task.id)


def test_history_edit_keeps_stable_slot_and_original_completion_date(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        old = seed(store)
        store.update_task(old.id, lambda t: replace(t, title="Edited old completion"))
        row = store.list_sync_regular_changes()[-1]
        assert row.kind == "history" and row.day == DAY
        assert type(row.before) is OccurrenceRecord and type(row.after) is OccurrenceRecord
        assert row.before.id == row.after.id == history_id(row.rule_id, DAY)
        assert row.before.current.completed_at == row.after.current.completed_at == NOW
        assert row.root_id == old.id
        assert store.list_sync_intents() == ()
        before = store.list_sync_regular_changes()
        store.update_task(old.id, lambda t: t)
        assert store.list_sync_regular_changes() == before


@pytest.mark.parametrize("operation", ["reopen", "deadline"])
def test_history_detaches_permanently_without_changing_root_or_active_series(tmp_path, operation):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path, clock=lambda: NOW) as store:
        old = seed(store)
        active = store.list_recurrences()
        if operation == "reopen":
            TaskService(store).set_completed(old.id, old.id, False)
        else:
            store.update_task(old.id, lambda t: replace(t, deadline=NOW + timedelta(days=2)))
        detached = store.get_task(old.id)
        row = store.list_sync_regular_changes()[-1]
        assert row.kind == "detach" and row.root_id == old.id and row.day == DAY
        assert type(row.before) is OccurrenceRecord and row.after == TaskRecord(
            task=detached, completed_at=NOW if operation == "deadline" else None
        )
        assert (
            store._db.execute(
                "SELECT 1 FROM recurrence_occurrences WHERE task_id=?", (str(old.id),)
            ).fetchone()
            is None
        )
        assert store.list_recurrences() == active
    with TaskStore(path, clock=lambda: NOW + timedelta(days=1)) as store:
        TaskService(store).set_completed(old.id, old.id, True)
        store.update_task(old.id, lambda t: replace(t, title="Ordinary forever"))
        assert store.list_sync_regular_changes()[-1].kind == "root_save"
        assert type(store.list_sync_regular_changes()[-1].after) is TaskRecord
        source = store.read_sync_source()
        assert any(t.task.id == old.id for t in source.tasks)
        assert not any(o.current.task.id == old.id for o in source.history)


def test_stop_then_edit_delete_restore_purge_keeps_lineage_and_blocks_id_reuse(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        old = seed(store)
        current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
        store.save_regular_task(
            current, None, interval=None, first=None, today=DAY + timedelta(days=1)
        )
        store.update_task(current.id, lambda t: replace(t, title="After stop"))
        assert store.list_sync_regular_changes()[-1].kind == "root_save"
        entry = store.archive_node(current.id, current.id, lambda t: None, NOW)
        deleted = store.list_sync_regular_changes()[-1]
        assert deleted.kind == "root_delete" and deleted.before is None and deleted.after is None
        store.restore_entry(UUID(entry), lambda current, before, after, node: before)
        restored = store.list_sync_regular_changes()[-1]
        assert restored.kind == "root_restore" and type(restored.after) is TaskRecord
        assert restored.root_id == current.id and not store.list_recurrences()
        entry = store.archive_node(current.id, current.id, lambda t: None, NOW)
        store.purge_trash({UUID(entry)})
        with pytest.raises(ValueError, match="восстановлением"):
            store.create_task(current)
        assert store.get_task(current.id) is None
        assert store.get_task(old.id).completed


def test_past_repeat_delete_restore_stays_history_and_current_restore_stays_ordinary(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        old = seed(store)
        entry = store.archive_node(old.id, old.id, lambda t: None, NOW)
        assert store.list_sync_regular_changes()[-1].kind == "root_delete"
        store.restore_entry(UUID(entry), lambda current, before, after, node: before)
        restored = store.list_sync_regular_changes()[-1]
        assert restored.kind == "root_restore" and type(restored.after) is OccurrenceRecord
        assert restored.after.current.completed_at == NOW
        current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
        entry = store.archive_node(current.id, current.id, lambda t: None, NOW)
        store.restore_entry(UUID(entry), lambda current, before, after, node: before)
        assert type(store.list_sync_regular_changes()[-1].after) is TaskRecord
        assert not store.list_recurrences()


@pytest.mark.parametrize("operation", ["edit", "detach", "delete", "restore"])
def test_failure_after_insert_rolls_back_history_and_membership(tmp_path, monkeypatch, operation):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        old = seed(store)
        entry = None
        if operation == "restore":
            entry = store.archive_node(old.id, old.id, lambda t: None, NOW)
        store.begin_garden_source(NOW)
        before = "\n".join(store._db.iterdump())
        original = store._record_regular_change

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("Synthetic history journal failure")

        monkeypatch.setattr(store, "_record_regular_change", fail)
        with pytest.raises(RuntimeError, match="Synthetic"):
            if operation == "delete":
                store.archive_node(old.id, old.id, lambda t: None, NOW)
            elif operation == "restore":
                store.restore_entry(UUID(entry), lambda current, before, after, node: before)
            elif operation == "detach":
                TaskService(store).set_completed(old.id, old.id, False)
            else:
                store.update_task(old.id, lambda t: replace(t, title="Edited"))
        assert "\n".join(store._db.iterdump()) == before


def test_v18_migration_preserves_queue_and_recovers_stopped_lineage(tmp_path, monkeypatch):
    from ritm.storage import sqlite as module

    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        seed(store)
        ordinary = Task(title="Frozen migration anchor")
        store.create_task(ordinary)
        pending = store.list_sync_intents()[0].intent
        frozen = store.freeze_sync_intent(pending.id)
        current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
        store.save_regular_task(current, None, interval=None, first=None, today=DAY)
        before = store.list_sync_regular_changes()
        assert before[-1].after_intent_id == pending.id
    with sqlite3.connect(path) as db:
        drop_later_task_tables(db, 18)
        db.execute("PRAGMA user_version=18")
    raw = path.read_bytes()
    original = module.migrate_regular_capture

    def fail(db):
        original(db)
        raise RuntimeError("Synthetic migration failure")

    with monkeypatch.context() as patch:
        patch.setattr(module, "migrate_regular_capture", fail)
        with pytest.raises(RuntimeError):
            TaskStore(path)
    assert path.read_bytes() == raw
    with TaskStore(path) as store:
        assert store.list_sync_regular_changes() == before
        assert store.get_frozen_sync_request(pending.id) == frozen
        store.update_task(current.id, lambda t: replace(t, title="After migration"))
        assert store.list_sync_regular_changes()[-1].kind == "root_save"


def test_backup_import_clears_lineage_only_in_copy(tmp_path):
    profile = tmp_path / "profile"
    with TaskStore(profile / backup.DATABASE) as store:
        old = seed(store)
        rows = store.list_sync_regular_changes()
    archive = backup.create_backup(profile, tmp_path / "copies", NOW)
    with TaskStore(profile / backup.DATABASE) as store:
        assert store.list_sync_regular_changes() == rows
        assert store._regular_lineage(old.id) is not None
    preview = backup.inspect_backup(archive)
    backup.restore_backup(profile, archive, preview["token"])
    with TaskStore(profile / backup.DATABASE) as store:
        assert store.list_sync_regular_changes() == ()
        assert store._db.execute("SELECT count(*) FROM sync_regular_roots").fetchone()[0] == 0
        assert store.get_task(old.id).completed


def test_incoming_and_capture_off_do_not_write_or_detach_history(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        old = seed(store)
        rows = store.list_sync_regular_changes()
        with store.suppress_sync_capture("incoming"):
            store.update_task(old.id, lambda t: replace(t, title="Incoming"))
        store.configure_sync_task_capture(False)
        TaskService(store).set_completed(old.id, old.id, False)
        assert store.list_sync_regular_changes() == rows
        assert store._db.execute(
            "SELECT 1 FROM recurrence_occurrences WHERE task_id=?", (str(old.id),)
        ).fetchone()


def test_current_subtree_restore_is_explicit_for_future_epoch_barrier(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        seed(store)
        current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
        service = TaskService(store)
        child = Subtask(title="Restore child")
        service.add_subtask(current.id, child)
        service.delete_node(current.id, child.id, NOW)
        service.restore(UUID(store.list_trash()[0]["id"]))
        row = store.list_sync_regular_changes()[-1]
        assert row.kind == "restore"
        assert not row.before.current.task.subtasks
        assert row.after.current.task.subtasks[0].id == child.id
