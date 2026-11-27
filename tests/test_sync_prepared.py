"""Durable preparation on disposable profiles, never real transport."""

import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest
from migration_support import drop_later_task_tables
from test_sync_regular_plan import NOW, accept, plan, seed

from ritm.integrations.sync_operations import Receipt, operation_hash
from ritm.storage import backup
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore


def ack(frozen):
    op = frozen.operation
    return Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=op.base_revision + 1,
        delete_epoch=op.delete_epoch + (op.action in ("prune", "restore")),
        error=None,
    )


def test_prepare_freeze_ack_and_mapping_survive_reopen(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    proposal = plan(changes, context).proposals[0]
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_regular(context, proposal.id)
        assert frozen.operation.payload == proposal.payload
        assert store.enqueue_sync_regular(context, proposal.id) == frozen
        assert store.read_sync_prepared_state().identities == ()
        assert store.list_sync_regular_changes() == changes
        assert store.next_sync_sendable() is None  # Capture coverage barrier remains.
    with TaskStore(path) as store:
        assert store.enqueue_sync_regular(context, proposal.id).raw == frozen.raw
        store.save_sync_receipt(ack(frozen))
        state = store.read_sync_prepared_state()
        assert state.identities == tuple(sorted(proposal.identities, key=lambda x: str(x[0])))
        assert store.save_sync_receipt(ack(frozen)).raw == frozen.raw
        assert store.list_sync_regular_changes() == changes
    with TaskStore(path) as store:
        assert store.read_sync_prepared_state() == state
        advanced = accept(proposal, context)
        next_proposal = plan(changes, advanced).proposals[0]
        second = store.enqueue_sync_regular(advanced, next_proposal.id)
        assert second.intent_id != frozen.intent_id
        assert second.operation.base_revision == ack(frozen).revision


def test_pending_overlap_and_stale_base_do_not_enqueue_partial_rows(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    proposal = plan(changes, context).proposals[0]
    with TaskStore(path) as store:
        first = store.enqueue_sync_regular(context, proposal.id)
        advanced = accept(proposal, context)
        with pytest.raises(ValueError):
            store.enqueue_sync_regular(advanced, proposal.id)
        assert len(store.list_sync_intents()) == 1
        store.save_sync_receipt(ack(first))
        stale = replace(
            advanced,
            bases=(
                replace(advanced.bases[0], entry=replace(advanced.bases[0].entry, revision=20)),
            ),
        )
        with pytest.raises(ValueError):
            store.enqueue_sync_regular(stale, proposal.id)
        assert len(store.list_sync_intents()) == 1
        assert len(store.list_frozen_sync_requests()) == 1


def test_receipt_mapping_failure_rolls_back_receipt_and_all_mappings(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    proposal = plan(changes, context).proposals[0]
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_regular(context, proposal.id)
        local, remote = proposal.identities[-1]
        with store._db:
            store._db.execute(
                "INSERT INTO sync_identities VALUES (?,?,1)", (str(local), str(uuid4()))
            )
        with pytest.raises(ValueError):
            store.save_sync_receipt(ack(frozen))
        assert store.get_frozen_sync_request(frozen.intent_id).receipt is None
        assert store._db.execute("SELECT count(*) FROM sync_identities").fetchone()[0] == 1
        assert not store.read_sync_prepared_state().barriers


def test_failed_preparation_leaves_no_intent_or_request(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    proposal = plan(changes, context).proposals[0]
    with TaskStore(path) as store:
        with store._db:
            store._db.execute(
                "CREATE TRIGGER fail_prepare BEFORE INSERT ON sync_outbox "
                "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            store.enqueue_sync_regular(context, proposal.id)
        assert not store.list_sync_intents() and not store.list_frozen_sync_requests()
        assert store._db.execute("SELECT count(*) FROM sync_prepared").fetchone()[0] == 0
        assert store.list_sync_regular_changes() == changes


def test_v19_migration_keeps_frozen_queue_and_facts(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_regular(context, plan(changes, context).proposals[0].id)
    with sqlite3.connect(path) as db:
        drop_later_task_tables(db, 19)
        db.execute("PRAGMA user_version=19")
    with TaskStore(path) as store:
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert store.get_frozen_sync_request(frozen.intent_id).raw == frozen.raw
        assert store.list_sync_regular_changes() == changes
        assert store.read_sync_prepared_state().identities == ()


def test_backup_clears_ledger_only_in_copy(tmp_path):
    profile = tmp_path / "profile"
    path = profile / "tasks.sqlite3"
    changes, context = seed(path, 1)
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_regular(context, plan(changes, context).proposals[0].id)
        store.save_sync_receipt(ack(frozen))
        expected = store.read_sync_prepared_state()
    archive = backup.create_backup(profile, tmp_path / "copies", NOW)
    with TaskStore(path) as store:
        assert store.read_sync_prepared_state() == expected
    preview = backup.inspect_backup(archive)
    backup.restore_backup(profile, archive, preview["token"])
    with TaskStore(path) as store:
        assert store.get_sync_binding() is None
        assert store.read_sync_prepared_state().identities == ()
        assert not store.list_sync_intents()


def test_barrier_receipt_is_recovered_without_caller_remembering_ack(tmp_path):
    from test_sync_regular_plan import mutate

    from ritm.application.tasks import TaskService

    path = tmp_path / "tasks.sqlite3"
    _, context = seed(path)
    changes = mutate(
        path, lambda store, task: TaskService(store).delete_node(task.id, task.subtasks[0].id, NOW)
    )
    proposal = plan(changes, context).proposals[0]
    assert proposal.action == "prune"
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_regular(context, proposal.id)
        store.save_sync_receipt(ack(frozen))
    with TaskStore(path) as store:
        assert store.read_sync_prepared_state().barriers == frozenset(proposal.barrier_changes)
        advanced = replace(accept(proposal, context), acknowledged_barriers=frozenset())
        # Persisted phase suppresses an accidental second prune after restart.
        with pytest.raises(ValueError, match="Нет однозначного"):
            store.enqueue_sync_regular(advanced, proposal.id)
        assert len(store.list_sync_intents()) == 1


def test_rejected_receipt_does_not_confirm_mappings_or_release_overlap(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    proposal = plan(changes, context).proposals[0]
    with TaskStore(path) as store:
        frozen = store.enqueue_sync_regular(context, proposal.id)
        rejected = replace(ack(frozen), status="rejected", error="stale_epoch")
        store.save_sync_receipt(rejected)
        assert store.read_sync_prepared_state().identities == ()
        assert store.enqueue_sync_regular(context, proposal.id).receipt == rejected
        advanced = accept(proposal, context)
        with pytest.raises(ValueError, match="подтверждения"):
            store.enqueue_sync_regular(advanced, proposal.id)
        assert len(store.list_sync_intents()) == 1


def test_invalid_context_and_outer_transaction_do_not_change_queue(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    changes, context = seed(path, 1)
    proposal = plan(changes, context).proposals[0]
    with TaskStore(path) as store:
        with pytest.raises(ValueError):
            store.enqueue_sync_regular(
                replace(context, identities=(*context.identities, context.identities[0])),
                proposal.id,
            )
        assert not store.list_sync_intents()
        store._db.execute("BEGIN")
        with pytest.raises(ValueError, match="отдельную транзакцию"):
            store.enqueue_sync_regular(context, proposal.id)
        assert store._db.in_transaction
        store._db.rollback()
