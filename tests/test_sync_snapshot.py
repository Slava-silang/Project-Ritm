"""Remote knowledge is durable and atomic with receipts, never local replacement."""

import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest
from migration_support import drop_later_task_tables
from test_sync_prepared import ack
from test_sync_regular_plan import NOW, plan, seed

from ritm.integrations.sync_pages import Snapshot, SnapshotEntry
from ritm.storage import backup
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore


def snapshot(context, entity=None, revision=5):
    return Snapshot(
        server_id=context.server_id,
        snapshot_id=uuid4(),
        set_revision=revision,
        entries=(
            SnapshotEntry(revision=1, delete_epoch=0, entity=context.settings),
            replace(
                context.bases[0].entry,
                revision=revision,
                entity=entity or context.bases[0].entry.entity,
            ),
        ),
    )


def test_receipt_snapshot_reopen_and_next_command_use_actual_cached_base(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    proposal = plan(changes, context).proposals[0]
    with TaskStore(path) as store:
        store.remember_sync_snapshot(snapshot(context))
        frozen = store.enqueue_sync_regular(context, proposal.id)
        local = store.list_sync_regular_changes()
        remote = snapshot(context, frozen.operation.payload, revision=ack(frozen).revision)
        store.save_sync_receipt_and_snapshot(ack(frozen), remote)
        assert store.list_sync_regular_changes() == local
        assert store.next_sync_sendable() is None
    with TaskStore(path) as store:
        cached = store.read_sync_snapshot()
        assert cached == remote
        assert store.get_frozen_sync_request(frozen.intent_id).receipt == ack(frozen)
        context = replace(context, bases=(replace(context.bases[0], entry=cached.entries[1]),))
        next_command = store.enqueue_sync_regular(context, proposal.id)
        assert next_command.operation.action == "rollover"
        assert next_command.operation.base_revision == ack(frozen).revision
        assert store.list_sync_regular_changes() == changes


@pytest.mark.parametrize("failure", ["write", "old_watermark", "wrong_server", "contradiction"])
def test_snapshot_failure_rolls_back_receipt_and_mappings(tmp_path, failure):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    with TaskStore(path) as store:
        before = snapshot(context)
        store.remember_sync_snapshot(before)
        frozen = store.enqueue_sync_regular(context, plan(changes, context).proposals[0].id)
        after = snapshot(context, frozen.operation.payload, revision=ack(frozen).revision)
        if failure == "write":
            with store._db:
                store._db.execute(
                    "CREATE TRIGGER fail_snapshot BEFORE UPDATE ON sync_remote_snapshot "
                    "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
                )
        elif failure == "old_watermark":
            after = before
        elif failure == "wrong_server":
            after = replace(after, server_id=uuid4())
        else:
            # A pre-existing later snapshot cannot be overwritten at the same watermark.
            before = snapshot(context, revision=ack(frozen).revision)
            store.remember_sync_snapshot(before)
        with pytest.raises((ValueError, sqlite3.IntegrityError)):
            store.save_sync_receipt_and_snapshot(ack(frozen), after)
        assert store.read_sync_snapshot() == before
        assert store.get_frozen_sync_request(frozen.intent_id).receipt is None
        assert not store.read_sync_prepared_state().identities


def test_cache_rejects_incomplete_graph_or_duplicate_entries_and_older_snapshot(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path)
    good = snapshot(context)
    with TaskStore(path) as store:
        store.remember_sync_snapshot(good)
        for invalid in (
            replace(good, entries=good.entries + good.entries),
            replace(good, entries=(good.entries[1],)),  # current regular requires timezone
            replace(good, set_revision=4),
            replace(good, server_id=uuid4()),
        ):
            with pytest.raises(ValueError):
                store.remember_sync_snapshot(invalid)
            assert store.read_sync_snapshot() == good
        store.remember_sync_snapshot(
            replace(good, snapshot_id=uuid4(), entries=tuple(reversed(good.entries)))
        )
        assert store.read_sync_snapshot().set_revision == good.set_revision


def test_v20_migration_preserves_existing_queue(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_regular(context, plan(changes, context).proposals[0].id)
    with sqlite3.connect(path) as db:
        drop_later_task_tables(db, 20)
        db.execute("PRAGMA user_version=20")
    with TaskStore(path) as store:
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert store.read_sync_snapshot() is None
        assert store.get_frozen_sync_request(frozen.intent_id).raw == frozen.raw
        assert store.list_sync_regular_changes() == changes


def test_backup_excludes_remote_cache_without_clearing_live_profile(tmp_path):
    profile = tmp_path / "profile"
    path = profile / "tasks.sqlite3"
    _, context = seed(path)
    remote = snapshot(context)
    with TaskStore(path) as store:
        store.remember_sync_snapshot(remote)
    archive = backup.create_backup(profile, tmp_path / "copies", NOW)
    with TaskStore(path) as store:
        assert store.read_sync_snapshot() == remote
    preview = backup.inspect_backup(archive)
    backup.restore_backup(profile, archive, preview["token"])
    with TaskStore(path) as store:
        assert store.read_sync_snapshot() is None
        assert store.get_sync_binding() is None
        assert store.get_task(context.bases[0].entry.entity.current.task.id) is not None


def test_newer_snapshot_can_cover_an_operation_already_superseded(tmp_path):
    from ritm.integrations.sync_codec import RemovalRecord

    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_regular(context, plan(changes, context).proposals[0].id)
        mark = RemovalRecord(
            entity_kind="regular",
            entity_id=context.bases[0].id,
            revision=10,
            delete_epoch=3,
            reason="user_deleted",
        )
        newer = replace(
            snapshot(context),
            set_revision=10,
            entries=(
                snapshot(context).entries[0],
                SnapshotEntry(revision=10, delete_epoch=3, entity=mark),
            ),
        )
        store.save_sync_receipt_and_snapshot(ack(frozen), newer)
        assert store.read_sync_snapshot() == newer
        assert store.list_sync_regular_changes() == changes
        assert store.get_task(changes[-1].after.current.task.id) is not None


def test_many_pages_roundtrip_and_corrupt_cache_is_rejected(tmp_path):
    from ritm.domain.tasks import Category
    from ritm.integrations.sync_codec import CategoryRecord

    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path)
    remote = replace(
        snapshot(context),
        entries=tuple(
            SnapshotEntry(
                revision=1,
                delete_epoch=0,
                entity=CategoryRecord(category=Category(title=f"Category {i}")),
            )
            for i in range(105)
        ),
    )
    with TaskStore(path) as store:
        store.remember_sync_snapshot(remote)
    with TaskStore(path) as store:
        assert store.read_sync_snapshot() == remote
        with store._db:
            store._db.execute("UPDATE sync_remote_snapshot SET pages=?", (b"[]",))
        with pytest.raises(ValueError):
            store.read_sync_snapshot()


@pytest.mark.parametrize("mode", ["same_revision", "older_entity", "lower_epoch"])
def test_new_watermark_does_not_hide_rewinding_an_entity(tmp_path, mode):
    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path)
    before = snapshot(context)
    old = before.entries[1]
    if mode == "same_revision":
        changed = replace(
            old,
            entity=replace(
                old.entity,
                current=replace(
                    old.entity.current,
                    task=replace(old.entity.current.task, title="Conflicting body"),
                ),
            ),
        )
    elif mode == "older_entity":
        changed = replace(old, revision=old.revision - 1)
    else:
        changed = replace(old, revision=old.revision + 1, delete_epoch=old.delete_epoch - 1)
    with TaskStore(path) as store:
        store.remember_sync_snapshot(before)
        with pytest.raises(ValueError):
            store.remember_sync_snapshot(
                replace(
                    before,
                    snapshot_id=uuid4(),
                    set_revision=20,
                    entries=(before.entries[0], changed),
                )
            )
        assert store.read_sync_snapshot() == before
