import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ritm.application.calendar import CalendarService
from ritm.application.tasks import TaskService
from ritm.domain.calendar import WorkBlock, instant_us
from ritm.domain.tasks import Task
from ritm.storage import sqlite as storage
from ritm.storage.codec import encode_task
from ritm.storage.sqlite import (
    APPLICATION_ID,
    CALENDAR_SCHEMA,
    SCHEMA,
    SCHEMA_VERSION,
    TRASH_SCHEMA,
    TaskStore,
)
from ritm.ui.calendar import calendar_view, change_block

START = datetime(2026, 11, 9, 10, tzinfo=UTC)


def event(title="Пара"):
    return WorkBlock(title=title, starts_at=START, ends_at=START + timedelta(hours=1))


def test_event_requires_name_and_no_fake_task(tmp_path):
    for title in ("", "   ", None):
        with pytest.raises(ValueError):
            event(title)
    with pytest.raises(ValueError):
        replace(event(), task_id=uuid4())
    with TaskStore(tmp_path / "db.sqlite3") as store:
        original = event("  Прогулка с друзьями  ")
        result = CalendarService(store).create_block(original)
        assert result.block.title == "Прогулка с друзьями"
        assert result.block.task_id is None
        assert not result.warnings.after_deadline
        assert store.list_tasks() == ()
        assert store.get_work_block(original.id) == original


def test_event_and_task_conflicts_do_not_change_task_totals(tmp_path):
    task = Task(title="Лабораторная", deadline=START + timedelta(minutes=15))
    with TaskStore(tmp_path / "db.sqlite3") as store:
        TaskService(store).create_task(task)
        service = CalendarService(store)
        work = WorkBlock(task_id=task.id, starts_at=START, ends_at=START + timedelta(hours=2))
        service.create_block(work)
        original = event()
        result = service.create_block(original)
        assert result.warnings.overlapping_ids == (work.id,)
        assert not result.warnings.after_deadline
        assert service.warnings(work.id).overlapping_ids == (original.id,)
        assert service.warnings(work.id).after_deadline
        assert service.planned_time(task.id) == timedelta(hours=2)
        entry = TaskService(store).delete_node(task.id, task.id)
        assert store.list_work_blocks() == (original,)
        assert store.get_work_block(original.id) == original
        TaskService(store).restore(UUID(entry))
        assert len(store.list_work_blocks()) == 2


def test_event_edit_and_drag_preserve_id_and_title(tmp_path):
    path = tmp_path / "db.sqlite3"
    original = event()
    with TaskStore(path) as store:
        service = CalendarService(store)
        service.create_block(original)
        moved = change_block(original, {"operation": "move", "minutes": 30}, UTC)
        service.update_block(moved, expected=original)
        renamed = replace(moved, title="Пара по физике")
        service.update_block(renamed, expected=moved)
        with pytest.raises(ValueError):
            service.update_block(replace(moved, title="Устаревшее имя"), expected=moved)
        assert store.get_work_block(original.id) == renamed
    with TaskStore(path) as store:
        assert store.list_work_blocks() == (renamed,)
        assert not store.list_tasks()


def test_event_has_own_rendering_and_timeline_without_tasks():
    original = event()
    day = START.date()
    view = calendar_view(
        (), (original,), mode="week", anchor=day, selected=day, today=day, zone=UTC
    )
    assert view["selectedEvents"][0]["kind"] == "event"
    assert view["selectedEvents"][0]["title"] == "Пара"
    assert view["selectedEvents"][0]["taskId"] == ""
    assert view["timeline"][0]["isEvent"]
    assert sum(x["eventCount"] for x in view["days"]) == 1
    assert sum(x["blockCount"] for x in view["days"]) == 0


def make_v3(path):
    live, archived = Task(title="Живая"), Task(title="В корзине")
    first = WorkBlock(
        task_id=live.id, starts_at=START, ends_at=START + timedelta(hours=1, microseconds=9)
    )
    second = WorkBlock(task_id=archived.id, starts_at=START, ends_at=START + timedelta(hours=2))
    entry = uuid4()
    with sqlite3.connect(path) as db:
        for statement in SCHEMA:
            db.execute(statement)
        db.execute(TRASH_SCHEMA)
        db.execute(
            "INSERT INTO nodes(id,position,kind,title,completed,description) VALUES(?,0,0,?,0,'')",
            (str(live.id), live.title),
        )
        db.execute(
            "INSERT INTO trash VALUES (?,?,?,?,?,?,?,NULL)",
            (
                str(entry),
                str(archived.id),
                str(archived.id),
                archived.title,
                0,
                START.isoformat(),
                encode_task(archived),
            ),
        )
        for statement in CALENDAR_SCHEMA:
            db.execute(statement)
        for block in (first, second):
            db.execute(
                "INSERT INTO work_blocks VALUES (?,?,?,?)",
                (
                    str(block.id),
                    str(block.task_id),
                    instant_us(block.starts_at),
                    instant_us(block.ends_at),
                ),
            )
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA user_version=3")
    return live, archived, first, second, entry


def test_v3_migration_retains_live_and_archived_blocks(tmp_path):
    path = tmp_path / "db.sqlite3"
    live, archived, first, second, entry = make_v3(path)
    with TaskStore(path) as store:
        assert store.get_task(live.id) == live
        assert store.list_work_blocks() == (first,)
        assert TaskService(store).restore(entry) == archived
        assert store.get_work_block(second.id) == second
        CalendarService(store).create_block(event())
        assert len(store.list_work_blocks()) == 3
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_event_migration_rolls_back_all_ddl(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    make_v3(path)
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(storage, "EVENT_SCHEMA", (*storage.EVENT_SCHEMA[:4], "invalid SQL"))
        with pytest.raises(sqlite3.Error):
            TaskStore(path)
    assert path.read_bytes() == before
    with TaskStore(path) as store:
        assert len(store.list_trash()) == 1


def test_sql_rejects_unnamed_event_and_invalid_task_reference(tmp_path):
    path = tmp_path / "db.sqlite3"
    with TaskStore(path):
        pass
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO work_blocks VALUES (?,NULL,0,1,'')", (str(uuid4()),))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO work_blocks VALUES (?,?,0,1,'')", (str(uuid4()), str(uuid4())))


def test_event_gui_without_tasks_and_restart(tmp_path):
    env = os.environ.copy()
    env.update(
        QT_QPA_PLATFORM=os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        QT_QUICK_BACKEND="software",
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    script = str(Path(__file__).with_name("ui_event_scenario.py"))
    result = subprocess.run(
        [sys.executable, script, str(tmp_path)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    identifier = json.loads(result.stdout)["eventId"]
    again = subprocess.run(
        [sys.executable, script, str(tmp_path), "--reopen", identifier],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert '"reopened": true' in again.stdout
    for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
        assert warning not in result.stderr + again.stderr, result.stderr + again.stderr
