"""Next-step regular preparation with explicit contexts and simulated verified receipts."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.integrations.sync_codec import CalendarRecord, RegularRecord, RemovalRecord
from ritm.integrations.sync_operations import (
    Operation,
    Receipt,
    encode_operation,
    occurrence_id,
    operation_hash,
)
from ritm.integrations.sync_pages import SnapshotEntry
from ritm.integrations.sync_regular_changes import task_node_ids
from ritm.integrations.sync_regular_plan import RegularBase, RegularContext, plan_regular
from ritm.integrations.sync_transitions import plan_regular_effects
from ritm.storage.sqlite import TaskStore
from ritm.storage.sync_intents import SyncBinding

DAY = date(2026, 11, 27)
NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
SERVER = UUID(int=960)
SETTINGS = CalendarRecord(id=UUID(int=961), timezone="UTC")


def seed(path, days=0):
    with TaskStore(path, clock=lambda: NOW) as store:
        store.initialize_sync_binding(
            SyncBinding(owner_id=UUID(int=962), device_id=UUID(int=963), server_id=SERVER)
        )
        store.configure_sync_task_capture(True, {})
        task = Task(
            title="Synthetic current",
            subtasks=(Subtask(title="Step", items=(ChecklistItem(title="Item"),)),),
        )
        store.save_regular_task(task, None, interval=1, first=DAY, today=DAY)
        original = store.list_sync_regular_changes()[0].after
        for offset in range(1, days + 1):
            current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
            TaskService(store).set_completed(current.id, current.id, True)
            store.refresh_recurrences(DAY + timedelta(days=offset))
        changes = store.list_sync_regular_changes()
    ids = task_node_ids(original.current.task)
    context = RegularContext(
        server_id=SERVER,
        settings=SETTINGS,
        moment=NOW + timedelta(days=days),
        bases=(
            RegularBase(
                id=original.id, entry=SnapshotEntry(revision=5, delete_epoch=2, entity=original)
            ),
        ),
        identities=tuple((key, key) for key in sorted(ids, key=str)),
        occupied_ids=frozenset(ids),
    )
    return changes, context


def plan(changes, context):
    return plan_regular(changes, context, server_id=SERVER)


def operation(proposal):
    result = Operation(
        op_id=uuid4(),
        device_id=UUID(int=963),
        server_id=SERVER,
        target_kind="regular",
        target_id=proposal.id,
        base_revision=proposal.base_revision,
        delete_epoch=proposal.delete_epoch,
        action=proposal.action,
        payload=proposal.payload,
    )
    encode_operation(result)
    return result


def accept(proposal, context):
    op = operation(proposal)
    ack = Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=SERVER,
        request_hash=operation_hash(op),
        status="applied",
        revision=op.base_revision + 1,
        delete_epoch=op.delete_epoch
        + (op.action in ("delete", "stop_regular", "prune", "restore")),
        error=None,
    )
    before = next(base.entry for base in context.bases if base.id == proposal.id)
    if op.action in ("rollover", "stop_regular", "delete"):
        effects = plan_regular_effects(before, op, ack, context.settings, context.moment)
        entry = effects.entries[0]
    else:
        entry = SnapshotEntry(
            revision=ack.revision, delete_epoch=ack.delete_epoch, entity=op.payload
        )
    mapping = dict(context.identities)
    mapping.update(proposal.identities)
    occupied = set(context.occupied_ids)
    if type(entry.entity) is RegularRecord:
        if before is not None and type(before.entity) is RegularRecord and op.action != "rollover":
            occupied.difference_update(task_node_ids(before.entity.current.task))
        occupied.update(task_node_ids(entry.entity.current.task))
    return replace(
        context,
        bases=tuple(
            RegularBase(id=b.id, entry=entry) if b.id == proposal.id else b for b in context.bases
        ),
        identities=tuple(mapping.items()),
        occupied_ids=frozenset(occupied),
        acknowledged_barriers=context.acknowledged_barriers | frozenset(proposal.barrier_changes),
    )


def mutate(path, callback):
    with TaskStore(path, clock=lambda: NOW) as store:
        current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
        callback(store, current)
        return store.list_sync_regular_changes()


def test_offline_completion_then_direct_latest_rollover_then_unchanged(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 3)
    raw = path.read_bytes()
    result = plan(changes, context)
    assert result == plan(changes, context) and path.read_bytes() == raw
    assert len(result.proposals) == 1 and not result.blocked
    first = result.proposals[0]
    assert first.action == "put" and first.payload.current.task.completed
    assert first.payload.current_day == DAY and first.payload.current.completed_at == NOW
    context = accept(first, context)
    second = plan(changes, context).proposals[0]
    assert second.action == "rollover" and second.base_revision == 6 and second.delete_epoch == 2
    assert second.payload.current_day == DAY + timedelta(days=3)
    assert second.payload.current.task.id == occurrence_id(second.id, DAY + timedelta(days=3))
    assert not second.payload.current.task.completed and second.payload.current.completed_at is None
    local = changes[-1].after.current.task
    mapping = dict(second.identities)
    assert mapping[local.id] == second.payload.current.task.id
    assert (
        mapping[local.subtasks[0].items[0].id]
        == second.payload.current.task.subtasks[0].items[0].id
    )
    context = accept(second, context)
    third = plan(changes, context)
    assert not third.proposals and not third.blocked
    assert third.unchanged == (second.id,)
    assert third.remaining_changes == tuple(c.id for c in changes)
    assert "Synthetic current" not in repr(result)


def test_edit_latest_occurrence_waits_for_canonical_rollover(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 2)
    changes = mutate(
        path,
        lambda store, current: TaskService(store).rename_node(
            current.id, current.id, "Latest edit"
        ),
    )
    first = plan(changes, context).proposals[0]
    context = accept(first, context)
    rollover = plan(changes, context).proposals[0]
    assert (
        rollover.action == "rollover" and rollover.payload.current.task.title == "Synthetic current"
    )
    context = accept(rollover, context)
    update = plan(changes, context).proposals[0]
    assert update.action == "put" and update.payload.current.task.title == "Latest edit"
    assert update.payload.current.task.id == rollover.payload.current.task.id


def test_new_rule_can_create_latest_only_when_absence_and_provenance_are_explicit(tmp_path):
    changes, context = seed(tmp_path / "tasks.sqlite3", 3)
    context = replace(
        context,
        bases=(RegularBase(id=context.bases[0].id, entry=None),),
        identities=(),
        occupied_ids=frozenset(),
    )
    proposal = plan(changes, context).proposals[0]
    assert proposal.action == "create" and proposal.base_revision == proposal.delete_epoch == 0
    assert proposal.payload == changes[-1].after
    operation(proposal)
    assert plan(changes[1:], context).blocked[0].reason == "creation_provenance_required"
    assert plan(changes, replace(context, bases=())).blocked[0].reason == "unknown_base"


def test_stop_is_atomic_and_does_not_repeat_or_restart_on_later_day(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path)
    changes = mutate(
        path,
        lambda store, current: store.save_regular_task(
            current, None, interval=None, first=None, today=DAY
        ),
    )
    context = replace(context, moment=NOW + timedelta(days=100))
    proposal = plan(changes, context).proposals[0]
    assert proposal.action == "stop_regular" and proposal.payload is None
    context = accept(proposal, context)
    assert not plan(changes, context).proposals
    assert plan(changes, context).blocked[0].reason == "deleted"


def test_delete_uses_exact_current_root_and_never_sends_its_body(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path)
    changes = mutate(
        path, lambda store, current: TaskService(store).delete_node(current.id, current.id, NOW)
    )
    proposal = plan(changes, replace(context, moment=NOW + timedelta(days=100))).proposals[0]
    assert proposal.action == "delete" and proposal.payload is None
    context = accept(proposal, context)
    assert plan(changes, context).unchanged == (proposal.id,)


def test_prune_and_restore_acknowledgments_do_not_loop_forever(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path)
    changes = mutate(
        path,
        lambda store, current: TaskService(store).delete_node(
            current.id, current.subtasks[0].id, NOW
        ),
    )
    proposal = plan(changes, context).proposals[0]
    assert proposal.action == "prune" and proposal.barrier_changes
    context = accept(proposal, context)
    assert not plan(changes, context).proposals
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(store.list_trash()[0]["id"]))
        changes = store.list_sync_regular_changes()
    restored = plan(changes, context).proposals[0]
    assert restored.action == "restore" and restored.barrier_changes
    context = accept(restored, context)
    assert not plan(changes, context).proposals and not plan(changes, context).blocked


def test_frozen_or_unknown_identity_does_not_reprepare(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    assert (
        plan(changes, replace(context, frozen_changes=frozenset({changes[0].id}))).blocked[0].reason
        == "frozen_change"
    )
    assert (
        plan(changes, replace(context, identities=())).blocked[0].reason
        == "identity_mapping_required"
    )


@pytest.mark.parametrize("offset,reason", [(-1, "future_local_day"), (1, "local_refresh_required")])
def test_server_calendar_controls_due_day(tmp_path, offset, reason):
    changes, context = seed(tmp_path / "tasks.sqlite3", 2)
    result = plan(changes, replace(context, moment=context.moment + timedelta(days=offset)))
    assert result.blocked[0].reason == reason


def test_future_first_day_can_be_created_without_spurious_rollover(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path)
    with TaskStore(path) as store:
        current = store.list_tasks()[0]
        store.save_regular_task(current, None, interval=1, first=DAY + timedelta(days=5), today=DAY)
        all_changes = store.list_sync_regular_changes()
    # An independently new waiting rule, not a reschedule of the known initial rule.
    new = all_changes[-1].after
    change = replace(all_changes[-1], before=None)
    context = replace(
        context,
        bases=(RegularBase(id=new.id, entry=None),),
        identities=(),
        occupied_ids=frozenset(),
    )
    proposal = plan((change,), context).proposals[0]
    assert proposal.action == "create" and proposal.payload.current_day == DAY + timedelta(days=5)


def test_reschedule_is_blocked_for_both_rules(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path)
    changes = mutate(
        path,
        lambda store, current: store.save_regular_task(
            current, None, interval=2, first=DAY, today=DAY
        ),
    )
    new = changes[-1].after
    context = replace(context, bases=(*context.bases, RegularBase(id=new.id, entry=None)))
    result = plan(changes, context)
    assert not result.proposals and len(result.blocked) == 2
    assert {b.reason for b in result.blocked} == {"atomic_schedule_change"}


def test_server_removed_node_requires_explicit_restore(tmp_path):
    changes, context = seed(tmp_path / "tasks.sqlite3")
    remote = context.bases[0].entry.entity
    pruned = replace(
        remote, current=replace(remote.current, task=replace(remote.current.task, subtasks=()))
    )
    context = replace(
        context,
        bases=(replace(context.bases[0], entry=replace(context.bases[0].entry, entity=pruned)),),
        occupied_ids=frozenset({remote.current.task.id}),
    )
    assert plan(changes, context).blocked[0].reason == "node_restore_required"


def test_changed_template_across_skipped_days_requires_separate_resolution(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    with TaskStore(path) as store:
        current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
        TaskService(store).rename_node(current.id, current.id, "Changed between days")
        store.refresh_recurrences(DAY + timedelta(days=2))
        changes = store.list_sync_regular_changes()
    context = replace(context, moment=NOW + timedelta(days=2))
    context = accept(plan(changes, context).proposals[0], context)
    result = plan(changes, context)
    assert not result.proposals and result.blocked[0].reason == "template_transition_required"


def test_store_preparation_reads_one_snapshot_and_leaves_all_facts(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    with TaskStore(path) as store:
        raw, sql = path.read_bytes(), []
        store._db.set_trace_callback(sql.append)
        result = store.prepare_sync_regular(context)
        store._db.set_trace_callback(None)
        assert result == plan(changes, context) and path.read_bytes() == raw
        assert sum(statement == "BEGIN" for statement in sql) == 1
        assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in sql)
        assert store.next_sync_sendable() is None
        store._db.execute("BEGIN IMMEDIATE")
        store._db.execute("UPDATE sync_capture SET enabled=0")
        with pytest.raises(ValueError):
            store.prepare_sync_regular(context)
        assert store._db.in_transaction
        store._db.rollback()
        assert store.is_sync_task_capture_enabled()


@pytest.mark.parametrize(
    "case",
    [
        "generation",
        "duplicate_base",
        "duplicate_map",
        "counter",
        "naive_time",
        "barrier_conflict",
        "reverse_journal",
    ],
)
def test_invalid_inputs_reject_whole_plan(tmp_path, case):
    changes, context = seed(tmp_path / "tasks.sqlite3", 1)
    if case == "generation":
        context = replace(context, server_id=uuid4())
    elif case == "duplicate_base":
        context = replace(context, bases=(*context.bases, context.bases[0]))
    elif case == "duplicate_map":
        context = replace(context, identities=(*context.identities, context.identities[0]))
    elif case == "counter":
        context = replace(
            context,
            bases=(
                replace(context.bases[0], entry=replace(context.bases[0].entry, delete_epoch=True)),
            ),
        )
    elif case == "naive_time":
        context = replace(context, moment=context.moment.replace(tzinfo=None))
    elif case == "barrier_conflict":
        context = replace(
            context,
            frozen_changes=frozenset({changes[0].id}),
            acknowledged_barriers=frozenset({changes[0].id}),
        )
    else:
        changes = tuple(reversed(changes))
    with pytest.raises(ValueError):
        plan(changes, context)


def test_stop_after_offline_rollovers_waits_for_the_matching_current_root(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 2)
    changes = mutate(
        path,
        lambda store, current: store.save_regular_task(
            current, None, interval=None, first=None, today=DAY + timedelta(days=2)
        ),
    )
    actions = []
    for _ in range(3):
        proposal = plan(changes, context).proposals[0]
        actions.append(proposal.action)
        context = accept(proposal, context)
    assert actions == ["put", "rollover", "stop_regular"]
    # A delayed reconnect cannot send a historical rollover forbidden by the server-day contract.
    _, early_context = seed(tmp_path / "other.sqlite3")
    original_base = changes[0].after
    delayed = replace(
        early_context,
        moment=NOW + timedelta(days=20),
        bases=(
            RegularBase(
                id=original_base.id,
                entry=SnapshotEntry(revision=5, delete_epoch=2, entity=original_base),
            ),
        ),
        identities=tuple((i, i) for i in task_node_ids(original_base.current.task)),
    )
    assert plan(changes, delayed).blocked[0].reason == "terminal_day_conflict"


def test_deleted_rule_is_not_resurrected_and_new_creation_respects_occupied_ids(tmp_path):
    changes, context = seed(tmp_path / "tasks.sqlite3")
    base = context.bases[0]
    mark = RemovalRecord(
        entity_kind="regular", entity_id=base.id, reason="user_deleted", revision=7, delete_epoch=3
    )
    removed = replace(
        context,
        bases=(
            RegularBase(id=base.id, entry=SnapshotEntry(revision=7, delete_epoch=3, entity=mark)),
        ),
    )
    assert plan(changes, removed).blocked[0].reason == "deleted"
    absent = replace(context, bases=(RegularBase(id=base.id, entry=None),), identities=())
    assert plan(changes, absent).blocked[0].reason == "occupied_identity"


def test_unbound_store_and_oversized_context_are_rejected(tmp_path, monkeypatch):
    from ritm.integrations import sync_regular_plan as module

    changes, context = seed(tmp_path / "tasks.sqlite3")
    with TaskStore(tmp_path / "unbound.sqlite3") as store:
        with pytest.raises(ValueError):
            store.prepare_sync_regular(context)
    monkeypatch.setattr(module, "MAX_STAGED_BYTES", 1)
    with pytest.raises(ValueError):
        plan(changes, context)
    with pytest.raises(ValueError):
        plan(changes, replace(context, bases=()))


def test_first_delete_of_existing_rule_needs_no_body_snapshot(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        task = Task(title="Existing before connection")
        store.save_regular_task(task, None, interval=1, first=DAY, today=DAY)
        original = store._regular_sync_record(task)
        store.initialize_sync_binding(
            SyncBinding(owner_id=UUID(int=962), device_id=UUID(int=963), server_id=SERVER)
        )
        store.configure_sync_task_capture(True, {})
        TaskService(store).delete_node(task.id, task.id, NOW)
        changes = store.list_sync_regular_changes()
    assert len(changes) == 1 and changes[0].before is changes[0].after is None
    context = RegularContext(
        server_id=SERVER,
        settings=SETTINGS,
        moment=NOW + timedelta(days=100),
        bases=(
            RegularBase(
                id=original.id, entry=SnapshotEntry(revision=5, delete_epoch=2, entity=original)
            ),
        ),
        identities=((task.id, task.id),),
    )
    proposal = plan(changes, context).proposals[0]
    assert proposal.action == "delete" and proposal.payload is None
    assert (
        plan(changes, replace(context, identities=())).blocked[0].reason
        == "identity_mapping_required"
    )
