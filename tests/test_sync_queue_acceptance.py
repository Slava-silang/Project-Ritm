"""First-block acceptance through the public queue API and restart at every step."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from test_sync_prepare_loop import confirmed, remote
from test_sync_regular_plan import DAY, NOW, seed

from ritm.application.tasks import TaskService
from ritm.domain.tasks import Subtask
from ritm.integrations.sync_codec import OccurrenceRecord, RemovalRecord, TaskRecord
from ritm.integrations.sync_pages import SnapshotEntry
from ritm.storage.sqlite import TaskStore


def drain(path, context, snapshot, limit=30):
    actions = []
    for _ in range(limit):
        with TaskStore(path) as store:
            step = store.prepare_next_sync(context.moment)
            if step.status in ("settled", "idle"):
                assert not store.list_sync_regular_changes()
                return snapshot, actions
            assert step.status == "prepared", (step, actions)
            frozen = step.request
            before_facts = store.list_sync_regular_changes()
        with TaskStore(path) as store:
            retry = store.prepare_next_sync(context.moment)
            assert retry.status == "pending" and retry.request.raw == frozen.raw
            receipt, snapshot = confirmed(snapshot, frozen, context)
            store.save_sync_receipt_and_snapshot(receipt, snapshot)
            assert store.list_sync_regular_changes() == before_facts
            actions.append(frozen.operation.action)
    raise AssertionError(f"Queue did not settle: {actions}")


def setup(tmp_path, days=0, empty=False):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, days)
    snapshot = remote(context)
    if empty:
        snapshot = replace(snapshot, set_revision=1, entries=(snapshot.entries[0],))
    with TaskStore(path) as store:
        store.remember_sync_snapshot(snapshot)
    return path, changes, context, snapshot


@pytest.mark.parametrize("origin", ["stop", "detach"])
def test_exit_then_edit_prune_delete_restore_after_each_restart(tmp_path, origin):
    path, changes, context, snapshot = setup(tmp_path, days=2 if origin == "detach" else 0)
    snapshot, _ = drain(path, context, snapshot)
    root = context.bases[0].entry.entity.current.task.id
    with TaskStore(path) as store:
        task = store.get_task(root)
        if origin == "stop":
            store.save_regular_task(task, None, interval=None, first=None, today=DAY)
        else:
            TaskService(store).set_completed(root, root, False)
    snapshot, actions = drain(path, context, snapshot)
    assert ("stop_regular" if origin == "stop" else "detach_occurrence") in actions
    with TaskStore(path) as store:
        store.update_task(root, lambda task: replace(task, title="Edited ordinary lineage"))
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["put"]
    with TaskStore(path) as store:
        task = store.get_task(root)
        TaskService(store).delete_node(root, task.subtasks[0].id, context.moment)
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["prune"]
    with TaskStore(path) as store:
        child_trash = next(UUID(row["id"]) for row in store.list_trash() if row["kind"] == 1)
        TaskService(store).restore(child_trash)
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["restore"]
    with TaskStore(path) as store:
        TaskService(store).delete_node(root, root, context.moment)
        trash = next(UUID(row["id"]) for row in store.list_trash() if row["kind"] == 0)
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["delete"]
    with TaskStore(path) as store:
        TaskService(store).restore(trash)
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["restore"]
    assert any(type(e.entity) is TaskRecord and e.entity.task.id == root for e in snapshot.entries)


@pytest.mark.parametrize("terminal", ["stop", "delete"])
def test_never_uploaded_regular_can_close_without_losing_old_slots(tmp_path, terminal):
    path, changes, context, snapshot = setup(tmp_path, days=2, empty=True)
    with TaskStore(path) as store:
        task = store.get_task(changes[-1].after.current.task.id)
        if terminal == "stop":
            store.save_regular_task(
                task, None, interval=None, first=None, today=DAY + timedelta(days=2)
            )
        else:
            TaskService(store).delete_node(task.id, task.id, context.moment)
    snapshot, actions = drain(path, context, snapshot)
    assert actions[0] == "create"
    assert ("stop_regular" if terminal == "stop" else "delete") in actions
    assert len([e for e in snapshot.entries if type(e.entity) is OccurrenceRecord]) == 2


@pytest.mark.parametrize("empty", [False, True])
def test_offline_reschedule_preserves_intermediate_completions(tmp_path, empty):
    path, changes, context, snapshot = setup(tmp_path, days=2, empty=empty)
    with TaskStore(path) as store:
        task = store.get_task(changes[-1].after.current.task.id)
        store.save_regular_task(task, None, interval=3, first=DAY, today=DAY + timedelta(days=2))
    snapshot, actions = drain(path, context, snapshot)
    assert any(a.startswith("reschedule") for a in actions)
    assert len([e for e in snapshot.entries if type(e.entity) is OccurrenceRecord]) == 2


def test_change_schedule_and_tree_together(tmp_path):
    path, changes, context, snapshot = setup(tmp_path)
    with TaskStore(path) as store:
        task = store.get_task(changes[0].root_id)
        task = replace(task, subtasks=(*task.subtasks, Subtask(title="New step")))
        store.save_regular_task(task, None, interval=2, first=DAY, today=DAY)
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["reschedule_offline"]


@pytest.mark.parametrize("source", ["history", "ordinary"])
def test_promotion_after_unsent_edits(tmp_path, source):
    path, changes, context, snapshot = setup(tmp_path, days=2 if source == "history" else 0)
    snapshot, _ = drain(path, context, snapshot)
    root = context.bases[0].entry.entity.current.task.id
    if source == "ordinary":
        with TaskStore(path) as store:
            store.save_regular_task(
                store.get_task(root), None, interval=None, first=None, today=DAY
            )
        snapshot, _ = drain(path, context, snapshot)
    with TaskStore(path) as store:
        store.update_task(root, lambda task: replace(task, title="Edited before promotion"))
        task = store.get_task(root)
        store.save_regular_task(task, None, interval=2, first=DAY, today=context.moment.date())
    snapshot, actions = drain(path, context, snapshot)
    assert "promote_regular" in actions


def test_retained_history_rename_is_local_and_not_resurrected(tmp_path):
    path, changes, context, snapshot = setup(tmp_path, days=2)
    snapshot, _ = drain(path, context, snapshot)
    old = next(e for e in snapshot.entries if type(e.entity) is OccurrenceRecord)
    mark = RemovalRecord(
        entity_kind="occurrence",
        entity_id=old.entity.id,
        root_id=old.entity.current.task.id,
        revision=snapshot.set_revision + 1,
        delete_epoch=old.delete_epoch + 1,
        reason="retention",
    )
    retained = replace(
        snapshot,
        snapshot_id=uuid4(),
        set_revision=mark.revision,
        entries=tuple(
            SnapshotEntry(revision=mark.revision, delete_epoch=mark.delete_epoch, entity=mark)
            if e is old
            else e
            for e in snapshot.entries
        ),
    )
    with TaskStore(path) as store:
        store.remember_sync_snapshot(retained)
        store.update_task(
            old.entity.current.task.id, lambda task: replace(task, title="Local history rename")
        )
    after, actions = drain(path, context, retained)
    assert actions == [] and after == retained


@pytest.mark.parametrize("edit", ["add_subtask", "add_item", "deadline"])
def test_history_edit_detaches_with_new_tree_or_deadline(tmp_path, edit):
    from ritm.domain.tasks import ChecklistItem

    path, changes, context, snapshot = setup(tmp_path, days=2)
    snapshot, _ = drain(path, context, snapshot)
    root = context.bases[0].entry.entity.current.task.id
    with TaskStore(path) as store:
        task = store.get_task(root)
        if edit == "add_subtask":
            TaskService(store).add_subtask(root, Subtask(title="New unfinished step"))
        elif edit == "add_item":
            TaskService(store).add_item(root, task.subtasks[0].id, ChecklistItem(title="New item"))
        else:
            store.update_task(
                root, lambda task: replace(task, deadline=context.moment + timedelta(days=1))
            )
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["detach_occurrence"]
    with TaskStore(path) as store:
        assert store.get_task(root) is not None


def test_history_to_regular_to_history_can_detach_back_to_ordinary(tmp_path):
    path, changes, context, snapshot = setup(tmp_path, days=2)
    snapshot, _ = drain(path, context, snapshot)
    root = context.bases[0].entry.entity.current.task.id
    with TaskStore(path) as store:
        store.save_regular_task(
            store.get_task(root), None, interval=1, first=DAY, today=context.moment.date()
        )
    snapshot, _ = drain(path, context, snapshot)
    later = replace(context, moment=context.moment + timedelta(days=1))
    with TaskStore(path) as store:
        store.refresh_recurrences(later.moment.date())
    snapshot, _ = drain(path, later, snapshot)
    with TaskStore(path) as store:
        TaskService(store).set_completed(root, root, False)
    snapshot, actions = drain(path, later, snapshot)
    assert actions == ["detach_occurrence"]
    with TaskStore(path) as store:
        store.update_task(
            root, lambda task: replace(task, title="Edited after repeated role changes")
        )
    snapshot, actions = drain(path, later, snapshot)
    assert actions == ["put"]


@pytest.mark.parametrize("terminal", ["stop", "delete"])
@pytest.mark.parametrize("destination", ["absent", "history", "retained"])
def test_late_terminal_preserves_history_without_new_unseen_repeats(
    tmp_path, destination, terminal
):
    from ritm.domain import completion
    from ritm.integrations.sync_operations import rollover_record
    from ritm.integrations.sync_transitions import history_record

    path, changes, context, snapshot = setup(tmp_path, days=2)
    selected = changes[-1].after.current.task.id
    if destination != "absent":
        snapshot, _ = drain(path, context, snapshot)
        source = next(
            e for e in snapshot.entries if type(e.entity) is type(context.bases[0].entry.entity)
        )
        task = source.entity.current.task
        completed = replace(
            source.entity,
            current=TaskRecord(
                task=completion.set_completed(task, task.id, True), completed_at=context.moment
            ),
        )
        historical = history_record(completed)
        advanced = rollover_record(completed, DAY + timedelta(days=10))
        revision = snapshot.set_revision + 1
        old = SnapshotEntry(revision=revision, delete_epoch=source.delete_epoch, entity=historical)
        if destination == "retained":
            mark = RemovalRecord(
                entity_kind="occurrence",
                entity_id=historical.id,
                root_id=historical.current.task.id,
                revision=revision,
                delete_epoch=source.delete_epoch + 1,
                reason="retention",
            )
            old = SnapshotEntry(revision=revision, delete_epoch=mark.delete_epoch, entity=mark)
        snapshot = replace(
            snapshot,
            snapshot_id=uuid4(),
            set_revision=revision,
            entries=tuple(
                SnapshotEntry(revision=revision, delete_epoch=e.delete_epoch, entity=advanced)
                if e is source
                else e
                for e in snapshot.entries
            )
            + (old,),
        )
        with TaskStore(path) as store:
            store.remember_sync_snapshot(snapshot)
    with TaskStore(path) as store:
        if terminal == "stop":
            store.save_regular_task(
                store.get_task(selected),
                None,
                interval=None,
                first=None,
                today=DAY + timedelta(days=2),
            )
        else:
            TaskService(store).delete_node(selected, selected, context.moment)
    late = replace(context, moment=NOW + timedelta(days=10))
    snapshot, actions = drain(path, late, snapshot)
    assert "rollover" not in actions
    if terminal == "delete":
        assert ("forget_task" if destination == "absent" else "delete") in actions
        assert "close_regular" in actions
    elif destination == "absent":
        assert "stop_offline" in actions
    elif destination == "history":
        assert actions == ["detach_occurrence", "close_regular"]
    else:
        assert actions == ["close_regular"]
    with TaskStore(path) as store:
        assert (store.get_task(selected) is None) == (terminal == "delete")
        if terminal == "delete":
            assert store.list_trash()
    if terminal == "stop" and destination == "retained":
        with TaskStore(path) as store:
            store.update_task(selected, lambda task: replace(task, title="Local retained rename"))
        snapshot, later_actions = drain(path, late, snapshot)
        assert later_actions == []
        with TaskStore(path) as store:
            trash = TaskService(store).delete_node(selected, selected, late.moment)
        snapshot, later_actions = drain(path, late, snapshot)
        assert later_actions == ["delete"]
        with TaskStore(path) as store:
            TaskService(store).restore(UUID(trash))
        snapshot, later_actions = drain(path, late, snapshot)
        assert later_actions == ["detach_occurrence"]


@pytest.mark.parametrize("epoch_change", [False, True])
def test_new_remote_revision_is_used_but_new_deletion_epoch_requires_review(tmp_path, epoch_change):
    path, changes, context, snapshot = setup(tmp_path)
    root = context.bases[0].entry.entity.current.task.id
    with TaskStore(path) as store:
        store.save_regular_task(store.get_task(root), None, interval=None, first=None, today=DAY)
    snapshot, _ = drain(path, context, snapshot)
    with TaskStore(path) as store:
        store.update_task(root, lambda task: replace(task, title="First local rename"))
    snapshot, _ = drain(path, context, snapshot)
    old = next(e for e in snapshot.entries if type(e.entity) is TaskRecord)
    revision = snapshot.set_revision + 1
    changed = replace(
        old,
        revision=revision,
        delete_epoch=old.delete_epoch + epoch_change,
        entity=replace(old.entity, task=replace(old.entity.task, title="Other device edit")),
    )
    snapshot = replace(
        snapshot,
        snapshot_id=uuid4(),
        set_revision=revision,
        entries=tuple(changed if e is old else e for e in snapshot.entries),
    )
    with TaskStore(path) as store:
        before_requests = store.list_frozen_sync_requests()
        store.remember_sync_snapshot(snapshot)
        store.update_task(root, lambda task: replace(task, title="Newest local rename"))
        if epoch_change:
            before = store.list_sync_regular_changes()
            step = store.prepare_next_sync(context.moment)
            assert step.status == "conflict" and step.reasons == ("base_epoch_conflict",)
            assert store.list_sync_regular_changes() == before
            assert store.list_frozen_sync_requests() == before_requests
            assert store.get_task(root).title == "Newest local rename"
            return
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["put"]
    with TaskStore(path) as store:
        for old_request in before_requests:
            assert store.get_frozen_sync_request(old_request.intent_id) == old_request


def test_foreign_global_identity_does_not_leave_half_a_command(tmp_path):
    from ritm.domain.tasks import Category
    from ritm.integrations.sync_codec import CategoryRecord

    path, changes, context, snapshot = setup(tmp_path)
    root = changes[0].root_id
    with TaskStore(path) as store:
        store.save_regular_task(store.get_task(root), None, interval=None, first=None, today=DAY)
    snapshot, _ = drain(path, context, snapshot)
    category = Category(title="Remote category")
    snapshot = replace(
        snapshot,
        snapshot_id=uuid4(),
        set_revision=snapshot.set_revision + 1,
        entries=(
            *snapshot.entries,
            SnapshotEntry(
                revision=snapshot.set_revision + 1,
                delete_epoch=0,
                entity=CategoryRecord(category=category),
            ),
        ),
    )
    with TaskStore(path) as store:
        store.remember_sync_snapshot(snapshot)
        requests = store.list_frozen_sync_requests()
        TaskService(store).add_subtask(root, Subtask(id=category.id, title="Conflicting node"))
        facts = store.list_sync_regular_changes()
        step = store.prepare_next_sync(context.moment)
        assert step.status == "conflict" and step.reasons == ("occupied_identity",)
        assert store.list_sync_regular_changes() == facts
        assert store.list_frozen_sync_requests() == requests


@pytest.mark.parametrize("empty", [False, True])
def test_restore_deleted_current_is_ordinary_and_does_not_restart_schedule(tmp_path, empty):
    path, changes, context, snapshot = setup(tmp_path, empty=empty)
    root = changes[0].root_id
    with TaskStore(path) as store:
        trash = TaskService(store).delete_node(root, root, context.moment)
    snapshot, _ = drain(path, context, snapshot)
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(trash))
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["restore"]
    assert not any(type(e.entity) is type(context.bases[0].entry.entity) for e in snapshot.entries)
    with TaskStore(path) as store:
        assert store.get_task(root) is not None and not store.list_recurrences()


def test_offline_schedule_chain_with_new_rules_rollovers_keeps_each_completion(tmp_path):
    path, changes, context, snapshot = setup(tmp_path)
    with TaskStore(path) as store:
        task = store.get_task(changes[0].root_id)
        store.save_regular_task(task, None, interval=2, first=DAY, today=DAY)
        for offset in (2, 4):
            current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
            TaskService(store).set_completed(current.id, current.id, True)
            store.refresh_recurrences(DAY + timedelta(days=offset))
        current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
        store.save_regular_task(current, None, interval=3, first=DAY, today=DAY + timedelta(days=4))
        TaskService(store).set_completed(current.id, current.id, True)
        store.refresh_recurrences(DAY + timedelta(days=6))
        current = store.get_task(UUID(store.list_recurrences()[0]["task_id"]))
        store.save_regular_task(current, None, interval=2, first=DAY, today=DAY + timedelta(days=6))
    later = replace(context, moment=NOW + timedelta(days=6))
    snapshot, actions = drain(path, later, snapshot)
    assert actions.count("reschedule_offline") == 3
    assert len([e for e in snapshot.entries if type(e.entity) is OccurrenceRecord]) == 3


def test_new_local_edit_while_request_is_pending_is_not_lost(tmp_path):
    path, changes, context, snapshot = setup(tmp_path, days=1)
    with TaskStore(path) as store:
        first = store.prepare_next_sync(context.moment)
        current = store.get_task(changes[-1].after.current.task.id)
        store.update_task(
            current.id, lambda task: replace(task, title="Edited during pending request")
        )
        assert store.prepare_next_sync(context.moment).request.raw == first.request.raw
        receipt, snapshot = confirmed(snapshot, first.request, context)
        store.save_sync_receipt_and_snapshot(receipt, snapshot)
    snapshot, actions = drain(path, context, snapshot)
    active = next(
        e.entity for e in snapshot.entries if type(e.entity) is type(context.bases[0].entry.entity)
    )
    assert active.current.task.title == "Edited during pending request"


def test_unuploaded_intermediate_history_can_be_promoted_then_roll_forward(tmp_path):
    path, changes, context, snapshot = setup(tmp_path, days=2)
    # Day 1 is not the server current and has never been uploaded as history.
    old = [c.before.current.task for c in changes if c.kind == "rollover"][1]
    with TaskStore(path) as store:
        store.save_regular_task(
            store.get_task(old.id), None, interval=1, first=DAY, today=context.moment.date()
        )
        store.refresh_recurrences(DAY + timedelta(days=3))
    later = replace(context, moment=NOW + timedelta(days=3))
    snapshot, actions = drain(path, later, snapshot)
    assert "promote_offline" in actions
    assert len([e for e in snapshot.entries if type(e.entity) is OccurrenceRecord]) == 2


def test_restore_then_promote_before_restore_has_been_sent(tmp_path):
    path, changes, context, snapshot = setup(tmp_path, days=2)
    snapshot, _ = drain(path, context, snapshot)
    root = context.bases[0].entry.entity.current.task.id
    with TaskStore(path) as store:
        trash = TaskService(store).delete_node(root, root, context.moment)
    snapshot, _ = drain(path, context, snapshot)
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(trash))
        store.save_regular_task(
            store.get_task(root), None, interval=2, first=DAY, today=context.moment.date()
        )
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["restore", "promote_regular"]


def test_current_regular_subtask_can_be_pruned_and_restored(tmp_path):
    path, changes, context, snapshot = setup(tmp_path)
    snapshot, _ = drain(path, context, snapshot)
    root = changes[0].root_id
    with TaskStore(path) as store:
        child = store.get_task(root).subtasks[0].id
        trash = TaskService(store).delete_node(root, child, context.moment)
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["prune"]
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(trash))
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["restore"]
    with TaskStore(path) as store:
        assert store.get_task(root).subtasks[0].id == child


def test_skipped_incomplete_slots_do_not_become_completed_history(tmp_path):
    path, changes, context, snapshot = setup(tmp_path)
    later = replace(context, moment=NOW + timedelta(days=3))
    with TaskStore(path) as store:
        store.refresh_recurrences(later.moment.date())
    snapshot, actions = drain(path, later, snapshot)
    assert actions == ["rollover"]
    assert not any(type(e.entity) is OccurrenceRecord for e in snapshot.entries)
    with TaskStore(path) as store:
        assert not store.get_task(changes[0].root_id).completed


def test_raw_ordinary_create_edit_complete_delete_restore_keeps_trash_and_dates(tmp_path):
    from ritm.domain.tasks import Task

    path, changes, context, snapshot = setup(tmp_path)
    with TaskStore(path, clock=lambda: context.moment) as store:
        task = Task(title="Ordinary queue lifecycle")
        TaskService(store).create_task(task)
        TaskService(store).rename_node(task.id, task.id, "Edited ordinary")
        TaskService(store).set_completed(task.id, task.id, True)
        trash = TaskService(store).delete_node(task.id, task.id, context.moment)
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["create", "put", "put", "delete"]
    with TaskStore(path, clock=lambda: context.moment) as store:
        assert store.get_task(task.id) is None and store.list_trash()
        TaskService(store).restore(UUID(trash))
    snapshot, actions = drain(path, context, snapshot)
    assert actions == ["restore"]
    with TaskStore(path) as store:
        assert store.get_task(task.id).completed
        assert store.list_completion_dates()[task.id] == context.moment
