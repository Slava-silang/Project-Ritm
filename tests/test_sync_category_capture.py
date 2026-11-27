"""Category changes and dependent task writes commit together on temporary profiles."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.tasks import Category, Task
from ritm.integrations.sync_codec import CalendarRecord, CategoryRecord
from ritm.integrations.sync_pages import Snapshot, SnapshotEntry
from ritm.storage.sqlite import TaskStore
from ritm.storage.sync_intents import SyncBinding

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


def bound(path, *, snapshot=False):
    store = TaskStore(path, clock=lambda: NOW)
    binding = SyncBinding(owner_id=uuid4(), device_id=uuid4(), server_id=uuid4())
    store.initialize_sync_binding(binding)
    store.configure_sync_task_capture(True, {})
    if snapshot:
        store.remember_sync_snapshot(
            Snapshot(
                server_id=binding.server_id,
                snapshot_id=uuid4(),
                set_revision=1,
                entries=(
                    SnapshotEntry(
                        revision=1,
                        delete_epoch=0,
                        entity=CalendarRecord(id=uuid4(), timezone="UTC"),
                    ),
                ),
            )
        )
    return store


def test_category_precedes_dependent_task_and_noop_does_not_duplicate(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        category = Category(title="Study")
        task = Task(title="Reading", category_id=category.id)
        TaskService(store).create_task(task, category)
        queue = store.list_sync_intents()
        assert [(q.intent.target_kind, q.intent.action) for q in queue] == [
            ("category", "create"),
            ("task", "create"),
        ]
        assert queue[1].intent.depends_on == queue[0].intent.id
        assert queue[0].intent.payload == CategoryRecord(category=category)
        store.save_category(category)
        store.update_task(task.id, lambda value: value)
        assert store.list_sync_intents() == queue


def test_category_rename_reopen_and_suppressed_origin(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    category = Category(title="Study")
    with bound(path) as store:
        store.save_category(category)
        renamed = replace(category, title="University")
        store.save_category(renamed)
        queue = store.list_sync_intents()
        assert [q.intent.action for q in queue] == ["create", "put"]
        assert queue[1].intent.depends_on == queue[0].intent.id
        with store.suppress_sync_capture("incoming"):
            store.save_category(replace(renamed, title="Incoming title"))
        assert store.list_sync_intents() == queue
    with TaskStore(path) as store:
        assert store.list_sync_intents() == queue
        assert store.list_categories()[0].title == "Incoming title"


def test_existing_unpublished_category_is_captured_before_regular_fact(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    category = Category(title="Existing catalog")
    with TaskStore(path) as store:
        store.save_category(category)
    with bound(path, snapshot=True) as store:
        task = Task(title="Daily reading", category_id=category.id)
        store.save_regular_task(
            task, None, interval=1, first=date(2026, 11, 27), today=date(2026, 11, 27)
        )
        queue = store.list_sync_intents()
        assert len(queue) == 1 and queue[0].intent.target_kind == "category"
        fact = store.list_sync_regular_changes()[0]
        assert fact.after_intent_id == queue[0].intent.id


@pytest.mark.parametrize("entrypoint", ["category", "task", "regular", "proposal"])
def test_category_capture_failure_rolls_back_all_owned_data(tmp_path, entrypoint):
    path = tmp_path / "tasks.sqlite3"
    with bound(path, snapshot=True) as store:
        category = Category(title="Must roll back")
        task = Task(title="Task must roll back", category_id=category.id)
        if entrypoint == "proposal":
            with store.suppress_sync_capture("bootstrap"):
                store.save_category(category)
        with store._db:
            store._db.execute(
                "CREATE TRIGGER reject_category_intent BEFORE INSERT ON sync_intents "
                "WHEN NEW.target_kind='category' BEGIN SELECT RAISE(ABORT,'synthetic'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            if entrypoint == "category":
                store.save_category(category)
            elif entrypoint == "task":
                store.create_task(task, category)
            elif entrypoint == "regular":
                store.save_regular_task(
                    task, category, interval=1, first=NOW.date(), today=NOW.date()
                )
            else:
                store.apply_proposal(uuid4(), "synthetic digest", None, task)
        assert store.get_task(task.id) is None
        assert not store.list_sync_intents() and not store.list_sync_regular_changes()
        assert len(store.list_categories()) == (entrypoint == "proposal")
        assert store._db.execute("SELECT count(*) FROM applied_proposals").fetchone()[0] == 0


def test_default_capture_off_preserves_local_only_behavior(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.save_category(Category(title="Local catalog"))
        assert not store.list_sync_intents()


def test_category_then_task_exchange_survives_reopen_with_valid_graph(tmp_path):
    from types import SimpleNamespace

    from test_sync_prepare_loop import confirmed

    path = tmp_path / "tasks.sqlite3"
    with bound(path, snapshot=True) as store:
        snapshot = store.read_sync_snapshot()
        context = SimpleNamespace(settings=snapshot.entries[0].entity, moment=NOW)
        category = Category(title="Study")
        task = Task(title="Reading", category_id=category.id)
        store.create_task(task, category)
    actions = []
    for _ in range(2):
        with TaskStore(path) as store:
            step = store.prepare_next_sync(NOW)
            assert step.status == "prepared"
            actions.append(step.request.operation.target_kind)
        with TaskStore(path) as store:
            assert store.prepare_next_sync(NOW).request.raw == step.request.raw
            receipt, snapshot = confirmed(snapshot, step.request, context)
            store.save_sync_receipt_and_snapshot(receipt, snapshot)
    assert actions == ["category", "task"]
    with TaskStore(path) as store:
        assert store.prepare_next_sync(NOW).status == "idle"


def test_failure_after_category_intent_rolls_back_category_and_queue(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        category = Category(title="Must roll back after intent")
        task = Task(title="Rejected root", category_id=category.id)
        with store._db:
            store._db.execute(
                "CREATE TRIGGER reject_root BEFORE INSERT ON nodes "
                "WHEN NEW.kind=0 BEGIN SELECT RAISE(ABORT,'synthetic'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            store.create_task(task, category)
        assert not store.list_categories() and not store.list_sync_intents()
        assert store.get_task(task.id) is None


def test_ai_material_validation_failure_keeps_existing_catalog_and_rolls_back_intents(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        category = Category(title="Existing category")
        with store.suppress_sync_capture("bootstrap"):
            store.save_category(category)
        task = Task(title="AI proposal with missing material", category_id=category.id)
        with pytest.raises(ValueError, match="Материал больше"):
            store.apply_proposal(uuid4(), "synthetic", None, task, material_ids=(uuid4(),))
        assert store.list_categories() == (category,)
        assert not store.list_sync_intents() and store.get_task(task.id) is None


def test_known_cloud_category_is_not_created_again(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        category = Category(title="Known catalog")
        with store.suppress_sync_capture("bootstrap"):
            store.save_category(category)
        remote = store.read_sync_snapshot()
        store.remember_sync_snapshot(
            replace(
                remote,
                snapshot_id=uuid4(),
                set_revision=2,
                entries=(
                    *remote.entries,
                    SnapshotEntry(
                        revision=2, delete_epoch=0, entity=CategoryRecord(category=category)
                    ),
                ),
            )
        )
        store.create_task(Task(title="Uses existing cloud category", category_id=category.id))
        assert [q.intent.target_kind for q in store.list_sync_intents()] == ["task"]
        store.save_category(replace(category, title="Known category renamed"))
        queue = store.list_sync_intents()
        assert queue[-1].intent.target_kind == "category" and queue[-1].intent.action == "put"
        assert queue[-1].intent.depends_on == queue[0].intent.id


def test_unknown_bootstrap_catalog_is_not_assumed_absent(tmp_path):
    with bound(tmp_path / "tasks.sqlite3") as store:
        category = Category(title="Existing before bootstrap")
        with store.suppress_sync_capture("bootstrap"):
            store.save_category(category)
        store.save_category(replace(category, title="Local rename"))
        queue = store.list_sync_intents()
        assert queue[0].intent.action == "put"
        assert store.prepare_next_sync(NOW).status == "needs_snapshot"
        assert store.list_sync_intents() == queue
