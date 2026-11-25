import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from ritm.application.garden_runtime import sync_garden
from ritm.application.tasks import TaskService
from ritm.domain.garden import Garden
from ritm.domain.tasks import Task
from ritm.storage import backup
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore


def test_live_checkpoint_crosses_local_midnight_before_rotating_property(tmp_path):
    now = [datetime(2026, 11, 30, 20, 55, tzinfo=UTC)]  # 23:55 Moscow
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: now[0]) as source:
        task = TaskService(source).create_task(Task(title="Ночная работа"))
        sync_garden(source, tmp_path, now[0], zone="Europe/Moscow", enabled=True)
        with GardenStore(tmp_path / "garden.sqlite3") as garden:
            garden.execute(
                uuid4(),
                {"action": "buy", "kind": "daisy"},
                expected_revision=garden.snapshot().revision,
            )
            original = garden.snapshot().garden
        source.change_focus("start", now[0], None, task_id=task.id)
        now[0] += timedelta(minutes=10)
        view = sync_garden(source, tmp_path, now[0], zone="Europe/Moscow", checkpoint=True)
        assert view["currentMonth"] == "2026-12" and view["balance"] == "40.00"
        # Goals retain the local month of the source event that created payment.
        assert {m["month"]: m["paidHours"] for m in view["months"]} == {"2026-12": "0.17"}
        with GardenStore(tmp_path / "garden.sqlite3") as garden:
            archive = garden.archive_snapshot()
            assert archive.current.garden == Garden("2026-12")
            assert archive.history == (original,)
        repeated = sync_garden(source, tmp_path, now[0], zone="Europe/Moscow", checkpoint=True)
        assert repeated == view


def test_pending_work_is_delivered_before_rollover_and_not_repaid(tmp_path):
    now = [datetime(2026, 11, 30, 23, 59, tzinfo=UTC)]
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: now[0]) as source:
        sync_garden(source, tmp_path, now[0], zone="UTC", enabled=True)
        TaskService(source).create_task(Task(title="До полуночи"))
        now[0] += timedelta(minutes=2)
        view = sync_garden(source, tmp_path, now[0], zone="UTC")
        assert view["balance"] == "400.00" and view["firstTask"]
        assert view["currentMonth"] == "2026-12"
        assert sync_garden(source, tmp_path, now[0], zone="UTC") == view


def test_disabled_months_do_not_rotate_or_backfill_until_enable(tmp_path):
    now = [datetime(2026, 11, 30, 12, tzinfo=UTC)]
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: now[0]) as source:
        sync_garden(source, tmp_path, now[0], zone="UTC", enabled=True)
        sync_garden(source, tmp_path, now[0], zone="UTC", enabled=False)
        with GardenStore(tmp_path / "garden.sqlite3") as garden:
            before = garden.archive_snapshot()
        now[0] += timedelta(days=100)
        view = sync_garden(source, tmp_path, now[0], zone="UTC")
        assert not view["enabled"] and view["currentMonth"] == "2026-11"
        with GardenStore(tmp_path / "garden.sqlite3") as garden:
            assert garden.archive_snapshot() == before
        view = sync_garden(source, tmp_path, now[0], zone="UTC", enabled=True)
        assert view["currentMonth"] == "2027-03" and view["balance"] == "100.00"
        assert view["months"] == []
        with GardenStore(tmp_path / "garden.sqlite3") as garden:
            assert garden.archive_snapshot().current.garden == Garden("2027-03")


def test_large_gap_reopen_and_clock_rewind_keep_favorite_and_month_watermark(tmp_path):
    now = datetime(2026, 11, 30, 12, tzinfo=UTC)
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: now) as source:
        sync_garden(source, tmp_path, now, zone="UTC", enabled=True)
        with GardenStore(tmp_path / "garden.sqlite3") as garden:
            garden.execute(
                uuid4(),
                {"action": "favorite", "month": "2026-11", "enabled": True},
                expected_revision=garden.snapshot().revision,
            )
    later = datetime(2029, 1, 1, tzinfo=UTC)
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: later) as source:
        view = sync_garden(source, tmp_path, later, zone="UTC")
        assert view["currentMonth"] == "2029-01" and view["balance"] == "100.00"
        with GardenStore(tmp_path / "garden.sqlite3") as garden:
            saved = garden.archive_snapshot()
            assert saved.history == (Garden("2026-11"),)
        rewind = sync_garden(source, tmp_path, now, zone="UTC")
        assert rewind["currentMonth"] == "2029-01"
        assert rewind["progress"]["currentMonth"] == "2029-01"
        with GardenStore(tmp_path / "garden.sqlite3") as garden:
            assert garden.archive_snapshot() == saved


def test_startup_with_unconfirmed_focus_rotates_without_paying_absence(tmp_path):
    now = datetime(2026, 11, 30, 23, 50, tzinfo=UTC)
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: now) as source:
        task = TaskService(source).create_task(Task(title="Не подтверждено"))
        sync_garden(source, tmp_path, now, zone="UTC", enabled=True)
        source.change_focus("start", now, None, task_id=task.id)
    later = now + timedelta(days=1)
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: later) as source:
        view = sync_garden(source, tmp_path, later, zone="UTC", checkpoint=False)
        assert view["currentMonth"] == "2026-12"
        assert view["balance"] == "100.00" and view["paidHours"] == "0.00"


def test_restored_old_month_rotates_on_next_sync_without_repeating_welcome(tmp_path):
    now = datetime(2026, 11, 30, 23, 50, tzinfo=UTC)
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: now) as source:
        sync_garden(source, tmp_path, now, zone="UTC", enabled=True)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", now)
    preview = backup.inspect_backup(archive)
    later = now + timedelta(days=2)
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: later) as source:
        sync_garden(source, tmp_path, later, zone="UTC")
    backup.restore_backup(tmp_path, archive, preview["token"])
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: later) as source:
        view = sync_garden(source, tmp_path, later, zone="UTC")
        assert view["currentMonth"] == "2026-12" and view["balance"] == "100.00"
        with GardenStore(tmp_path / "garden.sqlite3") as garden:
            assert garden.archive_snapshot().history == (Garden("2026-11"),)


def test_month_rollover_worker_and_editor_gui(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{k}_HOME": str(tmp_path / k.lower()) for k in ("CACHE", "DATA", "CONFIG")},
    }
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("ui_garden_month_scenario.py")),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
