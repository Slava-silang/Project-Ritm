import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from ritm.domain.calendar import instant_us
from ritm.domain.garden_rewards import (
    DAY_LIMIT,
    MINUTE,
    RewardLedger,
    decode_ledger,
    encode_ledger,
    reconcile_time,
)
from ritm.storage.garden import SCHEMA_VERSION, GardenStore

HOUR = 60 * MINUTE
START = instant_us(datetime(2026, 11, 19, tzinfo=UTC))


def update(ledger, intervals, now, enabled=None, zone="UTC"):
    return reconcile_time(ledger, intervals, now_us=now, zone=ZoneInfo(zone), enabled=enabled)


def fresh(start=START):
    return update(RewardLedger(), (), start, True).ledger


def sync(store, intervals, now, enabled=None, identifier=None, revision=None):
    return store.reconcile_focus(
        identifier or uuid4(),
        intervals,
        now_us=now,
        zone="UTC",
        enabled=enabled,
        expected_revision=store.snapshot().revision if revision is None else revision,
    )


def test_first_enable_excludes_history_and_counts_only_later_parts():
    before = ((START - HOUR, START),)
    assert update(RewardLedger(), before, START).coins == 0
    ledger = update(RewardLedger(), before, START, True).ledger
    reward = update(ledger, ((START - HOUR, START + 6 * MINUTE),), START + 6 * MINUTE)
    assert reward.coins == 10 and reward.ledger.remainder_us == MINUTE


def test_residual_crosses_pauses_reopen_and_day_boundary():
    start = START + 24 * HOUR - 2 * MINUTE
    first = ((start, start + MINUTE),)
    reward = update(fresh(start), first, start + MINUTE)
    assert reward.coins == 0 and reward.ledger.remainder_us == MINUTE
    ledger = decode_ledger(encode_ledger(reward.ledger))
    second = (*first, (start + 3 * MINUTE, start + 7 * MINUTE))
    reward = update(ledger, second, start + 7 * MINUTE)
    assert reward.coins == 10 and reward.ledger.remainder_us == 0
    assert len(reward.ledger.days) == 2
    assert update(reward.ledger, second, start + 8 * MINUTE).coins == 0


def test_limit_is_shared_by_all_intervals_and_excess_is_not_debt():
    intervals = ((START, START + 7 * HOUR), (START + 7 * HOUR, START + 13 * HOUR))
    reward = update(fresh(), intervals, START + 13 * HOUR)
    assert reward.coins == 1440 and reward.ledger.paid_us == DAY_LIMIT
    shorter = ((START, START + 12 * HOUR),)
    reward = update(reward.ledger, shorter, START + 14 * HOUR)
    assert reward.coins == 0 and reward.ledger.debt_us == 0


def test_deletion_debt_survives_days_months_and_restart_without_clawback():
    start = instant_us(datetime(2026, 11, 30, 10, tzinfo=UTC))
    original = ((start, start + 2 * HOUR),)
    reward = update(fresh(start), original, start + 2 * HOUR)
    assert reward.coins == 240
    shortened = ((start, start + HOUR),)
    reward = update(reward.ledger, shortened, start + 3 * HOUR)
    assert reward.coins == 0 and reward.ledger.debt_us == HOUR
    ledger = decode_ledger(encode_ledger(reward.ledger))
    next_day = start + 24 * HOUR
    repaid = (*shortened, (next_day, next_day + HOUR))
    reward = update(ledger, repaid, next_day + HOUR)
    assert reward.coins == 0 and reward.ledger.debt_us == 0
    reward = update(
        reward.ledger,
        (*repaid, (next_day + HOUR, next_day + HOUR + 5 * MINUTE)),
        next_day + HOUR + 5 * MINUTE,
    )
    assert reward.coins == 10


def test_deleting_unpaid_residual_does_not_create_debt():
    reward = update(fresh(), ((START, START + 4 * MINUTE),), START + 4 * MINUTE)
    reward = update(reward.ledger, (), START + 5 * MINUTE)
    assert reward.ledger.debt_us == 0 and reward.ledger.remainder_us == 0
    reward = update(
        reward.ledger, ((START + 5 * MINUTE, START + 10 * MINUTE),), START + 10 * MINUTE
    )
    assert reward.coins == 10


def test_multiple_enable_windows_are_half_open_and_do_not_pay_twice():
    reward = update(fresh(), ((START, START + 5 * MINUTE),), START + 5 * MINUTE, False)
    assert reward.coins == 10
    intervals = ((START, START + 20 * MINUTE),)
    reward = update(reward.ledger, intervals, START + 10 * MINUTE, True)
    reward = update(reward.ledger, intervals, START + 15 * MINUTE, False)
    assert reward.coins == 10
    reward = update(reward.ledger, intervals, START + 15 * MINUTE, True)
    reward = update(reward.ledger, intervals, START + 20 * MINUTE)
    assert reward.coins == 10 and reward.ledger.paid_us == 15 * MINUTE


def test_partial_interval_over_midnight_has_independent_daily_caps():
    start = START + 11 * HOUR
    reward = update(fresh(start), ((start, start + 26 * HOUR),), start + 26 * HOUR)
    assert reward.coins == 2880
    assert reward.ledger.days == (("2026-11-19", DAY_LIMIT), ("2026-11-20", DAY_LIMIT))


def test_disable_settles_old_work_and_excludes_off_time_and_late_manual_record():
    ledger = fresh()
    stop = START + 6 * MINUTE
    reward = update(ledger, ((START, stop),), stop, False)
    assert reward.coins == 10 and not reward.ledger.enabled
    resume = START + HOUR
    # Включает позднюю ручную запись за выключенный период и текущую сессию.
    intervals = ((START, START + 2 * HOUR), (stop, resume))
    reward = update(reward.ledger, intervals, resume, True)
    assert reward.coins == 0 and reward.ledger.remainder_us == MINUTE
    reward = update(reward.ledger, intervals, resume + 4 * MINUTE)
    assert reward.coins == 10 and reward.ledger.remainder_us == 0


def test_clock_rollback_rejected_then_forward_does_not_duplicate():
    reward = update(fresh(), ((START, START + HOUR),), START + HOUR)
    with pytest.raises(ValueError, match="назад"):
        update(reward.ledger, (), START)
    assert update(reward.ledger, ((START, START + HOUR),), START + 2 * HOUR).coins == 0


@pytest.mark.parametrize("day,hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_local_day_limit_uses_real_midnight_across_dst(day, hours):
    zone = ZoneInfo("Europe/Berlin")
    local = datetime.fromisoformat(day).replace(tzinfo=zone)
    start = instant_us(local)
    reward = update(
        fresh(start), ((start, start + hours * HOUR),), start + hours * HOUR, zone="Europe/Berlin"
    )
    assert reward.coins == 1440 and len(reward.ledger.days) == 1
    assert reward.ledger.days[0][0] == day


@pytest.mark.parametrize(
    "change",
    [
        {"paid_us": 1},
        {"last_us": True},
        {"windows": ((START, START - 1),)},
        {"days": (("2026-11-19", DAY_LIMIT + 1),)},
        {"days": (("2026-11-19", 1), ("2026-11-19", 2))},
        {"windows": ((START, None), (START + MINUTE, None))},
    ],
)
def test_invalid_ledger_is_rejected(change):
    with pytest.raises(ValueError):
        replace(fresh(), **change)


def test_codec_rejects_unknown_duplicate_fields_and_never_resets():
    payload = encode_ledger(fresh())
    for invalid in (
        "{",
        payload.replace('"paid_us": 0', '"paid_us": 0, "paid_us": 0'),
        payload[:-1] + ', "extra": 0}',
    ):
        with pytest.raises(ValueError, match="повреждён"):
            decode_ledger(invalid)


def test_storage_atomically_saves_residual_wallet_receipt_and_replays(tmp_path):
    path = tmp_path / "garden.sqlite3"
    identifier = uuid4()
    with GardenStore(path, month="2026-11") as store:
        sync(store, (), START, True)
        sync(store, ((START, START + 4 * MINUTE),), START + 4 * MINUTE)
    with GardenStore(path) as store:
        intervals = ((START, START + 5 * MINUTE),)
        result = sync(store, intervals, START + 5 * MINUTE, identifier=identifier)
        assert result.state.balance == 10
        assert sync(
            store, intervals, START + 5 * MINUTE, identifier=identifier, revision=0
        ).replayed
        with pytest.raises(ValueError, match="другим содержимым"):
            sync(store, (), START + 5 * MINUTE, identifier=identifier)
        with pytest.raises(ValueError, match="изменились"):
            sync(store, (), START + 5 * MINUTE, revision=0)
        assert store.reward_snapshot().paid_us == 5 * MINUTE


def test_receipt_failure_rolls_back_ledger_and_wallet_and_retry_pays_once(tmp_path):
    with GardenStore(tmp_path / "garden.sqlite3", month="2026-11") as store:
        sync(store, (), START, True)
        before, ledger = store.snapshot(), store.reward_snapshot()
        store._db.execute(
            "CREATE TRIGGER fail_rewards BEFORE INSERT ON garden_receipts "
            "BEGIN SELECT RAISE(ABORT,'fake'); END"
        )
        operation = uuid4()
        with pytest.raises(sqlite3.IntegrityError):
            sync(store, ((START, START + HOUR),), START + HOUR, identifier=operation)
        assert store.snapshot() == before and store.reward_snapshot() == ledger
        store._db.execute("DROP TRIGGER fail_rewards")
        assert (
            sync(store, ((START, START + HOUR),), START + HOUR, identifier=operation).state.balance
            == 120
        )


def test_v1_migration_preserves_items_wallet_and_receipts(tmp_path):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11") as store:
        store.credit("old", 300)
        before = store.snapshot()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE garden_favorites")
        db.execute("DROP TABLE garden_source_binding")
        db.execute("DROP TABLE garden_achievement_state")
        db.execute("DROP TABLE garden_source_state")
        db.execute("DROP TABLE garden_reward_state")
        db.execute("PRAGMA user_version=1")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="ротация"):
        GardenStore(path, month="2026-12")
    assert path.read_bytes() == original
    with GardenStore(path) as store:
        assert store.snapshot() == before and store.credit("old", 300).replayed
        assert store.reward_snapshot() == RewardLedger()
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_competing_connections_pay_same_observation_once(tmp_path):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11") as store:
        sync(store, (), START, True)
        revision = store.snapshot().revision
    identifier = uuid4()

    def reconcile(_):
        with GardenStore(path) as store:
            return sync(
                store,
                ((START, START + HOUR),),
                START + HOUR,
                identifier=identifier,
                revision=revision,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reconcile, (0, 1)))
    assert sorted(r.replayed for r in results) == [False, True]
    with GardenStore(path) as store:
        assert store.snapshot().balance == 120 and store.reward_snapshot().paid_us == HOUR


@pytest.mark.parametrize("case", ["clock", "interval", "enabled", "zone"])
def test_invalid_observation_rolls_back_everything(tmp_path, case):
    with GardenStore(tmp_path / "garden.sqlite3", month="2026-11") as store:
        sync(store, (), START, True)
        before, ledger = store.snapshot(), store.reward_snapshot()
        args = dict(
            now_us=START + HOUR, zone="UTC", enabled=None, expected_revision=before.revision
        )
        intervals = ((START, START + HOUR),)
        if case == "clock":
            args["now_us"] = START - 1
        elif case == "interval":
            intervals = ((START, START - HOUR),)
        elif case == "enabled":
            args["enabled"] = 1
        else:
            args["zone"] = "../UTC"
        with pytest.raises(ValueError):
            store.reconcile_focus(uuid4(), intervals, **args)
        assert store.snapshot() == before and store.reward_snapshot() == ledger


def test_failed_migration_and_corrupt_ledger_leave_database_unchanged(tmp_path):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11"):
        pass
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=1")  # Existing table makes migration fail.
    original = path.read_bytes()
    with pytest.raises(sqlite3.OperationalError):
        GardenStore(path)
    assert path.read_bytes() == original
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.execute("UPDATE garden_reward_state SET payload='{}'")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="повреждён"):
        GardenStore(path)
    assert path.read_bytes() == original


def test_new_process_preserves_debt_and_does_not_repay_existing_time(tmp_path):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11") as store:
        sync(store, (), START, True)
        sync(store, ((START, START + HOUR),), START + HOUR)
        sync(store, (), START + 2 * HOUR)
    code = """from pathlib import Path
from uuid import uuid4
import sys
from ritm.storage.garden import SCHEMA_VERSION, GardenStore
with GardenStore(Path(sys.argv[1])) as store:
    start, hour = int(sys.argv[2]), int(sys.argv[3])
    assert store.reward_snapshot().debt_us == hour
    result = store.reconcile_focus(uuid4(), ((start+2*hour,start+3*hour),),
        now_us=start+3*hour, zone='UTC', expected_revision=store.snapshot().revision)
    assert result.state.balance == 120
    assert store.reward_snapshot().debt_us == 0
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(path), str(START), str(HOUR)],
        cwd=tmp_path,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
