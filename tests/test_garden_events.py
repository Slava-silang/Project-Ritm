import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from migration_support import drop_later_task_tables

from ritm.application.garden_events import GardenEventService, apply_event
from ritm.application.tasks import TaskService
from ritm.domain.calendar import instant_us
from ritm.domain.tasks import Task
from ritm.storage import backup
from ritm.storage.codec import decode_task
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore

NOW = datetime(2026, 11, 19, 10, tzinfo=UTC)
MINUTE = 60_000_000


@pytest.fixture
def pair(tmp_path):
    now = [NOW]
    with ExitStack() as stack:
        source = stack.enter_context(TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: now[0]))
        task = Task(title="Лабораторная")
        TaskService(source).create_task(task)
        garden = stack.enter_context(GardenStore(tmp_path / "garden.sqlite3", month="2026-11"))
        service = GardenEventService(source, garden)
        yield source, garden, service, task, now


def active_key(source):
    active = source.active_focus()
    return (str(active.id), active.revision) if active else None


def test_subscription_is_explicit_baseline_and_task_changes_are_ordered(pair):
    source, garden, service, task, now = pair
    assert source.garden_events_after()[1] == ()
    assert service.begin(NOW) == 1
    assert garden.snapshot().balance == 0
    assert decode_task(garden.source_snapshot()["tasks"][str(task.id)]) == task
    for completed in (True, False, True):
        TaskService(source).set_completed(task.id, task.id, completed)
    events = source.garden_events_after()[1]
    assert [decode_task(e["payload"]["after"]).completed for e in events[1:]] == [True, False, True]
    assert service.drain() == 3 and service.drain() == 0
    assert garden.source_snapshot()["seq"] == 4
    assert garden.snapshot().balance == 5  # One minimum bonus, despite the repeated mark.
    assert service.begin(NOW) == 0


def test_manual_records_edits_and_debt_are_delivered_from_real_store(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    now[0] += timedelta(hours=2)
    source.add_focus_record(task.id, NOW, now[0], now[0])
    assert service.drain() == 1 and garden.snapshot().balance == 240
    session = source.list_focus_sessions()[0]
    source.edit_focus_duration(session.id, session.revision, 60 * MINUTE, now[0])
    service.drain()
    assert garden.snapshot().balance == 240 and garden.reward_snapshot().debt_us == 60 * MINUTE
    later = now[0]
    now[0] += timedelta(hours=1)
    source.add_focus_record(task.id, later, now[0], now[0])
    service.drain()
    assert garden.snapshot().balance == 240 and garden.reward_snapshot().debt_us == 0
    assert service.drain() == 0


def test_live_checkpoints_pause_resume_and_recovery_do_not_invent_time(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    source.change_focus("start", NOW, None, task_id=task.id, target_us=5 * MINUTE)
    service.drain()
    # No automatic trust of the open interval even when delivery happens much later.
    assert garden.snapshot().balance == 0
    now[0] += timedelta(minutes=6)
    source.checkpoint_garden_focus(now[0])
    service.drain()
    assert garden.snapshot().balance == 10
    source.change_focus("overtime", now[0], active_key(source))
    service.drain()
    assert garden.reward_snapshot().debt_us == 0  # Checkpoint must survive an ordinary update.
    source.change_focus("pause", now[0], active_key(source))
    now[0] += timedelta(hours=1)
    source.change_focus("resume", now[0], active_key(source))
    service.drain()
    assert garden.snapshot().balance == 10
    now[0] += timedelta(minutes=4)
    source.change_focus("finish", now[0], active_key(source))
    service.drain()
    assert garden.snapshot().balance == 20


def test_crash_recovery_can_shorten_checkpoint_and_creates_debt(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    source.change_focus("start", NOW, None, task_id=task.id)
    source.checkpoint_garden_focus(NOW + timedelta(minutes=5))
    service.drain()
    assert garden.snapshot().balance == 10
    source.change_focus(
        "recover",
        NOW + timedelta(hours=3),
        active_key(source),
        recovered_end=NOW + timedelta(minutes=1),
    )
    service.drain()
    assert garden.snapshot().balance == 10 and garden.reward_snapshot().debt_us == 4 * MINUTE


def test_pomodoro_break_has_no_work_event_or_income(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    source.change_pomodoro("start", NOW, 0, None, task_id=task.id, work=5, short=5, long=15)
    now[0] += timedelta(minutes=5)
    source.change_pomodoro("break", now[0], source.get_pomodoro().revision, active_key(source))
    service.drain()
    assert garden.snapshot().balance == 10
    count = garden.source_snapshot()["seq"]
    now[0] += timedelta(hours=1)
    source.checkpoint_pomodoro(now[0])
    source.checkpoint_garden_focus(now[0])
    assert service.drain() == 0 and garden.source_snapshot()["seq"] == count
    assert garden.snapshot().balance == 10


def test_disable_and_late_manual_records_exclude_off_period(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    now[0] += timedelta(minutes=5)
    source.add_focus_record(task.id, NOW, now[0], now[0])
    source.set_garden_source_enabled(False, now[0])
    service.drain()
    assert garden.snapshot().balance == 10
    later = now[0]
    now[0] += timedelta(hours=1)
    source.add_focus_record(task.id, later, now[0], now[0])
    source.set_garden_source_enabled(True, now[0])
    service.drain()
    assert garden.snapshot().balance == 10 and garden.reward_snapshot().remainder_us == 0


@pytest.mark.parametrize("action", ["task", "focus"])
def test_source_journal_failure_rolls_back_user_change(pair, action):
    source, garden, service, task, now = pair
    service.begin(NOW)
    source._db.execute(
        "CREATE TRIGGER fail_journal BEFORE INSERT ON garden_events "
        "BEGIN SELECT RAISE(ABORT,'fake'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        if action == "task":
            TaskService(source).set_completed(task.id, task.id, True)
        else:
            source.add_focus_record(
                task.id, NOW, NOW + timedelta(minutes=5), NOW + timedelta(minutes=5)
            )
    assert source.get_task(task.id) == task and not source.list_focus_sessions()
    assert garden.source_snapshot()["seq"] == 1


def test_receiver_failure_keeps_source_and_retry_updates_money_cursor_once(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    now[0] += timedelta(hours=1)
    source.add_focus_record(task.id, NOW, now[0], now[0])
    before, cursor, ledger = garden.snapshot(), garden.source_snapshot(), garden.reward_snapshot()
    garden._db.execute(
        "CREATE TRIGGER fail_delivery BEFORE INSERT ON garden_receipts "
        "BEGIN SELECT RAISE(ABORT,'fake'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        service.drain()
    assert len(source.garden_events_after()[1]) == 2
    assert garden.snapshot() == before and garden.source_snapshot() == cursor
    assert garden.reward_snapshot() == ledger
    garden._db.execute("DROP TRIGGER fail_delivery")
    assert service.drain() == 1 and garden.snapshot().balance == 120
    identity, events = source.garden_events_after()
    assert garden.apply_source_event(
        identity, events[-1], zone="UTC", expected_revision=0, transition=apply_event
    ).replayed
    assert garden.snapshot().balance == 120


def test_root_restore_is_marked_and_purge_removes_time_with_debt(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    now[0] += timedelta(hours=1)
    source.add_focus_record(task.id, NOW, now[0], now[0])
    service.drain()
    identifier = TaskService(source).delete_node(task.id, task.id, now[0])
    service.drain()
    TaskService(source).restore(UUID(identifier))
    service.drain()
    assert source.garden_events_after()[1][-1]["payload"]["restore"]
    identifier = TaskService(source).delete_node(task.id, task.id, now[0])
    source.purge_trash({UUID(identifier)})
    service.drain()
    assert garden.snapshot().balance == 120 and garden.reward_snapshot().debt_us == 60 * MINUTE
    assert garden.source_snapshot()["focus"] == {}


def test_source_replacement_rewind_and_prefix_fork_are_rejected(pair, tmp_path):
    source, garden, service, task, now = pair
    service.begin(NOW)
    before = garden.snapshot()
    with TaskStore(tmp_path / "other.sqlite3") as other:
        with pytest.raises(ValueError, match="другим источником"):
            GardenEventService(other, garden).drain()
    event = source._db.execute("SELECT * FROM garden_events").fetchone()
    with source._db:
        source._db.execute("DELETE FROM garden_events")
    with pytest.raises(ValueError, match="Курсор"):
        service.drain()
    with source._db:
        source._db.execute(
            "INSERT INTO garden_events VALUES (?,?,?,?,?)",
            (event["seq"], str(task.id), event["happened_us"], event["kind"], event["payload"]),
        )
    with pytest.raises(ValueError, match="Курсор"):
        service.drain()
    assert garden.snapshot() == before


def test_new_process_delivers_unprocessed_source_event_once(pair, tmp_path):
    source, garden, service, task, now = pair
    service.begin(NOW)
    now[0] += timedelta(hours=1)
    source.add_focus_record(task.id, NOW, now[0], now[0])
    code = """from pathlib import Path
import sys
from ritm.storage.sqlite import TaskStore
from ritm.storage.garden import GardenStore
from ritm.application.garden_events import GardenEventService, apply_event
p=Path(sys.argv[1])
with TaskStore(p/'tasks.sqlite3') as source, GardenStore(p/'garden.sqlite3') as garden:
    service=GardenEventService(source,garden)
    assert service.drain()==1
    assert service.drain()==0
    assert garden.snapshot().balance==120
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=tmp_path,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert garden.snapshot().balance == 120


def test_v12_migration_and_backup_preserve_source_journal(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    task = Task(title="До миграции")
    with TaskStore(path) as source:
        source.create_task(task)
    with sqlite3.connect(path) as db:
        drop_later_task_tables(db, 12)
        db.execute("DROP TABLE garden_events")
        db.execute("DROP TABLE garden_source")
        db.execute("PRAGMA user_version=12")
    # Old v12 backups remain readable and migrate on restore.
    old = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    assert backup.inspect_backup(old)["token"]
    with TaskStore(path) as source:
        assert source.get_task(task.id) == task and source.garden_events_after()[1] == ()
        assert source._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        source.begin_garden_source(NOW)
        identity, events = source.garden_events_after()
    copy = backup.create_backup(tmp_path, tmp_path / "copies", NOW + timedelta(minutes=1))
    backup.restore_backup(tmp_path, copy, backup.inspect_backup(copy)["token"])
    with TaskStore(path) as source:
        assert source.garden_events_after() == (identity, events)


def test_v2_garden_migration_preserves_money_and_time_ledger(tmp_path):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11") as garden:
        garden.credit("old", 75)
        before, ledger = garden.snapshot(), garden.reward_snapshot()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE garden_favorites")
        db.execute("DROP TABLE garden_source_binding")
        db.execute("DROP TABLE garden_achievement_state")
        db.execute("DROP TABLE garden_source_state")
        db.execute("PRAGMA user_version=2")
    with GardenStore(path) as garden:
        assert garden.snapshot() == before and garden.reward_snapshot() == ledger
        assert garden.source_snapshot()["identity"] is None


def test_failed_baseline_subscription_is_rolled_back_and_retry_is_safe(pair):
    source, garden, service, task, now = pair
    source._db.execute(
        "CREATE TRIGGER fail_begin BEFORE INSERT ON garden_events "
        "BEGIN SELECT RAISE(ABORT,'fake'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        service.begin(NOW)
    assert not source._garden_subscribed() and source.garden_events_after()[1] == ()
    assert garden.source_snapshot()["seq"] == 0 and not garden.reward_snapshot().enabled
    source._db.execute("DROP TRIGGER fail_begin")
    assert service.begin(NOW) == 1


def test_unsupported_event_or_wrong_task_baseline_stops_without_partial_delivery(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    TaskService(source).set_completed(task.id, task.id, True)
    before, cursor = garden.snapshot(), garden.source_snapshot()
    with source._db:
        row = source._db.execute("SELECT payload FROM garden_events WHERE seq=2").fetchone()
        payload = json.loads(row[0])
        payload["before"] = None
        source._db.execute("UPDATE garden_events SET payload=? WHERE seq=2", (json.dumps(payload),))
    with pytest.raises(ValueError, match="Исходная задача"):
        service.drain()
    assert garden.snapshot() == before and garden.source_snapshot() == cursor
    with source._db:
        source._db.execute("UPDATE garden_events SET kind='unsupported' WHERE seq=2")
    with pytest.raises(ValueError, match="Неизвестный вид"):
        service.drain()
    assert garden.snapshot() == before


def test_backward_wall_clock_keeps_event_order_and_does_not_double_pay(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    source.change_focus("start", NOW, None, task_id=task.id)
    source.checkpoint_garden_focus(NOW + timedelta(minutes=5))
    service.drain()
    source.checkpoint_garden_focus(NOW + timedelta(minutes=1))
    service.drain()
    assert garden.snapshot().balance == 10 and garden.reward_snapshot().debt_us == 0
    stamps = [e["happened_us"] for e in source.garden_events_after()[1]]
    assert stamps == sorted(stamps)


def test_concurrent_receivers_with_separate_connections_deliver_once(pair, tmp_path):
    source, garden, service, task, now = pair
    service.begin(NOW)
    now[0] += timedelta(hours=1)
    source.add_focus_record(task.id, NOW, now[0], now[0])

    def drain(_):
        with (
            TaskStore(tmp_path / "tasks.sqlite3") as s,
            GardenStore(tmp_path / "garden.sqlite3") as g,
        ):
            return GardenEventService(s, g).drain()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(drain, (0, 1)))
    assert garden.snapshot().balance == 120 and garden.source_snapshot()["seq"] == 2


def test_direct_time_reconciliation_cannot_bypass_attached_source(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    before = garden.snapshot()
    with pytest.raises(ValueError, match="журнал событий"):
        garden.reconcile_focus(
            uuid4(), (), now_us=instant_us(NOW), zone="UTC", expected_revision=before.revision
        )
    assert garden.snapshot() == before


def test_preexisting_standalone_ledger_cannot_be_silently_attached(pair):
    source, garden, service, task, now = pair
    garden.reconcile_focus(
        uuid4(), (), now_us=instant_us(NOW), zone="UTC", enabled=True, expected_revision=0
    )
    before, ledger = garden.snapshot(), garden.reward_snapshot()
    with pytest.raises(ValueError, match="явная миграция"):
        service.begin(NOW)
    assert garden.snapshot() == before and garden.reward_snapshot() == ledger
    assert garden.source_snapshot()["seq"] == 0
