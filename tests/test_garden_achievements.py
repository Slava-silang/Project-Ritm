import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from test_garden_events import NOW
from test_garden_events import pair as pair
from test_garden_payments import complete, work

from ritm.application.garden_events import GardenEventService
from ritm.application.tasks import TaskService
from ritm.domain.calendar import instant_us
from ritm.domain.garden_achievements import (
    HOUR,
    AchievementState,
    advance_achievements,
    decode_achievements,
    encode_achievements,
)
from ritm.domain.tasks import Subtask, Task
from ritm.storage.garden import SCHEMA_VERSION, GardenStore


def enable(pair):
    source, garden, _, task, now = pair
    service = GardenEventService(source, garden, award_achievements=True)
    service.begin(NOW)
    return source, garden, service, task, now


def test_welcome_and_first_created_task_are_once_and_baseline_is_not_creation(pair):
    source, garden, service, task, now = enable(pair)
    assert garden.snapshot().balance == 100
    assert garden.achievement_snapshot().welcomed and not garden.achievement_snapshot().first_task
    assert service.begin(NOW) == 0
    TaskService(source).create_task(Task(title="Первая после включения"))
    TaskService(source).create_task(Task(title="Следующая"))
    service.drain()
    assert garden.snapshot().balance == 400 and garden.achievement_snapshot().first_task
    assert service.drain() == 0


def test_completed_roots_cross_thresholds_and_unlock_jungle_once(pair):
    source, garden, service, task, now = enable(pair)
    roots = [Task(title=str(i)) for i in range(30)]
    for i, root in enumerate(roots, 1):
        TaskService(source).create_task(root)
        complete(source, root)
        service.drain()
        progress = garden.achievement_snapshot()
        assert len(progress.roots) == i
        if i == 5:
            assert garden.snapshot().balance == 475
        if i == 15:
            assert garden.snapshot().balance == 675 and "jungle" in progress.themes
    assert garden.snapshot().balance == 1050  # 400 + 150 task bonuses + 500 monthly goals.
    assert garden.achievement_snapshot().months[0].claimed == ("tasks:15", "tasks:30", "tasks:5")
    complete(source, roots[0], value=False)
    complete(source, roots[0])
    service.drain()
    assert garden.snapshot().balance == 1050


def test_subtasks_do_not_count_as_independent_roots_and_restore_does_not_count(pair):
    source, garden, _, task, now = pair
    a, b = Subtask(title="a"), Subtask(title="b")
    TaskService(source).add_subtask(task.id, a)
    TaskService(source).add_subtask(task.id, b)
    source, garden, service, task, now = enable(pair)
    complete(source, task, a.id)
    service.drain()
    assert garden.achievement_snapshot().roots == ()
    complete(source, task, b.id)
    service.drain()
    assert garden.achievement_snapshot().roots == (str(task.id),)
    identifier = TaskService(source).delete_node(task.id, task.id, now[0])
    TaskService(source).restore(UUID(identifier))
    service.drain()
    assert garden.achievement_snapshot().roots == (str(task.id),)


def test_thirty_paid_hours_unlock_space_and_deleted_time_does_not_take_it_away(pair):
    source, garden, service, task, now = enable(pair)
    for day in range(3):
        now[0] = NOW + timedelta(days=day)
        work(source, task, now, 10 * 60)
        service.drain()
    progress = garden.achievement_snapshot()
    assert progress.paid_us == 30 * HOUR and "space" in progress.themes
    assert garden.snapshot().balance == 4200  # 3600 focus + 500 monthly + 100 welcome.
    session = source.list_focus_sessions()[0]
    source.edit_focus_duration(session.id, session.revision, HOUR, now[0])
    service.drain()
    now[0] = NOW + timedelta(days=3)
    work(source, task, now, 5 * 60)
    service.drain()
    assert garden.achievement_snapshot() == progress
    assert garden.snapshot().balance == 4200


def test_cap_overflow_and_off_time_do_not_count_for_focus_milestones(pair):
    source, garden, service, task, now = enable(pair)
    work(source, task, now, 14 * 60)  # Ends at midnight, 12h paid.
    service.drain()
    assert garden.achievement_snapshot().paid_us == 12 * HOUR
    assert garden.snapshot().balance == 1590  # 100 welcome + 1440 time + 50 milestone.
    source.set_garden_source_enabled(False, now[0])
    work(source, task, now, 10 * 60)
    complete(source, task)
    service.drain()
    assert garden.achievement_snapshot().paid_us == 12 * HOUR
    assert garden.achievement_snapshot().roots == ()
    source.set_garden_source_enabled(True, now[0])
    service.drain()
    assert garden.snapshot().balance == 1590


def test_task_created_while_off_cannot_trigger_first_creation_after_reenable(pair):
    source, garden, service, task, now = enable(pair)
    source.set_garden_source_enabled(False, now[0])
    off = Task(title="Создана в выключенный период")
    TaskService(source).create_task(off)
    source.set_garden_source_enabled(True, now[0])
    source.update_task(off.id, lambda t: replace(t, title="Правка после включения"))
    service.drain()
    assert garden.snapshot().balance == 100 and not garden.achievement_snapshot().first_task
    TaskService(source).create_task(Task(title="Новая во включённом периоде"))
    service.drain()
    assert garden.snapshot().balance == 400


def test_monthly_goals_restart_but_same_root_cannot_be_recounted(pair):
    source, garden, service, task, now = enable(pair)
    roots = [Task(title=str(i)) for i in range(5)]
    for root in roots:
        TaskService(source).create_task(root)
        complete(source, root)
    service.drain()
    assert garden.snapshot().balance == 475
    now[0] = datetime(2026, 12, 1, 10, tzinfo=UTC)
    for root in roots:
        complete(source, root, value=False)
        complete(source, root)
    service.drain()
    assert garden.snapshot().balance == 475
    for i in range(5):
        new = Task(title=f"Декабрь {i}")
        TaskService(source).create_task(new)
        complete(source, new)
    service.drain()
    progress = garden.achievement_snapshot()
    assert [(m.month, m.roots) for m in progress.months] == [("2026-11", 5), ("2026-12", 5)]
    assert garden.snapshot().balance == 550


def test_counter_month_is_local_payment_date_not_date_of_backdated_record(pair):
    source, garden, _, task, now = pair
    service = GardenEventService(source, garden, zone="Europe/Moscow", award_achievements=True)
    service.begin(NOW)
    now[0] = datetime(2026, 11, 30, 22, tzinfo=UTC)  # Dec 1 in Moscow.
    source.add_focus_record(task.id, NOW, NOW + timedelta(hours=5), now[0])
    service.drain()
    progress = garden.achievement_snapshot()
    assert progress.months[0].month == "2026-12" and progress.paid_us == 5 * HOUR


def test_monthly_milestone_and_first_task_failure_roll_back_with_wallet_and_cursor(pair):
    source, garden, service, task, now = enable(pair)
    TaskService(source).create_task(Task(title="Первая новая"))
    before, progress, cursor = (
        garden.snapshot(),
        garden.achievement_snapshot(),
        garden.source_snapshot(),
    )
    garden._db.execute(
        "CREATE TRIGGER fail_achievements BEFORE INSERT ON garden_receipts "
        "BEGIN SELECT RAISE(ABORT,'fake'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        service.drain()
    assert garden.snapshot() == before and garden.achievement_snapshot() == progress
    assert garden.source_snapshot() == cursor
    garden._db.execute("DROP TRIGGER fail_achievements")
    service.drain()
    assert garden.snapshot().balance == 400
    work(source, task, now, 5 * 60)
    before, progress, cursor = (
        garden.snapshot(),
        garden.achievement_snapshot(),
        garden.source_snapshot(),
    )
    garden._db.execute(
        "CREATE TRIGGER fail_threshold BEFORE UPDATE ON garden_achievement_state "
        "BEGIN SELECT RAISE(ABORT,'fake'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        service.drain()
    assert garden.snapshot() == before and garden.achievement_snapshot() == progress
    assert garden.source_snapshot() == cursor
    garden._db.execute("DROP TRIGGER fail_threshold")
    service.drain()
    assert garden.snapshot().balance == 1050  # 400 + 600 focus + 50 milestone.


def test_after_activation_legacy_delivery_mode_cannot_silently_skip_progress(pair):
    source, garden, service, task, now = enable(pair)
    TaskService(source).create_task(Task(title="Новая"))
    before, progress = garden.snapshot(), garden.achievement_snapshot()
    with pytest.raises(ValueError, match="без их учёта"):
        GardenEventService(source, garden).drain()
    assert garden.snapshot() == before and garden.achievement_snapshot() == progress
    service.drain()
    assert garden.snapshot().balance == 400


def test_v4_upgrade_preserves_money_and_starts_without_historical_rewards(pair, tmp_path):
    source, garden, legacy, task, now = pair
    legacy.begin(NOW)
    work(source, task, now, 5 * 60)
    complete(source, task)
    legacy.drain()
    old_balance, old_ledger = garden.snapshot().balance, garden.reward_snapshot()
    garden.close()
    path = tmp_path / "garden.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE garden_source_binding")
        db.execute("DROP TABLE garden_achievement_state")
        db.execute("PRAGMA user_version=4")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="ротация"):
        GardenStore(path, month="2026-12")
    assert path.read_bytes() == original
    with GardenStore(path) as upgraded:
        assert upgraded.snapshot().balance == old_balance
        assert upgraded.reward_snapshot() == old_ledger
        assert upgraded.achievement_snapshot() == AchievementState()
        assert upgraded._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        active = GardenEventService(source, upgraded, award_achievements=True)
        assert active.drain() == 0 and upgraded.achievement_snapshot().paid_us == 0
        source.set_garden_source_enabled(True, now[0])
        active.drain()
        assert upgraded.snapshot().balance == old_balance + 100
        assert upgraded.achievement_snapshot().paid_us == 0


def test_new_process_preserves_welcome_progress_and_avoids_repeated_awards(pair, tmp_path):
    source, garden, service, task, now = enable(pair)
    work(source, task, now, 5 * 60)
    service.drain()
    assert garden.snapshot().balance == 750
    code = """from pathlib import Path
import sys
from ritm.storage.sqlite import TaskStore
from ritm.storage.garden import GardenStore
from ritm.application.garden_events import GardenEventService
p=Path(sys.argv[1])
with TaskStore(p/'tasks.sqlite3') as s, GardenStore(p/'garden.sqlite3') as g:
    service=GardenEventService(s,g,award_achievements=True)
    assert service.drain()==0
    assert g.snapshot().balance==750
    assert g.achievement_snapshot().welcomed
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


def test_pure_state_can_cross_all_thresholds_and_has_no_telegram_reward():
    root = Task(title="Первая", completed=True)
    reward = advance_achievements(
        AchievementState(),
        enabled=True,
        paid_us=30 * HOUR,
        now_us=instant_us(NOW),
        zone=ZoneInfo("UTC"),
        after=root,
    )
    assert reward.coins == 900  # 100 + 300 + 50/150/300 focus; no task threshold yet.
    assert "theme:space" in reward.unlocked and "telegram" not in reward.unlocked
    assert reward.state.themes == ("dark", "light", "space")
    assert decode_achievements(encode_achievements(reward.state)) == reward.state


@pytest.mark.parametrize(
    "change",
    [
        {"welcomed": 1},
        {"first_task": True},
        {"paid_us": HOUR},
        {"themes": ("dark", "light", "space")},
        {"roots": ("bad",)},
    ],
)
def test_invalid_progress_is_rejected(change):
    with pytest.raises(ValueError):
        replace(AchievementState(), **change)


@pytest.mark.parametrize(
    "field", ["welcomed", "themes", "paid_us", "months", "duplicate", "json", "missing"]
)
def test_corrupt_achievement_file_does_not_reset_data(pair, tmp_path, field):
    source, garden, service, task, now = enable(pair)
    work(source, task, now, 5 * 60)
    service.drain()
    garden.close()
    path = tmp_path / "garden.sqlite3"
    with sqlite3.connect(path) as db:
        raw = db.execute("SELECT payload FROM garden_achievement_state").fetchone()[0]
        data = json.loads(raw)
        if field == "welcomed":
            data[field] = False
        elif field == "themes":
            data[field] = ["dark", "light", "space"]
        elif field == "paid_us":
            data[field] += HOUR
        elif field == "months":
            data[field][0]["claimed"] = []
        payload = json.dumps(data)
        if field == "duplicate":
            payload = raw.replace('"welcomed": true', '"welcomed": true, "welcomed": true')
        if field == "json":
            payload = "{"
        db.execute("UPDATE garden_achievement_state SET payload=?", (payload,))
        if field == "missing":
            db.execute("DELETE FROM garden_achievement_state")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        GardenStore(path)
    assert path.read_bytes() == before


def test_old_completed_task_and_old_work_are_not_credited_from_baseline(pair):
    source, garden, _, task, now = pair
    complete(source, task)
    source.add_focus_record(task.id, NOW - timedelta(hours=5), NOW, NOW)
    source, garden, service, task, now = enable(pair)
    progress = garden.achievement_snapshot()
    assert garden.snapshot().balance == 100
    assert progress.roots == () and progress.paid_us == 0 and not progress.first_task


def test_first_welcome_failure_rolls_back_window_achievement_and_money(pair):
    source, garden, _, task, now = pair
    service = GardenEventService(source, garden, award_achievements=True)
    garden._db.execute(
        "CREATE TRIGGER fail_welcome BEFORE INSERT ON garden_receipts "
        "BEGIN SELECT RAISE(ABORT,'fake'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        service.begin(NOW)
    assert garden.snapshot().balance == 0 and not garden.reward_snapshot().enabled
    assert garden.achievement_snapshot() == AchievementState()
    assert garden.source_snapshot()["seq"] == 0
    garden._db.execute("DROP TRIGGER fail_welcome")
    service.begin(NOW)
    assert garden.snapshot().balance == 100 and service.drain() == 0


def test_both_monthly_lines_pay_only_1000_and_permanent_openings_survive_new_month():
    state, coins = AchievementState(), 0
    for i in range(30):
        reward = advance_achievements(
            state,
            enabled=True,
            paid_us=30 * HOUR if i == 0 else 0,
            now_us=instant_us(NOW),
            zone=ZoneInfo("UTC"),
            after=Task(title=str(i), completed=True),
        )
        state, coins = reward.state, coins + reward.coins
    assert coins == 1400  # 400 onboarding + 1000 monthly; no theme currency.
    assert state.themes == ("dark", "jungle", "light", "space")
    reward = advance_achievements(
        state,
        enabled=True,
        paid_us=0,
        now_us=instant_us(datetime(2026, 12, 1, tzinfo=UTC)),
        zone=ZoneInfo("UTC"),
    )
    assert reward.coins == 0 and reward.state.themes == state.themes


def test_partial_five_minutes_are_counted_in_month_when_they_are_paid(pair):
    source, garden, service, task, now = enable(pair)
    now[0] = datetime(2026, 11, 30, 23, 58, tzinfo=UTC)
    work(source, task, now, 2)
    service.drain()
    assert garden.achievement_snapshot().paid_us == 0
    work(source, task, now, 3)
    service.drain()
    progress = garden.achievement_snapshot()
    assert len(progress.months) == 1 and progress.months[0].month == "2026-12"
    assert progress.paid_us == 5 * 60_000_000 and garden.snapshot().balance == 110
