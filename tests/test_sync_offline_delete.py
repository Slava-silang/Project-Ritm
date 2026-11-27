from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from test_sync_regular_plan import NOW, accept, mutate, operation, plan, seed

from ritm.integrations.sync_codec import OccurrenceRecord, RemovalRecord, SyncCodecError
from ritm.integrations.sync_operations import (
    Operation,
    Receipt,
    decode_operation,
    encode_operation,
    operation_hash,
)
from ritm.integrations.sync_pages import SnapshotCollector, SnapshotEntry, SnapshotPage
from ritm.integrations.sync_regular_plan import RegularBase
from ritm.integrations.sync_transitions import plan_forget_task_effects, plan_regular_effects


@pytest.fixture
def deleted(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path, 2)
    changes = mutate(
        path,
        lambda store, current: store.archive_node(
            current.id, current.id, lambda task: None, NOW + timedelta(days=2)
        ),
    )
    context = replace(context, moment=NOW + timedelta(days=20))
    context = accept(plan(changes, context).proposals[0], context)
    root = changes[-1].root_id
    return changes, replace(context, terminal_bases=(RegularBase(id=root, entry=None),)), root


def request(proposal, context):
    return Operation(
        op_id=uuid4(),
        device_id=uuid4(),
        server_id=context.server_id,
        target_kind=proposal.target_kind,
        target_id=proposal.id,
        action=proposal.action,
        base_revision=proposal.base_revision,
        delete_epoch=proposal.delete_epoch,
        payload=proposal.payload,
    )


def receipt(op):
    return Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=op.base_revision + 1,
        delete_epoch=op.delete_epoch + 1,
        error=None,
    )


def test_late_delete_protects_absent_slot_before_closing_rule(deleted):
    changes, context, root = deleted
    proposal = plan(changes, context).proposals[0]
    assert proposal.action == "forget_task" and proposal.id == root
    assert proposal.payload is None and proposal.base_revision == proposal.delete_epoch == 0
    op = request(proposal, context)
    assert decode_operation(encode_operation(op)) == op
    effects = plan_forget_task_effects(op, receipt(op), vacant_task_id=root)
    marker = effects.entries[0]
    assert marker.entity.reason == "user_deleted"
    with pytest.raises(SyncCodecError):
        plan_forget_task_effects(op, receipt(op))
    context = replace(context, terminal_bases=(RegularBase(id=root, entry=marker),))
    closing = plan(changes, context).proposals[0]
    assert closing.action == "close_regular"
    close_op = operation(closing)
    result = plan_regular_effects(
        context.bases[0].entry, close_op, receipt(close_op), context.settings, context.moment
    )
    assert len(result.entries) == 2
    entries = (
        SnapshotEntry(revision=1, delete_epoch=0, entity=context.settings),
        marker,
        *result.entries,
    )
    collector = SnapshotCollector(close_op.server_id)
    collector.add(
        SnapshotPage(
            server_id=close_op.server_id,
            snapshot_id=uuid4(),
            set_revision=receipt(close_op).revision,
            total=len(entries),
            offset=0,
            cursor=None,
            next_cursor=None,
            entries=entries,
        )
    )
    assert collector.finish().entries == entries
    assert type(result.entries[0].entity) is RemovalRecord
    assert type(result.entries[1].entity) is OccurrenceRecord
    assert result.entries[1].entity.current == context.bases[0].entry.entity.current
    assert plan(changes, context).remaining_changes == tuple(c.id for c in changes)


def test_unknown_or_retained_slot_is_not_delete_proof(deleted):
    changes, context, root = deleted
    result = plan(changes, replace(context, terminal_bases=()))
    assert not result.proposals and result.blocked[0].reason == "terminal_day_conflict"
    mark = RemovalRecord(
        entity_kind="task", entity_id=root, revision=9, delete_epoch=2, reason="retention"
    )
    context = replace(
        context,
        terminal_bases=(
            RegularBase(id=root, entry=SnapshotEntry(revision=9, delete_epoch=2, entity=mark)),
        ),
    )
    result = plan(changes, context)
    assert result.proposals[0].action == "delete"
    assert result.proposals[0].payload is None


def test_delete_known_live_slot_uses_its_counters(deleted):
    changes, context, root = deleted
    from ritm.integrations.sync_codec import TaskRecord

    body = next(
        c.after.current
        for c in reversed(changes)
        if hasattr(c.after, "current") and c.after.current.task.id == root
    )
    assert type(body) is TaskRecord
    context = replace(
        context,
        terminal_bases=(
            RegularBase(id=root, entry=SnapshotEntry(revision=19, delete_epoch=4, entity=body)),
        ),
    )
    proposal = plan(changes, context).proposals[0]
    assert proposal.action == "delete" and proposal.target_kind == "task"
    assert proposal.base_revision == 19 and proposal.delete_epoch == 4
    assert proposal.payload is None


@pytest.mark.parametrize(
    "action,kind,base",
    [
        ("forget_task", "regular", 0),
        ("forget_task", "task", 1),
        ("close_regular", "task", 1),
        ("close_regular", "regular", 0),
    ],
)
def test_delete_commands_reject_wrong_kind_or_base(action, kind, base, deleted):
    _, context, root = deleted
    op = Operation(
        op_id=uuid4(),
        device_id=uuid4(),
        server_id=context.server_id,
        target_kind=kind,
        target_id=root,
        action=action,
        base_revision=base,
        delete_epoch=0,
        payload=None,
    )
    with pytest.raises(SyncCodecError):
        encode_operation(op)


@pytest.mark.parametrize(
    "action,kind,revision,epoch",
    [("forget_task", "task", 0, 0), ("close_regular", "regular", 8, 3)],
)
def test_terminal_requests_and_receipts_survive_reopen(tmp_path, action, kind, revision, epoch):
    from ritm.storage.sqlite import TaskStore
    from ritm.storage.sync_intents import SyncBinding, SyncIntent
    from ritm.storage.sync_outbox import SyncBase

    path = tmp_path / "queue.sqlite3"
    binding = SyncBinding(owner_id=uuid4(), device_id=uuid4(), server_id=uuid4())
    item = SyncIntent(id=uuid4(), target_kind=kind, target_id=uuid4(), action=action, payload=None)
    basis = SyncBase(
        server_id=binding.server_id,
        target_kind=kind,
        target_id=item.target_id,
        revision=revision,
        delete_epoch=epoch,
    )
    with TaskStore(path) as store:
        store.initialize_sync_binding(binding)
        store.enqueue_sync_intent(item)
        saved = store.freeze_sync_intent(item.id, basis)
        assert store.freeze_sync_intent(item.id).raw == saved.raw
        ack = receipt(saved.operation)
        store.save_sync_receipt(ack)
    with TaskStore(path) as store:
        restored = store.get_frozen_sync_request(item.id)
        assert restored.raw == saved.raw and restored.receipt == ack
        assert store.save_sync_receipt(ack) == restored
        assert store.next_sync_sendable() is None
