"""Real disposable calendar queue, fake verified responses, no network."""

import sqlite3
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from test_sync_block_capture import event
from test_sync_category_capture import NOW, bound
from test_sync_prepare_loop import confirmed

from ritm.domain.tasks import Task
from ritm.integrations.sync_codec import BlockRecord, RemovalRecord, TaskRecord
from ritm.integrations.sync_pages import SnapshotEntry
from ritm.storage.sqlite import TaskStore


def ack(store, step):
    receipt, snapshot = confirmed(store.read_sync_snapshot(), step.request, None)
    store.save_sync_receipt_and_snapshot(receipt, snapshot)


def known(store, record, *, revision=1, epoch=0):
    snapshot = store.read_sync_snapshot()
    store.remember_sync_snapshot(
        replace(
            snapshot,
            snapshot_id=uuid4(),
            set_revision=max(snapshot.set_revision + 1, revision),
            entries=(
                *snapshot.entries,
                SnapshotEntry(
                    revision=revision,
                    delete_epoch=epoch,
                    entity=record,
                ),
            ),
        )
    )


def test_event_pending_bytes_restart_and_new_edit_survive_ack(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    original = event()
    with bound(path, snapshot=True) as store:
        store.create_work_block(original)
        first = store.prepare_next_sync(NOW)
        assert first.status == "prepared" and first.request.operation.action == "create"
        assert first.request.operation.payload.block == original
        edited = replace(original, title="Changed")
        store.update_work_block(edited)
        assert store.prepare_next_sync(NOW).request.raw == first.request.raw
    with TaskStore(path) as store:
        assert store.prepare_next_sync(NOW).request.raw == first.request.raw
        ack(store, first)
        second = store.prepare_next_sync(NOW)
        assert second.status == "prepared" and second.request.operation.action == "put"
        assert second.request.operation.payload.block == edited
        assert len(store.list_sync_block_changes()) == 1
        ack(store, second)
        assert store.prepare_next_sync(NOW).status == "settled"
        assert not store.list_sync_block_changes()
        assert store.get_work_block(original.id) == edited
        assert store.prepare_next_sync(NOW).status == "idle"


def test_coalesce_unsent_edits_and_separate_events(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        original, other = event(), event()
        store.create_work_block(original)
        store.create_work_block(other)
        updated = replace(original, title="Final")
        store.update_work_block(updated)
        first = store.prepare_next_sync(NOW)
        assert first.request.operation.payload.block == updated
        ack(store, first)
        second = store.prepare_next_sync(NOW)
        assert second.request.operation.payload.block == other
        ack(store, second)
        assert store.prepare_next_sync(NOW).status == "settled"
        assert not store.list_sync_block_changes()


def test_task_is_confirmed_before_linked_block(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        task = Task(title="Reading")
        store.create_task(task)
        block = event(task.id)
        store.create_work_block(block)
        first = store.prepare_next_sync(NOW)
        assert first.request.operation.target_kind == "task"
        assert store.prepare_next_sync(NOW).status == "pending"
        ack(store, first)
        second = store.prepare_next_sync(NOW)
        assert second.request.operation.target_kind == "block"
        assert second.request.operation.payload == BlockRecord(block=block)
        ack(store, second)
        assert store.prepare_next_sync(NOW).status == "settled"


@pytest.mark.parametrize("reason", ["retention", "user_deleted", None])
def test_missing_root_caption_requires_real_retention_marker(tmp_path, reason):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        task = Task(title="Local history")
        with store.suppress_sync_capture("bootstrap"):
            store.create_task(task)
        if reason is not None:
            known(
                store,
                RemovalRecord(
                    entity_kind="task",
                    entity_id=task.id,
                    revision=1,
                    delete_epoch=1,
                    reason=reason,
                ),
                epoch=1,
            )
        store.create_work_block(event(task.id))
        step = store.prepare_next_sync(NOW)
        if reason == "retention":
            assert step.status == "prepared"
            assert step.request.operation.payload.archived_task_title == task.title
            ack(store, step)
            assert store.prepare_next_sync(NOW).status == "settled"
        else:
            assert step.status == "blocked"
            assert step.reasons == (
                ("calendar_parent_deleted",) if reason else ("calendar_parent_pending",)
            )
            assert store.list_sync_block_changes() and not store.list_sync_intents()


def test_canonical_parent_identity_is_used_without_changing_local_block(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        local = Task(title="Local")
        remote = replace(local, id=uuid4())
        with store.suppress_sync_capture("bootstrap"):
            store.create_task(local)
        known(store, TaskRecord(task=remote))
        with store._db:
            store._db.execute(
                "INSERT INTO sync_identities VALUES (?,?,1)", (str(local.id), str(remote.id))
            )
        block = event(local.id)
        store.create_work_block(block)
        step = store.prepare_next_sync(NOW)
        assert step.request.operation.payload.block.task_id == remote.id
        assert store.get_work_block(block.id).task_id == local.id
        ack(store, step)
        assert store.prepare_next_sync(NOW).status == "settled"


@pytest.mark.parametrize("age, included", [(30, True), (31, False)])
def test_calendar_age_boundary_preserves_local_history(tmp_path, age, included):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        block = replace(
            event(), starts_at=NOW - timedelta(days=age, hours=1), ends_at=NOW - timedelta(days=age)
        )
        store.create_work_block(block)
        step = store.prepare_next_sync(NOW)
        assert step.status == ("prepared" if included else "settled")
        if included:
            ack(store, step)
            assert store.prepare_next_sync(NOW).status == "settled"
        assert not store.list_sync_block_changes()
        assert store.get_work_block(block.id) == block


def test_unknown_existing_event_is_not_created_as_new(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        block = event()
        with store.suppress_sync_capture("bootstrap"):
            store.create_work_block(block)
        store.update_work_block(replace(block, title="Edit existing"))
        step = store.prepare_next_sync(NOW)
        assert step.status == "blocked" and step.reasons == ("calendar_base_missing",)
        assert store.list_sync_block_changes()


def test_stale_snapshot_does_not_consume_acked_calendar_fact(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        store.create_work_block(event())
        step = store.prepare_next_sync(NOW)
        receipt, _ = confirmed(store.read_sync_snapshot(), step.request, None)
        store.save_sync_receipt(receipt)
        assert store.prepare_next_sync(NOW).status == "needs_snapshot"
        assert len(store.list_sync_block_changes()) == 1


@pytest.mark.parametrize("failure", ["freeze", "retire"])
def test_sql_failure_rolls_back_preparation_or_retirement(tmp_path, failure):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        store.create_work_block(event())
        facts = store.list_sync_block_changes()
        table = "sync_outbox" if failure == "freeze" else "sync_block_changes"
        verb = "INSERT" if failure == "freeze" else "DELETE"
        if failure == "retire":
            ack(store, store.prepare_next_sync(NOW))
        before_queue = store.list_sync_intents()
        with store._db:
            store._db.execute(
                f"CREATE TRIGGER fail BEFORE {verb} ON {table} "
                "BEGIN SELECT RAISE(ABORT,'calendar failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="calendar failure"):
            store.prepare_next_sync(NOW)
        assert store.list_sync_block_changes() == facts
        assert store.list_sync_intents() == before_queue


def test_live_event_moved_outside_window_is_put_for_server_retention(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        block = event()
        store.create_work_block(block)
        ack(store, store.prepare_next_sync(NOW))
        assert store.prepare_next_sync(NOW).status == "settled"
        old = replace(block, starts_at=NOW - timedelta(days=32), ends_at=NOW - timedelta(days=31))
        store.update_work_block(old)
        step = store.prepare_next_sync(NOW)
        assert step.request.operation.action == "put"
        assert step.request.operation.payload.block == old
        receipt, snapshot = confirmed(store.read_sync_snapshot(), step.request, None)
        mark = RemovalRecord(
            entity_kind="block",
            entity_id=block.id,
            revision=receipt.revision,
            delete_epoch=receipt.delete_epoch,
            reason="retention",
        )
        entries = tuple(
            replace(e, entity=mark) if type(e.entity) is BlockRecord else e
            for e in snapshot.entries
        )
        store.save_sync_receipt_and_snapshot(receipt, replace(snapshot, entries=entries))
        assert store.prepare_next_sync(NOW).status == "settled"
        assert store.get_work_block(block.id) == old
        # An intentional edit into the retained window uses restore, never put.
        store.update_work_block(block)
        step = store.prepare_next_sync(NOW)
        assert step.request.operation.action == "restore"
        ack(store, step)
        assert store.prepare_next_sync(NOW).status == "settled"


def test_user_deleted_marker_is_not_revived_by_ordinary_edit(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        block = event()
        with store.suppress_sync_capture("bootstrap"):
            store.create_work_block(block)
        known(
            store,
            RemovalRecord(
                entity_kind="block",
                entity_id=block.id,
                revision=1,
                delete_epoch=1,
                reason="user_deleted",
            ),
            epoch=1,
        )
        store.update_work_block(replace(block, title="Changed"))
        step = store.prepare_next_sync(NOW)
        assert step.status == "blocked" and step.reasons == ("calendar_deleted",)
        assert not store.list_sync_intents()


def test_coalesced_create_then_root_delete_never_uploads_hidden_block(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        task = Task(title="Delete before exchange")
        store.create_task(task)
        block = event(task.id)
        store.create_work_block(block)
        store.archive_node(task.id, task.id, lambda _: None, NOW)
        first = store.prepare_next_sync(NOW)
        ack(store, first)
        second = store.prepare_next_sync(NOW)
        assert second.request.operation.action == "delete"
        ack(store, second)
        assert store.prepare_next_sync(NOW).status == "settled"
        assert not store.list_sync_block_changes()
        assert all(q.intent.target_kind == "task" for q in store.list_sync_intents())


def test_nested_node_uuid_cannot_be_taken_by_calendar_block(tmp_path):
    from decimal import Decimal

    from ritm.domain.tasks import Subtask

    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        child = Subtask(title="Child", weight=Decimal(100))
        task = Task(title="Task", subtasks=(child,))
        known(store, TaskRecord(task=task))
        store.create_work_block(replace(event(), id=child.id))
        step = store.prepare_next_sync(NOW)
        assert step.status == "conflict" and step.reasons == ("calendar_identity_occupied",)
        assert not store.list_sync_intents() and store.list_sync_block_changes()


def test_ack_retirement_and_next_freeze_share_one_transaction(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        store.create_work_block(event())
        first = store.prepare_next_sync(NOW)
        store.create_work_block(event())
        ack(store, first)
        facts = store.list_sync_block_changes()
        queue = store.list_sync_intents()
        with store._db:
            store._db.execute(
                "CREATE TRIGGER fail BEFORE INSERT ON sync_outbox "
                "BEGIN SELECT RAISE(ABORT,'next freeze failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="next freeze failure"):
            store.prepare_next_sync(NOW)
        assert store.list_sync_block_changes() == facts
        assert store.list_sync_intents() == queue


@pytest.mark.parametrize("foreign", [False, True])
def test_delete_then_restore_uses_confirmed_block_marker(tmp_path, foreign):
    from uuid import UUID

    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        task = Task(title="Reading")
        store.create_task(task)
        ack(store, store.prepare_next_sync(NOW))
        block = event(task.id)
        store.create_work_block(block)
        ack(store, store.prepare_next_sync(NOW))
        assert store.prepare_next_sync(NOW).status == "settled"
        entry = store.archive_node(task.id, task.id, lambda _: None, NOW)
        step = store.prepare_next_sync(NOW)
        receipt, snapshot = confirmed(store.read_sync_snapshot(), step.request, None)
        # Fake server's atomic task-delete effect also marks linked calendar blocks.
        mark = RemovalRecord(
            entity_kind="block",
            entity_id=block.id,
            revision=receipt.revision,
            delete_epoch=1,
            reason="user_deleted",
        )
        entries = tuple(
            replace(e, entity=mark, revision=receipt.revision, delete_epoch=1)
            if type(e.entity) is BlockRecord
            else e
            for e in snapshot.entries
        )
        store.save_sync_receipt_and_snapshot(receipt, replace(snapshot, entries=entries))
        assert store.prepare_next_sync(NOW).status == "settled"
        assert not store.list_sync_block_changes()
        if foreign:
            snapshot = store.read_sync_snapshot()
            changed = replace(mark, revision=snapshot.set_revision + 1, delete_epoch=2)
            store.remember_sync_snapshot(
                replace(
                    snapshot,
                    snapshot_id=uuid4(),
                    set_revision=changed.revision,
                    entries=tuple(
                        replace(e, entity=changed, revision=changed.revision, delete_epoch=2)
                        if type(e.entity) is RemovalRecord and e.entity.entity_kind == "block"
                        else e
                        for e in snapshot.entries
                    ),
                )
            )
        store.restore_entry(UUID(entry), lambda current, before, after, node: before)
        root = store.prepare_next_sync(NOW)
        assert root.request.operation.target_kind == "task"
        ack(store, root)
        calendar = store.prepare_next_sync(NOW)
        if foreign:
            assert calendar.status == "conflict"
            assert calendar.reasons == ("base_epoch_conflict",)
            assert store.list_sync_block_changes()
            return
        assert calendar.request.operation.action == "restore"
        assert calendar.request.operation.payload.block == block
        ack(store, calendar)
        assert store.prepare_next_sync(NOW).status == "settled"
        assert store.get_work_block(block.id) == block


def test_retention_uses_utc_duration_across_daylight_saving_change(tmp_path):
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    moment = datetime(2026, 11, 15, 12, tzinfo=ZoneInfo("Europe/Paris"))
    cutoff = moment.astimezone(UTC) - timedelta(days=30)
    block = replace(
        event(), starts_at=cutoff - timedelta(hours=1), ends_at=cutoff - timedelta(minutes=30)
    )
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        store.create_work_block(block)
        assert store.prepare_next_sync(moment).status == "settled"
        assert not store.list_sync_intents()
        assert store.get_work_block(block.id) == block


@pytest.mark.parametrize("effect", ["missing", "different"])
def test_receipt_watermark_alone_does_not_prove_calendar_effect(tmp_path, effect):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        store.create_work_block(event())
        step = store.prepare_next_sync(NOW)
        receipt, snapshot = confirmed(store.read_sync_snapshot(), step.request, None)
        entries = []
        for entry in snapshot.entries:
            if type(entry.entity) is BlockRecord:
                if effect == "missing":
                    continue
                entry = replace(
                    entry,
                    entity=replace(
                        entry.entity, block=replace(entry.entity.block, title="Wrong payload")
                    ),
                )
            entries.append(entry)
        store.save_sync_receipt_and_snapshot(receipt, replace(snapshot, entries=tuple(entries)))
        result = store.prepare_next_sync(NOW)
        assert result.status == ("needs_snapshot" if effect == "missing" else "conflict")
        assert len(store.list_sync_block_changes()) == 1
