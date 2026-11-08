import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Task
from ritm.ui.calendar import calendar_view, local_instant, period_days, shift_period


def test_week_and_month_cover_full_weeks_across_boundaries():
    assert period_days(date(2026, 12, 31), "week") == tuple(
        date(2026, 12, 28) + timedelta(days=i) for i in range(7)
    )
    february = period_days(date(2028, 2, 29), "month")
    assert february[0].weekday() == 0 and february[-1].weekday() == 6
    assert date(2028, 2, 29) in february
    assert shift_period(date(2026, 12, 31), "month", 1) == date(2027, 1, 1)
    assert shift_period(date(2027, 1, 31), "month", 1) == date(2027, 2, 1)
    assert shift_period(date(2027, 1, 1), "month", -1) == date(2026, 12, 1)
    assert shift_period(date(2026, 12, 31), "week", 1) == date(2027, 1, 7)


def test_local_input_handles_ambiguous_missing_and_explicit_offset():
    berlin = ZoneInfo("Europe/Berlin")
    with pytest.raises(ValueError, match="дважды"):
        local_instant("25.10.2026 02:30", berlin)
    with pytest.raises(ValueError, match="не существует"):
        local_instant("29.03.2026 02:30", berlin)
    assert local_instant("25.10.2026 02:30 +02:00", berlin) == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )
    assert local_instant("25.10.2026 02:30 +01:00", berlin) == datetime(
        2026, 10, 25, 1, 30, tzinfo=UTC
    )


def test_overnight_segments_and_separate_deadline_mark():
    task = Task(title="Ночная работа", deadline=datetime(2027, 1, 1, 0, 30, tzinfo=UTC))
    block = WorkBlock(
        task_id=task.id,
        starts_at=datetime(2026, 12, 31, 23, tzinfo=UTC),
        ends_at=datetime(2027, 1, 1, 1, tzinfo=UTC),
    )
    view = calendar_view(
        (task,),
        (block,),
        mode="week",
        anchor=date(2026, 12, 31),
        selected=date(2027, 1, 1),
        today=date(2026, 12, 31),
        zone=UTC,
    )
    previous = next(day for day in view["days"] if day["date"] == "2026-12-31")
    current = next(day for day in view["days"] if day["date"] == "2027-01-01")
    assert previous["entries"][0]["time"] == "23:00–24:00"
    assert current["entries"][0]["time"] == "00:00–01:00"
    assert current["blockCount"] == 1 and current["deadlineCount"] == 1
    assert current["entries"][0]["duration"] == "2 ч"
    assert current["entries"][0]["segmentDuration"] == "1 ч"
    assert current["entries"][0]["warning"] == "Выходит за дедлайн"
    assert previous["today"] and current["selected"]
    empty = calendar_view(
        (task,),
        (),
        mode="month",
        anchor=date(2027, 1, 1),
        selected=date(2027, 1, 1),
        today=date(2027, 1, 1),
        zone=UTC,
    )
    assert sum(day["blockCount"] for day in empty["days"]) == 0
    assert sum(day["deadlineCount"] for day in empty["days"]) == 1


def test_dst_day_length_and_midnight_end_are_not_duplicated():
    berlin = ZoneInfo("Europe/Berlin")
    task = Task(title="День перевода часов")
    block = WorkBlock(
        task_id=task.id,
        starts_at=datetime(2026, 3, 29, tzinfo=berlin),
        ends_at=datetime(2026, 3, 30, tzinfo=berlin),
    )
    view = calendar_view(
        (task,),
        (block,),
        mode="week",
        anchor=date(2026, 3, 29),
        selected=date(2026, 3, 29),
        today=date(2026, 3, 29),
        zone=berlin,
    )
    assert sum(day["blockCount"] for day in view["days"]) == 1
    assert view["selectedEvents"][0]["segmentDuration"] == "23 ч"
    assert view["selectedEvents"][0]["time"] == "00:00–24:00"


def test_calendar_qml_and_restart(tmp_path):
    env = os.environ.copy()
    env.update(
        QT_QPA_PLATFORM=os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        QT_QUICK_BACKEND="software",
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    script = str(Path(__file__).with_name("ui_calendar_scenario.py"))
    result = subprocess.run(
        [sys.executable, script, str(tmp_path)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    identifier = json.loads(result.stdout)["taskId"]
    reopened = subprocess.run(
        [sys.executable, script, str(tmp_path), "--reopen", identifier],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=25,
    )
    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    assert '"reopened": true' in reopened.stdout
    for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
        assert warning not in result.stderr + reopened.stderr, result.stderr + reopened.stderr
