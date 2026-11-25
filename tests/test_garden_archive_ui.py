import os
import subprocess
import sys
from pathlib import Path

import pytest

from ritm.domain.garden import Garden, GardenState
from ritm.domain.garden_archive import GardenArchive, rotate_month, set_favorite
from ritm.ui.garden_archive import archive_view


def test_archive_lists_share_months_sort_favorites_and_fall_back_after_expiry():
    archive = GardenArchive(GardenState(Garden("2025-01")))
    archive = set_favorite(archive, "2025-01", True)
    archive = rotate_month(archive, "2026-11")
    archive = set_favorite(archive, "2026-10", True)
    view = archive_view(archive, "2025-01")
    assert len(view["recent"]) == 12 and view["count"] == 2
    assert view["selectedMonth"] == "2025-01"
    assert [m["month"] for m in view["favorites"]] == ["2026-10", "2025-01"]
    assert view["recent"][0]["title"] == "2026-11 · текущий"
    assert view["recent"][1] == view["favorites"][0]
    assert archive_view(archive, "2024-01")["selectedMonth"] == "2026-11"


@pytest.mark.parametrize("mode", ["view", "limit", "restore"])
def test_archive_gui(tmp_path, mode):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{k}_HOME": str(tmp_path / k.lower()) for k in ("CACHE", "DATA", "CONFIG")},
    }
    script = str(Path(__file__).with_name("ui_garden_archive_scenario.py"))
    for step in (mode, "reopen") if mode == "view" else (mode,):
        result = subprocess.run(
            [sys.executable, script, str(tmp_path), step],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in ("TypeError", "ReferenceError", "Binding loop", "Cannot assign"):
            assert warning not in result.stderr, result.stderr
