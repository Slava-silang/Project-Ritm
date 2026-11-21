import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["garden", "pomodoro", "garden-close"])
def test_background_checkpoint_keeps_ui_stable_and_queues_user_action(tmp_path, mode):
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("ui_focus_stability_scenario.py")),
            str(tmp_path),
            mode,
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
            "QT_QUICK_BACKEND": "software",
            **{f"XDG_{k}_HOME": str(tmp_path / k.lower()) for k in ("CACHE", "DATA", "CONFIG")},
        },
        capture_output=True,
        text=True,
        timeout=60,
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
