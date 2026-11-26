import os
import subprocess
import sys
from pathlib import Path

from ritm.storage.appearance import load_appearance, save_appearance


def test_dusk_persists_as_a_free_dark_variant_without_garden(tmp_path):
    save_appearance(tmp_path, "dusk", 125)
    assert load_appearance(tmp_path) == {"theme": "dusk", "scale": 125, "panel": "left"}
    assert not (tmp_path / "garden.sqlite3").exists()


def test_dusk_pages_dialogs_accessibility_and_restart(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{k}_HOME": str(tmp_path / k.lower()) for k in ("CACHE", "DATA", "CONFIG")},
    }
    for mode in ("view", "reopen"):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("ui_dusk_scenario.py")),
                str(tmp_path),
                mode,
            ],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=120,
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
