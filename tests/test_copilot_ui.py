import os
import subprocess
import sys
from pathlib import Path


def test_copilot_account_and_provider_switch_gui(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{
            f"XDG_{kind}_HOME": str(tmp_path / kind.lower()) for kind in ("DATA", "CACHE", "CONFIG")
        },
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ui_copilot_scenario.py")), str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
        assert warning not in result.stderr, result.stderr
