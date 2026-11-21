import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from ritm.application.garden_progress import achievement_view
from ritm.domain.garden_achievements import HOUR, AchievementState, MonthProgress


def test_empty_current_month_is_read_only_and_has_all_six_goals():
    saved = AchievementState()
    view = achievement_view(saved, "2026-12")
    period = view["periods"][0]
    assert period["month"] == "2026-12" and period["earned"] == 0
    assert period["maximum"] == 1000 and len(period["goals"]) == 6
    assert all(not g["done"] and g["ratio"] == 0 for g in period["goals"])
    assert all(not p["done"] for p in view["permanent"])
    assert saved == AchievementState()  # viewing a month cannot claim or create anything


@pytest.mark.parametrize("hours", [HOUR * 30 - HOUR // 12, HOUR * 30])
def test_saved_claims_and_permanent_unlocks_remain_distinct_from_current_month(hours):
    claims = ("focus:15", "focus:5", "tasks:15", "tasks:5")
    if hours == 30 * HOUR:
        claims = tuple(sorted((*claims, "focus:30")))
    state = AchievementState(
        welcomed=True,
        first_task=True,
        roots=tuple(sorted(str(uuid4()) for _ in range(15))),
        paid_us=hours,
        months=(MonthProgress("2026-11", 15, hours, claims),),
        themes=("dark", "jungle", "light", "space")
        if hours == 30 * HOUR
        else ("dark", "jungle", "light"),
    )
    view = achievement_view(state, "2026-12")
    current, past = view["periods"]
    assert current["earned"] == 0
    assert past["earned"] == (700 if hours == 30 * HOUR else 400)
    last = next(g for g in past["goals"] if g["key"] == "focus:30")
    assert last["done"] == (hours == 30 * HOUR)
    assert last["ratio"] == hours / (30 * HOUR)
    assert last["progress"] == ("30.00 / 30" if hours == 30 * HOUR else "29.91 / 30")
    permanent = {p["key"]: p for p in view["permanent"]}
    assert permanent["jungle"]["done"]
    assert permanent["space"]["done"] == (hours == 30 * HOUR)
    assert len(state.months) == 1


def test_garden_progress_gui_and_reopen(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{k}_HOME": str(tmp_path / k.lower()) for k in ("CACHE", "DATA", "CONFIG")},
    }
    script = Path(__file__).with_name("ui_garden_progress_scenario.py")
    for mode in ("view", "reopen", "restore"):
        result = subprocess.run(
            [sys.executable, str(script), str(tmp_path), mode],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in (
            "TypeError",
            "ReferenceError",
            "Binding loop",
            "Cannot assign",
            "failed to load",
        ):
            assert warning not in result.stderr, result.stderr
