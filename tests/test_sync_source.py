"""Source adapter reads a consistent local snapshot without queueing or sending."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Category, Task
from ritm.integrations.sync_codec import (
    CalendarRecord,
    OccurrenceRecord,
    RegularRecord,
    TaskRecord,
    encode_entity,
)
from ritm.integrations.sync_projection import CompletionSelectionRequired
from ritm.storage.profile_guard import PENDING_RESTORE
from ritm.storage.sqlite import TaskStore
from ritm.storage.sync_intents import SyncBinding, SyncIntent

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
DAY = NOW.date()
CALENDAR = CalendarRecord(id=uuid4(), timezone="Europe/Moscow")


def regular(store, title="Read", first=DAY):
    task = Task(title=title)
    store.save_regular_task(task, None, interval=3, first=first, today=DAY)
    return task


def test_snapshot_maps_current_completed_history_skips_and_future_templates(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        old = regular(store)
        TaskService(store).set_completed(old.id, old.id, True)
        store.refresh_recurrences(DAY + timedelta(days=3))
        skipped = store.list_tasks()[-1]
        store.refresh_recurrences(DAY + timedelta(days=9))
        current = store.list_tasks()[-1]
        future = regular(store, "Future", first=DAY + timedelta(days=20))
        calls = []
        original = store.get_task

        def tracked(identifier):
            calls.append(identifier)
            return original(identifier)

        store.get_task = tracked
        data = store.read_sync_source(CALENDAR)
        assert skipped.id not in calls and skipped.id in data.skipped_ids
        assert {r.current.task.id for r in data.regulars} == {current.id, future.id}
        assert len(data.history) == 1 and data.history[0].current.task.id == old.id
        assert data.history[0].day == DAY and data.history[0].current.completed_at == NOW
        preview = store.prepare_sync_preview(NOW, CALENDAR)
        assert sum(isinstance(e, RegularRecord) for e in preview.entities) == 2
        assert sum(isinstance(e, OccurrenceRecord) for e in preview.entities) == 1
        assert not any(isinstance(e, TaskRecord) for e in preview.entities)
        assert original(skipped.id) is not None


def test_stopped_completed_current_and_restored_deleted_current_are_ordinary(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        stopped = regular(store, "Stop complete")
        completed = TaskService(store).set_completed(stopped.id, stopped.id, True)
        store.save_regular_task(completed, None, interval=None, first=None, today=DAY)
        removed = regular(store, "Delete complete")
        TaskService(store).set_completed(removed.id, removed.id, True)
        receipt = TaskService(store).delete_node(removed.id, removed.id, NOW)
        from uuid import UUID

        TaskService(store).restore(UUID(receipt))
        preview = store.prepare_sync_preview(NOW, CALENDAR)
        roots = {e.task.id for e in preview.entities if isinstance(e, TaskRecord)}
        assert roots == {stopped.id, removed.id}
        assert not any(isinstance(e, (RegularRecord, OccurrenceRecord)) for e in preview.entities)
        assert store.list_completion_dates()[stopped.id] == NOW


def test_reopened_historical_root_is_active_ordinary_and_date_not_invented(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        old = regular(store)
        TaskService(store).set_completed(old.id, old.id, True)
        store.refresh_recurrences(DAY + timedelta(days=3))
        TaskService(store).set_completed(old.id, old.id, False)
        data = store.read_sync_source(CALENDAR)
        assert not data.history
        preview = store.prepare_sync_preview(NOW, CALENDAR)
        plain = next(e for e in preview.entities if isinstance(e, TaskRecord))
        assert plain.task.id == old.id and not plain.task.completed and plain.completed_at is None


def test_no_trash_files_focus_garden_credentials_or_queue_read_or_writes(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path, clock=lambda: NOW) as store:
        keep = Task(title="Keep")
        deleted = Task(title="Private deleted title", description="Private deleted description")
        store.create_task(keep)
        store.create_task(deleted)
        store.create_work_block(
            WorkBlock(task_id=deleted.id, starts_at=NOW, ends_at=NOW + timedelta(hours=1))
        )
        TaskService(store).delete_node(deleted.id, deleted.id, NOW)
        binding = SyncBinding(owner_id=uuid4(), device_id=uuid4(), server_id=uuid4())
        store.initialize_sync_binding(binding)
        pending = SyncIntent(
            id=uuid4(),
            target_kind="task",
            target_id=keep.id,
            action="put",
            payload=TaskRecord(task=keep),
        )
        store.enqueue_sync_intent(pending)
        before = path.read_bytes()
        statements = []
        store._db.set_trace_callback(statements.append)
        data = store.read_sync_source(CALENDAR)
        preview = store.prepare_sync_preview(NOW, CALENDAR)
        store._db.set_trace_callback(None)
        assert path.read_bytes() == before
        sql = " ".join(statements).lower()
        for forbidden in (
            "trash",
            "before_json",
            "after_json",
            "attachments",
            "materials",
            "focus_sessions",
            "garden_events",
            "sync_intents",
            "sync_binding",
            "sync_outbox",
            "sync_capture",
            "sync_task_presence",
        ):
            assert forbidden not in sql
        assert not any(
            s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "PRAGMA"))
            for s in statements
        )
        assert deleted.id in data.deleted_ids and not data.blocks
        raw = b"".join(encode_entity(e) for e in preview.entities)
        assert b"Private deleted" not in raw
        assert (
            store.get_sync_binding() == binding and store.list_sync_intents()[0].intent == pending
        )


def test_purge_leaves_no_tree_or_blocks_and_no_invented_root_ids(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        task = Task(title="Deleted")
        store.create_task(task)
        store.create_work_block(
            WorkBlock(task_id=task.id, starts_at=NOW, ends_at=NOW + timedelta(hours=1))
        )
        receipt = TaskService(store).delete_node(task.id, task.id, NOW)
        from uuid import UUID

        store.purge_trash({UUID(receipt)})
        assert not store.list_trash()
        data = store.read_sync_source()
        assert not data.tasks and not data.blocks and task.id not in data.deleted_ids


def test_wal_writer_between_tree_reads_cannot_mix_dates_or_categories(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path, clock=lambda: NOW) as store:
        store._db.execute("PRAGMA journal_mode=WAL")
        category = Category(title="Before category")
        task = Task(title="Before task", category_id=category.id)
        store.create_task(task, category)
        original = store.get_task
        changed = []

        def writer():
            with TaskStore(path, clock=lambda: NOW) as other:
                other.update_task(task.id, lambda t: replace(t, title="After task", completed=True))
                other.save_category(replace(category, title="After category"))

        def tracked(identifier):
            if not changed:
                changed.append(True)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(writer).result(timeout=5)
            return original(identifier)

        store.get_task = tracked
        data = store.read_sync_source()
        assert data.tasks[0].task.title == "Before task" and not data.tasks[0].task.completed
        assert data.tasks[0].completed_at is None
        assert data.categories[0].category.title == "Before category"
        latest = store.read_sync_source()
        assert latest.tasks[0].task.title == "After task" and latest.tasks[0].completed_at == NOW
        assert latest.categories[0].category.title == "After category"


def test_unknown_dates_selection_propagates_without_auto_queue_or_binding(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        for i in range(51):
            store.create_task(Task(title=f"History {i}", completed=True))
        with store._db:
            store._db.execute("DELETE FROM task_completion_dates")
        with pytest.raises(CompletionSelectionRequired) as caught:
            store.prepare_sync_preview(NOW)
        ids = frozenset(list(caught.value.unknown_ids)[:50])
        preview = store.prepare_sync_preview(NOW, selected_completed=ids)
        assert len(preview.entities) == 50 and all(e.completed_at is None for e in preview.entities)
        assert store.get_sync_binding() is None and not store.list_sync_intents()
        assert len(store.list_tasks()) == 51


def test_bad_rule_read_rolls_back_without_repairing_or_echoing_source(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        task = regular(store)
        with store._db:
            store._db.execute(
                "UPDATE recurrence_rules SET current_day=?", ("synthetic-private-invalid-day",)
            )
        with pytest.raises(ValueError) as caught:
            store.read_sync_source(CALENDAR)
        assert "synthetic-private" not in str(caught.value)
        assert not store._db.in_transaction
        assert store.get_task(task.id) == task
        assert (
            store._db.execute("SELECT current_day FROM recurrence_rules").fetchone()[0]
            == "synthetic-private-invalid-day"
        )


def test_nested_transaction_and_pending_restore_preserve_guard(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store._db.execute("BEGIN")
        with pytest.raises(ValueError):
            store.read_sync_source()
        assert store._db.in_transaction
        store._db.rollback()
        marker = tmp_path / PENDING_RESTORE
        marker.write_text("{}")
        with pytest.raises(ValueError, match="Восстановление профиля не завершено"):
            store.read_sync_source()
        marker.unlink()
        assert store.read_sync_source().tasks == ()


def test_legacy_orphan_relation_is_read_as_stored_not_repaired_or_guessed(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        task = regular(store)
        TaskService(store).set_completed(task.id, task.id, True)
        with store._db:
            store._db.execute("DELETE FROM recurrence_rules")
        before = tuple(store._db.execute("SELECT * FROM recurrence_occurrences").fetchall())
        data = store.read_sync_source(CALENDAR)
        assert not data.regulars and len(data.history) == 1
        assert data.history[0].current.task.id == task.id
        assert tuple(store._db.execute("SELECT * FROM recurrence_occurrences").fetchall()) == before


def test_archive_and_restore_old_history_keep_slot_but_not_current_template(tmp_path):
    from uuid import UUID

    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        task = regular(store)
        TaskService(store).set_completed(task.id, task.id, True)
        store.refresh_recurrences(DAY + timedelta(days=3))
        receipt = TaskService(store).delete_node(task.id, task.id, NOW)
        data = store.read_sync_source(CALENDAR)
        assert not data.history and len(data.regulars) == 1
        assert task.id in data.deleted_ids
        TaskService(store).restore(UUID(receipt))
        data = store.read_sync_source(CALENDAR)
        assert len(data.history) == 1 and len(data.regulars) == 1
        assert data.history[0].day == DAY and data.history[0].current.completed_at == NOW


def test_deadline_added_to_old_slot_is_preserved_as_ordinary_task(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        task = regular(store)
        TaskService(store).set_completed(task.id, task.id, True)
        store.refresh_recurrences(DAY + timedelta(days=3))
        deadline = NOW + timedelta(days=7)
        store.update_task(task.id, lambda t: replace(t, deadline=deadline))
        data = store.read_sync_source(CALENDAR)
        assert not data.history and len(data.regulars) == 1
        preview = store.prepare_sync_preview(NOW, CALENDAR)
        plain = next(e for e in preview.entities if isinstance(e, TaskRecord))
        assert plain.task.id == task.id and plain.task.deadline == deadline
        assert store.get_task(task.id).deadline == deadline
