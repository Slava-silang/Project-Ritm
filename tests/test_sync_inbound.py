"""Pure inbound preview preserves local work and never treats absence as deletion."""

from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest
from test_sync_block_capture import event

from ritm.domain.tasks import Category, Subtask, Task
from ritm.integrations.sync_codec import (
    BlockRecord,
    CalendarRecord,
    CategoryRecord,
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    TaskRecord,
    history_id,
)
from ritm.integrations.sync_inbound import plan_inbound
from ritm.integrations.sync_pages import Snapshot, SnapshotEntry

SERVER = uuid4()
SETTINGS = CalendarRecord(id=uuid4(), timezone="UTC")


def snapshot(*records, revision=2, epoch=0):
    return Snapshot(
        server_id=SERVER,
        snapshot_id=uuid4(),
        set_revision=revision,
        entries=tuple(
            SnapshotEntry(
                revision=e.revision if type(e) is RemovalRecord else revision,
                delete_epoch=e.delete_epoch if type(e) is RemovalRecord else epoch,
                entity=e,
            )
            for e in (SETTINGS, *records)
        ),
    )


def local(*records):
    return (SETTINGS, *records)


def plan(records, incoming, accepted=None, protected=frozenset()):
    return plan_inbound(
        local=records, incoming=incoming, accepted=accepted, server_id=SERVER, protected=protected
    )


def decision(result, kind, identifier):
    return next(d for d in result.decisions if d.key == (kind, identifier))


def mark(record, reason="user_deleted", revision=2, epoch=1):
    kind = "task" if type(record) is TaskRecord else "category"
    identifier = record.task.id if kind == "task" else record.category.id
    return RemovalRecord(
        entity_kind=kind, entity_id=identifier, revision=revision, delete_epoch=epoch, reason=reason
    )


def test_bootstrap_preview_imports_new_and_preserves_local_only_without_rewards():
    private = TaskRecord(task=Task(title="Local private history"))
    remote = TaskRecord(task=Task(title="Remote completed", completed=True))
    # TaskRecord completed_at is mandatory for a completed DTO.
    from test_sync_category_capture import NOW

    remote = replace(remote, completed_at=NOW)
    result = plan(local(private), snapshot(remote))
    assert result.bootstrap and not result.needs_resolution
    assert decision(result, "task", remote.task.id).action == "insert"
    assert decision(result, "task", private.task.id).action == "retain"
    assert not hasattr(result, "rewards")
    assert private.task.title not in repr(result)
    assert remote.task.title not in repr(decision(result, "task", remote.task.id))


def test_bootstrap_conflict_needs_choice_even_for_remote_delete():
    root = TaskRecord(task=Task(title="Local"))
    changed = replace(root, task=replace(root.task, title="Cloud"))
    for incoming in (snapshot(changed), snapshot(mark(root))):
        result = plan(local(root), incoming)
        assert result.needs_resolution
        assert decision(result, "task", root.task.id).action == "conflict"
        assert decision(result, "task", root.task.id).local == root


def test_incremental_unchanged_then_remote_update_then_delete():
    root = TaskRecord(task=Task(title="Before"))
    accepted = snapshot(root, revision=1)
    assert (
        decision(plan(local(root), snapshot(root), accepted), "task", root.task.id).action == "keep"
    )
    changed = replace(root, task=replace(root.task, title="After"))
    d = decision(plan(local(root), snapshot(changed), accepted), "task", root.task.id)
    assert d.action == "update" and d.local == root and d.incoming.entity == changed
    assert (
        decision(plan(local(root), snapshot(mark(root)), accepted), "task", root.task.id).action
        == "archive"
    )


@pytest.mark.parametrize("remote", ["same", "different", "deleted", "retained", "absent"])
def test_pending_root_is_preserved_even_if_cloud_body_is_equal(remote):
    root = TaskRecord(task=Task(title="Unsent"))
    value = root if remote == "same" else replace(root, task=replace(root.task, title="Other"))
    if remote in ("deleted", "retained"):
        value = mark(root, "retention" if remote == "retained" else "user_deleted")
    incoming = snapshot() if remote == "absent" else snapshot(value)
    result = plan(
        local(root), incoming, snapshot(root, revision=1), frozenset({("task", root.task.id)})
    )
    assert decision(result, "task", root.task.id).action == "defer"


def test_unreported_local_change_is_not_overwritten():
    before = TaskRecord(task=Task(title="Baseline"))
    edited = replace(before, task=replace(before.task, title="Local edit"))
    result = plan(local(edited), snapshot(before), snapshot(before, revision=1))
    d = decision(result, "task", before.task.id)
    assert (d.action, d.reason) == ("conflict", "local_changed")


@pytest.mark.parametrize("mode", ["absent", "retained"])
def test_cloud_history_cleanup_never_archives_local_task(mode):
    root = TaskRecord(task=Task(title="Old local history"))
    incoming = snapshot() if mode == "absent" else snapshot(mark(root, "retention"))
    d = decision(plan(local(root), incoming, snapshot(root, revision=1)), "task", root.task.id)
    assert d.action == "retain" and d.local == root


def test_known_remote_restore_requires_new_epoch_and_preserves_bootstrap_rules():
    root = TaskRecord(task=Task(title="Restored"))
    accepted = snapshot(mark(root, revision=1), revision=1)
    with pytest.raises(ValueError, match="поколения"):
        plan(local(), snapshot(root, epoch=1), accepted)
    result = plan(local(), snapshot(root, epoch=2), accepted)
    assert decision(result, "task", root.task.id).action == "insert"


@pytest.mark.parametrize("fault", ["server", "older", "same_set", "same_entry", "epoch"])
def test_inconsistent_snapshots_are_rejected_before_any_decisions(fault):
    root = TaskRecord(task=Task(title="Baseline"))
    accepted = snapshot(root, revision=3, epoch=1)
    incoming = snapshot(root, revision=4, epoch=1)
    if fault == "server":
        incoming = replace(incoming, server_id=uuid4())
    elif fault == "older":
        incoming = snapshot(root, revision=2, epoch=1)
    elif fault == "same_set":
        incoming = snapshot(
            replace(root, task=replace(root.task, title="Wrong")), revision=3, epoch=1
        )
    elif fault == "same_entry":
        incoming = replace(
            incoming,
            entries=(
                incoming.entries[0],
                replace(
                    incoming.entries[1],
                    revision=3,
                    entity=replace(root, task=replace(root.task, title="Wrong")),
                ),
            ),
        )
    else:
        incoming = snapshot(root, revision=4, epoch=0)
    with pytest.raises(ValueError):
        plan(local(root), incoming, accepted)


def test_nested_uuid_collision_does_not_reuse_local_node_as_new_root():
    child = Subtask(title="Keep me")
    existing = TaskRecord(task=Task(title="Local owner", subtasks=(child,)))
    other = TaskRecord(task=Task(id=child.id, title="Conflicting root"))
    result = plan(local(existing), snapshot(other))
    d = decision(result, "task", other.task.id)
    assert (d.action, d.reason) == ("conflict", "local_identity_occupied")


def test_category_conflict_defers_new_root_and_its_calendar():
    category = CategoryRecord(category=Category(title="Local category"))
    conflicting = replace(category, category=replace(category.category, title="Cloud category"))
    root = TaskRecord(task=Task(title="Remote task", category_id=category.category.id))
    block = BlockRecord(block=event(root.task.id))
    result = plan(local(category), snapshot(conflicting, root, block))
    assert decision(result, "category", category.category.id).action == "conflict"
    assert decision(result, "task", root.task.id).reason == "category_unresolved"
    assert decision(result, "block", block.block.id).reason == "parent_unresolved"


def test_category_referenced_by_pending_local_root_is_not_deleted():
    category = CategoryRecord(category=Category(title="Study"))
    root = TaskRecord(task=Task(title="Task", category_id=category.category.id))
    without = replace(root, task=replace(root.task, category_id=None))
    result = plan(
        local(category, root),
        snapshot(mark(category), without),
        snapshot(category, root, revision=1),
        frozenset({("task", root.task.id)}),
    )
    d = decision(result, "category", category.category.id)
    assert (d.action, d.reason) == ("defer", "referenced_by_pending_root")


def test_pending_node_protects_whole_tree_and_linked_calendar():
    child = Subtask(title="Edited child")
    root = TaskRecord(task=Task(title="Task", subtasks=(child,)))
    changed = replace(root, task=replace(root.task, title="Cloud task"))
    block = BlockRecord(block=event(root.task.id))
    result = plan(
        local(root),
        snapshot(changed, block),
        snapshot(root, revision=1),
        frozenset({("node", child.id)}),
    )
    assert decision(result, "task", root.task.id).action == "defer"
    assert decision(result, "block", block.block.id).action == "defer"


def test_role_transfer_keeps_physical_tree_and_stages_transition():
    from test_sync_category_capture import NOW

    record = TaskRecord(task=Task(title="Completed history", completed=True), completed_at=NOW)
    rule, day = uuid4(), date(2026, 11, 27)
    history = OccurrenceRecord(id=history_id(rule, day), rule_id=rule, day=day, current=record)
    marker = mark(record, "type_changed")
    result = plan(local(record), snapshot(marker, history), snapshot(record, revision=1))
    assert decision(result, "task", record.task.id).action == "retain"
    assert decision(result, "occurrence", history.id).action == "transition"


def test_regular_root_delete_is_protected_and_regular_rollover_is_explicit():
    old = TaskRecord(task=Task(title="Current"))
    regular = RegularRecord(
        id=uuid4(),
        first_day=date(2026, 11, 27),
        interval_days=1,
        current_day=date(2026, 11, 27),
        current=old,
    )
    marker = RemovalRecord(
        entity_kind="regular",
        entity_id=regular.id,
        revision=2,
        delete_epoch=1,
        reason="user_deleted",
    )
    result = plan(
        local(regular),
        snapshot(marker),
        snapshot(regular, revision=1),
        frozenset({("task", old.task.id)}),
    )
    assert decision(result, "regular", regular.id).action == "defer"
    new = replace(
        regular, current_day=date(2026, 11, 28), current=TaskRecord(task=Task(title="Next"))
    )
    result = plan(local(regular), snapshot(new), snapshot(regular, revision=1))
    d = decision(result, "regular", regular.id)
    assert d.action == "transition" and d.local == regular


def test_local_only_pending_category_does_not_crash_or_disappear():
    category = CategoryRecord(category=Category(title="Pending category"))
    result = plan(
        local(category), snapshot(), protected=frozenset({("category", category.category.id)})
    )
    assert decision(result, "category", category.category.id).action == "defer"


def test_preview_rejects_partial_graph_and_bad_inputs():
    root = TaskRecord(task=Task(title="Missing category", category_id=uuid4()))
    with pytest.raises(ValueError):
        plan(local(), snapshot(root))
    with pytest.raises(ValueError):
        plan([], snapshot())
    with pytest.raises(ValueError):
        plan(local(), snapshot(), protected=frozenset({("unknown", uuid4())}))


def test_new_current_cannot_take_child_uuid_from_preserved_old_root():
    child = Subtask(title="Old child")
    old = TaskRecord(task=Task(title="Old current", subtasks=(child,)))
    regular = RegularRecord(
        id=uuid4(),
        first_day=date(2026, 11, 27),
        interval_days=1,
        current_day=date(2026, 11, 27),
        current=old,
    )
    unsafe = replace(
        regular,
        current_day=date(2026, 11, 28),
        current=TaskRecord(task=Task(title="New root", subtasks=(child,))),
    )
    result = plan(local(regular), snapshot(unsafe), snapshot(regular, revision=1))
    d = decision(result, "regular", regular.id)
    assert (d.action, d.reason) == ("conflict", "local_identity_occupied")


def test_closing_rule_with_retention_does_not_archive_its_local_tree():
    root = TaskRecord(task=Task(title="Retained previous current"))
    regular = RegularRecord(
        id=uuid4(),
        first_day=date(2026, 11, 27),
        interval_days=1,
        current_day=date(2026, 11, 27),
        current=root,
    )
    closed = RemovalRecord(
        entity_kind="regular",
        entity_id=regular.id,
        revision=2,
        delete_epoch=1,
        reason="user_deleted",
    )
    retained = mark(root, "retention")
    result = plan(local(regular), snapshot(closed, retained), snapshot(regular, revision=1))
    assert decision(result, "regular", regular.id).action == "retire_rule"
    assert decision(result, "task", root.task.id).action == "retain"
    assert all(d.action != "archive" for d in result.decisions)


def test_category_still_used_by_retained_local_history_is_preserved():
    category = CategoryRecord(category=Category(title="History subject"))
    root = TaskRecord(task=Task(title="Local history", category_id=category.category.id))
    result = plan(
        local(category, root), snapshot(mark(category)), snapshot(category, root, revision=1)
    )
    d = decision(result, "category", category.category.id)
    assert (d.action, d.reason) == ("retain", "referenced_by_local_history")


def test_existing_calendar_link_is_not_silently_reassigned():
    one, two = TaskRecord(task=Task(title="One")), TaskRecord(task=Task(title="Two"))
    block = BlockRecord(block=event(one.task.id))
    moved = replace(block, block=replace(block.block, task_id=two.task.id))
    result = plan(
        local(one, two, block), snapshot(one, two, moved), snapshot(one, two, block, revision=1)
    )
    d = decision(result, "block", block.block.id)
    assert (d.action, d.reason) == ("conflict", "calendar_parent_changed")


def test_closed_rule_without_replacement_body_keeps_referenced_local_category():
    category = CategoryRecord(category=Category(title="Retained subject"))
    root = TaskRecord(task=Task(title="Current", category_id=category.category.id))
    regular = RegularRecord(
        id=uuid4(),
        first_day=date(2026, 11, 27),
        interval_days=1,
        current_day=date(2026, 11, 27),
        current=root,
    )
    closed = RemovalRecord(
        entity_kind="regular",
        entity_id=regular.id,
        revision=2,
        delete_epoch=1,
        reason="user_deleted",
    )
    result = plan(
        local(category, regular),
        snapshot(closed, mark(category)),
        snapshot(category, regular, revision=1),
    )
    assert decision(result, "regular", regular.id).action == "retire_rule"
    assert decision(result, "category", category.category.id).action == "retain"
