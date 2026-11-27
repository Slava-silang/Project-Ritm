from dataclasses import replace
from uuid import uuid4

import pytest
from test_sync_history_plan import journal as journal
from test_sync_history_plan import mutate, plan, with_base

from ritm.application.tasks import TaskService
from ritm.integrations.sync_history_plan import HistoryBase
from ritm.integrations.sync_operations import Operation, Receipt, operation_hash
from ritm.integrations.sync_transitions import plan_detach_effects


@pytest.fixture
def detached(journal):
    _, changes, context = journal
    original = plan(changes, context).proposals[0]
    changes, _ = mutate(
        journal, lambda store, old: TaskService(store).set_completed(old.id, old.id, False)
    )
    root = original.payload.current.task.id
    context = replace(
        with_base(context, original.id, original.payload),
        task_bases=(HistoryBase(id=root, entry=None),),
    )
    return changes, context, original


def test_detach_proposal_roundtrip_to_atomic_effects_and_ack(detached):
    changes, context, original = detached
    result = plan(changes, context)
    proposal = next(p for p in result.proposals if p.action == "detach_occurrence")
    assert proposal.payload.task.id == original.payload.current.task.id
    assert not proposal.payload.task.completed
    op = Operation(
        op_id=uuid4(),
        device_id=uuid4(),
        server_id=context.server_id,
        target_kind="occurrence",
        target_id=proposal.id,
        action=proposal.action,
        base_revision=proposal.base_revision,
        delete_epoch=proposal.delete_epoch,
        payload=proposal.payload,
    )
    ack = Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=8,
        delete_epoch=3,
        error=None,
    )
    before = next(b.entry for b in context.bases if b.id == proposal.id)
    effects = plan_detach_effects(before, op, ack, vacant_task_id=proposal.payload.task.id)
    context = replace(
        context,
        bases=tuple(
            HistoryBase(id=b.id, entry=effects.entries[0]) if b.id == proposal.id else b
            for b in context.bases
        ),
        task_bases=(HistoryBase(id=proposal.payload.task.id, entry=effects.entries[1]),),
        ordinary_roots=frozenset({proposal.payload.task.id}),
        acknowledged_detaches=frozenset({proposal.transition_change}),
    )
    final = plan(changes, context)
    assert proposal.id in final.unchanged
    assert not any(p.id == proposal.id for p in final.proposals)
    assert final.remaining_changes == tuple(c.id for c in changes)


@pytest.mark.parametrize(
    "case,reason",
    [("unknown", "unknown_base"), ("frozen", "frozen_change"), ("ack", "detach_ack_conflict")],
)
def test_detach_does_not_guess_knowledge(detached, case, reason):
    changes, context, original = detached
    source = next(c for c in changes if c.kind == "detach")
    if case == "unknown":
        context = replace(context, task_bases=(HistoryBase(id=uuid4(), entry=None),))
    elif case == "frozen":
        context = replace(context, frozen_changes=frozenset({source.id}))
    else:
        context = replace(context, acknowledged_detaches=frozenset({source.id}))
    result = plan(changes, context)
    assert next(b.reason for b in result.blocked if b.id == original.id) == reason
    assert not any(p.id == original.id for p in result.proposals)


def test_detach_ack_does_not_cover_later_ordinary_edit(detached, journal):
    from ritm.storage.sqlite import TaskStore

    changes, context, original = detached
    proposal = next(p for p in plan(changes, context).proposals if p.action == "detach_occurrence")
    op = Operation(
        op_id=uuid4(),
        device_id=uuid4(),
        server_id=context.server_id,
        target_kind="occurrence",
        target_id=proposal.id,
        action=proposal.action,
        base_revision=proposal.base_revision,
        delete_epoch=proposal.delete_epoch,
        payload=proposal.payload,
    )
    ack = Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=8,
        delete_epoch=3,
        error=None,
    )
    effects = plan_detach_effects(
        context.bases[0].entry, op, ack, vacant_task_id=proposal.payload.task.id
    )
    context = replace(
        context,
        bases=(HistoryBase(id=original.id, entry=effects.entries[0]), *context.bases[1:]),
        task_bases=(HistoryBase(id=proposal.payload.task.id, entry=effects.entries[1]),),
        acknowledged_detaches=frozenset({proposal.transition_change}),
    )
    with TaskStore(journal[0]) as store:
        local_root = next(c.after.task.id for c in changes if c.kind == "detach")
        store.update_task(local_root, lambda task: replace(task, title="Later ordinary edit"))
        changes = store.list_sync_regular_changes()
    assert changes[-1].kind == "root_save"
    result = plan(changes, context)
    assert changes[-1].id not in result.projected_changes
    assert proposal.transition_change in result.projected_changes
    assert original.id in result.unchanged
