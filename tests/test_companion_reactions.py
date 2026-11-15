import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ritm.domain.companion import Reactions
from ritm.storage.companion import DEFAULT, LEGACY_KEYS, load_companion, save_companion


def focus(seconds=0, *, paused=False, recovery=False, identifier="session"):
    return {
        "recoveryId": identifier,
        "elapsed": f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}",
        "paused": paused,
        "recovery": recovery,
    }


def options(**changes):
    return dict(DEFAULT, enabled=True, break_minutes=5, frequency="often", **changes)


def test_states_start_pause_happy_and_expiry():
    model, settings = Reactions(), options()
    assert model.update({}, 0, settings, True) == ("idle", "")
    mode, phrase = model.update(focus(), 1, settings, True)
    assert mode == "working" and phrase
    assert model.update(focus(10), 10, settings, True) == ("working", "")
    assert model.update(focus(10, paused=True), 11, settings, True)[0] == "paused"
    model.celebrate(40, settings, True)
    assert model.update(focus(10, paused=True), 40, settings, True)[0] == "happy"
    assert model.text
    assert model.update(focus(10, paused=True), 48, settings, True) == ("paused", "")
    assert model.update({}, 49, settings, True)[0] == "idle"


def test_break_counts_active_time_after_resume_not_wall_clock():
    model, settings = Reactions(), options()
    model.update(focus(), 0, settings, True)
    model.update(focus(240, paused=True), 240, settings, True)
    assert not model.update(focus(240, paused=True), 9000, settings, True)[1]
    model.update(focus(240), 9010, settings, True)
    assert not model.update(focus(539), 9309, settings, True)[1]
    assert "перерыв" in model.update(focus(540), 9310, settings, True)[1]
    assert not model.update(focus(550), 9320, settings, True)[1]
    assert not model.update(focus(839), 9609, settings, True)[1]
    assert model.update(focus(840), 9610, settings, True)[1]


@pytest.mark.parametrize("frequency,delay", [("rare", 300), ("normal", 120), ("often", 30)])
def test_frequency_is_global_cooldown(frequency, delay):
    model, settings = Reactions(), dict(options(), frequency=frequency)
    model.update(focus(), 0, settings, True)
    model.celebrate(delay - 1, settings, True)
    assert model._spoken == 0
    model.celebrate(delay, settings, True)
    assert model._spoken == delay


def test_muted_hidden_and_recovery_never_queue_old_messages():
    model, settings = Reactions(), options()
    assert model.update(focus(100, recovery=True), 0, settings, True) == ("paused", "")
    assert model.update(focus(100), 1, settings, False) == ("working", "")
    assert model.update(focus(400), 301, settings, False) == ("working", "")
    assert model.update(focus(400), 302, settings, True) == ("working", "")
    assert model.update(focus(700), 602, dict(settings, muted=True), True) == ("working", "")
    assert model.update(focus(700), 603, settings, True) == ("working", "")
    model.celebrate(604, dict(settings, muted=True), True)
    assert model.mode == "happy" and not model.text


def test_new_session_settings_change_and_backward_elapsed_reset_break():
    model, settings = Reactions(), options()
    model.update(focus(), 0, settings, True)
    model.update(focus(200), 200, settings, True)
    settings = dict(settings, break_minutes=10)
    model.update(focus(200), 201, settings, True)
    assert not model.update(focus(799), 800, settings, True)[1]
    assert model.update(focus(800), 801, settings, True)[1]
    model.update(focus(10), 820, settings, True)
    assert not model.update(focus(609), 1419, settings, True)[1]
    model.update(focus(identifier="other"), 1500, settings, True)
    assert model._baseline == 0


def test_old_preferences_upgrade_without_modifying_file(tmp_path):
    legacy = {key: DEFAULT[key] for key in LEGACY_KEYS}
    path = tmp_path / "companion.json"
    path.write_text(json.dumps(legacy))
    original = path.read_bytes()
    assert load_companion(tmp_path) == DEFAULT
    assert path.read_bytes() == original
    value = dict(
        DEFAULT, frequency="rare", break_minutes=25, reduce_motion=True, hide_fullscreen=False
    )
    save_companion(tmp_path, value)
    assert load_companion(tmp_path) == value


@pytest.mark.parametrize(
    "change",
    [
        {"frequency": "other"},
        {"break_minutes": True},
        {"break_minutes": 4},
        {"break_minutes": 241},
        {"reduce_motion": 1},
        {"hide_fullscreen": "true"},
    ],
)
def test_invalid_reaction_preferences(tmp_path, change):
    with pytest.raises(ValueError):
        save_companion(tmp_path, dict(DEFAULT, **change))


def test_reactions_gui_and_fullscreen(tmp_path):
    env = {
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
                str(Path(__file__).with_name("ui_reactions_scenario.py")),
                str(tmp_path),
                mode,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
            assert warning not in result.stderr, result.stderr


def test_failed_and_stalled_fullscreen_probe_is_bounded(tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ui_fullscreen_probe.py")), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=12,
    )
    assert result.returncode == 0, result.stdout + result.stderr
