from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from ritm.domain.tasks import Task
from ritm.integrations.sync_codec import (
    CalendarRecord,
    OccurrenceRecord,
    RegularRecord,
    TaskRecord,
    history_id,
)
from ritm.integrations.sync_operations import (
    Operation,
    Receipt,
    decode_operation,
    encode_operation,
    operation_hash,
)
from ritm.integrations.sync_pages import SnapshotCollector, SnapshotEntry, SnapshotPage
from ritm.integrations.sync_promotion_plan import PromotionBase
from ritm.integrations.sync_regular_changes import RegularChange
from ritm.integrations.sync_regular_plan import RegularBase, RegularContext, plan_regular
from ritm.integrations.sync_transitions import plan_promotion_effects

DAY = date(2026, 11, 27)
NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


def setup(kind):
    root, rule, server = uuid4(), uuid4(), uuid4()
    task = Task(id=root, title="Promote", completed=kind == "occurrence")
    current = TaskRecord(task=task)
    source = (
        current
        if kind == "task"
        else OccurrenceRecord(id=history_id(rule, DAY), rule_id=rule, day=DAY, current=current)
    )
    identifier = root if kind == "task" else source.id
    target = RegularRecord(
        id=uuid4(), first_day=DAY, interval_days=2, current_day=DAY, current=current
    )
    change = RegularChange(
        sequence=1,
        id=uuid4(),
        after_intent_id=None,
        kind="save",
        rule_id=target.id,
        root_id=root,
        before=source,
        after=target,
    )
    before = SnapshotEntry(revision=4, delete_epoch=2, entity=source)
    settings = CalendarRecord(id=uuid4(), timezone="UTC")
    context = RegularContext(
        server_id=server,
        settings=settings,
        moment=NOW,
        bases=(RegularBase(id=target.id, entry=None),),
        identities=((root, root),),
        promotion_bases=(PromotionBase(kind=kind, id=identifier, entry=before),),
    )
    return change, context, before


@pytest.mark.parametrize("kind", ["task", "occurrence"])
def test_prepare_promote_confirm_and_validate_complete_snapshot(kind):
    change, context, before = setup(kind)
    result = plan_regular((change,), context, server_id=context.server_id)
    assert not result.blocked and len(result.proposals) == 1
    proposal = result.proposals[0]
    assert proposal.action == "promote_regular" and proposal.target_kind == kind
    op = Operation(
        op_id=uuid4(),
        device_id=uuid4(),
        server_id=context.server_id,
        target_kind=kind,
        target_id=proposal.id,
        action=proposal.action,
        payload=proposal.payload,
        base_revision=4,
        delete_epoch=2,
    )
    assert decode_operation(encode_operation(op)) == op
    ack = Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=5,
        delete_epoch=3,
        error=None,
    )
    effects = plan_promotion_effects(
        before, op, ack, context.settings, NOW, vacant_rule_id=proposal.payload.id
    )
    mark, new = effects.entries
    assert mark.entity.reason == "type_changed" and new.entity.current.task.id == change.root_id
    entries = (SnapshotEntry(revision=1, delete_epoch=0, entity=context.settings), mark, new)
    collector = SnapshotCollector(context.server_id)
    collector.add(
        SnapshotPage(
            server_id=context.server_id,
            snapshot_id=uuid4(),
            set_revision=5,
            total=3,
            offset=0,
            cursor=None,
            next_cursor=None,
            entries=entries,
        )
    )
    assert collector.finish().entries == entries
    confirmed = replace(
        context,
        bases=(RegularBase(id=new.entity.id, entry=new),),
        promotion_bases=(PromotionBase(kind=kind, id=proposal.id, entry=mark),),
        acknowledged_promotions=frozenset({change.id}),
    )
    final = plan_regular((change,), confirmed, server_id=context.server_id)
    assert not final.proposals and not final.blocked
    assert final.remaining_changes == (change.id,)
    with pytest.raises(ValueError):
        plan_promotion_effects(before, op, ack, context.settings, NOW)
    duplicate = SnapshotCollector(context.server_id)
    with pytest.raises(ValueError):
        duplicate.add(
            SnapshotPage(
                server_id=context.server_id,
                snapshot_id=uuid4(),
                set_revision=5,
                total=4,
                offset=0,
                cursor=None,
                next_cursor=None,
                entries=(*entries, mark),
            )
        )


@pytest.mark.parametrize(
    "case,reason",
    [("unknown", "unknown_base"), ("frozen", "frozen_change"), ("ack", "promotion_ack_conflict")],
)
def test_promotion_blocks_unknown_or_unproved_state(case, reason):
    change, context, _ = setup("task")
    if case == "unknown":
        context = replace(context, bases=())
    elif case == "frozen":
        context = replace(context, frozen_changes=frozenset({change.id}))
    else:
        context = replace(context, acknowledged_promotions=frozenset({change.id}))
    result = plan_regular((change,), context, server_id=context.server_id)
    assert not result.proposals and result.blocked[0].reason == reason


def test_new_rule_id_cannot_reuse_source_tree_identity():
    change, context, before = setup("task")
    target = replace(change.after, id=change.root_id)
    op = Operation(
        op_id=uuid4(),
        device_id=uuid4(),
        server_id=context.server_id,
        target_kind="task",
        target_id=change.root_id,
        action="promote_regular",
        payload=target,
        base_revision=4,
        delete_epoch=2,
    )
    with pytest.raises(ValueError):
        operation_hash(op)  # The strict RegularRecord codec rejects this even before effects.


def test_promotion_followed_by_reschedule_keeps_one_next_transition():
    from test_sync_regular_plan import SERVER, accept

    change, context, before = setup("task")
    context = replace(context, server_id=SERVER)
    replacement = replace(change.after, id=uuid4(), interval_days=3)
    following = RegularChange(
        sequence=2,
        id=uuid4(),
        after_intent_id=None,
        kind="save",
        rule_id=replacement.id,
        root_id=change.root_id,
        before=change.after,
        after=replacement,
    )
    changes = (change, following)
    context = replace(context, bases=(*context.bases, RegularBase(id=replacement.id, entry=None)))
    first = plan_regular(changes, context, server_id=context.server_id)
    assert len(first.proposals) == 1 and first.proposals[0].action == "promote_regular"
    assert not any(b.id == change.after.id for b in first.blocked)
    proposal = first.proposals[0]
    op = Operation(
        op_id=uuid4(),
        device_id=uuid4(),
        server_id=context.server_id,
        target_kind=proposal.target_kind,
        target_id=proposal.id,
        action=proposal.action,
        payload=proposal.payload,
        base_revision=4,
        delete_epoch=2,
    )
    ack = Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=5,
        delete_epoch=3,
        error=None,
    )
    marker, active = plan_promotion_effects(
        before, op, ack, context.settings, NOW, vacant_rule_id=change.after.id
    ).entries
    context = replace(
        context,
        bases=(RegularBase(id=change.after.id, entry=active), context.bases[1]),
        promotion_bases=(PromotionBase(kind="task", id=proposal.id, entry=marker),),
        acknowledged_promotions=frozenset({change.id}),
    )
    second = plan_regular(changes, context, server_id=context.server_id)
    assert len(second.proposals) == 1 and second.proposals[0].action == "reschedule_regular"
    context = accept(second.proposals[0], context)
    final = plan_regular(changes, context, server_id=SERVER)
    assert not final.proposals and not final.blocked
    assert final.remaining_changes == tuple(c.id for c in changes)
