from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from ritm.domain.tasks import Task
from ritm.integrations.sync_codec import OccurrenceRecord, TaskRecord, history_id
from ritm.integrations.sync_operations import (
    Operation,
    Receipt,
    decode_operation,
    encode_operation,
    operation_hash,
)
from ritm.integrations.sync_pages import SnapshotCollector, SnapshotEntry, SnapshotPage
from ritm.integrations.sync_transitions import plan_detach_effects


def case():
    rule, root, server, device = uuid4(), uuid4(), uuid4(), uuid4()
    history = OccurrenceRecord(
        id=history_id(rule, date(2026, 11, 27)),
        rule_id=rule,
        day=date(2026, 11, 27),
        current=TaskRecord(task=Task(id=root, title="History", completed=True)),
    )
    op = Operation(
        op_id=uuid4(),
        device_id=device,
        server_id=server,
        target_kind="occurrence",
        target_id=history.id,
        base_revision=4,
        delete_epoch=2,
        action="detach_occurrence",
        payload=TaskRecord(task=replace(history.current.task, completed=False)),
    )
    ack = Receipt(
        op_id=op.op_id,
        device_id=device,
        server_id=server,
        request_hash=operation_hash(op),
        status="applied",
        revision=5,
        delete_epoch=3,
        error=None,
    )
    return SnapshotEntry(revision=4, delete_epoch=2, entity=history), op, ack


def test_detach_preserves_root_and_validates_complete_snapshot():
    before, op, ack = case()
    assert decode_operation(encode_operation(op)) == op
    result = plan_detach_effects(before, op, ack, vacant_task_id=op.payload.task.id)
    assert result.entries[0].entity.reason == "type_changed"
    assert result.entries[1].entity.task.id == before.entity.current.task.id
    collector = SnapshotCollector(op.server_id)
    collector.add(
        SnapshotPage(
            server_id=op.server_id,
            snapshot_id=uuid4(),
            set_revision=5,
            total=2,
            offset=0,
            cursor=None,
            next_cursor=None,
            entries=result.entries,
        )
    )
    assert collector.finish().entries == result.entries


@pytest.mark.parametrize("field", ["vacancy", "base", "epoch", "root"])
def test_detach_rejects_invalid_proof(field):
    before, op, ack = case()
    vacant = op.payload.task.id
    if field == "vacancy":
        vacant = uuid4()
    elif field == "base":
        before = replace(before, revision=6)
    elif field == "epoch":
        ack = replace(ack, delete_epoch=2)
    else:
        op = replace(op, payload=TaskRecord(task=Task(title="Other root")))
        ack = replace(ack, request_hash=operation_hash(op))
    with pytest.raises(ValueError):
        plan_detach_effects(before, op, ack, vacant_task_id=vacant)


@pytest.mark.parametrize("reason", ["retention", "user_deleted"])
def test_explicit_restore_of_removed_history_as_ordinary_has_no_fake_completion(reason):
    from ritm.integrations.sync_codec import RemovalRecord

    before, op, ack = case()
    before = replace(
        before,
        entity=RemovalRecord(
            entity_kind="occurrence",
            entity_id=op.target_id,
            root_id=op.payload.task.id,
            revision=before.revision,
            delete_epoch=before.delete_epoch,
            reason=reason,
        ),
    )
    result = plan_detach_effects(before, op, ack, vacant_task_id=op.payload.task.id)
    assert result.entries[0].entity.reason == "type_changed"
    assert result.entries[1].entity == op.payload
    assert not result.entries[1].entity.task.completed
    inconsistent = replace(before, entity=replace(before.entity, revision=before.revision + 1))
    with pytest.raises(ValueError):
        plan_detach_effects(inconsistent, op, ack, vacant_task_id=op.payload.task.id)
