"""Category/calendar/AI acceptance on real disposable stores, fake verified exchange."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from test_sync_block_capture import event
from test_sync_category_capture import NOW, bound
from test_sync_prepare_loop import confirmed
from test_sync_queue_acceptance import setup
from test_sync_regular_plan import DAY

from ritm.application.tasks import TaskService
from ritm.domain.tasks import Category, Task
from ritm.integrations.sync_codec import BlockRecord, OccurrenceRecord, RemovalRecord, TaskRecord
from ritm.storage.sqlite import TaskStore


def drain(path, context, limit=30):
    actions = []
    for _ in range(limit):
        with TaskStore(path) as store:
            step = store.prepare_next_sync(context.moment)
            if step.status in ("idle", "settled"):
                assert not store.list_sync_block_changes()
                assert not store.list_sync_regular_changes()
                return actions, store.read_sync_snapshot()
            assert step.status == "prepared", (step, actions)
            frozen = step.request
        with TaskStore(path) as store:
            retry = store.prepare_next_sync(context.moment)
            assert retry.status == "pending" and retry.request.raw == frozen.raw
            receipt, snapshot = confirmed(store.read_sync_snapshot(), frozen, context)
            store.save_sync_receipt_and_snapshot(receipt, snapshot)
            actions.append((frozen.operation.target_kind, frozen.operation.action))
    raise AssertionError(actions)


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("foreign", [False, True])
def test_regular_and_history_delete_restore_calendar_after_journal_retirement(
    tmp_path,
    history,
    foreign,
):
    path, _, context, _ = setup(tmp_path)
    drain(path, context)
    root = context.bases[0].entry.entity.current.task.id
    with TaskStore(path) as store:
        block = event(root)
        store.create_work_block(block)
    drain(path, context)
    if history:
        with TaskStore(path) as store:
            TaskService(store).set_completed(root, root, True)
            store.refresh_recurrences(DAY + timedelta(days=1))
        context = replace(context, moment=NOW + timedelta(days=1))
        drain(path, context)
    with TaskStore(path) as store:
        entry = store.archive_node(root, root, lambda _: None, context.moment)
    actions, snapshot = drain(path, context)
    assert ("occurrence" if history else "regular", "delete") in actions
    mark = next(
        e
        for e in snapshot.entries
        if type(e.entity) is RemovalRecord
        and e.entity.entity_kind == "block"
        and e.entity.entity_id == block.id
    )
    assert mark.entity.reason == "user_deleted"
    if foreign:
        changed = replace(
            mark.entity, revision=snapshot.set_revision + 1, delete_epoch=mark.delete_epoch + 1
        )
        snapshot = replace(
            snapshot,
            snapshot_id=uuid4(),
            set_revision=changed.revision,
            entries=tuple(
                replace(
                    e, entity=changed, revision=changed.revision, delete_epoch=changed.delete_epoch
                )
                if e == mark
                else e
                for e in snapshot.entries
            ),
        )
        with TaskStore(path) as store:
            store.remember_sync_snapshot(snapshot)
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(entry))
    if foreign:
        for _ in range(8):
            with TaskStore(path) as store:
                step = store.prepare_next_sync(context.moment)
                if step.status == "conflict":
                    assert store.list_sync_block_changes()
                    return
                assert step.status == "prepared", step
                receipt, snapshot = confirmed(store.read_sync_snapshot(), step.request, context)
                store.save_sync_receipt_and_snapshot(receipt, snapshot)
        raise AssertionError("Foreign epoch must not be waived")
    actions, _ = drain(path, context)
    assert ("block", "restore") in actions
    with TaskStore(path) as store:
        assert store.get_work_block(block.id) == block


@pytest.mark.parametrize("published", [False, True])
def test_skipped_repeat_never_uploads_calendar_as_retained_history(tmp_path, published):
    path, _, context, _ = setup(tmp_path)
    drain(path, context)
    root = context.bases[0].entry.entity.current.task.id
    with TaskStore(path) as store:
        block = event(root)
        store.create_work_block(block)
    if published:
        drain(path, context)
    with TaskStore(path) as store:
        store.refresh_recurrences(DAY + timedelta(days=1))
    later = replace(context, moment=NOW + timedelta(days=1))
    actions, snapshot = drain(path, later)
    live = [e.entity for e in snapshot.entries if type(e.entity) is BlockRecord]
    assert not live
    assert all(kind != "block" for kind, _ in actions)
    with TaskStore(path) as store:
        assert store.get_work_block(block.id) == block
        assert store.is_skipped_occurrence(root)


def test_completed_repeat_keeps_calendar_attached_to_history(tmp_path):
    path, _, context, _ = setup(tmp_path)
    drain(path, context)
    root = context.bases[0].entry.entity.current.task.id
    with TaskStore(path) as store:
        block = event(root)
        store.create_work_block(block)
        TaskService(store).set_completed(root, root, True)
        store.refresh_recurrences(DAY + timedelta(days=1))
    actions, snapshot = drain(path, replace(context, moment=NOW + timedelta(days=1)))
    assert ("block", "create") in actions
    saved = next(e.entity for e in snapshot.entries if type(e.entity) is BlockRecord)
    assert saved.block.task_id == root and saved.archived_task_title is None
    assert any(
        type(e.entity) is OccurrenceRecord and e.entity.current.task.id == root
        for e in snapshot.entries
    )


@pytest.mark.parametrize("writer", ["proposal", "mail_task", "mail_event", "schedule"])
def test_all_ai_writers_reach_confirmed_queue_without_files_or_mail_payload(tmp_path, writer):
    from types import SimpleNamespace

    from test_mail_proposals import event as mail_event
    from test_mail_proposals import proposal as mail_proposal
    from test_planning import START
    from test_planning import context as planning_context
    from test_planning import proposal as schedule_proposal

    from ritm.application.planning import apply_schedule

    path = tmp_path / "tasks.sqlite3"
    with bound(path, snapshot=True) as store:
        category = Category(title="Study")
        task = Task(title="AI task", expected_duration=timedelta(hours=4), category_id=category.id)
        if writer == "proposal":
            store.save_category(category)
            identifier = uuid4()
            store.apply_proposal(identifier, "a" * 64, None, task)

            def replay(s):
                return s.apply_proposal(identifier, "a" * 64, None, task)
        elif writer == "mail_task":
            draft = mail_proposal()
            store.apply_mail_proposal(draft)

            def replay(s):
                return s.apply_mail_proposal(draft)
        elif writer == "mail_event":
            draft = mail_proposal(event=mail_event())
            store.apply_mail_proposal(draft)

            def replay(s):
                return s.apply_mail_proposal(draft)
        else:
            store._clock = lambda: START
            TaskService(store).create_task(task, category)
            draft = schedule_proposal(planning_context(store, task))
            apply_schedule(store, draft)

            def replay(s):
                return apply_schedule(s, draft)

        context = SimpleNamespace(moment=START if writer == "schedule" else NOW)
    actions, snapshot = drain(path, context)
    assert ("block" if writer in ("schedule", "mail_event") else "task", "create") in actions
    for entry in snapshot.entries:
        assert type(entry.entity).__name__ in {
            "CalendarRecord",
            "CategoryRecord",
            "TaskRecord",
            "BlockRecord",
        }
    with TaskStore(path) as store:
        table = "mail_proposals" if writer.startswith("mail_") else "applied_proposals"
        assert store._db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
        queue = store.list_sync_intents()
        replay(store)
        assert store.list_sync_intents() == queue
        assert not store.list_sync_block_changes()
        for queued in queue:
            raw = store.get_frozen_sync_request(queued.intent.id).raw
            assert b"student@example.org" not in raw
            assert b"fingerprint" not in raw


@pytest.mark.parametrize("writer", ["proposal", "mail_task", "mail_event"])
def test_ai_updates_existing_completed_task_or_event_in_confirmed_queue(tmp_path, writer):
    from types import SimpleNamespace

    from test_mail_proposals import event as mail_event
    from test_mail_proposals import proposal as mail_proposal
    from test_mail_proposals import source

    path = tmp_path / "tasks.sqlite3"
    context = SimpleNamespace(moment=NOW)
    with bound(path, snapshot=True) as store:
        if writer == "proposal":
            task = Task(title="Completed")
            store.create_task(task)
            TaskService(store).set_completed(task.id, task.id, True)
        else:
            draft = mail_proposal(event=mail_event()) if writer == "mail_event" else mail_proposal()
            store.apply_mail_proposal(draft)
            if writer == "mail_task":
                TaskService(store).set_completed(draft.after.id, draft.after.id, True)
    drain(path, context)
    with TaskStore(path) as store:
        if writer == "proposal":
            before = store.get_task(task.id)
            store.apply_proposal(uuid4(), "b" * 64, before, replace(before, title="Updated"))
        else:
            link = store.get_source_link(draft.source.key)
            before = (
                store.get_work_block(draft.after.id)
                if writer == "mail_event"
                else store.get_task(draft.after.id)
            )
            update = mail_proposal(
                source("Changed mail"),
                link=link,
                before=before,
                event=mail_event("Changed event") if writer == "mail_event" else None,
                task={"task_id": str(before.id), "changes": {"title": "Changed task"}}
                if writer == "mail_task"
                else None,
            )
            store.apply_mail_proposal(update)
    actions, snapshot = drain(path, context)
    assert actions == [("block" if writer == "mail_event" else "task", "put")]
    if writer != "mail_event":
        record = next(e.entity for e in snapshot.entries if type(e.entity) is TaskRecord)
        assert record.task.completed and record.completed_at is not None


def test_retained_history_caption_does_not_resurrect_task(tmp_path):
    path, _, context, _ = setup(tmp_path, days=1)
    _, snapshot = drain(path, context)
    history = next(e for e in snapshot.entries if type(e.entity) is OccurrenceRecord)
    root = history.entity.current.task.id
    marker = RemovalRecord(
        entity_kind="occurrence",
        entity_id=history.entity.id,
        root_id=root,
        revision=snapshot.set_revision + 1,
        delete_epoch=history.delete_epoch,
        reason="retention",
    )
    retained = replace(
        snapshot,
        snapshot_id=uuid4(),
        set_revision=marker.revision,
        entries=tuple(
            replace(e, entity=marker, revision=marker.revision) if e == history else e
            for e in snapshot.entries
        ),
    )
    with TaskStore(path) as store:
        store.remember_sync_snapshot(retained)
        block = event(root)
        store.create_work_block(block)
    actions, snapshot = drain(path, context)
    assert actions == [("block", "create")]
    record = next(e.entity for e in snapshot.entries if type(e.entity) is BlockRecord)
    assert record.archived_task_title == history.entity.current.task.title
    assert not any(type(e.entity) is OccurrenceRecord for e in snapshot.entries)
    with TaskStore(path) as store:
        assert store.get_task(root) is not None


@pytest.mark.parametrize("writer", ["proposal", "mail_task", "mail_event", "schedule"])
def test_ai_receipt_failure_rolls_back_captured_queue_and_data(tmp_path, writer):
    import sqlite3

    from test_mail_proposals import event as mail_event
    from test_mail_proposals import proposal as mail_proposal
    from test_planning import START
    from test_planning import context as planning_context
    from test_planning import proposal as schedule_proposal

    from ritm.application.planning import apply_schedule

    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        table = "mail_proposals" if writer.startswith("mail_") else "applied_proposals"
        task = Task(title="Planned", expected_duration=timedelta(hours=4))
        if writer == "schedule":
            store._clock = lambda: START
            store.create_task(task)
            draft = schedule_proposal(planning_context(store, task))
        tasks, queue = store.list_tasks(), store.list_sync_intents()
        with store._db:
            store._db.execute(
                f"CREATE TRIGGER reject BEFORE INSERT ON {table} "
                "BEGIN SELECT RAISE(ABORT,'receipt failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="receipt failure"):
            if writer == "proposal":
                store.apply_proposal(uuid4(), "c" * 64, None, task)
            elif writer == "schedule":
                apply_schedule(store, draft)
            else:
                store.apply_mail_proposal(
                    mail_proposal(event=mail_event() if writer == "mail_event" else None)
                )
        assert store.list_tasks() == tasks
        assert store.list_sync_intents() == queue
        assert not store.list_sync_block_changes()
        assert not store.list_work_blocks() and not store.list_source_links()


def test_canonical_current_parent_survives_delete_restore_and_restart(tmp_path):
    path, _, context, _ = setup(tmp_path)
    drain(path, context)
    original = context.bases[0].entry.entity.current.task.id
    with TaskStore(path) as store:
        TaskService(store).set_completed(original, original, True)
        store.refresh_recurrences(DAY + timedelta(days=1))
        local_root = UUID(store.list_recurrences()[0]["task_id"])
        block = event(local_root)
        store.create_work_block(block)
    context = replace(context, moment=NOW + timedelta(days=1))
    _, snapshot = drain(path, context)
    remote_block = next(e.entity for e in snapshot.entries if type(e.entity) is BlockRecord)
    assert remote_block.block.task_id != local_root
    with TaskStore(path) as store:
        assert (
            dict(store.read_sync_prepared_state().identities)[local_root]
            == remote_block.block.task_id
        )
        entry = store.archive_node(local_root, local_root, lambda _: None, context.moment)
    drain(path, context)
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(entry))
    actions, snapshot = drain(path, context)
    assert ("block", "restore") in actions
    saved = next(e.entity for e in snapshot.entries if type(e.entity) is BlockRecord)
    assert saved.block.task_id == remote_block.block.task_id
    with TaskStore(path) as store:
        assert store.get_work_block(block.id).task_id == local_root


@pytest.mark.parametrize("destination", ["absent", "history", "retained"])
@pytest.mark.parametrize("terminal", ["stop", "delete"])
def test_calendar_survives_late_terminal_without_unseen_repeats(tmp_path, destination, terminal):
    from ritm.domain import completion
    from ritm.integrations.sync_operations import rollover_record
    from ritm.integrations.sync_pages import SnapshotEntry
    from ritm.integrations.sync_transitions import history_record

    path, changes, context, _ = setup(tmp_path, days=2)
    selected = changes[-1].after.current.task.id
    with TaskStore(path) as store:
        block = event(selected)
        store.create_work_block(block)
    if destination != "absent":
        _, snapshot = drain(path, context)
        source = next(
            e for e in snapshot.entries if type(e.entity) is type(context.bases[0].entry.entity)
        )
        completed = replace(
            source.entity,
            current=TaskRecord(
                task=completion.set_completed(
                    source.entity.current.task, source.entity.current.task.id, True
                ),
                completed_at=context.moment,
            ),
        )
        history = history_record(completed)
        revision = snapshot.set_revision + 1
        old = SnapshotEntry(revision=revision, delete_epoch=source.delete_epoch, entity=history)
        if destination == "retained":
            mark = RemovalRecord(
                entity_kind="occurrence",
                entity_id=history.id,
                root_id=history.current.task.id,
                revision=revision,
                delete_epoch=source.delete_epoch + 1,
                reason="retention",
            )
            old = SnapshotEntry(revision=revision, delete_epoch=mark.delete_epoch, entity=mark)
        entries = []
        for e in snapshot.entries:
            if e == source:
                e = replace(
                    e,
                    revision=revision,
                    entity=rollover_record(completed, DAY + timedelta(days=10)),
                )
            elif destination == "retained" and type(e.entity) is BlockRecord:
                e = replace(
                    e,
                    revision=revision,
                    entity=replace(e.entity, archived_task_title=history.current.task.title),
                )
            entries.append(e)
        snapshot = replace(
            snapshot, snapshot_id=uuid4(), set_revision=revision, entries=(*entries, old)
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
    actions, snapshot = drain(path, replace(context, moment=NOW + timedelta(days=10)))
    assert not any(action == "rollover" for _, action in actions)
    with TaskStore(path) as store:
        assert (store.get_work_block(block.id) is None) == (terminal == "delete")
        assert store._db.execute(
            "SELECT 1 FROM work_blocks WHERE id=?", (str(block.id),)
        ).fetchone()
    assert (
        not any(type(e.entity) is BlockRecord for e in snapshot.entries)
        if terminal == "delete"
        else any(type(e.entity) is BlockRecord for e in snapshot.entries)
    )


def test_missing_cascade_effect_rejects_entire_receipt_and_snapshot(tmp_path):
    path, _, context, _ = setup(tmp_path)
    drain(path, context)
    root = context.bases[0].entry.entity.current.task.id
    with TaskStore(path) as store:
        store.create_work_block(event(root))
    drain(path, context)
    with TaskStore(path) as store:
        task = store.get_task(root)
        store.archive_node(root, root, lambda _: None, NOW)
        step = store.prepare_next_sync(NOW)
        before = store.read_sync_snapshot()
        old_block = next(e.entity for e in before.entries if type(e.entity) is BlockRecord)
        receipt, complete = confirmed(before, step.request, context)
        # A structurally valid but incomplete response kept the block as a caption.
        broken = replace(
            complete,
            entries=tuple(
                replace(e, entity=replace(old_block, archived_task_title=task.title))
                if type(e.entity) is RemovalRecord and e.entity.entity_kind == "block"
                else e
                for e in complete.entries
            ),
        )
        with pytest.raises(ValueError, match="связанного календаря"):
            store.save_sync_receipt_and_snapshot(receipt, broken)
        assert store.get_frozen_sync_request(step.request.intent_id).receipt is None
        assert store.read_sync_snapshot() == before
        assert store.list_sync_block_changes() and store.list_sync_regular_changes()
        store.save_sync_receipt_and_snapshot(receipt, complete)
    drain(path, context)
