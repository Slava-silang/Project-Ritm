"""Drive real disposable queues from durable snapshots, without manual planner contexts."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from test_sync_regular_plan import DAY, NOW, seed

from ritm.integrations.sync_codec import RegularRecord
from ritm.integrations.sync_operations import Receipt, operation_hash, target_identity
from ritm.integrations.sync_pages import Snapshot, SnapshotEntry
from ritm.integrations.sync_transitions import plan_regular_effects
from ritm.storage.sqlite import TaskStore


def remote(context):
    return Snapshot(
        server_id=context.server_id,
        snapshot_id=uuid4(),
        set_revision=5,
        entries=(
            SnapshotEntry(revision=1, delete_epoch=0, entity=context.settings),
            context.bases[0].entry,
        ),
    )


def confirmed(snapshot, frozen, context):
    """Test-only server response, formed with the existing pure transition validator."""
    from ritm.integrations.sync_codec import RemovalRecord
    from ritm.integrations.sync_transitions import (
        plan_detach_effects,
        plan_forget_task_effects,
        plan_promotion_effects,
    )

    def key(entity):
        return (
            (entity.entity_kind, entity.entity_id)
            if type(entity) is RemovalRecord
            else target_identity(entity)
        )

    op = frozen.operation
    receipt = Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=snapshot.set_revision + 1,
        delete_epoch=op.delete_epoch
        + (
            op.action
            in (
                "delete",
                "restore",
                "prune",
                "reschedule_regular",
                "reschedule_offline",
                "stop_regular",
                "stop_offline",
                "close_regular",
                "detach_occurrence",
                "promote_regular",
                "promote_offline",
                "forget_task",
            )
        ),
        error=None,
    )
    entries = {key(e.entity): e for e in snapshot.entries}
    if op.target_kind == "regular" and op.action not in ("create", "put", "prune", "restore"):
        effects = plan_regular_effects(
            entries[("regular", op.target_id)],
            op,
            receipt,
            context.settings,
            context.moment,
            vacant_rule_id=op.payload.id
            if op.action in ("reschedule_regular", "reschedule_offline")
            else None,
            vacant_task_id=op.payload.task.id if op.action == "stop_offline" else None,
        ).entries
    elif op.action == "detach_occurrence":
        effects = plan_detach_effects(
            entries[(op.target_kind, op.target_id)], op, receipt, vacant_task_id=op.payload.task.id
        ).entries
    elif op.action in ("promote_regular", "promote_offline"):
        effects = plan_promotion_effects(
            entries[(op.target_kind, op.target_id)],
            op,
            receipt,
            context.settings,
            context.moment,
            vacant_rule_id=op.payload.id,
        ).entries
    elif op.action == "forget_task":
        effects = plan_forget_task_effects(op, receipt, vacant_task_id=op.target_id).entries
    elif op.action == "delete":
        old = entries[(op.target_kind, op.target_id)].entity
        effects = (
            SnapshotEntry(
                revision=receipt.revision,
                delete_epoch=receipt.delete_epoch,
                entity=RemovalRecord(
                    entity_kind=op.target_kind,
                    entity_id=op.target_id,
                    root_id=(old.root_id if type(old) is RemovalRecord else old.current.task.id)
                    if op.target_kind == "occurrence"
                    else None,
                    revision=receipt.revision,
                    delete_epoch=receipt.delete_epoch,
                    reason="user_deleted",
                ),
            ),
        )
    else:
        effects = (
            SnapshotEntry(
                revision=receipt.revision, delete_epoch=receipt.delete_epoch, entity=op.payload
            ),
        )
    from ritm.integrations.sync_calendar_effects import calendar_transition_effects

    effects = (*effects, *calendar_transition_effects(snapshot, op, receipt, effects))
    for entry in effects:
        entries[key(entry.entity)] = entry
    return receipt, replace(
        snapshot,
        snapshot_id=uuid4(),
        set_revision=receipt.revision,
        entries=tuple(entries.values()),
    )


def test_resume_offline_completions_to_settled_from_cached_state(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 3)
    snapshot = remote(context)
    with TaskStore(path) as store:
        store.remember_sync_snapshot(snapshot)
    actions = []
    for _ in range(7):
        with TaskStore(path) as store:
            step = store.prepare_next_sync(context.moment)
            if step.status == "settled":
                break
            assert step.status == "prepared", step
            frozen = step.request
            assert store.prepare_next_sync(context.moment).status == "pending"
            assert store.prepare_next_sync(context.moment).request.raw == frozen.raw
            actions.append(frozen.operation.action)
        with TaskStore(path) as store:
            assert store.prepare_next_sync(context.moment).request.raw == frozen.raw
            receipt, snapshot = confirmed(snapshot, frozen, context)
            store.save_sync_receipt_and_snapshot(receipt, snapshot)
            assert store.list_sync_regular_changes() == changes
    else:
        raise AssertionError("Preparation did not settle within the expected finite steps")
    assert actions == ["put", "rollover", "create", "create"]
    with TaskStore(path) as store:
        assert store.prepare_next_sync(context.moment).status == "idle"
        assert not store.list_sync_regular_changes()
        assert len([e for e in snapshot.entries if type(e.entity) is RegularRecord]) == 1
        assert store.next_sync_sendable() is None


def test_missing_snapshot_does_not_invent_absence_or_create_commands(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path, 1)
    with TaskStore(path) as store:
        assert store.prepare_next_sync(context.moment).status == "needs_snapshot"
        assert not store.list_sync_intents()


def test_local_day_requires_explicit_refresh_not_phantom_completion(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path)
    with TaskStore(path) as store:
        store.remember_sync_snapshot(remote(context))
        step = store.prepare_next_sync(NOW + timedelta(days=3))
        assert step.status == "blocked" and "local_refresh_required" in step.reasons
        assert not store.list_sync_intents()


def test_ordinary_captured_intent_is_frozen_from_actual_cached_base(tmp_path):
    from ritm.domain.tasks import Task

    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path)
    with TaskStore(path) as store:
        store.remember_sync_snapshot(remote(context))
        task = Task(title="Independent ordinary task")
        store.create_task(task)
        step = store.prepare_next_sync(NOW)
        assert step.status == "prepared"
        assert step.request.operation.target_id == task.id
        assert step.request.operation.action == "create"
        receipt, snapshot = confirmed(remote(context), step.request, context)
        store.save_sync_receipt_and_snapshot(receipt, snapshot)
        assert store.prepare_next_sync(NOW).status == "settled"


def test_new_regular_reschedule_and_stop_resume_without_manual_context(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path)
    snapshot = replace(remote(context), set_revision=1, entries=(remote(context).entries[0],))
    with TaskStore(path) as store:
        store.remember_sync_snapshot(snapshot)
        first = store.prepare_next_sync(NOW)
        assert first.request.operation.action == "create"
        receipt, snapshot = confirmed(snapshot, first.request, context)
        store.save_sync_receipt_and_snapshot(receipt, snapshot)
        task = store.get_task(context.bases[0].entry.entity.current.task.id)
        store.save_regular_task(task, None, interval=2, first=DAY, today=DAY)
        changed = store.prepare_next_sync(NOW)
        assert changed.request.operation.action == "reschedule_regular"
        receipt, snapshot = confirmed(snapshot, changed.request, context)
        store.save_sync_receipt_and_snapshot(receipt, snapshot)
    with TaskStore(path) as store:
        assert store.prepare_next_sync(NOW).status == "settled"
        store.save_regular_task(task, None, interval=None, first=None, today=DAY)
        stop = store.prepare_next_sync(NOW)
        assert stop.request.operation.action == "stop_regular"
        receipt, snapshot = confirmed(snapshot, stop.request, context)
        store.save_sync_receipt_and_snapshot(receipt, snapshot)
    with TaskStore(path) as store:
        result = store.prepare_next_sync(NOW)
        assert result.status == "settled", result
        assert store.get_task(task.id) is not None
        assert len(store.list_frozen_sync_requests()) == 3


def test_receipt_without_covering_snapshot_waits_for_refresh(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path, 1)
    snapshot = remote(context)
    with TaskStore(path) as store:
        store.remember_sync_snapshot(snapshot)
        first = store.prepare_next_sync(context.moment)
        receipt, after = confirmed(snapshot, first.request, context)
        store.save_sync_receipt(receipt)
        result = store.prepare_next_sync(context.moment)
        assert result.status == "needs_snapshot"
        assert len(store.list_sync_intents()) == 1
        store.remember_sync_snapshot(after)
        result = store.prepare_next_sync(context.moment)
        assert result.status == "prepared" and result.request.operation.action == "rollover"


def test_rejected_head_is_not_reprepared_or_overwritten(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path, 1)
    with TaskStore(path) as store:
        snapshot = remote(context)
        store.remember_sync_snapshot(snapshot)
        first = store.prepare_next_sync(context.moment)
        receipt, _ = confirmed(snapshot, first.request, context)
        store.save_sync_receipt(replace(receipt, status="rejected", error="stale_epoch"))
    with TaskStore(path) as store:
        result = store.prepare_next_sync(context.moment)
        assert result.status == "conflict"
        assert result.request.raw == first.request.raw
        assert len(store.list_sync_intents()) == 1


def test_loop_does_not_commit_callers_transaction(tmp_path):
    import pytest

    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path)
    with TaskStore(path) as store:
        store._db.execute("BEGIN")
        with pytest.raises(ValueError, match="отдельную транзакцию"):
            store.prepare_next_sync(context.moment)
        assert store._db.in_transaction
        store._db.rollback()


def test_settled_retirement_keeps_tasks_lineage_and_idempotent_receipts(tmp_path):
    import sqlite3

    import pytest

    from ritm.application.tasks import TaskService

    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path)
    snapshot = remote(context)
    with TaskStore(path) as store:
        store.remember_sync_snapshot(snapshot)
        task = store.get_task(context.bases[0].entry.entity.current.task.id)
        TaskService(store).delete_node(task.id, task.subtasks[0].id, NOW)
        changes = store.list_sync_regular_changes()
        step = store.prepare_next_sync(NOW)
        assert step.request.operation.action == "prune"
        receipt, snapshot = confirmed(snapshot, step.request, context)
        store.save_sync_receipt_and_snapshot(receipt, snapshot)
        assert store.read_sync_prepared_state().barriers
        with store._db:
            store._db.execute(
                "CREATE TRIGGER fail_retire BEFORE DELETE ON sync_regular_changes "
                "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            store.prepare_next_sync(NOW)
        assert store.read_sync_prepared_state().barriers
        assert store.list_sync_regular_changes() == changes
        with store._db:
            store._db.execute("DROP TRIGGER fail_retire")
        retired = store.prepare_next_sync(NOW)
        assert retired.status == "settled" and retired.retired_facts == len(changes)
        assert retired.remaining_facts == 0 and not store.list_sync_regular_changes()
        assert not store.read_sync_prepared_state().barriers
        assert store.get_task(task.id) is not None
        assert store._db.execute("SELECT count(*) FROM sync_regular_roots").fetchone()[0] == 1
        assert store.save_sync_receipt(receipt).raw == step.request.raw
        assert not store.read_sync_prepared_state().barriers
        assert store.prepare_next_sync(NOW).status == "idle"
