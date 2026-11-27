"""Atomic reschedule contract, pure effects, preparation and immutable local outbox."""

from dataclasses import replace
from uuid import uuid4

import pytest
from test_sync_regular_plan import DAY, SERVER, accept, mutate, operation, plan, seed

from ritm.application.tasks import TaskService
from ritm.domain.tasks import Subtask
from ritm.integrations.sync_codec import RegularRecord, RemovalRecord
from ritm.integrations.sync_operations import (
    Receipt,
    decode_operation,
    encode_operation,
    operation_hash,
    verify_receipt,
)
from ritm.integrations.sync_pages import SnapshotCollector, SnapshotEntry, SnapshotPage
from ritm.integrations.sync_regular_plan import RegularBase
from ritm.integrations.sync_transitions import plan_regular_effects
from ritm.storage.sqlite import TaskStore
from ritm.storage.sync_intents import SyncIntent
from ritm.storage.sync_outbox import SyncBase


@pytest.fixture
def transition(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path)
    changes = mutate(
        path,
        lambda store, current: store.save_regular_task(
            replace(current, title="Changed with interval"), None, interval=2, first=DAY, today=DAY
        ),
    )
    replacement = changes[-1].after
    context = replace(context, bases=(*context.bases, RegularBase(id=replacement.id, entry=None)))
    proposal = plan(changes, context).proposals[0]
    return path, changes, context, proposal


def ack(op):
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


def effects(context, op, *, vacant=None):
    before = next(base.entry for base in context.bases if base.id == op.target_id)
    return plan_regular_effects(
        before, op, ack(op), context.settings, context.moment, vacant_rule_id=vacant
    )


def test_wire_and_atomic_effects_preserve_root_and_form_a_valid_full_snapshot(transition):
    _, changes, context, proposal = transition
    op = operation(proposal)
    assert decode_operation(encode_operation(op)) == op
    verify_receipt(op, ack(op))
    result = effects(context, op, vacant=op.payload.id)
    old, new = result.entries
    assert type(old.entity) is RemovalRecord and old.entity.entity_kind == "regular"
    assert old.entity.entity_id == op.target_id and old.entity.root_id is None
    assert type(new.entity) is RegularRecord and new.entity.id == op.payload.id != op.target_id
    assert new.entity.current.task.id == changes[-1].before.current.task.id
    assert new.entity.current.task.title == "Changed with interval"
    assert old.revision == new.revision == ack(op).revision
    assert old.delete_epoch == new.delete_epoch == ack(op).delete_epoch
    settings = SnapshotEntry(revision=1, delete_epoch=0, entity=context.settings)
    collector = SnapshotCollector(SERVER)
    collector.add(
        SnapshotPage(
            server_id=SERVER,
            snapshot_id=uuid4(),
            set_revision=old.revision,
            total=3,
            offset=0,
            cursor=None,
            next_cursor=None,
            entries=(settings, *result.entries),
        )
    )
    assert collector.finish().entries == (settings, *result.entries)
    assert effects(context, op, vacant=op.payload.id) == result


@pytest.mark.parametrize(
    "case",
    [
        "no_vacancy",
        "wrong_vacancy",
        "root",
        "nodes",
        "schedule",
        "day",
        "base",
        "receipt_epoch",
        "same_id",
        "wrong_action",
    ],
)
def test_invalid_reschedule_never_produces_half_an_effect(transition, case):
    _, _, context, proposal = transition
    op = operation(proposal)
    vacant = op.payload.id
    if case == "no_vacancy":
        vacant = None
    elif case == "wrong_vacancy":
        vacant = uuid4()
    elif case == "root":
        op = replace(
            op,
            payload=replace(
                op.payload,
                current=replace(
                    op.payload.current, task=replace(op.payload.current.task, id=uuid4())
                ),
            ),
        )
    elif case == "nodes":
        op = replace(
            op,
            payload=replace(
                op.payload,
                current=replace(
                    op.payload.current,
                    task=replace(
                        op.payload.current.task,
                        subtasks=(*op.payload.current.task.subtasks, Subtask(title="Extra")),
                    ),
                ),
            ),
        )
    elif case == "schedule":
        op = replace(op, payload=replace(op.payload, interval_days=1))
    elif case == "day":
        from datetime import timedelta

        op = replace(op, payload=replace(op.payload, current_day=DAY + timedelta(days=2)))
    elif case == "base":
        context = replace(
            context,
            bases=(
                replace(context.bases[0], entry=replace(context.bases[0].entry, revision=8)),
                context.bases[1],
            ),
        )
    elif case == "receipt_epoch":
        with pytest.raises(ValueError):
            verify_receipt(op, replace(ack(op), delete_epoch=op.delete_epoch))
        return
    elif case == "same_id":
        op = replace(op, payload=replace(op.payload, id=op.target_id))
    else:
        op = replace(op, action="put")
    with pytest.raises(ValueError):
        effects(context, op, vacant=vacant)


def test_snapshot_without_matching_receipt_is_not_treated_as_our_reschedule(transition):
    _, changes, context, proposal = transition
    confirmed = accept(proposal, context)
    assert not plan(changes, confirmed).proposals
    unproved = replace(confirmed, acknowledged_reschedules=frozenset())
    result = plan(changes, unproved)
    assert not result.proposals and {b.reason for b in result.blocked} == {
        "reschedule_confirmation_required"
    }
    contradictory = replace(
        context, acknowledged_reschedules=frozenset({proposal.transition_change})
    )
    assert {b.reason for b in plan(changes, contradictory).blocked} == {"reschedule_ack_conflict"}


def test_missing_absence_or_frozen_fact_blocks_both_sides(transition):
    _, changes, context, proposal = transition
    result = plan(changes, replace(context, bases=(context.bases[0],)))
    assert len(result.blocked) == 2 and not result.proposals
    assert {b.reason for b in result.blocked} == {"unknown_base"}
    result = plan(changes, replace(context, frozen_changes=frozenset({proposal.transition_change})))
    assert len(result.blocked) == 2 and not result.proposals
    assert {b.reason for b in result.blocked} == {"frozen_change"}


def test_later_edits_to_new_rule_wait_for_atomic_confirmation(transition):
    path, _, context, _ = transition
    changes = mutate(
        path,
        lambda store, current: TaskService(store).rename_node(current.id, current.id, "Later edit"),
    )
    first = plan(changes, context).proposals[0]
    assert (
        first.action == "reschedule_regular"
        and first.payload.current.task.title == "Changed with interval"
    )
    confirmed = accept(first, context)
    second = plan(changes, confirmed).proposals[0]
    assert second.id == first.payload.id and second.action == "put"
    assert second.base_revision == first.base_revision + 1
    assert second.delete_epoch == first.delete_epoch + 1
    assert second.payload.current.task.title == "Later edit"


def test_old_current_is_preserved_before_offline_rescheduling(tmp_path):
    from datetime import timedelta

    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 2)
    changes = mutate(
        path,
        lambda store, current: store.save_regular_task(
            current, None, interval=3, first=DAY, today=DAY + timedelta(days=2)
        ),
    )
    context = replace(
        context, bases=(*context.bases, RegularBase(id=changes[-1].after.id, entry=None))
    )
    actions = []
    for _ in range(2):
        proposal = plan(changes, context).proposals[0]
        actions.append(proposal.action)
        context = accept(proposal, context)
    assert actions == ["put", "reschedule_offline"]
    result = plan(changes, context)
    assert not result.proposals and not result.blocked
    assert result.remaining_changes == tuple(c.id for c in changes)


def test_reschedule_chain_advances_one_confirmed_link_at_a_time(transition):
    path, _, context, _ = transition
    changes = mutate(
        path,
        lambda store, current: store.save_regular_task(
            current, None, interval=3, first=DAY, today=DAY
        ),
    )
    context = replace(
        context, bases=(*context.bases, RegularBase(id=changes[-1].after.id, entry=None))
    )
    result = plan(changes, context)
    assert len(result.proposals) == 1
    assert result.proposals[0].payload.id == changes[-1].before.id
    assert {b.reason for b in result.blocked} == {"transition_dependency"}
    context = accept(result.proposals[0], context)
    second = plan(changes, context)
    assert len(second.proposals) == 1
    assert second.proposals[0].payload.id == changes[-1].after.id
    context = accept(second.proposals[0], context)
    final = plan(changes, context)
    assert not final.proposals and not final.blocked
    assert final.remaining_changes == tuple(c.id for c in changes)


def test_reschedule_request_and_receipt_survive_reopen_without_changing_facts(transition):
    path, changes, context, proposal = transition
    pending = SyncIntent(
        id=uuid4(),
        target_kind="regular",
        target_id=proposal.id,
        action=proposal.action,
        payload=proposal.payload,
    )
    with TaskStore(path) as store:
        store.enqueue_sync_intent(pending)
        frozen = store.freeze_sync_intent(
            pending.id,
            SyncBase(
                server_id=SERVER,
                target_kind="regular",
                target_id=proposal.id,
                revision=proposal.base_revision,
                delete_epoch=proposal.delete_epoch,
            ),
        )
        assert store.freeze_sync_intent(pending.id).raw == frozen.raw
        store.save_sync_receipt(ack(frozen.operation))
        assert store.list_sync_regular_changes() == changes
        assert store.next_sync_sendable() is None
    with TaskStore(path) as store:
        saved = store.get_frozen_sync_request(pending.id)
        assert saved.raw == frozen.raw and saved.receipt == ack(frozen.operation)
        assert store.list_sync_regular_changes() == changes
        child = SyncIntent(
            id=uuid4(),
            target_kind="regular",
            target_id=proposal.payload.id,
            action="put",
            payload=proposal.payload,
            depends_on=pending.id,
        )
        store.enqueue_sync_intent(child)
        with pytest.raises(ValueError):
            store.freeze_sync_intent(child.id)  # Cross-target receipt is not an inferred base.
        store.freeze_sync_intent(
            child.id,
            SyncBase(
                server_id=SERVER,
                target_kind="regular",
                target_id=child.target_id,
                revision=saved.receipt.revision,
                delete_epoch=saved.receipt.delete_epoch,
            ),
        )


def test_replacement_id_already_deleted_is_not_reused(transition):
    _, changes, context, proposal = transition
    mark = RemovalRecord(
        entity_kind="regular",
        entity_id=proposal.payload.id,
        revision=9,
        delete_epoch=4,
        reason="user_deleted",
    )
    context = replace(
        context,
        bases=(
            context.bases[0],
            RegularBase(
                id=mark.entity_id, entry=SnapshotEntry(revision=9, delete_epoch=4, entity=mark)
            ),
        ),
    )
    result = plan(changes, context)
    assert not result.proposals and len(result.blocked) == 2
    assert {b.reason for b in result.blocked} == {"new_rule_exists"}
