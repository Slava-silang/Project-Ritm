"""History commands use durable queue without discarding other offline slots."""

from dataclasses import replace
from uuid import UUID

import pytest
from test_sync_history_plan import NOW, mutate, plan, with_base
from test_sync_history_plan import journal as journal

from ritm.application.tasks import TaskService
from ritm.integrations.sync_codec import RemovalRecord
from ritm.integrations.sync_history_plan import HistoryBase
from ritm.integrations.sync_operations import Receipt, operation_hash
from ritm.integrations.sync_pages import SnapshotEntry
from ritm.integrations.sync_transitions import plan_detach_effects
from ritm.storage.sqlite import TaskStore


def ack(frozen):
    op = frozen.operation
    return Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=op.base_revision + 1,
        delete_epoch=op.delete_epoch
        + (op.action in ("prune", "restore", "delete", "detach_occurrence")),
        error=None,
    )


def test_all_three_completions_survive_prepare_ack_and_restart(journal):
    path, changes, context = journal
    proposals = plan(changes, context).proposals
    requests = []
    for proposal in proposals:
        with TaskStore(path) as store:
            frozen = store.enqueue_sync_history(context, proposal.id)
            assert frozen.operation.target_kind == "occurrence"
            assert frozen.operation.payload == proposal.payload
            assert store.enqueue_sync_history(context, proposal.id) == frozen
            store.save_sync_receipt(ack(frozen))
            assert store.list_sync_regular_changes() == changes
            requests.append(frozen)
    with TaskStore(path) as store:
        assert len(store.list_frozen_sync_requests()) == 3
        for frozen in requests:
            assert store.get_frozen_sync_request(frozen.intent_id).receipt == ack(frozen)
        assert set(store.read_sync_prepared_state().identities) == set(context.identities)
        assert store.next_sync_sendable() is None


def test_pending_history_does_not_enqueue_another_request(journal):
    path, changes, context = journal
    proposals = plan(changes, context).proposals
    with TaskStore(path) as store:
        first = store.enqueue_sync_history(context, proposals[0].id)
        with pytest.raises(ValueError):
            store.enqueue_sync_history(context, proposals[1].id)
        assert len(store.list_sync_intents()) == 1
        assert len(store.list_frozen_sync_requests()) == 1
        assert store.enqueue_sync_history(context, proposals[0].id).raw == first.raw


def test_history_prune_is_not_repeated_after_restart(journal):
    path, changes, context = journal
    original = plan(changes, context).proposals[0]
    context = with_base(context, original.id, original.payload)
    changes, _ = mutate(
        journal, lambda store, old: TaskService(store).delete_node(old.id, old.subtasks[0].id, NOW)
    )
    proposal = next(p for p in plan(changes, context).proposals if p.id == original.id)
    assert proposal.action == "prune" and proposal.barrier_changes
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_history(context, original.id)
        store.save_sync_receipt(ack(frozen))
    context = with_base(
        context,
        original.id,
        proposal.payload,
        revision=ack(frozen).revision,
        epoch=ack(frozen).delete_epoch,
    )
    with TaskStore(path) as store:
        with pytest.raises(ValueError, match="Нет однозначного"):
            store.enqueue_sync_history(context, original.id)
        assert len(store.list_sync_intents()) == 1
        assert store.read_sync_prepared_state().barriers == frozenset(proposal.barrier_changes)


def test_detach_confirmation_is_recovered_from_queue(journal):
    path, changes, context = journal
    original = plan(changes, context).proposals[0]
    changes, _ = mutate(
        journal, lambda store, old: TaskService(store).set_completed(old.id, old.id, False)
    )
    context = replace(
        with_base(context, original.id, original.payload),
        task_bases=(HistoryBase(id=original.payload.current.task.id, entry=None),),
    )
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_history(context, original.id)
        receipt = ack(frozen)
        store.save_sync_receipt(receipt)
    effects = plan_detach_effects(
        context.bases[0].entry,
        frozen.operation,
        receipt,
        vacant_task_id=frozen.operation.payload.task.id,
    )
    context = replace(
        context,
        bases=(HistoryBase(id=original.id, entry=effects.entries[0]), *context.bases[1:]),
        task_bases=(HistoryBase(id=frozen.operation.payload.task.id, entry=effects.entries[1]),),
    )
    with TaskStore(path) as store:
        assert store.read_sync_prepared_state().detaches == frozenset({changes[-1].id})
        with pytest.raises(ValueError, match="Нет однозначного"):
            store.enqueue_sync_history(context, original.id)
        assert len(store.list_sync_intents()) == 1


def test_old_restore_does_not_resurrect_a_new_remote_deletion(journal):
    path, changes, context = journal
    original = plan(changes, context).proposals[0]
    changes, _ = mutate(
        journal, lambda store, old: TaskService(store).delete_node(old.id, old.id, NOW)
    )
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(store.list_trash()[0]["id"]))
        changes = store.list_sync_regular_changes()
    context = with_base(context, original.id, original.payload)
    proposal = next(p for p in plan(changes, context).proposals if p.id == original.id)
    assert proposal.action == "restore"
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_history(context, original.id)
        store.save_sync_receipt(ack(frozen))
    mark = RemovalRecord(
        entity_kind="occurrence",
        entity_id=original.id,
        root_id=original.payload.current.task.id,
        revision=20,
        delete_epoch=8,
        reason="user_deleted",
    )
    context = replace(
        context,
        bases=(
            HistoryBase(
                id=original.id, entry=SnapshotEntry(revision=20, delete_epoch=8, entity=mark)
            ),
            *context.bases[1:],
        ),
    )
    with TaskStore(path) as store:
        with pytest.raises(ValueError, match="Нет однозначного"):
            store.enqueue_sync_history(context, original.id)
        assert len(store.list_sync_intents()) == 1


def test_history_restore_ack_does_not_repeat_and_later_edit_uses_put(journal):
    path, changes, context = journal
    original = plan(changes, context).proposals[0]
    changes, _ = mutate(
        journal, lambda store, old: TaskService(store).delete_node(old.id, old.id, NOW)
    )
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(store.list_trash()[0]["id"]))
        changes = store.list_sync_regular_changes()
    context = with_base(context, original.id, original.payload)
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_history(context, original.id)
        store.save_sync_receipt(ack(frozen))
    context = with_base(
        context,
        original.id,
        frozen.operation.payload,
        revision=ack(frozen).revision,
        epoch=ack(frozen).delete_epoch,
    )
    with TaskStore(path) as store:
        with pytest.raises(ValueError, match="Нет однозначного"):
            store.enqueue_sync_history(context, original.id)
        old = next(c.before.current.task for c in changes if c.kind == "rollover")
        store.update_task(old.id, lambda task: replace(task, title="Edited after restore"))
        edited = store.enqueue_sync_history(context, original.id)
        assert edited.operation.action == "put"
        assert edited.operation.payload.current.task.title == "Edited after restore"
        assert edited.operation.delete_epoch == ack(frozen).delete_epoch
