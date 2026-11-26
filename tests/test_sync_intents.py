"""Disposable profiles: intent durability, transaction rollback and backup stripping."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from migration_support import drop_later_task_tables

from ritm.domain.tasks import Task
from ritm.integrations.sync_codec import TaskRecord
from ritm.storage import backup
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore
from ritm.storage.sync_intents import SyncBinding, SyncIntent

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
BINDING = SyncBinding(owner_id=UUID(int=701), device_id=UUID(int=702), server_id=UUID(int=703))


def intent(record=None, **changes):
    record = record or TaskRecord(task=Task(title="Synthetic queue only text"))
    values = dict(
        id=uuid4(), target_kind="task", target_id=record.task.id, action="put", payload=record
    )
    values.update(changes)
    return SyncIntent(**values)


def test_fresh_profile_is_unbound_and_ordinary_edits_do_not_queue(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        assert store.get_sync_binding() is None and store.list_sync_intents() == ()
        task = Task(title="Local")
        store.create_task(task)
        store.update_task(task.id, lambda t: replace(t, title="Still local"))
        assert store.list_sync_intents() == ()
        with pytest.raises(ValueError):
            store.enqueue_sync_intent(intent())
        assert store.get_task(task.id).title == "Still local"


def test_binding_intent_order_dependency_and_retry_survive_reopen(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    first = intent()
    second = intent(depends_on=first.id)
    with TaskStore(path) as store:
        store.initialize_sync_binding(BINDING)
        store.initialize_sync_binding(BINDING)
        assert store.enqueue_sync_intent(first) == 1
        assert store.enqueue_sync_intent(second) == 2
        assert store.enqueue_sync_intent(first) == 1
    with TaskStore(path) as store:
        assert store.get_sync_binding() == BINDING
        rows = store.list_sync_intents()
        assert [r.sequence for r in rows] == [1, 2]
        assert rows[0].intent == first and rows[1].intent == second
        with pytest.raises(ValueError):
            store.enqueue_sync_intent(replace(first, action="restore"))
        assert store.list_sync_intents() == rows
        for different in (
            replace(BINDING, owner_id=uuid4()),
            replace(BINDING, server_id=uuid4()),
            replace(BINDING, device_id=uuid4()),
        ):
            with pytest.raises(ValueError):
                store.initialize_sync_binding(different)
        assert store.get_sync_binding() == BINDING


def test_invalid_dependencies_and_delete_payload_rejected_without_partial_rows(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.initialize_sync_binding(BINDING)
        sample = intent()
        for invalid in (
            replace(sample, depends_on=sample.id),
            replace(sample, depends_on=uuid4()),
            replace(sample, action="delete"),
            replace(sample, target_id=uuid4()),
            replace(sample, target_kind="token"),
        ):
            with pytest.raises(ValueError):
                store.enqueue_sync_intent(invalid)
            assert not store.list_sync_intents()
        deletion = replace(sample, action="delete", payload=None)
        store.enqueue_sync_intent(deletion)
        raw = store._db.execute("SELECT desired_json FROM sync_intents").fetchone()[0]
        assert raw is None
        assert not hasattr(store.list_sync_intents()[0].intent, "before")


def test_atomic_task_change_completion_and_intent_rollback(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path, clock=lambda: NOW) as store:
        store.initialize_sync_binding(BINDING)
        task = Task(title="Original")
        store.create_task(task)
        store.begin_garden_source(NOW)
        events = store._db.execute("SELECT count(*) FROM garden_events").fetchone()[0]

        def invalid(record):
            return intent(record, depends_on=uuid4())

        with pytest.raises(ValueError):
            store.update_task_and_enqueue(task.id, lambda t: replace(t, completed=True), invalid)
        assert store.get_task(task.id) == task
        assert task.id not in store.list_completion_dates()
        assert not store.list_sync_intents()
        assert store._db.execute("SELECT count(*) FROM garden_events").fetchone()[0] == events
        fixed_id = uuid4()
        store.update_task_and_enqueue(
            task.id, lambda t: replace(t, completed=True), lambda r: intent(r, id=fixed_id)
        )
        saved = store.list_sync_intents()[0].intent
        assert saved.payload.completed_at == NOW and saved.payload.task.completed
        with pytest.raises(ValueError):
            store.update_task_and_enqueue(
                task.id,
                lambda t: replace(t, title="Must rollback"),
                lambda r: intent(r, id=fixed_id),
            )
        assert store.get_task(task.id).title == "Original"
    with TaskStore(path) as store:
        assert store.list_sync_intents()[0].intent == saved
        assert store.get_task(task.id).completed


def test_atomic_snapshot_mismatch_and_write_failure_roll_back(tmp_path, monkeypatch):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.initialize_sync_binding(BINDING)
        task = Task(title="Before")
        store.create_task(task)
        with pytest.raises(ValueError):
            store.update_task_and_enqueue(
                task.id, lambda t: replace(t, title="After"), lambda r: intent()
            )
        assert store.get_task(task.id) == task

        def fail(_intent):
            raise sqlite3.OperationalError("synthetic write failure")

        monkeypatch.setattr(store, "_enqueue_sync_intent", fail)
        with pytest.raises(sqlite3.OperationalError):
            store.update_task_and_enqueue(task.id, lambda t: replace(t, title="After"), intent)
        assert store.get_task(task.id) == task and not store.list_sync_intents()


def test_v14_migration_and_failed_ddl_are_atomic(tmp_path, monkeypatch):
    import ritm.storage.sqlite as module

    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        task = Task(title="Before migration")
        store.create_task(task)
        source = store._db.execute("SELECT identity FROM garden_source").fetchone()[0]
    with sqlite3.connect(path) as db:
        drop_later_task_tables(db, 14)
        db.execute("PRAGMA user_version=14")
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(module, "SYNC_SCHEMA", (module.SYNC_SCHEMA[0], "INVALID SQL"))
        with pytest.raises(sqlite3.Error):
            TaskStore(path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='sync_binding'").fetchone()
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task and not store.list_sync_intents()
        assert store._db.execute("SELECT identity FROM garden_source").fetchone()[0] == source
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_backup_strips_only_copy_queue_and_binding_including_freed_pages(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    path = profile / "tasks.sqlite3"
    marker = "Queue-only-unique-payload-123456789"
    pending = intent(TaskRecord(task=Task(title=marker)))
    with TaskStore(path) as store:
        local = Task(title="Keep this local task")
        store.create_task(local)
        store.initialize_sync_binding(BINDING)
        store.enqueue_sync_intent(pending)
        store.enqueue_sync_intent(
            replace(pending, id=uuid4(), depends_on=pending.id, action="restore")
        )
    original = path.read_bytes()
    archive = backup.create_backup(profile, tmp_path / "copies", NOW)
    assert path.read_bytes() == original
    with ZipFile(archive) as zipped:
        copied = zipped.read("tasks.sqlite3")
    assert marker.encode() not in copied
    assert str(BINDING.owner_id).encode() not in copied
    staged = tmp_path / "read.sqlite3"
    staged.write_bytes(copied)
    with TaskStore(staged) as store:
        assert store.get_task(local.id) == local
        assert store.get_sync_binding() is None and not store.list_sync_intents()
    with TaskStore(path) as store:
        assert (
            store.get_sync_binding() == BINDING and store.list_sync_intents()[0].intent == pending
        )
    preview = backup.inspect_backup(archive)
    backup.restore_backup(profile, archive, preview["token"])
    with TaskStore(path) as store:
        assert store.get_task(local.id) == local
        assert store.get_sync_binding() is None and not store.list_sync_intents()


@pytest.mark.parametrize("version", [14, 15])
def test_manually_constructed_archive_cannot_restore_old_queue(tmp_path, version):
    import hashlib
    import json

    profile = tmp_path / "profile"
    profile.mkdir()
    path = profile / "tasks.sqlite3"
    with TaskStore(path) as store:
        store.create_task(Task(title="Keep task"))
        store.initialize_sync_binding(BINDING)
        store.enqueue_sync_intent(intent())
    if version == 14:
        with sqlite3.connect(path) as db:
            drop_later_task_tables(db, 14)
            db.execute("PRAGMA user_version=14")
    raw = path.read_bytes()
    archive = tmp_path / "synthetic.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("tasks.sqlite3", raw)
        zipped.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "ritm-profile-1",
                    "kind": "manual",
                    "created": NOW.isoformat(),
                    "files": {"tasks.sqlite3": hashlib.sha256(raw).hexdigest()},
                }
            ),
        )
    preview = backup.inspect_backup(archive)
    backup.restore_backup(profile, archive, preview["token"])
    with TaskStore(path) as store:
        assert store.get_sync_binding() is None and not store.list_sync_intents()
        assert len(store.list_tasks()) == 1


def test_queue_read_detects_corrupt_dependency_without_erasing_it(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.initialize_sync_binding(BINDING)
        first = intent()
        second = intent(depends_on=first.id)
        store.enqueue_sync_intent(first)
        store.enqueue_sync_intent(second)
        with store._db:
            store._db.execute(
                "UPDATE sync_intents SET depends_on=? WHERE id=?", (str(second.id), str(first.id))
            )
        with pytest.raises(ValueError):
            store.list_sync_intents()
        assert store._db.execute("SELECT count(*) FROM sync_intents").fetchone()[0] == 2


def test_two_connections_enqueue_same_intent_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "tasks.sqlite3"
    sample = intent()
    with TaskStore(path) as store:
        store.initialize_sync_binding(BINDING)

    def write():
        with TaskStore(path) as store:
            return store.enqueue_sync_intent(sample)

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: write(), range(2))) == [1, 1]
    with TaskStore(path) as store:
        assert len(store.list_sync_intents()) == 1


def test_queue_read_in_fresh_process(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        store.initialize_sync_binding(BINDING)
        store.enqueue_sync_intent(intent())
    script = """import sys
from pathlib import Path
from ritm.storage.sqlite import TaskStore
with TaskStore(Path(sys.argv[1])) as store:
    assert store.get_sync_binding() is not None
    assert len(store.list_sync_intents()) == 1
    assert store.list_sync_intents()[0].intent.action == 'put'
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
