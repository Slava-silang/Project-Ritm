import os
import subprocess
import sys
from pathlib import Path

import pytest

from ritm.storage import appearance


def test_four_theme_slots_and_system_mode_persist_without_task_data(tmp_path):
    for key in ("space", "jungle", "light", "dark", "system"):
        appearance.save_appearance(tmp_path, key, 125)
        assert appearance.load_appearance(tmp_path) == {"theme": key, "scale": 125}
    saved = (tmp_path / "appearance.json").read_bytes()
    for key, scale in (("missing", 100), ("dark", 200), ("light", True)):
        with pytest.raises(ValueError):
            appearance.save_appearance(tmp_path, key, scale)
        assert (tmp_path / "appearance.json").read_bytes() == saved
    assert not (tmp_path / "tasks.sqlite3").exists()


def test_failed_settings_write_preserves_previous_choice(tmp_path, monkeypatch):
    appearance.save_appearance(tmp_path, "jungle", 100)

    def fail(*args):
        raise OSError("Нет места")

    monkeypatch.setattr(appearance.os, "replace", fail)
    with pytest.raises(OSError):
        appearance.save_appearance(tmp_path, "dark", 150)
    assert appearance.load_appearance(tmp_path) == {"theme": "jungle", "scale": 100}
    assert [file.name for file in tmp_path.iterdir()] == ["appearance.json"]


def test_theme_scale_shortcuts_and_restart(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    script = Path(__file__).with_name("ui_appearance_scenario.py")
    for mode in ("run", "reopen"):
        result = subprocess.run(
            [sys.executable, str(script), str(tmp_path), mode],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in (
            "TypeError",
            "ReferenceError",
            "Binding loop",
            "failed to load",
            "Cannot assign",
        ):
            assert warning not in result.stderr, result.stderr
