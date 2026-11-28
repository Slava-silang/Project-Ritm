"""Real coherent incoming transactions; no accounts, network or personal profiles."""

import sqlite3
from dataclasses import replace
from uuid import uuid4
from zipfile import ZipFile

import pytest
from test_sync_block_capture import event
from test_sync_category_capture import NOW, bound
from test_sync_prepare_loop import confirmed

from ritm.domain.tasks import Category, Subtask, Task
from ritm.integrations.sync_codec import BlockRecord, CategoryRecord, RemovalRecord, TaskRecord
from ritm.integrations.sync_pages import SnapshotEntry
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore


def incoming(store, *records, revision=2, epoch=0):
    base = store.read_sync_snapshot()
    return replace(
        base,
        snapshot_id=uuid4(),
        set_revision=revision,
        entries=(
            base.entries[0],
            *(
                SnapshotEntry(
                    entity=r,
                    revision=r.revision if type(r) is RemovalRecord else revision,
                    delete_epoch=r.delete_epoch if type(r) is RemovalRecord else epoch,
                )
                for r in records
            ),
        ),
    )


def test_bootstrap_import_is_explicit_atomic_and_no_echo(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    category = CategoryRecord(category=Category(title="Subject"))
    task = TaskRecord(task=Task(title="Remote", category_id=category.category.id))
    block = BlockRecord(block=event(task.task.id))
    with bound(path, snapshot=True) as store:
        candidate = incoming(store, category, task, block)
        assert store.apply_sync_incoming(candidate).status == "bootstrap_required"
        assert store.get_task(task.task.id) is None
        assert store.read_sync_applied_snapshot() is None
        preview = store.preview_sync_incoming(candidate)
        assert not preview.needs_resolution
        result = store.apply_sync_incoming(candidate, bootstrap=True)
        assert result.status == "applied" and result.changed == 3
        assert not store.list_sync_intents() and not store.list_sync_block_changes()
        assert store.get_work_block(block.block.id) == block.block
    with TaskStore(path) as store:
        assert store.read_sync_applied_snapshot() == candidate
        assert store.apply_sync_incoming(candidate).status == "unchanged"
        assert store.get_task(task.task.id) == task.task


def test_applied_baseline_is_not_outgoing_cache(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        old = TaskRecord(task=Task(title="Old"))
        first = incoming(store, old)
        store.apply_sync_incoming(first, bootstrap=True)
        updated = replace(old, task=replace(old.task, title="Incoming"))
        candidate = incoming(store, updated, revision=3)
        store.remember_sync_snapshot(candidate)
        assert store.get_task(old.task.id) == old.task
        assert store.read_sync_applied_snapshot() == first
        assert store.apply_sync_incoming(candidate).status == "applied"
        assert store.get_task(old.task.id) == updated.task
        assert store.read_sync_applied_snapshot() == candidate


def test_pending_local_edit_blocks_entire_batch_and_preserves_queue(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        old = TaskRecord(task=Task(title="Old"))
        first = incoming(store, old)
        store.apply_sync_incoming(first, bootstrap=True)
        edited = store.update_task(old.task.id, lambda t: replace(t, title="Local"))
        queue = store.list_sync_intents()
        candidate = incoming(
            store,
            replace(old, task=replace(old.task, title="Cloud")),
            BlockRecord(block=event()),
            revision=3,
        )
        result = store.apply_sync_incoming(candidate)
        assert result.status == "blocked" and "local_pending" in result.reasons
        assert store.get_task(old.task.id) == edited
        assert store.list_sync_intents() == queue
        assert not store.list_work_blocks()
        assert store.read_sync_applied_snapshot() == first


def test_preview_is_recomputed_after_user_edits(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        record = TaskRecord(task=Task(title="Old"))
        first = incoming(store, record)
        store.apply_sync_incoming(first, bootstrap=True)
        candidate = incoming(
            store, replace(record, task=replace(record.task, title="Remote")), revision=3
        )
        assert not store.preview_sync_incoming(candidate).needs_resolution
        store.update_task(record.task.id, lambda task: replace(task, title="New local"))
        assert store.apply_sync_incoming(candidate).status == "blocked"
        assert store.get_task(record.task.id).title == "New local"


@pytest.mark.parametrize("failure", ["task", "block", "baseline"])
def test_sql_failure_rolls_back_data_cache_baseline_and_capture(tmp_path, failure):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        old_cache = store.read_sync_snapshot()
        category = CategoryRecord(category=Category(title="Subject"))
        record = TaskRecord(task=Task(title="Remote", category_id=category.category.id))
        block = BlockRecord(block=event(record.task.id))
        candidate = incoming(store, category, record, block)
        table = {"task": "nodes", "block": "work_blocks", "baseline": "sync_applied_snapshot"}[
            failure
        ]
        with store._db:
            store._db.execute(
                f"CREATE TRIGGER fail BEFORE INSERT ON {table} "
                "BEGIN SELECT RAISE(ABORT,'incoming failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="incoming failure"):
            store.apply_sync_incoming(candidate, bootstrap=True)
        assert store.get_task(record.task.id) is None
        assert not store.list_categories() and not store.list_work_blocks()
        assert store.read_sync_applied_snapshot() is None
        assert store.read_sync_snapshot() == old_cache
        assert not store.list_sync_intents() and not store.list_sync_block_changes()


def test_retention_preserves_local_history_and_prevents_outgoing_resurrection(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        record = TaskRecord(task=Task(title="History"))
        store.apply_sync_incoming(incoming(store, record), bootstrap=True)
        mark = RemovalRecord(
            entity_kind="task",
            entity_id=record.task.id,
            revision=3,
            delete_epoch=0,
            reason="retention",
        )
        candidate = incoming(store, mark, revision=3)
        assert store.apply_sync_incoming(candidate).status == "applied"
        assert store.get_task(record.task.id) == record.task
        assert store.get_sync_task_presence(record.task.id) == "retained"
        store.update_task(record.task.id, lambda t: replace(t, title="Local history edit"))
        assert not store.list_sync_intents()


@pytest.mark.parametrize("mode", ["prune", "delete", "completion"])
def test_unfinished_destructive_and_reward_paths_are_not_partially_applied(tmp_path, mode):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        record = TaskRecord(task=Task(title="Tree", subtasks=(Subtask(title="Keep"),)))
        first = incoming(store, record)
        store.apply_sync_incoming(first, bootstrap=True)
        if mode == "delete":
            changed = RemovalRecord(
                entity_kind="task",
                entity_id=record.task.id,
                revision=3,
                delete_epoch=1,
                reason="user_deleted",
            )
        elif mode == "prune":
            changed = replace(record, task=replace(record.task, subtasks=()))
        else:
            from ritm.domain.completion import set_completed

            changed = replace(
                record, task=set_completed(record.task, record.task.id, True), completed_at=NOW
            )
        result = store.apply_sync_incoming(incoming(store, changed, revision=3))
        assert result.status == "blocked"
        assert store.get_task(record.task.id) == record.task
        assert store.read_sync_applied_snapshot() == first
        assert not store.list_trash()


def test_bootstrap_completed_history_does_not_award_or_break_garden_mirror(tmp_path):
    from ritm.application.garden_runtime import sync_garden

    path = tmp_path / "tasks.sqlite3"
    with bound(path, snapshot=True) as store:
        sync_garden(store, tmp_path, NOW, zone="UTC", enabled=True)
        record = TaskRecord(task=Task(title="Old done", completed=True), completed_at=NOW)
        first = incoming(store, record)
        assert store.apply_sync_incoming(first, bootstrap=True).status == "applied"
        view = sync_garden(store, tmp_path, NOW, zone="UTC")
        assert view["roots"] == 0 and not view["firstTask"]
        assert view["balance"] == "100.00"
        assert store.list_completion_dates()[record.task.id] == NOW
        assert store.apply_sync_incoming(first).status == "unchanged"
        assert sync_garden(store, tmp_path, NOW, zone="UTC")["balance"] == "100.00"


def test_acknowledged_own_edit_is_not_mistaken_for_unsent_divergence(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        record = TaskRecord(task=Task(title="Original"))
        store.apply_sync_incoming(incoming(store, record), bootstrap=True)
        local = store.update_task(record.task.id, lambda t: replace(t, title="Own edit"))
        step = store.prepare_next_sync(NOW)
        receipt, acknowledged = confirmed(store.read_sync_snapshot(), step.request, None)
        store.save_sync_receipt_and_snapshot(receipt, acknowledged)
        remote = TaskRecord(task=replace(local, title="Newer remote edit"))
        candidate = incoming(store, remote, revision=receipt.revision + 1)
        result = store.apply_sync_incoming(candidate)
        assert result.status == "applied"
        assert store.get_task(record.task.id) == remote.task
        assert len(store.list_sync_intents()) == 1


def test_migration_v22_and_backup_strip_applied_only_in_copy(tmp_path):
    from ritm.storage.backup import create_backup, inspect_backup

    profile = tmp_path / "profile"
    profile.mkdir()
    path = profile / "tasks.sqlite3"
    with bound(path, snapshot=True) as store:
        record = TaskRecord(task=Task(title="Kept"))
        store.apply_sync_incoming(incoming(store, record), bootstrap=True)
    archive = create_backup(profile, tmp_path / "copies", NOW)
    inspect_backup(archive)
    staged = tmp_path / "staged.sqlite3"
    with ZipFile(archive) as zipped:
        staged.write_bytes(zipped.read("tasks.sqlite3"))
    with TaskStore(staged) as store:
        assert store.get_sync_binding() is None and store.read_sync_applied_snapshot() is None
        assert store.get_task(record.task.id) == record.task
    with TaskStore(path) as store:
        assert store.read_sync_applied_snapshot() is not None
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE sync_applied_snapshot")
        db.execute("PRAGMA user_version=22")
    with TaskStore(path) as store:
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert store.read_sync_applied_snapshot() is None
        assert store.get_task(record.task.id) == record.task


def test_unsaved_ui_input_can_protect_root_without_mutating_the_queue(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        record = TaskRecord(task=Task(title="Saved"))
        store.apply_sync_incoming(incoming(store, record), bootstrap=True)
        candidate = incoming(
            store, replace(record, task=replace(record.task, title="Incoming")), revision=3
        )
        protected = frozenset({("task", record.task.id)})
        assert store.preview_sync_incoming(candidate, protected_inputs=protected).needs_resolution
        result = store.apply_sync_incoming(candidate, protected_inputs=protected)
        assert result.status == "blocked"
        assert not store.list_sync_intents()
        assert store.get_task(record.task.id) == record.task


def test_valid_orphan_caption_is_blocked_until_local_archived_calendar_support(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        orphan = BlockRecord(block=event(uuid4()), archived_task_title="Retained cloud task")
        result = store.apply_sync_incoming(incoming(store, orphan), bootstrap=True)
        assert result.status == "blocked" and "inbound_archived_calendar_pending" in result.reasons
        assert store.read_sync_applied_snapshot() is None
        assert not store.list_work_blocks()


def test_new_snapshot_watermark_advances_even_without_entity_changes(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        first = incoming(store)
        store.apply_sync_incoming(first, bootstrap=True)
        newer = replace(first, snapshot_id=uuid4(), set_revision=first.set_revision + 1)
        assert store.apply_sync_incoming(newer).status == "applied"
        assert store.read_sync_applied_snapshot() == newer


def test_new_root_cannot_take_uuid_from_local_trash(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        original = Task(title="Local", subtasks=(Subtask(title="Reserved"),))
        with store.suppress_sync_capture("bootstrap"):
            store.create_task(original)
            store.archive_node(original.id, original.id, lambda _: None, NOW)
        foreign = TaskRecord(task=Task(title="Foreign", subtasks=original.subtasks))
        result = store.apply_sync_incoming(incoming(store, foreign), bootstrap=True)
        assert result.status == "blocked"
        assert store.get_task(foreign.task.id) is None and len(store.list_trash()) == 1


def test_matching_bootstrap_root_becomes_put_not_duplicate_create(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        task = Task(title="Existing local")
        with store.suppress_sync_capture("bootstrap"):
            store.create_task(task)
        assert store.get_sync_task_presence(task.id) is None
        assert (
            store.apply_sync_incoming(incoming(store, TaskRecord(task=task)), bootstrap=True).status
            == "applied"
        )
        store.update_task(task.id, lambda t: replace(t, title="Edited"))
        assert store.list_sync_intents()[0].intent.action == "put"


def test_coherent_wal_preview_does_not_mix_local_tree_and_new_pending_intent(tmp_path, monkeypatch):
    path = tmp_path / "tasks.sqlite3"
    with bound(path, snapshot=True) as store:
        store._db.execute("PRAGMA journal_mode=WAL")
        record = TaskRecord(task=Task(title="Original"))
        store.apply_sync_incoming(incoming(store, record), bootstrap=True)
        candidate = incoming(
            store, replace(record, task=replace(record.task, title="Remote")), revision=3
        )
        with TaskStore(path) as writer:
            original = store._regular_source_record
            fired = []

            def concurrent(task):
                if not fired:
                    fired.append(True)
                    writer.update_task(task.id, lambda t: replace(t, title="Concurrent local"))
                return original(task)

            with monkeypatch.context() as patch:
                patch.setattr(store, "_regular_source_record", concurrent)
                preview = store.preview_sync_incoming(candidate)
            assert not preview.needs_resolution
            assert store.apply_sync_incoming(candidate).status == "blocked"
            assert store.get_task(record.task.id).title == "Concurrent local"


def test_canonical_mapping_updates_local_owner_without_moving_block_or_files(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        local = Task(title="Local owner")
        remote = replace(local, id=uuid4())
        with store.suppress_sync_capture("bootstrap"):
            store.create_task(local)
        with store._db:
            store._db.execute(
                "INSERT INTO sync_identities VALUES (?,?,1)", (str(local.id), str(remote.id))
            )
        first = incoming(store, TaskRecord(task=remote))
        assert store.apply_sync_incoming(first, bootstrap=True).status == "applied"
        block = BlockRecord(block=event(remote.id))
        changed = replace(remote, title="Changed")
        assert (
            store.apply_sync_incoming(
                incoming(store, TaskRecord(task=changed), block, revision=3)
            ).status
            == "applied"
        )
        assert store.get_task(local.id).title == "Changed"
        assert store.get_task(remote.id) is None
        assert store.get_work_block(block.block.id).task_id == local.id
        assert not store.list_sync_intents()


def test_remote_marker_reserves_uuid_without_blocking_later_unrelated_import(tmp_path):
    with bound(tmp_path / "tasks.sqlite3", snapshot=True) as store:
        identifier = uuid4()
        mark = RemovalRecord(
            entity_kind="task",
            entity_id=identifier,
            revision=2,
            delete_epoch=1,
            reason="user_deleted",
        )
        assert store.apply_sync_incoming(incoming(store, mark), bootstrap=True).status == "applied"
        record = TaskRecord(task=Task(title="New unrelated"))
        assert (
            store.apply_sync_incoming(incoming(store, mark, record, revision=3)).status == "applied"
        )
        with pytest.raises(ValueError):
            store.create_task(Task(id=identifier, title="Forbidden reuse"))
        restored = TaskRecord(task=Task(id=identifier, title="Explicit remote restore"))
        result = store.apply_sync_incoming(incoming(store, restored, record, revision=4, epoch=2))
        assert result.status == "applied" and store.get_task(identifier) == restored.task
