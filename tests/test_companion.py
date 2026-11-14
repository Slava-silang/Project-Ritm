import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QRect

from ritm.platform.companion import fit_position
from ritm.storage import companion
from ritm.storage.companion import DEFAULT, load_companion, pet_asset, save_companion


def test_companion_preferences_restart_and_atomic_failure(tmp_path, monkeypatch):
    assert load_companion(tmp_path) == DEFAULT
    values = dict(DEFAULT, enabled=True, pet="cat", x=-100, y=50, size=288)
    save_companion(tmp_path, values)
    assert load_companion(tmp_path) == values
    before = (tmp_path / "companion.json").read_bytes()

    def fail(*_):
        raise OSError("full")

    monkeypatch.setattr(companion.os, "replace", fail)
    with pytest.raises(OSError):
        save_companion(tmp_path, dict(values, enabled=False))
    assert (tmp_path / "companion.json").read_bytes() == before
    assert len(list(tmp_path.iterdir())) == 1
    for pet in ("dragon", "cat", "dog"):
        assert pet_asset(pet).is_file()
    with pytest.raises(ValueError):
        pet_asset("../../private")


@pytest.mark.parametrize(
    "update",
    [{"pet": "unknown"}, {"size": True}, {"size": 100}, {"x": 1}, {"enabled": 1}, {"muted": None}],
)
def test_companion_invalid_settings(tmp_path, update):
    with pytest.raises(ValueError):
        save_companion(tmp_path, dict(DEFAULT, **update))
    assert not (tmp_path / "companion.json").exists()


def test_corrupt_settings_preserved_and_monitor_clamping(tmp_path):
    path = tmp_path / "companion.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        load_companion(tmp_path)
    assert path.read_text() == "broken"
    screens = [QRect(0, 20, 1920, 1060), QRect(-1280, 0, 1280, 1024)]
    assert fit_position(-1100, 50, 224, screens) == (-1100, 50)
    assert fit_position(9000, 5000, 224, screens) == (1696, 856)
    assert fit_position(-1100, 50, 224, screens[:1]) == (0, 50)
    assert fit_position(100, 100, 352, [QRect(0, 0, 200, 180)]) == (0, 0)


def test_companion_window_controls_and_restart(tmp_path):
    environment = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    for mode in ("run", "restart"):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("ui_companion_scenario.py")),
                str(tmp_path),
                mode,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
            assert warning not in result.stderr, result.stderr
    # Настоящий entrypoint также обязан завершиться от одного SIGINT.
    data = json.loads((tmp_path / "companion.json").read_text())
    save_companion(tmp_path, dict(data, enabled=True))
    process = subprocess.Popen(
        [sys.executable, "-m", "ritm", "--profile", str(tmp_path)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(1.5)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=12)
        assert process.returncode == 0, stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
