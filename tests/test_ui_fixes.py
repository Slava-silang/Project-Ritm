import os
import subprocess
import sys
from pathlib import Path


def test_completed_tasks_are_not_offered_for_new_blocks(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ui_fixes_scenario.py")), str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
        assert warning not in result.stderr, result.stderr


def test_date_time_picker_and_persisted_selection(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ui_picker_scenario.py")), str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
        assert warning not in result.stderr, result.stderr
