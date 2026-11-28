"""Calendar facts survive restart and share the owning write transaction."""

import sqlite3
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from test_sync_category_capture import NOW, bound

from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore
from ritm.storage.sync_intents import sanitize_sync_backup


def event(task_id=None):
    return WorkBlock(
        task_id=task_id,
        title="Walk" if task_id is None else "",
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
    )


def test_event_create_edit_noop_restart_and_suppression(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    original = event()
    edited = replace(original, title="Class")
    with bound(path) as store:
        store.create_work_block(original)
        store.update_work_block(edited)
        store.update_work_block(edited)
        facts = store.list_sync_block_changes()
        assert len(facts) == 2
        assert facts[0].before is None and facts[0].after.block == original
        assert facts[1].before.block == original and facts[1].after.block == edited
        assert "Class" not in repr(facts)
        with store.suppress_sync_capture("incoming"):
            store.create_work_block(event())
        assert store.list_sync_block_changes() == facts
    with TaskStore(path) as store:
        assert store.list_sync_block_changes() == facts
        assert store.get_work_block(original.id) == edited


def test_task_delete_restore_records_logical_visibility(tmp_path):
    with bound(tmp_path / "tasks.sqlite3") as store:
        task = Task(title="Read")
        store.create_task(task)
        block = event(task.id)
        store.create_work_block(block)
        created = store.list_sync_block_changes()[0]
        assert created.after_intent_id == store.list_sync_intents()[-1].intent.id
        entry = store.archive_node(task.id, task.id, lambda _: None, NOW)
        assert not store.list_work_blocks()
        store.restore_entry(UUID(entry), lambda current, before, after, node: before)
        facts = store.list_sync_block_changes()
        assert [f.kind for f in facts] == ["save", "delete", "restore"]
        assert facts[1].before.block == block and facts[1].after is None
        assert facts[2].before is None and facts[2].after.block == block
        assert store.list_work_blocks() == (block,)


@pytest.mark.parametrize("operation", ["create", "edit", "archive", "restore"])
def test_journal_failure_rolls_back_owning_mutation(tmp_path, operation):
    with bound(tmp_path / "tasks.sqlite3") as store:
        task = Task(title="Read")
        store.create_task(task)
        original = event(task.id)
        if operation != "create":
            store.create_work_block(original)
        entry = None
        if operation == "restore":
            entry = store.archive_node(task.id, task.id, lambda _: None, NOW)
        facts, queue = store.list_sync_block_changes(), store.list_sync_intents()
        with store._db:
            store._db.execute(
                "CREATE TRIGGER fail_block BEFORE INSERT ON sync_block_changes "
                "BEGIN SELECT RAISE(ABORT,'block failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="block failure"):
            if operation == "create":
                store.create_work_block(original)
            elif operation == "edit":
                store.update_work_block(replace(original, ends_at=NOW + timedelta(hours=2)))
            elif operation == "archive":
                store.archive_node(task.id, task.id, lambda _: None, NOW)
            else:
                store.restore_entry(UUID(entry), lambda current, before, after, node: before)
        assert store.list_sync_block_changes() == facts
        assert store.list_sync_intents() == queue
        assert (store.get_task(task.id) is None) == (operation == "restore")
        assert store.get_work_block(original.id) == (
            None if operation in ("create", "restore") else original
        )


def test_disabled_and_staged_backup_sanitization(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        store.create_work_block(event())
        assert not store.list_sync_block_changes()
    with bound(path) as store:
        block = event()
        store.create_work_block(block)
        assert len(store.list_sync_block_changes()) == 1
    sanitize_sync_backup(path)
    with TaskStore(path) as store:
        assert not store.list_sync_block_changes()
        assert store.get_sync_binding() is None
        assert store.get_work_block(block.id) == block


def test_upgrade_v21_preserves_calendar_without_fabricating_changes(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    block = event()
    with TaskStore(path) as store:
        store.create_work_block(block)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE sync_block_changes")
        db.execute("PRAGMA user_version=21")
    with TaskStore(path) as store:
        assert store.get_work_block(block.id) == block
        assert not store.list_sync_block_changes()
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == 22


def test_regular_dependency_is_preserved_in_calendar_fact(tmp_path):
    with bound(tmp_path / "tasks.sqlite3") as store:
        task = Task(title="Daily reading")
        store.save_regular_task(task, None, interval=1, first=NOW.date(), today=NOW.date())
        regular = store.list_sync_regular_changes()[0]
        store.create_work_block(event(task.id))
        fact = store.list_sync_block_changes()[0]
        assert fact.after_regular_id == regular.id
        assert not store.list_sync_intents()


def test_ai_schedule_journal_and_receipt_are_atomic_and_retryable(tmp_path):
    from test_planning import START, context, proposal

    from ritm.application.planning import apply_schedule

    with bound(tmp_path / "tasks.sqlite3") as store:
        store._clock = lambda: START
        task = Task(title="Study", expected_duration=timedelta(hours=4))
        store.create_task(task)
        store.create_work_block(
            WorkBlock(
                title="Break",
                starts_at=START + timedelta(hours=2),
                ends_at=START + timedelta(hours=3),
            )
        )
        draft = proposal(context(store, task))
        assert len(draft.blocks) == 2
        prior = store.list_sync_block_changes()
        with store._db:
            store._db.execute(
                "CREATE TRIGGER fail_second BEFORE INSERT ON sync_block_changes "
                f"WHEN NEW.block_id='{draft.blocks[1].id}' "
                "BEGIN SELECT RAISE(ABORT,'second failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="second failure"):
            apply_schedule(store, draft)
        assert store.list_sync_block_changes() == prior
        assert len(store.list_work_blocks()) == 1
        assert not store._db.execute("SELECT * FROM applied_proposals").fetchall()
        with store._db:
            store._db.execute("DROP TRIGGER fail_second")
        apply_schedule(store, draft)
        facts = store.list_sync_block_changes()
        assert tuple(f.after.block for f in facts[1:]) == draft.blocks
        apply_schedule(store, draft)
        assert store.list_sync_block_changes() == facts


def test_mail_event_uses_calendar_journal(tmp_path):
    from test_mail_proposals import event as mail_event
    from test_mail_proposals import proposal

    with bound(tmp_path / "tasks.sqlite3") as store:
        draft = proposal(event=mail_event())
        store.apply_mail_proposal(draft)
        facts = store.list_sync_block_changes()
        assert len(facts) == 1 and facts[0].after.block == draft.after
        store.apply_mail_proposal(draft)
        assert store.list_sync_block_changes() == facts


def test_calendar_dependency_prevents_premature_regular_retirement(tmp_path):
    from test_sync_prepare_loop import confirmed, remote
    from test_sync_regular_plan import NOW as REGULAR_NOW
    from test_sync_regular_plan import seed

    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path)
    snapshot = remote(context)
    with TaskStore(path) as store:
        store.remember_sync_snapshot(snapshot)
        task = store.get_task(context.bases[0].entry.entity.current.task.id)
        store.update_task(task.id, lambda value: replace(value, title="Changed"))
        store.create_work_block(event(task.id))
        changes = store.list_sync_regular_changes()
        step = store.prepare_next_sync(REGULAR_NOW)
        assert step.status == "prepared"
        receipt, snapshot = confirmed(snapshot, step.request, context)
        store.save_sync_receipt_and_snapshot(receipt, snapshot)
        step = store.prepare_next_sync(REGULAR_NOW)
        assert step.status == "prepared" and step.request.operation.target_kind == "block"
        assert store.list_sync_regular_changes() == changes
        receipt, snapshot = confirmed(snapshot, step.request, context)
        store.save_sync_receipt_and_snapshot(receipt, snapshot)
        assert store.prepare_next_sync(REGULAR_NOW).status == "settled"
        assert not store.list_sync_regular_changes() and not store.list_sync_block_changes()


def test_archive_keeps_calendar_and_strips_only_copied_journal(tmp_path):
    from zipfile import ZipFile

    from ritm.storage.backup import create_backup, inspect_backup

    profile = tmp_path / "profile"
    profile.mkdir()
    path = profile / "tasks.sqlite3"
    block = event()
    with bound(path) as store:
        store.create_work_block(block)
        facts = store.list_sync_block_changes()
    archive = create_backup(profile, tmp_path / "copies", NOW)
    inspect_backup(archive)
    staged = tmp_path / "staged.sqlite3"
    with ZipFile(archive) as zipped:
        staged.write_bytes(zipped.read("tasks.sqlite3"))
    with TaskStore(staged) as store:
        assert not store.list_sync_block_changes()
        assert store.get_work_block(block.id) == block
    with TaskStore(path) as store:
        assert store.list_sync_block_changes() == facts
