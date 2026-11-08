import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from ritm.application.calendar import CalendarService
from ritm.application.tasks import TaskService
from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.calendar import calendar_view, change_block

START = datetime(2026, 11, 9, 10, tzinfo=UTC)


def original():
    return WorkBlock(task_id=uuid4(), starts_at=START, ends_at=START + timedelta(hours=1))


def test_elapsed_move_and_resize_keep_identity_and_duration():
    block = original()
    moved = change_block(block, {"operation": "move", "minutes": 30}, UTC)
    assert moved.id == block.id and moved.task_id == block.task_id
    assert moved.starts_at == START + timedelta(minutes=30)
    assert moved.duration == block.duration
    resized = change_block(moved, {"operation": "resize", "minutes": 15}, UTC)
    assert resized.starts_at == moved.starts_at
    assert resized.duration == timedelta(minutes=75)
    with pytest.raises(ValueError):
        change_block(block, {"operation": "resize", "minutes": -60}, UTC)
    with pytest.raises(ValueError):
        change_block(block, {"operation": "move", "minutes": 7}, UTC)


def test_day_move_keeps_local_clock_precision_and_elapsed_duration():
    zone = ZoneInfo("Europe/Berlin")
    block = WorkBlock(
        task_id=uuid4(),
        starts_at=datetime(2026, 3, 28, 10, 25, 17, 123, tzinfo=zone),
        ends_at=datetime(2026, 3, 28, 11, 25, 17, 123, tzinfo=zone),
    )
    moved = change_block(block, {"operation": "day", "day": "2026-03-29"}, zone)
    assert moved.starts_at.astimezone(zone).hour == 10
    assert moved.starts_at.astimezone(zone).minute == 25
    assert moved.starts_at.second == 17 and moved.starts_at.microsecond == 123
    assert moved.starts_at - block.starts_at == timedelta(hours=23)
    assert moved.duration == block.duration


def test_day_move_dst_gap_rejected_and_ambiguous_offset_preserved():
    zone = ZoneInfo("Europe/Berlin")
    block = WorkBlock(
        task_id=uuid4(),
        starts_at=datetime(2026, 3, 28, 2, 30, tzinfo=zone),
        ends_at=datetime(2026, 3, 28, 3, 30, tzinfo=zone),
    )
    with pytest.raises(ValueError, match="не существует"):
        change_block(block, {"operation": "day", "day": "2026-03-29"}, zone)
    fall = replace(
        block,
        starts_at=datetime(2026, 10, 24, 2, 30, tzinfo=zone),
        ends_at=datetime(2026, 10, 24, 3, 30, tzinfo=zone),
    )
    moved = change_block(fall, {"operation": "day", "day": "2026-10-25"}, zone)
    assert moved.starts_at.astimezone(zone).fold == 0
    later = replace(
        fall,
        starts_at=datetime(2026, 10, 26, 2, 30, tzinfo=zone),
        ends_at=datetime(2026, 10, 26, 3, 30, tzinfo=zone),
    )
    assert (
        change_block(later, {"operation": "day", "day": "2026-10-25"}, zone)
        .starts_at.astimezone(zone)
        .fold
        == 1
    )


def test_timeline_is_elapsed_day_with_distinct_repeated_hours():
    zone = ZoneInfo("Europe/Berlin")
    task = Task(title="Перевод часов")
    day = date(2026, 10, 25)
    block = WorkBlock(
        task_id=task.id,
        starts_at=datetime(2026, 10, 25, 2, 30, fold=0, tzinfo=zone),
        ends_at=datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=zone),
    )
    view = calendar_view(
        (task,), (block,), mode="week", anchor=day, selected=day, today=day, zone=zone
    )
    assert view["timelineMinutes"] == 1500
    assert [x["label"] for x in view["ticks"] if x["label"].startswith("02:00")] == [
        "02:00 +0200",
        "02:00 +0100",
    ]
    assert view["ticks"][-1]["label"] == "24:00"
    assert view["timeline"][0]["segmentMinutes"] == 60
    moved = change_block(block, {"operation": "move", "minutes": 60}, zone)
    assert moved.starts_at.astimezone(zone).fold == 1


def test_partial_overnight_segments_expose_only_correct_handles():
    task = Task(title="Ночь")
    block = WorkBlock(
        task_id=task.id,
        starts_at=datetime(2026, 11, 9, 23, tzinfo=UTC),
        ends_at=datetime(2026, 11, 10, 1, tzinfo=UTC),
    )
    for day, move, resize in ((date(2026, 11, 9), True, False), (date(2026, 11, 10), False, True)):
        view = calendar_view(
            (task,), (block,), mode="week", anchor=day, selected=day, today=day, zone=UTC
        )
        assert view["timeline"][0]["canMove"] is move
        assert view["timeline"][0]["canResize"] is resize


def test_exact_fields_preserve_unchanged_subminute_endpoint():
    block = replace(original(), starts_at=START + timedelta(microseconds=7))
    entry = calendar_view(
        (Task(id=block.task_id, title="Точность"),),
        (block,),
        mode="week",
        anchor=START.date(),
        selected=START.date(),
        today=START.date(),
        zone=UTC,
    )["timeline"][0]
    changed = change_block(
        block, {"operation": "fields", "start": entry["startText"], "end": "09.11.2026 12:00"}, UTC
    )
    assert changed.starts_at == block.starts_at
    assert changed.ends_at == START + timedelta(hours=2)


def test_stale_expected_version_is_rejected_atomically(tmp_path):
    block = original()
    task = Task(id=block.task_id, title="Не перезаписать")
    with TaskStore(tmp_path / "db.sqlite3") as store:
        TaskService(store).create_task(task)
        service = CalendarService(store)
        service.create_block(block)
        first = replace(block, ends_at=block.ends_at + timedelta(hours=1))
        service.update_block(first, expected=block)
        with pytest.raises(ValueError, match="уже изменился"):
            service.update_block(
                replace(block, starts_at=START + timedelta(minutes=15)), expected=block
            )
        assert store.get_work_block(block.id) == first


def test_pointer_gui_changes_and_failure_rollback(tmp_path):
    env = os.environ.copy()
    env.update(
        QT_QPA_PLATFORM=os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        QT_QUICK_BACKEND="software",
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    script = str(Path(__file__).with_name("ui_drag_scenario.py"))
    result = subprocess.run(
        [sys.executable, script, str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    reopened = subprocess.run(
        [sys.executable, script, str(tmp_path), "--reopen", data["blockId"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    assert '"reopened": true' in reopened.stdout
    for message in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
        assert message not in result.stderr + reopened.stderr, result.stderr + reopened.stderr


def test_dropping_overnight_continuation_moves_whole_block_by_day_difference():
    block = WorkBlock(
        task_id=uuid4(),
        starts_at=datetime(2026, 12, 31, 23, tzinfo=UTC),
        ends_at=datetime(2027, 1, 1, 1, tzinfo=UTC),
    )
    moved = change_block(
        block, {"operation": "day", "sourceDay": "2027-01-01", "day": "2027-01-02"}, UTC
    )
    assert moved.starts_at == datetime(2027, 1, 1, 23, tzinfo=UTC)
    assert moved.ends_at == datetime(2027, 1, 2, 1, tzinfo=UTC)
    assert moved.id == block.id
