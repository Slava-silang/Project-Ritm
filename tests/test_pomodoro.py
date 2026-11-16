import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ritm.domain.calendar import instant_us
from ritm.domain.companion import Reactions
from ritm.domain.pomodoro import Pomodoro
from ritm.domain.tasks import Task
from ritm.storage import backup
from ritm.storage.companion import DEFAULT
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore

START = datetime(2026, 11, 16, 10, tzinfo=UTC)


def key(store):
    focus = store.active_focus()
    return (str(focus.id), focus.revision) if focus else None


def command(store, action, now, **kwargs):
    store.change_pomodoro(action, now, store.get_pomodoro().revision, key(store), **kwargs)


def elapsed(store, now):
    return store.get_pomodoro().elapsed(instant_us(now), store.active_focus())


@pytest.mark.parametrize(
    "field,value",
    [
        ("work", 0),
        ("short", 241),
        ("long", True),
        ("cycle", 4),
        ("elapsed_us", -1),
        ("phase", "bad"),
    ],
)
def test_invalid_pomodoro_settings(field, value):
    with pytest.raises(ValueError):
        replace(Pomodoro(), **{field: value})


def test_four_intervals_overtime_pauses_switching_and_breaks(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        first, second = Task(title="Первая"), Task(title="Вторая")
        store.create_task(first)
        store.create_task(second)
        command(store, "start", START, task_id=first.id, work=1, short=1, long=2)
        store.change_focus("pause", START + timedelta(seconds=20), key(store))
        assert elapsed(store, START + timedelta(minutes=30)) == 20_000_000
        store.change_focus("resume", START + timedelta(minutes=30), key(store))
        now = START + timedelta(minutes=30, seconds=25)
        store.change_focus("start", now, key(store), task_id=second.id)
        assert elapsed(store, now) == 45_000_000
        now += timedelta(seconds=30)
        assert elapsed(store, now) == 75_000_000
        command(store, "break", now)
        assert store.active_focus() is None
        assert store.get_pomodoro().phase == "short"
        assert sum(s.worked_us for s in store.list_focus_sessions()) == 75_000_000
        with pytest.raises(ValueError, match="Перерыв"):
            command(store, "work", now, task_id=first.id)
        with pytest.raises(ValueError, match="перерыв"):
            store.change_focus("start", now, None, task_id=first.id)
        now += timedelta(seconds=20)
        command(store, "pause", now)
        now += timedelta(hours=1)
        assert elapsed(store, now) == 20_000_000
        command(store, "resume", now)
        now += timedelta(seconds=40)
        for index in range(2, 5):
            command(store, "work", now, task_id=first.id)
            assert store.get_pomodoro().cycle == index - 1
            now += timedelta(minutes=1)
            command(store, "break", now)
            assert store.get_pomodoro().phase == ("long" if index == 4 else "short")
            now += timedelta(minutes=2)
        assert store.get_pomodoro().cycle == 0
        assert sum(s.worked_us for s in store.list_focus_sessions()) == 255_000_000
        command(store, "work", now, task_id=second.id)
        assert store.get_pomodoro().phase == "work"
        assert elapsed(store, now) == 0
        command(store, "stop", now + timedelta(seconds=10))
        assert store.get_pomodoro().phase == "idle"
        assert store.get_pomodoro().work == 1
        assert store.active_focus() is None


def test_restart_break_and_confirmed_focus_recovery(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    task = Task(title="Восстановление")
    with TaskStore(path) as store:
        store.create_task(task)
        command(store, "start", START, task_id=task.id, work=1, short=1)
    with TaskStore(path) as store:
        assert (
            store.get_pomodoro().elapsed(
                instant_us(START + timedelta(days=1)), store.active_focus(), recovering=True
            )
            == 0
        )
        store.change_focus(
            "recover",
            START + timedelta(hours=2),
            key(store),
            recovered_end=START + timedelta(seconds=35),
        )
        assert elapsed(store, START + timedelta(hours=2)) == 35_000_000
        now = START + timedelta(hours=2)
        command(store, "work", now, task_id=task.id)
        now += timedelta(seconds=25)
        command(store, "break", now)
        store.checkpoint_pomodoro(now + timedelta(seconds=17))
    with TaskStore(path) as store:
        store.checkpoint_pomodoro(now + timedelta(days=1), startup=True)
        state = store.get_pomodoro()
        assert state.running_since_us is None
        assert elapsed(store, now + timedelta(days=1)) == 17_000_000
        command(store, "resume", now + timedelta(days=1))
        store.checkpoint_pomodoro(now + timedelta(days=1, seconds=4), pause=True)
    with TaskStore(path) as store:
        assert elapsed(store, now + timedelta(days=2)) == 21_000_000


def test_atomic_rollback_and_stale_actions(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        task = Task(title="Атомарность")
        store.create_task(task)
        command(store, "start", START, task_id=task.id, work=1)
        state = store.get_pomodoro()
        focus = store.active_focus()
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TRIGGER fail_pomo BEFORE UPDATE ON pomodoro "
                "BEGIN SELECT RAISE(ABORT,'fail'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            command(store, "break", START + timedelta(minutes=1))
        assert store.get_pomodoro() == state
        assert store.active_focus() == focus
        with sqlite3.connect(path) as db:
            db.execute("DROP TRIGGER fail_pomo")
        command(store, "break", START + timedelta(minutes=1))
        with pytest.raises(ValueError, match="изменились"):
            store.change_pomodoro("break", START, state.revision, (str(focus.id), focus.revision))
        with pytest.raises(KeyError):
            command(store, "work", START + timedelta(hours=1), task_id=Task(title="Нет").id)
        assert store.get_pomodoro().phase == "short"
        assert store.active_focus() is None


def test_v10_migration_preserves_tasks_and_focus(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    task = Task(title="Старая база")
    with TaskStore(path) as store:
        store.create_task(task)
        store.change_focus("start", START, None, task_id=task.id)
        original = store.active_focus()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE mail_proposals")
        db.execute("DROP TABLE source_links")
        db.execute("DROP TABLE pomodoro")
        db.execute("PRAGMA user_version=10")
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task
        assert store.active_focus() == original
        assert store.get_pomodoro() == Pomodoro()
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_reactions_suppress_regular_breaks_and_announce_timer_once():
    reactions = Reactions()
    options = {**DEFAULT, "break_minutes": 5}
    focus = {"elapsed": "00:00:00", "paused": False}
    reactions.update(focus, 0, options, True, pomodoro=True)
    focus["elapsed"] = "00:06:00"
    reactions.update(focus, 360, options, True, pomodoro=True)
    assert not reactions.text
    reactions.pomodoro_end("work", 360, options, True)
    assert "Pomodoro" in reactions.text
    reactions.update(focus, 361, options, True, pomodoro=True)
    assert "Pomodoro" in reactions.text
    reactions.pomodoro_end("short", 362, {**options, "muted": True}, True)
    reactions.update(focus, 363, {**options, "muted": True}, True, pomodoro=True)
    assert not reactions.text


def test_break_clock_backwards_and_corrupt_storage(tmp_path):
    state = Pomodoro(phase="short", elapsed_us=10_000_000, running_since_us=instant_us(START))
    assert state.elapsed(instant_us(START - timedelta(hours=1))) == 10_000_000
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store._db.execute("INSERT INTO pomodoro VALUES (1, '{}')")
        store._db.commit()
        with pytest.raises(ValueError, match="повреждены"):
            store.get_pomodoro()
        store._db.execute("UPDATE pomodoro SET payload='broken'")
        store._db.commit()
        with pytest.raises(ValueError, match="повреждены"):
            command(store, "start", START)
        assert store._db.execute("SELECT payload FROM pomodoro").fetchone()[0] == "broken"


def test_pomodoro_gui_and_restart(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{key}_HOME": str(tmp_path / key.lower()) for key in ("DATA", "CACHE", "CONFIG")},
    }
    for mode in ("run", "reopen"):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("ui_pomodoro_scenario.py")),
                str(tmp_path),
                mode,
            ],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
            assert warning not in result.stderr, result.stderr


def test_backup_preserves_cycle_and_restores_v10(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    task = Task(title="Копия цикла")
    with TaskStore(path) as store:
        store.create_task(task)
        command(store, "start", START, task_id=task.id, work=1, short=2)
        command(store, "break", START + timedelta(minutes=1))
        store.checkpoint_pomodoro(START + timedelta(minutes=1, seconds=12), pause=True)
        state = store.get_pomodoro()
    archive = backup.create_backup(tmp_path, tmp_path / "copies", START)
    with TaskStore(path) as store:
        command(store, "stop", START + timedelta(minutes=2))
    preview = backup.inspect_backup(archive)
    backup.restore_backup(tmp_path, archive, preview["token"])
    with TaskStore(path) as store:
        assert store.get_pomodoro() == state
        assert store.get_task(task.id) == task
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE mail_proposals")
        db.execute("DROP TABLE source_links")
        db.execute("DROP TABLE pomodoro")
        db.execute("PRAGMA user_version=10")
    old_archive = backup.create_backup(tmp_path, tmp_path / "copies", START)
    preview = backup.inspect_backup(old_archive)
    backup.restore_backup(tmp_path, old_archive, preview["token"])
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task
        assert store.get_pomodoro() == Pomodoro()
