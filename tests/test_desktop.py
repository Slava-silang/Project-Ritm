import os
import subprocess
import sys
from pathlib import Path

import pytest

from ritm.platform import autostart


def test_autostart_opt_in_profile_isolation_and_atomic_failure(tmp_path, monkeypatch):
    path = autostart.entry_path(tmp_path / "config", tmp_path / "profile")
    assert path != autostart.entry_path(tmp_path / "config", tmp_path / "other")
    assert autostart.read_autostart(path) == {"enabled": False, "hidden": False}
    assert not path.exists()
    command = ['/путь с пробелом/Ритм$`"\\%/python', "-m", "ritm", "--profile", str(tmp_path)]
    autostart.write_autostart(path, command, True, True)
    assert autostart.read_autostart(path) == {"enabled": True, "hidden": True}
    saved = path.read_bytes()
    assert b"--start-hidden" in saved
    assert b"%%" in saved

    def fail(*_):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(autostart.os, "replace", fail)
        with pytest.raises(OSError):
            autostart.write_autostart(path, command, True, False)
    assert path.read_bytes() == saved
    assert list(path.parent.iterdir()) == [path]
    autostart.write_autostart(path, command, False, False)
    assert not path.exists()
    with pytest.raises(ValueError):
        autostart.write_autostart(path, ["bad\npath"], True, False)


@pytest.mark.parametrize("mode", ["available", "unavailable"])
def test_tray_close_restore_exit_and_autostart(tmp_path, mode):
    environment = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("ui_desktop_scenario.py")),
            str(tmp_path),
            mode,
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("TypeError", "ReferenceError", "Binding loop", "Cannot assign"):
        assert warning not in result.stderr, result.stderr


def test_desktop_entry_launch_preserves_literal_paths(tmp_path):
    """GLib разбирает Exec: пробелы, кириллица, проценты и символы shell."""
    import json
    import shutil
    import time

    if not shutil.which("gio") or not shutil.which("desktop-file-validate"):
        pytest.skip("Нужен Linux GLib для проверки Desktop Entry")
    folder = tmp_path / 'путь $`"\\% с пробелом'
    folder.mkdir()
    marker = tmp_path / "arguments.json"
    executable = folder / "Ритм"
    executable.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"from pathlib import Path\nPath({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    executable.chmod(0o700)
    path = autostart.entry_path(tmp_path, folder)
    autostart.write_autostart(path, [str(executable), "--profile", str(folder)], True, True)
    validation = subprocess.run(
        ["desktop-file-validate", str(path)], capture_output=True, text=True
    )
    assert validation.returncode == 0, validation.stdout + validation.stderr
    result = subprocess.run(
        ["gio", "launch", str(path)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert json.loads(marker.read_text()) == ["--profile", str(folder), "--start-hidden"]
