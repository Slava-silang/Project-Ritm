"""History proposals from real disposable journals, without changing or sending them."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4, uuid5

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.integrations.sync_codec import RemovalRecord, history_id
from ritm.integrations.sync_history_plan import HistoryBase, HistoryContext, plan_history
from ritm.integrations.sync_operations import Operation, encode_operation
from ritm.integrations.sync_pages import SnapshotEntry
from ritm.storage.sqlite import TaskStore
from ritm.storage.sync_intents import SyncBinding

DAY = date(2026, 11, 27)
NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
SERVER = UUID(int=950)


@pytest.fixture
def journal(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path, clock=lambda: NOW) as store:
        store.initialize_sync_binding(
            SyncBinding(owner_id=UUID(int=951), device_id=UUID(int=952), server_id=SERVER)
        )
        store.configure_sync_task_capture(True, {})
        task = Task(
            title="Synthetic private reading",
            subtasks=(Subtask(title="Chapter", items=(ChecklistItem(title="Notes"),)),),
        )
        store.save_regular_task(task, None, interval=1, first=DAY, today=DAY)
        for offset in range(1, 4):
            current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
            TaskService(store).set_completed(current.id, current.id, True)
            store.refresh_recurrences(DAY + timedelta(days=offset))
        changes = store.list_sync_regular_changes()
    slots = [r.before for r in changes if r.kind == "rollover"]
    pairs = []
    for regular in slots:
        task = regular.current.task
        ids = [
            task.id,
            *(x.id for x in task.subtasks),
            *(y.id for x in task.subtasks for y in x.items),
        ]
        pairs.extend((key, uuid5(key, "verified-server-id")) for key in ids)
    context = HistoryContext(
        server_id=SERVER,
        bases=tuple(HistoryBase(id=history_id(r.id, r.current_day), entry=None) for r in slots),
        identities=tuple(pairs),
        regulars=(changes[-1].after,),
    )
    return path, changes, context


def plan(changes, context):
    return plan_history(changes, context, server_id=SERVER)


def with_base(context, identifier, entity, revision=7, epoch=2):
    entry = SnapshotEntry(revision=revision, delete_epoch=epoch, entity=entity)
    return replace(
        context,
        bases=tuple(
            HistoryBase(id=b.id, entry=entry) if b.id == identifier else b for b in context.bases
        ),
    )


def mutate(journal, operation):
    path, _, context = journal
    with TaskStore(path, clock=lambda: NOW + timedelta(days=4)) as store:
        old = next(
            r.before.current.task for r in store.list_sync_regular_changes() if r.kind == "rollover"
        )
        operation(store, old)
        return store.list_sync_regular_changes(), context


def test_three_offline_completions_have_distinct_mapped_histories_and_do_not_change_db(journal):
    path, changes, context = journal
    raw = path.read_bytes()
    result = plan(changes, context)
    assert result == plan(changes, context)
    assert path.read_bytes() == raw
    assert len(result.proposals) == 3 and not result.blocked
    assert [p.payload.day for p in result.proposals] == [DAY + timedelta(days=i) for i in range(3)]
    assert all(
        p.action == "create" and p.base_revision == p.delete_epoch == 0 for p in result.proposals
    )
    assert all(p.payload.current.completed_at == NOW for p in result.proposals)
    mapping = dict(context.identities)
    rollovers = [r for r in changes if r.kind == "rollover"]
    for proposed, source in zip(result.proposals, rollovers, strict=True):
        assert proposed.payload.current.task.id == mapping[source.before.current.task.id]
        assert (
            proposed.payload.current.task.subtasks[0].items[0].id
            == mapping[source.before.current.task.subtasks[0].items[0].id]
        )
        assert proposed.source_changes == (source.id,)
        encode_operation(
            Operation(
                op_id=uuid4(),
                device_id=UUID(int=952),
                server_id=SERVER,
                target_kind="occurrence",
                target_id=proposed.id,
                base_revision=proposed.base_revision,
                delete_epoch=proposed.delete_epoch,
                action=proposed.action,
                payload=proposed.payload,
            )
        )
    assert result.remaining_changes == tuple(r.id for r in changes)
    assert "Synthetic private" not in repr(result)


def test_unknown_absence_and_mapping_are_not_invented(journal):
    _, changes, context = journal
    result = plan(changes, replace(context, bases=()))
    assert not result.proposals and {b.reason for b in result.blocked} == {"unknown_base"}
    result = plan(changes, replace(context, identities=context.identities[1:]))
    assert len(result.proposals) == 2 and result.blocked[0].reason == "identity_mapping_required"


def test_current_root_and_not_yet_advanced_schedule_block_history(journal):
    _, changes, context = journal
    first = next(r.before for r in changes if r.kind == "rollover")
    root = dict(context.identities)[first.current.task.id]
    remote = replace(
        first, current=replace(first.current, task=replace(first.current.task, id=root))
    )
    result = plan(changes, replace(context, regulars=(remote,)))
    assert not result.proposals
    assert [b.reason for b in result.blocked] == [
        "current_root",
        "regular_transition_required",
        "regular_transition_required",
    ]
    result = plan(changes, replace(context, ordinary_roots=frozenset({root})))
    assert result.blocked[0].reason == "current_root"


@pytest.mark.parametrize("reason", ["retention", "user_deleted"])
def test_old_history_edit_does_not_override_tombstone_without_explicit_restore(journal, reason):
    _, changes, context = journal
    original = plan(changes, context).proposals[0]
    mark = RemovalRecord(
        entity_kind="occurrence",
        entity_id=original.id,
        root_id=original.payload.current.task.id,
        revision=7,
        delete_epoch=2,
        reason=reason,
    )
    result = plan(changes, with_base(context, original.id, mark))
    assert len(result.proposals) == 2
    if reason == "retention":
        assert original.id in result.unchanged
    else:
        assert result.blocked[0].reason == "deleted"


@pytest.mark.parametrize("reason", ["retention", "user_deleted"])
def test_explicit_history_restore_uses_known_epoch(journal, reason):
    _, changes, context = journal
    original = plan(changes, context).proposals[0]

    def restore(store, old):
        entry = TaskService(store).delete_node(old.id, old.id, NOW)
        TaskService(store).restore(UUID(entry))

    updated, _ = mutate(journal, restore)
    mark = RemovalRecord(
        entity_kind="occurrence",
        entity_id=original.id,
        root_id=original.payload.current.task.id,
        revision=7,
        delete_epoch=2,
        reason=reason,
    )
    result = plan(updated, with_base(context, original.id, mark))
    proposal = next(p for p in result.proposals if p.id == original.id)
    assert (
        proposal.action == "restore" and proposal.base_revision == 7 and proposal.delete_epoch == 2
    )
    assert proposal.payload == original.payload


def test_identical_server_history_is_unchanged_and_later_local_edit_is_put(journal):
    _, changes, context = journal
    original = plan(changes, context).proposals[0]
    context = with_base(context, original.id, original.payload)
    result = plan(changes, context)
    assert original.id in result.unchanged
    updated, _ = mutate(
        journal, lambda store, old: TaskService(store).rename_node(old.id, old.id, "Revised")
    )
    proposal = next(p for p in plan(updated, context).proposals if p.id == original.id)
    assert proposal.action == "put" and proposal.base_revision == 7
    assert proposal.payload.current.task.title == "Revised"
    assert proposal.payload.current.completed_at == NOW


def test_prune_and_restore_barriers_survive_folding(journal):
    _, changes, context = journal
    original = plan(changes, context).proposals[0]
    context = with_base(context, original.id, original.payload)
    updated, _ = mutate(
        journal, lambda store, old: TaskService(store).delete_node(old.id, old.subtasks[0].id, NOW)
    )
    proposal = next(p for p in plan(updated, context).proposals if p.id == original.id)
    assert proposal.action == "prune" and not proposal.payload.current.task.subtasks
    path = journal[0]
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(store.list_trash()[0]["id"]))
        updated = store.list_sync_regular_changes()
    proposal = next(p for p in plan(updated, context).proposals if p.id == original.id)
    assert proposal.action == "restore" and proposal.payload == original.payload


def test_deleted_history_has_no_payload_and_absent_delete_is_blocked(journal):
    _, changes, context = journal
    original = plan(changes, context).proposals[0]
    updated, _ = mutate(
        journal, lambda store, old: TaskService(store).delete_node(old.id, old.id, NOW)
    )
    result = plan(updated, with_base(context, original.id, original.payload))
    proposal = next(p for p in result.proposals if p.id == original.id)
    assert proposal.action == "delete" and proposal.payload is None
    assert plan(updated, context).blocked[0].reason == "delete_requires_base"


def test_detachment_is_not_published_as_half_a_type_transition(journal):
    _, changes, context = journal
    updated, _ = mutate(
        journal, lambda store, old: TaskService(store).set_completed(old.id, old.id, False)
    )
    result = plan(updated, context)
    assert len(result.proposals) == 2 and result.blocked[0].reason == "atomic_type_transition"
    assert result.remaining_changes == tuple(r.id for r in updated)


def test_frozen_sources_block_repreparation(journal):
    _, changes, context = journal
    source = next(r for r in changes if r.kind == "rollover")
    result = plan(changes, replace(context, frozen_changes=frozenset({source.id})))
    assert len(result.proposals) == 2 and result.blocked[0].reason == "frozen_change"


def test_conflicting_server_root_is_not_overwritten(journal):
    _, changes, context = journal
    original = plan(changes, context).proposals[0]
    other = replace(
        original.payload,
        current=replace(
            original.payload.current, task=replace(original.payload.current.task, id=uuid4())
        ),
    )
    result = plan(changes, with_base(context, original.id, other))
    assert result.blocked[0].reason == "root_conflict"


def test_server_schedule_grid_is_checked(journal):
    _, changes, context = journal
    remote = replace(context.regulars[0], interval_days=2, current_day=DAY + timedelta(days=4))
    result = plan(changes, replace(context, regulars=(remote,)))
    assert len(result.proposals) == 2 and result.blocked[0].reason == "schedule_conflict"


@pytest.mark.parametrize(
    "invalid",
    [
        "server",
        "duplicate_mapping",
        "colliding_mapping",
        "duplicate_base",
        "counter",
        "order",
        "duplicate_fact",
    ],
)
def test_invalid_context_or_journal_is_rejected_as_a_whole(journal, invalid):
    _, changes, context = journal
    if invalid == "server":
        context = replace(context, server_id=uuid4())
    elif invalid == "duplicate_mapping":
        context = replace(context, identities=(*context.identities, context.identities[0]))
    elif invalid == "colliding_mapping":
        context = replace(
            context, identities=(*context.identities, (uuid4(), context.identities[0][1]))
        )
    elif invalid == "duplicate_base":
        context = replace(context, bases=(*context.bases, context.bases[0]))
    elif invalid == "counter":
        original = plan(changes, context).proposals[0]
        context = with_base(context, original.id, original.payload, revision=True)
    elif invalid == "order":
        changes = tuple(reversed(changes))
    else:
        changes = (*changes, changes[-1])
    with pytest.raises(ValueError):
        plan(changes, context)


def test_skipped_unfinished_slot_is_not_fabricated_as_history(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.initialize_sync_binding(
            SyncBinding(owner_id=uuid4(), device_id=uuid4(), server_id=SERVER)
        )
        store.configure_sync_task_capture(True, {})
        task = Task(title="Skip")
        store.save_regular_task(task, None, interval=1, first=DAY, today=DAY)
        store.refresh_recurrences(DAY + timedelta(days=100))
        changes = store.list_sync_regular_changes()
    context = HistoryContext(server_id=SERVER, bases=(), identities=(), regulars=())
    result = plan(changes, context)
    assert not result.proposals and not result.blocked and not result.unchanged
    assert result.remaining_changes


def test_store_preparation_is_read_only_and_generation_bound(journal):
    path, changes, context = journal
    with TaskStore(path) as store:
        raw = path.read_bytes()
        statements = []
        store._db.set_trace_callback(statements.append)
        result = store.prepare_sync_history(context)
        store._db.set_trace_callback(None)
        assert result == plan(changes, context)
        assert path.read_bytes() == raw
        assert sum(s == "BEGIN" for s in statements) == 1
        assert not any(
            s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements
        )
        assert store.next_sync_sendable() is None
        with pytest.raises(ValueError):
            store.prepare_sync_history(replace(context, server_id=uuid4()))
        store._db.execute("BEGIN IMMEDIATE")
        store._db.execute("UPDATE sync_capture SET enabled=0")
        with pytest.raises(ValueError):
            store.prepare_sync_history(context)
        assert store._db.in_transaction
        store._db.rollback()
        assert store.is_sync_task_capture_enabled()


def test_unbound_preparation_is_rejected(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        context = HistoryContext(server_id=SERVER, bases=(), identities=(), regulars=())
        with pytest.raises(ValueError):
            store.prepare_sync_history(context)


def test_old_history_cannot_be_promoted_as_half_a_transition(journal):
    def promote(store, old):
        store.save_regular_task(old, None, interval=2, first=DAY, today=DAY)

    updated, context = mutate(journal, promote)
    result = plan(updated, context)
    assert result.blocked[0].reason == "atomic_type_transition"
    assert len(result.proposals) == 2


def test_readding_deleted_node_requires_explicit_restore(journal):
    def readd(store, old):
        child = old.subtasks[0]
        service = TaskService(store)
        service.delete_node(old.id, child.id, NOW)
        service.add_subtask(old.id, child)

    updated, context = mutate(journal, readd)
    result = plan(updated, context)
    assert result.blocked[0].reason == "node_restore_required"


def test_server_removed_old_nodes_are_not_resurrected_by_rebasing(journal):
    _, changes, context = journal
    original = plan(changes, context).proposals[0]
    pruned = replace(
        original.payload,
        current=replace(
            original.payload.current, task=replace(original.payload.current.task, subtasks=())
        ),
    )
    result = plan(changes, with_base(context, original.id, pruned))
    assert len(result.proposals) == 2
    assert result.blocked[0].reason == "node_restore_required"


def test_unknown_completion_date_is_preserved(journal):
    _, changes, context = journal

    def unknown(record):
        if record is None:
            return None
        return replace(record, current=replace(record.current, completed_at=None))

    changes = tuple(replace(c, before=unknown(c.before), after=unknown(c.after)) for c in changes)
    result = plan(changes, context)
    assert len(result.proposals) == 3
    assert all(p.payload.current.completed_at is None for p in result.proposals)


def test_root_owned_by_another_server_history_slot_blocks_creation(journal):
    _, changes, context = journal
    original = plan(changes, context).proposals[0].payload
    rule = uuid4()
    other = replace(original, id=history_id(rule, original.day), rule_id=rule)
    extra = HistoryBase(id=other.id, entry=SnapshotEntry(revision=7, delete_epoch=2, entity=other))
    result = plan(changes, replace(context, bases=(*context.bases, extra)))
    assert len(result.proposals) == 2 and result.blocked[0].reason == "root_conflict"


def test_context_and_journal_size_limits_fail_before_any_result(journal, monkeypatch):
    from ritm.integrations import sync_history_plan as module

    _, changes, context = journal
    monkeypatch.setattr(module, "MAX_STAGED_BYTES", 1)
    with pytest.raises(ValueError):
        plan(changes, context)
    with pytest.raises(ValueError):
        plan(changes, replace(context, regulars=()))
