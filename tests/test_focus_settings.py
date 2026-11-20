import os
import subprocess
import sys
from pathlib import Path

import pytest

from ritm.storage import focus_settings


def test_default_and_saved_values(tmp_path):
    assert focus_settings.load_focus_settings(tmp_path) is False
    for value in (True, False):
        assert focus_settings.save_focus_settings(tmp_path, value) is value
        assert focus_settings.load_focus_settings(tmp_path) is value
    with pytest.raises(ValueError):
        focus_settings.save_focus_settings(tmp_path, 1)


def test_failed_save_preserves_previous_file(tmp_path, monkeypatch):
    focus_settings.save_focus_settings(tmp_path, True)
    path = tmp_path / "focus-settings.json"
    previous = path.read_bytes()

    def fail(*args):
        raise OSError("test failure")

    monkeypatch.setattr(focus_settings.os, "replace", fail)
    with pytest.raises(OSError):
        focus_settings.save_focus_settings(tmp_path, False)
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "payload",
    ["broken", "[]", "{}", '{"pause_on_system":1}', '{"pause_on_system":true,"extra":false}'],
)
def test_corrupt_settings_are_not_overwritten(tmp_path, payload):
    path = tmp_path / "focus-settings.json"
    path.write_text(payload)
    with pytest.raises(ValueError, match="Автопауза выключена"):
        focus_settings.load_focus_settings(tmp_path)
    assert path.read_text() == payload


def test_sleep_policy_gui_and_restart(tmp_path):
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
                str(Path(__file__).with_name("ui_focus_settings_scenario.py")),
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
