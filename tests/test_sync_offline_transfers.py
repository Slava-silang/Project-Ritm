"""Offline transfer variants retain strict source/vacancy/epoch boundaries."""

from dataclasses import replace
from uuid import uuid4

import pytest
from test_sync_prepare_loop import confirmed
from test_sync_queue_acceptance import drain, setup
from test_sync_regular_plan import DAY

from ritm.domain.tasks import Subtask
from ritm.integrations.sync_operations import decode_operation, encode_operation
from ritm.integrations.sync_transitions import plan_promotion_effects, plan_regular_effects
from ritm.storage.sqlite import TaskStore


@pytest.mark.parametrize("source_kind", ["regular", "task"])
@pytest.mark.parametrize("violation", ["none", "vacancy", "base", "epoch", "kind", "zero_base"])
def test_offline_transfer_proof_is_checked_before_any_effect(tmp_path, source_kind, violation):
    path, changes, context, snapshot = setup(tmp_path)
    root = changes[0].root_id
    if source_kind == "task":
        with TaskStore(path) as store:
            store.save_regular_task(
                store.get_task(root), None, interval=None, first=None, today=DAY
            )
        snapshot, _ = drain(path, context, snapshot)
    with TaskStore(path) as store:
        task = store.get_task(root)
        store.save_regular_task(
            replace(task, subtasks=(*task.subtasks, Subtask(title="New step"))),
            None,
            interval=2,
            first=DAY,
            today=DAY,
        )
        frozen = store.prepare_next_sync(context.moment).request
    op = frozen.operation
    assert op.action == ("reschedule_offline" if source_kind == "regular" else "promote_offline")
    assert decode_operation(encode_operation(op)) == op
    before = next(
        e
        for e in snapshot.entries
        if (
            getattr(e.entity, "id", None) == op.target_id
            if source_kind == "regular"
            else getattr(getattr(e.entity, "task", None), "id", None) == op.target_id
        )
    )
    receipt, _ = confirmed(snapshot, frozen, context)
    vacant = op.payload.id
    if violation == "vacancy":
        vacant = uuid4()
    elif violation == "base":
        before = replace(before, revision=before.revision + 1)
    elif violation == "epoch":
        receipt = replace(receipt, delete_epoch=op.delete_epoch)
    elif violation == "kind":
        op = replace(op, target_kind="category")
    elif violation == "zero_base":
        op = replace(op, base_revision=0)
    helper = plan_regular_effects if source_kind == "regular" else plan_promotion_effects
    if violation == "none":
        result = helper(
            before, op, receipt, context.settings, context.moment, vacant_rule_id=vacant
        )
        assert len(result.entries) == 2
        assert {e.revision for e in result.entries} == {receipt.revision}
        assert {e.delete_epoch for e in result.entries} == {receipt.delete_epoch}
    else:
        with pytest.raises(ValueError):
            helper(before, op, receipt, context.settings, context.moment, vacant_rule_id=vacant)
