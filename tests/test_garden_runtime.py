import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ritm.application.garden_runtime import empty_view, sync_garden
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore

NOW = datetime(2026, 11, 19, 9, tzinfo=UTC)


@pytest.fixture
def runtime(tmp_path):
    now = [NOW]
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: now[0]) as source:

        def sync(**kwargs):
            return sync_garden(source, tmp_path, now[0], zone="Europe/Moscow", **kwargs)

        yield source, TaskService(source), sync, now, tmp_path


def test_default_has_no_subscription_no_garden_no_history(runtime):
    source, tasks, sync, _, profile = runtime
    tasks.create_task(Task(title="Старая задача"))
    assert sync() == empty_view()
    assert source.garden_events_after()[1] == ()
    assert not (profile / "garden.sqlite3").exists()


def test_setting_and_achievements_survive_reopen_and_disabled_work(runtime):
    source, tasks, sync, now, profile = runtime
    old = tasks.create_task(Task(title="Старая"))
    assert sync(enabled=True)["balance"] == "100.00"
    assert not sync()["firstTask"]
    assert sync(enabled=True)["balance"] == "100.00"
    tasks.create_task(Task(title="Новая"))
    assert sync()["balance"] == "400.00"
    sync(enabled=False)
    now[0] += timedelta(hours=1)
    tasks.set_completed(old.id, old.id, True)
    assert sync()["roots"] == 0
    with TaskStore(profile / "tasks.sqlite3") as reopened:
        assert reopened.garden_source_status() == {"subscribed": True, "enabled": False}
        view = sync_garden(reopened, profile, now[0], zone="Europe/Moscow")
        assert view["balance"] == "400.00" and not view["enabled"]
    assert sync(enabled=True)["roots"] == 0


def test_disable_checkpoints_only_accepted_window_and_resume_has_no_backfill(runtime):
    source, tasks, sync, now, _ = runtime
    task = tasks.create_task(Task(title="Книга", difficulty=5))
    sync(enabled=True)
    source.change_focus("start", now[0], None, task_id=task.id)
    now[0] += timedelta(minutes=10)
    assert sync(enabled=False, checkpoint=True)["balance"] == "120.00"
    now[0] += timedelta(hours=2)
    assert sync(enabled=True, checkpoint=True)["balance"] == "120.00"
    now[0] += timedelta(minutes=5)
    view = sync(checkpoint=True)
    assert view["balance"] == "130.00"
    assert view["paidHours"] == "0.25"


def test_pending_completion_and_first_task_delivered_once_after_reopen(runtime):
    source, tasks, sync, now, profile = runtime
    sync(enabled=True)
    task = tasks.create_task(Task(title="Из другого worker"))
    tasks.set_completed(task.id, task.id, True)
    with TaskStore(profile / "tasks.sqlite3") as reopened:
        view = sync_garden(reopened, profile, now[0], zone="Europe/Moscow")
        assert view["balance"] == "405.00" and view["roots"] == 1
    assert sync()["balance"] == "405.00"


def test_missing_subscribed_garden_is_not_recreated(runtime):
    _, _, sync, _, profile = runtime
    sync(enabled=True)
    (profile / "garden.sqlite3").unlink()
    with pytest.raises(ValueError, match="отсутствует"):
        sync()
    assert not (profile / "garden.sqlite3").exists()


def test_corrupt_garden_does_not_enable_or_overwrite(runtime):
    source, _, sync, _, profile = runtime
    path = profile / "garden.sqlite3"
    path.write_bytes(b"not a garden")
    with pytest.raises(sqlite3.DatabaseError):
        sync(enabled=True)
    assert path.read_bytes() == b"not a garden"
    assert source.garden_source_status() == {"subscribed": False, "enabled": False}


def test_queue_failure_keeps_task_and_retry_pays_once(runtime, monkeypatch):
    source, tasks, sync, _, _ = runtime
    sync(enabled=True)
    task = tasks.create_task(Task(title="Не теряется"))
    original = GardenStore.apply_source_event
    monkeypatch.setattr(
        GardenStore, "apply_source_event", lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(OSError):
        sync()
    assert source.get_task(task.id) == task
    monkeypatch.setattr(GardenStore, "apply_source_event", original)
    assert sync()["balance"] == "400.00"
    assert sync()["balance"] == "400.00"


@pytest.mark.parametrize(
    "mode", ["live", "portable", "recovery", "restore", "legacy", "closing_restore"]
)
def test_garden_worker_gui(tmp_path, mode):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{k}_HOME": str(tmp_path / k.lower()) for k in ("CACHE", "DATA", "CONFIG")},
    }
    if mode == "portable":
        env.update(PYTHONTZPATH="", RITM_TEST_SYSTEM_ZONE="1", TZ="Europe/Moscow")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("ui_garden_runtime_scenario.py")),
            str(tmp_path),
            "live" if mode == "portable" else mode,
        ],
        env=env,
        cwd=tmp_path,
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


def test_first_enable_failure_retries_from_durable_subscription(runtime, monkeypatch):
    source, _, sync, _, _ = runtime
    original = GardenStore.apply_source_event

    def fail(*args, **kwargs):
        raise OSError("receipt unavailable")

    monkeypatch.setattr(GardenStore, "apply_source_event", fail)
    with pytest.raises(OSError):
        sync(enabled=True)
    assert source.garden_source_status() == {"subscribed": True, "enabled": True}
    monkeypatch.setattr(GardenStore, "apply_source_event", original)
    assert sync()["balance"] == "100.00"
    assert sync()["balance"] == "100.00"


def test_creation_and_rotation_use_local_month(runtime):
    _, _, sync, now, profile = runtime
    now[0] = datetime(2026, 11, 30, 22, tzinfo=UTC)
    sync(enabled=True)
    with GardenStore(profile / "garden.sqlite3") as garden:
        assert garden.snapshot().garden.month == "2026-12"
    now[0] += timedelta(days=40)
    sync()
    with GardenStore(profile / "garden.sqlite3") as garden:
        assert garden.snapshot().garden.month == "2027-01"


@pytest.mark.parametrize(
    ("zone", "month"),
    [("UTC", "2026-11"), ("Europe/Moscow", "2026-12"), ("America/New_York", "2026-11")],
)
def test_create_and_reopen_without_system_timezone_data(tmp_path, zone, month):
    """Windows-like Python environment: no OS IANA files, also on Linux CI."""
    script = r"""
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import TZPATH
from ritm.application.garden_runtime import sync_garden
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore

profile, zone, month, action = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
assert TZPATH == ()  # No accidental success via the host Linux timezone files.
now = datetime(2026, 11, 30, 22, tzinfo=UTC)
with TaskStore(profile / 'tasks.sqlite3', clock=lambda: now) as source:
    if action == 'create':
        assert not (profile / 'garden.sqlite3').exists()
        assert not sync_garden(source, profile, now, zone=zone)['subscribed']
        assert not (profile / 'garden.sqlite3').exists()
        view = sync_garden(source, profile, now, zone=zone, enabled=True)
        assert view['enabled'] and view['balance'] == '100.00', view
        TaskService(source).create_task(Task(title='Новая задача'))
        assert sync_garden(source, profile, now, zone=zone)['balance'] == '400.00'
        with GardenStore(profile / 'garden.sqlite3') as garden:
            garden.execute(uuid4(), {'action': 'buy', 'kind': 'daisy'},
                           expected_revision=garden.snapshot().revision)
    else:
        view = sync_garden(source, profile, now, zone=zone, enabled=True)
        assert view['enabled'] and view['balance'] == '320.00', view
        with GardenStore(profile / 'garden.sqlite3') as garden:
            state = garden.snapshot()
            assert state.garden.month == month
            assert len(state.garden.items) == 1
            assert state.garden.items[0].kind == 'daisy'
"""
    for action in ("create", "reopen"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), zone, month, action],
            env={**os.environ, "PYTHONTZPATH": ""},
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
