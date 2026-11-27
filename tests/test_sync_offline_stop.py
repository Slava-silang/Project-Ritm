from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from test_sync_regular_plan import DAY, NOW, accept, mutate, operation, plan, seed

from ritm.integrations.sync_codec import OccurrenceRecord, RemovalRecord, TaskRecord
from ritm.integrations.sync_operations import (
    Receipt,
    decode_operation,
    encode_operation,
    operation_hash,
)
from ritm.integrations.sync_pages import SnapshotCollector, SnapshotEntry, SnapshotPage
from ritm.integrations.sync_regular_plan import RegularBase
from ritm.integrations.sync_transitions import plan_regular_effects


@pytest.fixture
def stopped(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 2)
    changes = mutate(
        path,
        lambda store, current: store.save_regular_task(
            current, None, interval=None, first=None, today=DAY + timedelta(days=2)
        ),
    )
    target = changes[-1].after
    context = replace(
        context,
        moment=NOW + timedelta(days=20),
        terminal_bases=(RegularBase(id=target.task.id, entry=None),),
    )
    return changes, context, target


def test_offline_stop_completes_old_body_then_preserves_selected_task(stopped):
    changes, context, target = stopped
    first = plan(changes, context).proposals[0]
    assert first.action == "put" and first.payload.current.task.completed
    context = accept(first, context)
    proposal = plan(changes, context).proposals[0]
    assert proposal.action == "stop_offline" and proposal.payload == target
    assert proposal.transition_change == changes[-1].id
    op = operation(proposal)
    assert decode_operation(encode_operation(op)) == op
    ack = Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=op.base_revision + 1,
        delete_epoch=op.delete_epoch + 1,
        error=None,
    )
    before = context.bases[0].entry
    result = plan_regular_effects(
        before, op, ack, context.settings, context.moment, vacant_task_id=target.task.id
    )
    assert len(result.entries) == 3
    mark, history, ordinary = result.entries
    assert type(mark.entity) is RemovalRecord and mark.entity.entity_kind == "regular"
    assert (
        type(history.entity) is OccurrenceRecord and history.entity.current == before.entity.current
    )
    assert ordinary.entity == target and type(ordinary.entity) is TaskRecord
    assert {e.revision for e in result.entries} == {ack.revision}
    assert {e.delete_epoch for e in result.entries} == {ack.delete_epoch}
    entries = (SnapshotEntry(revision=1, delete_epoch=0, entity=context.settings), *result.entries)
    collector = SnapshotCollector(op.server_id)
    collector.add(
        SnapshotPage(
            server_id=op.server_id,
            snapshot_id=uuid4(),
            set_revision=ack.revision,
            total=4,
            offset=0,
            cursor=None,
            next_cursor=None,
            entries=entries,
        )
    )
    assert collector.finish().entries == entries
    with pytest.raises(ValueError):
        plan_regular_effects(before, op, ack, context.settings, context.moment)


def test_unknown_terminal_target_is_not_assumed_absent(stopped):
    changes, context, _ = stopped
    context = accept(plan(changes, context).proposals[0], context)
    result = plan(changes, replace(context, terminal_bases=()))
    assert not result.proposals and result.blocked[0].reason == "terminal_day_conflict"


def test_occupied_terminal_target_is_not_overwritten(stopped):
    changes, context, target = stopped
    context = accept(plan(changes, context).proposals[0], context)
    occupied = RegularBase(
        id=target.task.id, entry=SnapshotEntry(revision=8, delete_epoch=0, entity=target)
    )
    result = plan(changes, replace(context, terminal_bases=(occupied,)))
    assert not result.proposals and result.blocked[0].reason == "ordinary_target_exists"


def test_offline_stop_keeps_deadline_only_on_ordinary_target(stopped):
    changes, context, target = stopped
    target = replace(target, task=replace(target.task, deadline=NOW + timedelta(days=3)))
    changes = (*changes[:-1], replace(changes[-1], after=target))
    context = accept(plan(changes, context).proposals[0], context)
    proposal = plan(changes, context).proposals[0]
    assert proposal.action == "stop_offline"
    assert proposal.payload == target


def test_remote_unfinished_current_is_retained_not_completed(stopped):
    changes, context, target = stopped
    # Directly verify the compound semantics for an unfinished known server current.
    from ritm.integrations.sync_operations import Operation

    op = Operation(
        op_id=uuid4(),
        device_id=uuid4(),
        server_id=context.server_id,
        target_kind="regular",
        target_id=context.bases[0].id,
        action="stop_offline",
        payload=target,
        base_revision=5,
        delete_epoch=2,
    )
    ack = Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=6,
        delete_epoch=3,
        error=None,
    )
    result = plan_regular_effects(
        context.bases[0].entry,
        op,
        ack,
        context.settings,
        context.moment,
        vacant_task_id=target.task.id,
    )
    assert type(result.entries[1].entity) is RemovalRecord
    assert result.entries[1].entity.reason == "retention"


def test_stop_preserves_selection_when_server_has_already_advanced(stopped):
    from ritm.integrations.sync_operations import rollover_record
    from ritm.integrations.sync_regular_changes import task_node_ids

    changes, context, target = stopped
    advanced = rollover_record(context.bases[0].entry.entity, DAY + timedelta(days=10))
    context = replace(
        context,
        bases=(
            RegularBase(
                id=advanced.id, entry=SnapshotEntry(revision=30, delete_epoch=2, entity=advanced)
            ),
        ),
        occupied_ids=context.occupied_ids | frozenset(task_node_ids(advanced.current.task)),
    )
    result = plan(changes, context)
    assert len(result.proposals) == 1 and not result.blocked
    assert result.proposals[0].action == "stop_offline"
    assert result.proposals[0].payload == target
    assert result.remaining_changes == tuple(c.id for c in changes)
