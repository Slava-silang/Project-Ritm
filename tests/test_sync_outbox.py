"""Frozen byte identity, durable receipts and preparation ordering on fake contexts."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest
from migration_support import drop_later_task_tables

from ritm.domain.tasks import Category, Task
from ritm.integrations.sync_codec import CategoryRecord, TaskRecord
from ritm.integrations.sync_operations import Receipt, operation_hash
from ritm.storage import backup
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore
from ritm.storage.sync_intents import SyncBinding, SyncIntent
from ritm.storage.sync_outbox import SyncBase

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
BINDING = SyncBinding(owner_id=UUID(int=801), device_id=UUID(int=802), server_id=UUID(int=803))


def intent(record=None, **changes):
    record = record or TaskRecord(task=Task(title="Frozen-only synthetic marker"))
    values = dict(
        id=uuid4(), target_kind="task", target_id=record.task.id, action="put", payload=record
    )
    values.update(changes)
    return SyncIntent(**values)


def base(item, revision=4, epoch=2):
    return SyncBase(
        server_id=BINDING.server_id,
        target_kind=item.target_kind,
        target_id=item.target_id,
        revision=revision,
        delete_epoch=epoch,
    )


def ack(frozen, status="applied", error=None, revision=None):
    op = frozen.operation
    return Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status=status,
        revision=revision or op.base_revision + 1,
        delete_epoch=op.delete_epoch
        + (op.action in ("delete", "restore", "prune", "stop_regular")),
        error=error,
    )


def open_bound(path):
    store = TaskStore(path)
    store.initialize_sync_binding(BINDING)
    return store


def test_freeze_bytes_and_receipt_persist_reopen_without_editing_tasks(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with open_bound(path) as store:
        local = Task(title="Unrelated local task")
        store.create_task(local)
        item = intent()
        store.enqueue_sync_intent(item)
        frozen = store.freeze_sync_intent(item.id, base(item))
        assert frozen.raw == store.freeze_sync_intent(item.id).raw
        assert store.next_sync_sendable() == frozen
        receipt = ack(frozen)
        saved = store.save_sync_receipt(receipt)
        assert saved.raw == frozen.raw and saved.receipt == receipt
        assert store.save_sync_receipt(receipt) == saved
        assert store.next_sync_sendable() is None
        assert store.get_task(local.id) == local
        assert "Frozen-only" not in repr(saved)
    with TaskStore(path) as store:
        assert store.get_frozen_sync_request(item.id) == saved
        assert store.list_frozen_sync_requests() == (saved,)
        assert store.freeze_sync_intent(item.id, base(item)) == saved
        assert store.get_task(local.id) == local


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", 5),
        ("delete_epoch", 3),
        ("server_id", uuid4()),
        ("target_id", uuid4()),
        ("revision", True),
    ],
)
def test_frozen_basis_never_rewritten(field, value, tmp_path):
    with open_bound(tmp_path / "tasks.sqlite3") as store:
        item = intent()
        store.enqueue_sync_intent(item)
        saved = store.freeze_sync_intent(item.id, base(item))
        with pytest.raises(ValueError):
            store.freeze_sync_intent(item.id, replace(base(item), **{field: value}))
        assert store.get_frozen_sync_request(item.id) == saved


def test_pending_rejected_and_applied_parent_control_dependency(tmp_path):
    with open_bound(tmp_path / "tasks.sqlite3") as store:
        parent = intent()
        child = intent(parent.payload, depends_on=parent.id, action="delete", payload=None)
        store.enqueue_sync_intent(parent)
        store.enqueue_sync_intent(child)
        with pytest.raises(ValueError):
            store.freeze_sync_intent(child.id, base(child))
        first = store.freeze_sync_intent(parent.id, base(parent))
        with pytest.raises(ValueError):
            store.freeze_sync_intent(child.id)
        assert store.get_frozen_sync_request(child.id) is None
        store.save_sync_receipt(ack(first))
        with pytest.raises(ValueError):
            store.freeze_sync_intent(child.id, base(child))
        second = store.freeze_sync_intent(child.id)
        assert second.operation.base_revision == 5 and second.operation.delete_epoch == 2
        assert second.operation.payload is None
        assert store.next_sync_sendable() == second
        rejection = ack(second, status="rejected", error="stale_epoch")
        store.save_sync_receipt(rejection)
        third = intent(parent.payload, depends_on=child.id)
        store.enqueue_sync_intent(third)
        with pytest.raises(ValueError):
            store.freeze_sync_intent(third.id)
        assert store.next_sync_sendable() is None
        assert len(store.list_sync_intents()) == 3


def test_cross_target_parent_needs_own_base_and_same_target_must_link_latest(tmp_path):
    with open_bound(tmp_path / "tasks.sqlite3") as store:
        category = CategoryRecord(category=Category(title="Synthetic category"))
        parent = SyncIntent(
            id=uuid4(),
            target_kind="category",
            target_id=category.category.id,
            action="create",
            payload=category,
        )
        store.enqueue_sync_intent(parent)
        first = store.freeze_sync_intent(parent.id)
        store.save_sync_receipt(ack(first))
        item = intent(depends_on=parent.id)
        store.enqueue_sync_intent(item)
        with pytest.raises(ValueError):
            store.freeze_sync_intent(item.id)
        saved = store.freeze_sync_intent(item.id, base(item))
        store.save_sync_receipt(ack(saved))
        unlinked = intent(item.payload)
        store.enqueue_sync_intent(unlinked)
        with pytest.raises(ValueError):
            store.freeze_sync_intent(unlinked.id, base(unlinked, 5, 2))


def test_transitive_cross_target_dependency_derives_latest_same_target_receipt(tmp_path):
    with open_bound(tmp_path / "tasks.sqlite3") as store:
        parent = intent()
        store.enqueue_sync_intent(parent)
        first = store.freeze_sync_intent(parent.id, base(parent))
        store.save_sync_receipt(ack(first))
        middle = intent(depends_on=parent.id)
        store.enqueue_sync_intent(middle)
        second = store.freeze_sync_intent(middle.id, base(middle))
        store.save_sync_receipt(ack(second, revision=8))
        last = intent(parent.payload, depends_on=middle.id)
        store.enqueue_sync_intent(last)
        final = store.freeze_sync_intent(last.id)
        assert final.operation.base_revision == 5  # Parent receipt, not unrelated revision 8.


@pytest.mark.parametrize(
    "field,value",
    [
        ("op_id", uuid4()),
        ("device_id", uuid4()),
        ("server_id", uuid4()),
        ("request_hash", "a" * 64),
        ("revision", 4),
        ("delete_epoch", 3),
    ],
)
def test_invalid_receipt_never_settles_queue(field, value, tmp_path):
    with open_bound(tmp_path / "tasks.sqlite3") as store:
        item = intent()
        store.enqueue_sync_intent(item)
        frozen = store.freeze_sync_intent(item.id, base(item))
        with pytest.raises((ValueError, KeyError)):
            store.save_sync_receipt(replace(ack(frozen), **{field: value}))
        assert store.get_frozen_sync_request(item.id).receipt is None


def test_contradictory_receipt_cannot_replace_original(tmp_path):
    with open_bound(tmp_path / "tasks.sqlite3") as store:
        item = intent()
        store.enqueue_sync_intent(item)
        frozen = store.freeze_sync_intent(item.id, base(item))
        old = store.save_sync_receipt(ack(frozen))
        with pytest.raises(ValueError):
            store.save_sync_receipt(ack(frozen, revision=8))
        assert store.get_frozen_sync_request(item.id) == old


def test_fifo_never_skips_unprepared_or_rejected_head(tmp_path):
    with open_bound(tmp_path / "tasks.sqlite3") as store:
        head, tail = intent(), intent()
        store.enqueue_sync_intent(head)
        store.enqueue_sync_intent(tail)
        later = store.freeze_sync_intent(tail.id, base(tail))
        assert store.next_sync_sendable() is None
        first = store.freeze_sync_intent(head.id, base(head))
        assert store.next_sync_sendable() == first
        store.save_sync_receipt(ack(first, status="rejected", error="stale_revision"))
        assert store.next_sync_sendable() is None
        assert store.get_frozen_sync_request(tail.id) == later


def test_two_connections_freeze_once_and_read_detects_storage_tampering(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    item = intent()
    with open_bound(path) as store:
        store.enqueue_sync_intent(item)

    def freeze():
        with TaskStore(path) as store:
            return store.freeze_sync_intent(item.id, base(item)).raw

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.map(lambda _: freeze(), range(2))
    assert a == b
    with TaskStore(path) as store:
        assert len(store.list_frozen_sync_requests()) == 1
        with store._db:
            store._db.execute("UPDATE sync_outbox SET request_hash=?", ("a" * 64,))
        with pytest.raises(ValueError):
            store.get_frozen_sync_request(item.id)
        assert store._db.execute("SELECT count(*) FROM sync_outbox").fetchone()[0] == 1


def test_v15_migration_failed_ddl_preserves_binding_intents_and_tasks(tmp_path, monkeypatch):
    import ritm.storage.sqlite as module

    path = tmp_path / "tasks.sqlite3"
    item = intent()
    with open_bound(path) as store:
        task = Task(title="Before migration")
        store.create_task(task)
        store.enqueue_sync_intent(item)
    with sqlite3.connect(path) as db:
        drop_later_task_tables(db, 15)
        db.execute("PRAGMA user_version=15")
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(module, "OUTBOX_SCHEMA", (module.OUTBOX_SCHEMA[0], "INVALID SQL"))
        with pytest.raises(sqlite3.Error):
            TaskStore(path)
    assert path.read_bytes() == before
    with TaskStore(path) as store:
        assert store.get_sync_binding() == BINDING and store.list_sync_intents()[0].intent == item
        assert store.get_task(task.id) == task and not store.list_frozen_sync_requests()
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_backups_strip_frozen_requests_receipts_and_do_not_change_live_queue(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    path = profile / "tasks.sqlite3"
    item = intent()
    with open_bound(path) as store:
        local = Task(title="Keep local task")
        store.create_task(local)
        store.enqueue_sync_intent(item)
        frozen = store.freeze_sync_intent(item.id, base(item))
        saved = store.save_sync_receipt(ack(frozen))
    before = path.read_bytes()
    archive = backup.create_backup(profile, tmp_path / "copies", NOW)
    assert path.read_bytes() == before
    with ZipFile(archive) as zipped:
        assert b"Frozen-only synthetic marker" not in zipped.read("tasks.sqlite3")
    with TaskStore(path) as store:
        assert store.get_frozen_sync_request(item.id) == saved
    preview = backup.inspect_backup(archive)
    backup.restore_backup(profile, archive, preview["token"])
    with TaskStore(path) as store:
        assert not store.list_frozen_sync_requests() and not store.list_sync_intents()
        assert store.get_sync_binding() is None and store.get_task(local.id) == local


def test_import_of_manually_built_archive_strips_old_outbox(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    path = profile / "tasks.sqlite3"
    item = intent()
    with open_bound(path) as store:
        store.create_task(Task(title="Keep local"))
        store.enqueue_sync_intent(item)
        frozen = store.freeze_sync_intent(item.id, base(item))
        store.save_sync_receipt(ack(frozen))
    raw = path.read_bytes()
    archive = tmp_path / "manual.zip"
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
        assert not store.list_frozen_sync_requests() and not store.list_sync_intents()
        assert store.get_sync_binding() is None and len(store.list_tasks()) == 1


def test_freeze_and_receipt_write_failures_roll_back_without_losing_intent(tmp_path, monkeypatch):
    with open_bound(tmp_path / "tasks.sqlite3") as store:
        item = intent()
        store.enqueue_sync_intent(item)
        original = store._frozen_sync_request

        def failing_reader():
            calls = []

            def read(identifier):
                calls.append(identifier)
                if len(calls) == 2:
                    raise OSError("synthetic post-write failure")
                return original(identifier)

            return read

        with monkeypatch.context() as patch:
            patch.setattr(store, "_frozen_sync_request", failing_reader())
            with pytest.raises(OSError):
                store.freeze_sync_intent(item.id, base(item))
        assert store.get_frozen_sync_request(item.id) is None
        assert store.list_sync_intents()[0].intent == item
        frozen = store.freeze_sync_intent(item.id, base(item))
        with monkeypatch.context() as patch:
            patch.setattr(store, "_frozen_sync_request", failing_reader())
            with pytest.raises(OSError):
                store.save_sync_receipt(ack(frozen))
        assert store.get_frozen_sync_request(item.id) == frozen
        assert store.save_sync_receipt(ack(frozen)).receipt.status == "applied"


def test_pending_noncanonical_local_rollover_not_silently_rewritten(tmp_path):
    from pathlib import Path

    from ritm.integrations.sync_codec import decode_entity

    record = decode_entity(
        (Path(__file__).parent / "fixtures" / "sync" / "v1" / "regular.json").read_bytes()
    )
    item = SyncIntent(
        id=uuid4(), target_kind="regular", target_id=record.id, action="rollover", payload=record
    )
    with open_bound(tmp_path / "tasks.sqlite3") as store:
        store.enqueue_sync_intent(item)
        with pytest.raises(ValueError):
            store.freeze_sync_intent(item.id, base(item))
        assert store.get_frozen_sync_request(item.id) is None
        assert store.list_sync_intents()[0].intent == item
