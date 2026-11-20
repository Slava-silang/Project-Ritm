import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import pytest

from ritm.application.garden_runtime import coin_text, sync_garden
from ritm.domain.garden import Garden, GardenItem, GardenState
from ritm.platform.garden_assets import load_garden_assets
from ritm.storage.backup import create_backup, inspect_backup, restore_backup
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore
from ritm.ui.garden_editor import editor_view, fertilizer_quote, placement_preview

NOW = datetime(2026, 11, 20, 10, tzinfo=UTC)


def test_editor_uses_actual_stage_and_exact_affordability_without_rounding():
    item = GardenItem("daisy", uuid4(), 2)
    state = GardenState(Garden("2026-11", items=(item,), fertilizer=7), Fraction(79999, 1000))
    view = editor_view(state, str(uuid4()), load_garden_assets())
    daisy = next(p for p in view["catalog"] if p["kind"] == "daisy")
    assert coin_text(state.balance) == "79.99"
    assert not daisy["canBuy"] and daisy["source"].endswith("daisy_04.png")
    own = view["items"][0]
    assert own["source"].endswith("daisy_02.png") and own["dose"] == 8
    assert not own["canGrow"] and own["refund"] == 40
    assert view["fertilizerMax"] == "7"
    assert not fertilizer_quote("8", view["fertilizerMax"])["canBuy"]
    assert fertilizer_quote("7", "7") == {
        "valid": True,
        "cost": "70",
        "quantity": 7,
        "canBuy": True,
    }
    for value in ("0", "-1", "1.5", "abc", "١", "1" * 13):
        assert not fertilizer_quote(value, "10000000000000")["canBuy"]


def test_preview_checks_full_footprint_without_changing_state_or_money():
    bench = GardenItem("bench", uuid4(), 0)
    state = GardenState(Garden("2026-11", items=(bench,)), 200)
    assert not placement_preview(state, str(bench.id), 9, 8)
    assert "границы" in placement_preview(state, str(bench.id), 9, 9)
    values = list(state.garden.terrain)
    values[99] = "water"
    changed = replace(state, garden=replace(state.garden, terrain=tuple(values)))
    assert "грунтом" in placement_preview(changed, str(bench.id), 9, 8)
    assert not state.garden.item(bench.id).placed and state.balance == 200 and state.revision == 0


@pytest.mark.parametrize("with_receipt", [False, True])
def test_old_editor_generation_is_rejected_inside_transaction_even_for_receipt(
    tmp_path, with_receipt
):
    with TaskStore(tmp_path / "tasks.sqlite3") as source:
        sync_garden(source, tmp_path, NOW, zone="UTC", enabled=True)
    operation, command = uuid4(), {"action": "buy", "kind": "daisy"}
    with GardenStore(tmp_path / "garden.sqlite3") as garden:
        identity = garden.source_snapshot()["identity"]
        revision = garden.snapshot().revision
        if with_receipt:
            garden.execute(
                operation, command, expected_revision=revision, expected_identity=identity
            )
    archive = create_backup(tmp_path, tmp_path / "backups", NOW)
    restore_backup(tmp_path, archive, inspect_backup(archive)["token"])
    with GardenStore(tmp_path / "garden.sqlite3") as garden:
        before = garden.snapshot()
        if not with_receipt:
            assert before.revision == revision  # revision alone would accept this stale purchase
        with pytest.raises(ValueError, match="восстановлен"):
            garden.execute(
                operation, command, expected_revision=revision, expected_identity=identity
            )
        assert garden.snapshot() == before


@pytest.mark.parametrize("mode", ["edit", "failures", "restore", "resources"])
def test_garden_editor_gui(tmp_path, mode):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{k}_HOME": str(tmp_path / k.lower()) for k in ("CACHE", "DATA", "CONFIG")},
    }
    script = Path(__file__).with_name("ui_garden_editor_scenario.py")
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path), mode],
        cwd=tmp_path,
        env=env,
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
        "QML Image: Cannot open",
    ):
        assert warning not in result.stderr, result.stderr

    if mode == "edit":
        reopened = subprocess.run(
            [sys.executable, str(script), str(tmp_path), "reopen"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert reopened.returncode == 0, reopened.stdout + reopened.stderr
        for warning in (
            "TypeError",
            "ReferenceError",
            "Binding loop",
            "Cannot assign",
            "failed to load",
        ):
            assert warning not in reopened.stderr, reopened.stderr
