import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.calendar import instant_us
from ritm.domain.focus import focus_by_day
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore

START = datetime(2026, 11, 9, 23, 59, tzinfo=UTC)
MINUTE = 60_000_000


def test_pause_overtime_and_history_survive_reopen(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    task = Task(title="Лабораторная")
    with TaskStore(path) as store:
        store.create_task(task)
        store.change_focus("start", START, None, task_id=task.id, target_us=MINUTE)
        (session,) = store.list_focus_sessions()
        key = str(session.id)
        store.change_focus("pause", START + timedelta(seconds=20), (key, 0))
        with pytest.raises(ValueError, match="изменилась"):
            store.change_focus("pause", START, (key, 0))

    with TaskStore(path) as store:
        (session,) = store.list_focus_sessions()
        assert session.elapsed(instant_us(START + timedelta(days=1))) == 20_000_000
        assert session.running_since_us is None
        resume = START + timedelta(minutes=5)
        store.change_focus("resume", resume, (key, 1))
        reached = resume + timedelta(seconds=40)
        (session,) = store.list_focus_sessions()
        assert session.target_reached(instant_us(reached))
        store.change_focus("overtime", reached, (key, 2))
        (session,) = store.list_focus_sessions()
        assert not session.target_reached(instant_us(reached))
        store.change_focus("finish", reached + timedelta(seconds=15), (key, 3))
        (session,) = store.list_focus_sessions()
        assert session.finished_us is not None
        assert session.worked_us == 75_000_000
        assert store.get_task(task.id) == task
        trash = TaskService(store).delete_node(task.id, task.id)
        assert store.list_focus_sessions() == (session,)
        TaskService(store).restore(UUID(trash))
        assert store.list_focus_sessions() == (session,)

    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT starts_us, ends_us FROM focus_intervals ORDER BY position"
        ).fetchall() == [
            (instant_us(START), instant_us(START + timedelta(seconds=20))),
            (instant_us(resume), instant_us(reached + timedelta(seconds=15))),
        ]


def test_stale_start_and_failed_replacement_keep_current_session(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    first, second = Task(title="Первая"), Task(title="Вторая")
    with TaskStore(path) as a, TaskStore(path) as b:
        a.create_task(first)
        a.create_task(second)
        a.change_focus("start", START, None, task_id=first.id)
        with pytest.raises(ValueError, match="изменилась"):
            b.change_focus("start", START, None, task_id=second.id)
        (original,) = a.list_focus_sessions()
        expected = (str(original.id), original.revision)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TRIGGER fail_start BEFORE INSERT ON focus_intervals "
                "BEGIN SELECT RAISE(ABORT, 'failed interval'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="failed interval"):
            b.change_focus("start", START, expected, task_id=second.id)
        assert a.list_focus_sessions() == (original,)
        with sqlite3.connect(path) as db:
            db.execute("DROP TRIGGER fail_start")
        # A backward wall-clock adjustment must never record negative work.
        b.change_focus("start", START - timedelta(seconds=1), expected, task_id=second.id)
        sessions = a.list_focus_sessions()
        assert [(x.task_id, x.finished_us, x.worked_us) for x in sessions] == [
            (first.id, instant_us(START), 0),
            (second.id, None, 0),
        ]


def run_focus_gui(tmp_path, modes):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    script = Path(__file__).with_name("ui_focus_scenario.py")
    for mode in modes:
        result = subprocess.run(
            [sys.executable, str(script), str(tmp_path), mode],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
            assert warning not in result.stderr, result.stderr


def test_focus_card_and_global_controls(tmp_path):
    run_focus_gui(tmp_path, ("run", "reopen"))


def test_focus_recovery_and_editing_in_gui(tmp_path):
    run_focus_gui(tmp_path, ("interrupt", "recover", "verify_history"))


def test_recovery_uses_last_mark_and_rejects_invalid_end(tmp_path):
    task = Task(title="Сессия")
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.create_task(task)
        store.change_focus("start", START, None, task_id=task.id)
        (session,) = store.list_focus_sessions()
        expected = (str(session.id), 0)
        now = START + timedelta(hours=1)
        for invalid in (START - timedelta(seconds=1), now + timedelta(seconds=1)):
            with pytest.raises(ValueError, match="Окончание"):
                store.change_focus("recover", now, expected, recovered_end=invalid)
        store.change_focus("pause", START + timedelta(seconds=30), expected)
        store.change_focus("resume", START + timedelta(minutes=1), (str(session.id), 1))
        store.change_focus("recover", now, (str(session.id), 2))
        (finished,) = store.list_focus_sessions()
        assert finished.finished_us == instant_us(START + timedelta(minutes=1))
        assert finished.worked_us == 30_000_000


def test_edit_preserves_pauses_and_rejects_stale_revision(tmp_path):
    task = Task(title="Два рабочих интервала")
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.create_task(task)
        store.change_focus("start", START, None, task_id=task.id)
        (session,) = store.list_focus_sessions()
        key = str(session.id)
        store.change_focus("pause", START + timedelta(seconds=30), (key, 0))
        resume = START + timedelta(minutes=2)
        store.change_focus("resume", resume, (key, 1))
        store.change_focus("finish", resume + timedelta(minutes=1), (key, 2))
        now = START + timedelta(hours=1)
        with pytest.raises(ValueError, match="будущее"):
            store.edit_focus_duration(session.id, 3, MINUTE * 61, now)
        store.edit_focus_duration(session.id, 3, MINUTE, now)
        with pytest.raises(ValueError, match="изменилась"):
            store.edit_focus_duration(session.id, 3, MINUTE * 2, now)
        store.edit_focus_duration(session.id, 4, MINUTE * 2, now)
        intervals = store.list_finished_focus_intervals()[session.id]
        assert intervals == [
            (instant_us(START), instant_us(START) + 30_000_000),
            (instant_us(resume), instant_us(resume) + 90_000_000),
        ]
        assert focus_by_day(intervals, UTC) == {
            START.date(): 30_000_000,
            resume.date(): 90_000_000,
        }
        store.edit_focus_duration(session.id, 5, 15_000_000, now)
        assert store.list_finished_focus_intervals()[session.id] == [
            (instant_us(START), instant_us(START) + 15_000_000)
        ]


def test_day_split_uses_actual_length_of_dst_days():
    zone = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 3, 28, 23, 30, tzinfo=zone)
    end = datetime(2026, 3, 30, 0, 15, tzinfo=zone)
    assert list(focus_by_day([(instant_us(start), instant_us(end))], zone).values()) == [
        MINUTE * 30,
        MINUTE * 60 * 23,
        MINUTE * 15,
    ]
