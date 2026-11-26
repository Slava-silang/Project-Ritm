"""Regular transition facts use temporary profiles and never become network sends."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zipfile import ZipFile

import pytest
from migration_support import drop_later_task_tables

from ritm.application.tasks import TaskService
from ritm.domain.tasks import Subtask, Task
from ritm.integrations.sync_codec import RegularRecord, TaskRecord
from ritm.storage import backup
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore
from ritm.storage.sync_intents import SyncBinding

DAY = date(2026, 11, 27)
NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
BINDING = SyncBinding(owner_id=UUID(int=911), device_id=UUID(int=912), server_id=UUID(int=913))


def enable(store):
    store.initialize_sync_binding(BINDING)
    store.configure_sync_task_capture(True, {})


def create(store, first=DAY):
    task = Task(title="Read", subtasks=(Subtask(title="Chapter"),))
    store.save_regular_task(task, None, interval=1, first=first, today=DAY)
    return task


def test_creation_current_edit_completion_and_prune_are_typed(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        enable(store)
        task = create(store)
        service = TaskService(store)
        service.rename_node(task.id, task.id, "Reading")
        service.set_completed(task.id, task.id, True)
        service.delete_node(task.id, task.subtasks[0].id, NOW)
        rows = store.list_sync_regular_changes()
        assert [r.kind for r in rows] == ["save"] * 4
        assert rows[0].before is None
        assert all(type(r.after) is RegularRecord for r in rows)
        assert rows[2].after.current.completed_at == NOW
        assert rows[3].before.current.task.subtasks
        assert not rows[3].after.current.task.subtasks
        assert rows[3].after.current.task == store.get_task(task.id)
        assert not store.list_sync_intents()
        assert not store.list_frozen_sync_requests()
        assert "Reading" not in repr(rows)
        store.update_task(task.id, lambda t: t)
        assert store.list_sync_regular_changes() == rows


def test_offline_multiple_completions_survive_rollover_and_reopen(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path, clock=lambda: NOW) as store:
        enable(store)
        create(store)
        completed = []
        for offset in range(1, 4):
            current = next(t for t in store.list_tasks() if not t.completed)
            TaskService(store).set_completed(current.id, current.id, True)
            completed.append(store.get_task(current.id))
            store.refresh_recurrences(DAY + timedelta(days=offset))
        rows = store.list_sync_regular_changes()
        transitions = [r for r in rows if r.kind == "rollover"]
        assert [r.before.current.task for r in transitions] == completed
        assert [r.before.current_day for r in transitions] == [
            DAY + timedelta(days=i) for i in range(3)
        ]
        assert all(r.before.current.completed_at == NOW for r in transitions)
        assert all(not r.after.current.task.completed for r in transitions)
        assert all(r.after.current.completed_at is None for r in transitions)
        assert len({r.rule_id for r in rows}) == 1
    with TaskStore(path) as store:
        assert store.list_sync_regular_changes() == rows
        assert all(store.get_task(t.id) == t for t in completed)
        store.refresh_recurrences(DAY + timedelta(days=3))
        assert store.list_sync_regular_changes() == rows


def test_skip_to_latest_and_waiting_activation_do_not_create_backlog(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        enable(store)
        task = create(store, first=DAY + timedelta(days=2))
        initial = store.list_sync_regular_changes()
        store.refresh_recurrences(DAY + timedelta(days=2))
        assert store.list_sync_regular_changes() == initial
        store.refresh_recurrences(DAY + timedelta(days=100))
        rows = store.list_sync_regular_changes()
        assert len(rows) == 2 and rows[-1].kind == "rollover"
        assert rows[-1].before.current.task.id == task.id
        assert not rows[-1].before.current.task.completed
        assert rows[-1].after.current_day == DAY + timedelta(days=100)
        assert store.is_skipped_occurrence(task.id)


def test_conversion_reschedule_and_stop_preserve_local_facts(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        enable(store)
        task = Task(title="Ordinary")
        store.create_task(task)
        anchor = store.list_sync_intents()[0].intent.id
        store.save_regular_task(task, None, interval=1, first=DAY, today=DAY)
        first = store.list_sync_regular_changes()[0]
        assert type(first.before) is TaskRecord
        assert first.after_intent_id == anchor
        store.save_regular_task(task, None, interval=2, first=DAY, today=DAY)
        second = store.list_sync_regular_changes()[1]
        assert second.before == first.after and second.before.id != second.after.id
        changed = replace(task, title="Stop and edit")
        store.save_regular_task(changed, None, interval=None, first=None, today=DAY)
        last = store.list_sync_regular_changes()[-1]
        assert last.kind == "stop" and last.before == second.after
        assert last.after == TaskRecord(task=changed)
        assert not store.list_recurrences()


def test_deletion_keeps_only_identifiers_and_local_trash(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        enable(store)
        task = create(store)
        store.archive_node(task.id, task.id, lambda t: None, NOW)
        deleted = store.list_sync_regular_changes()[-1]
        assert deleted.kind == "delete"
        assert deleted.before is None and deleted.after is None
        assert deleted.root_id == task.id
        raw = store._db.execute(
            "SELECT before_json,after_json FROM sync_regular_changes ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        assert tuple(raw) == (None, None)
        assert len(store.list_trash()) == 1


@pytest.mark.parametrize("operation", ["create", "edit", "complete", "rollover", "stop", "delete"])
def test_failure_after_journal_insert_rolls_back_everything(tmp_path, monkeypatch, operation):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        enable(store)
        task = create(store)
        store.begin_garden_source(NOW)
        dump = "\n".join(store._db.iterdump())
        original = store._record_regular_change

        def fail(*args):
            original(*args)
            raise RuntimeError("Synthetic transition failure")

        monkeypatch.setattr(store, "_record_regular_change", fail)
        with pytest.raises(RuntimeError, match="Synthetic"):
            if operation == "create":
                create(store)
            elif operation == "complete":
                TaskService(store).set_completed(task.id, task.id, True)
            elif operation == "edit":
                store.update_task(task.id, lambda t: replace(t, title="Edited"))
            elif operation == "rollover":
                store.refresh_recurrences(DAY + timedelta(days=1))
            elif operation == "stop":
                store.save_regular_task(task, None, interval=None, first=None, today=DAY)
            else:
                store.archive_node(task.id, task.id, lambda t: None, NOW)
        assert "\n".join(store._db.iterdump()) == dump


def test_off_and_incoming_do_not_capture_and_disabled_keeps_facts(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        create(store)
        assert store.list_sync_regular_changes() == ()
        enable(store)
        with store.suppress_sync_capture("incoming"):
            create(store)
        assert store.list_sync_regular_changes() == ()
        create(store)
        rows = store.list_sync_regular_changes()
        store.configure_sync_task_capture(False)
        create(store)
        assert store.list_sync_regular_changes() == rows


def test_legacy_migration_and_ddl_failure_are_atomic(tmp_path, monkeypatch):
    from ritm.storage import sqlite as module

    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        task = create(store)
        enable(store)
    with sqlite3.connect(path) as db:
        drop_later_task_tables(db, 17)
        db.execute("PRAGMA user_version=17")
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(
            module, "REGULAR_CAPTURE_SCHEMA", (*module.REGULAR_CAPTURE_SCHEMA, "INVALID DDL")
        )
        with pytest.raises(sqlite3.OperationalError):
            TaskStore(path)
    assert path.read_bytes() == before
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task
        assert store.is_sync_task_capture_enabled()
        assert store.list_sync_regular_changes() == ()
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_public_backup_and_import_strip_facts_without_changing_source(tmp_path):
    profile = tmp_path / "profile"
    with TaskStore(profile / backup.DATABASE) as store:
        enable(store)
        task = create(store)
        rows = store.list_sync_regular_changes()
    archive = backup.create_backup(profile, tmp_path / "copies", NOW)
    copied = tmp_path / "copy.sqlite3"
    with ZipFile(archive) as zipped:
        copied.write_bytes(zipped.read(backup.DATABASE))
    with TaskStore(copied) as store:
        assert store.get_task(task.id) == task
        assert store.list_sync_regular_changes() == ()
        assert not store.is_sync_task_capture_enabled()
    with TaskStore(profile / backup.DATABASE) as store:
        assert store.list_sync_regular_changes() == rows
    preview = backup.inspect_backup(archive)
    backup.restore_backup(profile, archive, preview["token"])
    with TaskStore(profile / backup.DATABASE) as store:
        assert store.get_task(task.id) == task
        assert store.list_sync_regular_changes() == ()


def test_corruption_is_rejected_without_erasing_facts(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        enable(store)
        create(store)
        with store._db:
            store._db.execute(
                "UPDATE sync_regular_changes SET before_json=after_json,kind='rollover'"
            )
        before = "\n".join(store._db.iterdump())
        with pytest.raises(ValueError):
            store.list_sync_regular_changes()
        assert "\n".join(store._db.iterdump()) == before


def test_pending_regular_facts_block_send_candidate(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        enable(store)
        task = Task(title="Ordinary first")
        store.create_task(task)
        pending = store.list_sync_intents()[0].intent
        frozen = store.freeze_sync_intent(pending.id)
        assert store.next_sync_sendable() == frozen
        create(store)
        assert store.next_sync_sendable() is None
        assert store.get_frozen_sync_request(pending.id) == frozen
        assert store.list_frozen_sync_requests() == (frozen,)


def test_nested_read_does_not_commit_or_rollback_caller_transaction(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        enable(store)
        store._db.execute("BEGIN IMMEDIATE")
        store._db.execute("UPDATE sync_capture SET enabled=0")
        with pytest.raises(ValueError, match="транзакции"):
            store.list_sync_regular_changes()
        assert store._db.in_transaction
        store._db.rollback()
        assert store.is_sync_task_capture_enabled()
