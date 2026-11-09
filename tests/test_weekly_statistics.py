import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from ritm.application.tasks import TaskService
from ritm.domain.calendar import instant_us
from ritm.domain.statistics import weekly_statistics
from ritm.domain.tasks import Subtask, Task
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)


def test_week_progress_uses_local_deadlines_and_equal_task_weights():
    zone = ZoneInfo("Europe/Moscow")
    boundary = datetime(2026, 11, 8, 21, tzinfo=UTC)
    done = Task(title="Готово", completed=True, deadline=boundary, difficulty=10)
    half = Task(
        title="Половина",
        deadline=boundary + timedelta(days=4),
        expected_duration=timedelta(hours=100),
        subtasks=(Subtask(title="Готово", completed=True), Subtask(title="Осталось")),
    )
    zero = Task(title="На границе", deadline=boundary + timedelta(days=7, seconds=-1))
    past = Task(title="Прошлая", deadline=boundary - timedelta(seconds=1), completed=True)
    future = Task(title="Следующая", deadline=boundary + timedelta(days=7))
    undated = Task(title="Без срока", completed=True)
    result = weekly_statistics(
        [done, half, zero, past, future, undated],
        {done.id: NOW, past.id: NOW - timedelta(days=2), undated.id: NOW},
        {},
        date(2026, 11, 15),
        zone,
    )
    assert (result.start, result.end) == (date(2026, 11, 9), date(2026, 11, 16))
    assert (result.task_count, result.progress, result.completed_count) == (3, Decimal(50), 2)
    assert future.id not in result.task_ids and past.id not in result.task_ids
    empty = weekly_statistics([undated], {}, {}, date(2026, 11, 15), zone)
    assert empty.progress is None and empty.task_count == empty.completed_count == 0
    assert empty.undated_count == 1


def test_focus_week_clips_boundaries_and_uses_actual_dst_duration():
    zone = ZoneInfo("Europe/Berlin")
    anchor = date(2026, 3, 29)
    start = datetime(2026, 3, 22, 23, 55, tzinfo=zone)
    monday = datetime(2026, 3, 23, 0, 10, tzinfo=zone)
    saturday = datetime(2026, 3, 28, 23, 30, tzinfo=zone)
    sunday = datetime(2026, 3, 29, 3, 30, tzinfo=zone)
    sunday_late = datetime(2026, 3, 29, 23, 45, tzinfo=zone)
    next_week = datetime(2026, 3, 30, 0, 20, tzinfo=zone)
    task = Task(title="Работа без дедлайна")
    result = weekly_statistics(
        [task],
        {},
        {
            task.id: [
                (instant_us(start), instant_us(monday)),
                (instant_us(saturday), instant_us(sunday)),
                (instant_us(sunday_late), instant_us(next_week)),
            ]
        },
        anchor,
        zone,
    )
    minute = 60_000_000
    assert result.daily_focus == tuple(x * minute for x in (10, 0, 0, 0, 0, 30, 165))
    assert result.task_focus[task.id] == 205 * minute and result.task_count == 0


def test_v5_migration_and_completion_transition_dates(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    old = Task(title="Выполнена до обновления", completed=True)
    step = Subtask(title="Шаг")
    task = Task(title="Новая", subtasks=(step,))
    now = NOW
    with TaskStore(path) as store:
        store.create_task(old)
        store.create_task(task)
        store.add_focus_record(task.id, now - timedelta(minutes=10), now, now)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE task_completion_dates")
        db.execute("DROP TABLE attachments")
        db.execute("PRAGMA user_version=5")
    with TaskStore(path, clock=lambda: now) as store:
        service = TaskService(store)
        assert store.get_task(old.id) == old and not store.list_completion_dates()
        assert len(store.list_focus_sessions()) == 1
        service.set_completed(task.id, step.id, True)
        assert store.list_completion_dates() == {task.id: NOW}
        now += timedelta(days=7)
        store.update_task(task.id, lambda x: replace(x, deadline=now))
        assert store.list_completion_dates() == {task.id: NOW}
        entry = service.delete_node(task.id, task.id, now)
        service.restore(UUID(entry))
        assert store.list_completion_dates() == {task.id: NOW}
        entry = service.delete_node(old.id, old.id, now)
        service.restore(UUID(entry))
        assert store.list_completion_dates() == {task.id: NOW}
        service.set_completed(task.id, task.id, False)
        assert not store.list_completion_dates()
        service.set_completed(task.id, task.id, True)
        assert store.list_completion_dates() == {task.id: now}
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_statistics_gui_updates_and_navigates(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "TZ": "UTC",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ui_statistics_scenario.py")), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
        assert warning not in result.stderr, result.stderr
