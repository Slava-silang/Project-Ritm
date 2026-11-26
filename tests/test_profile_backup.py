import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest

from ritm.application.garden_events import GardenEventService, apply_event
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage import backup, profile_backup
from ritm.storage.attachments import copy_attachment, saved_attachment_path
from ritm.storage.garden import GardenStore
from ritm.storage.profile_guard import PENDING_RESTORE
from ritm.storage.sqlite import TaskStore

NOW = datetime(2026, 11, 19, 10, tzinfo=UTC)


@pytest.mark.parametrize("legacy", [False, True])
def test_monthly_archive_backup_keeps_properties_wallet_and_payment_history(tmp_path, legacy):
    seed(tmp_path)
    with stores(tmp_path) as (_, garden, _):
        if not legacy:
            garden.execute(
                uuid4(),
                {"action": "favorite", "month": "2026-11", "enabled": True},
                expected_revision=garden.snapshot().revision,
            )
            garden.rotate_month("2028-01")
        expected = garden.archive_snapshot()
        achievements = garden.achievement_snapshot()
        ledger = garden.reward_snapshot()
        source = garden.source_snapshot()
    if legacy:
        with sqlite3.connect(tmp_path / "garden.sqlite3") as db:
            db.execute("DROP TABLE garden_favorites")
            db.execute("PRAGMA user_version=6")
    original = (tmp_path / "garden.sqlite3").read_bytes()
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    preview = backup.inspect_backup(archive)
    assert (tmp_path / "garden.sqlite3").read_bytes() == original
    with stores(tmp_path) as (_, garden, _):
        if not legacy:
            garden.execute(
                uuid4(),
                {"action": "favorite", "month": "2026-11", "enabled": False},
                expected_revision=garden.snapshot().revision,
            )
        garden.rotate_month("2030-01")
    backup.restore_backup(tmp_path, archive, preview["token"])
    with stores(tmp_path) as (_, garden, service):
        assert garden.archive_snapshot() == expected
        assert garden.achievement_snapshot() == achievements
        assert garden.reward_snapshot() == ledger
        restored = garden.source_snapshot()
        assert restored["identity"] != source["identity"]
        assert restored["payments"] == source["payments"]
        assert service.drain() == 0
        assert garden.archive_snapshot() == expected
        with pytest.raises(ValueError, match="восстановлен"):
            garden.rotate_month("2030-01", expected_identity=source["identity"])


@contextmanager
def stores(profile):
    with (
        TaskStore(profile / "tasks.sqlite3", clock=lambda: NOW + timedelta(hours=2)) as source,
        GardenStore(profile / "garden.sqlite3") as garden,
    ):
        yield source, garden, GardenEventService(source, garden, award_achievements=True)


def seed(profile, title="В копии"):
    with (
        TaskStore(profile / "tasks.sqlite3", clock=lambda: NOW + timedelta(hours=2)) as source,
        GardenStore(profile / "garden.sqlite3", month="2026-11") as garden,
    ):
        task = Task(title=title)
        TaskService(source).create_task(task)
        service = GardenEventService(source, garden, award_achievements=True)
        service.begin(NOW)
        source.add_focus_record(task.id, NOW, NOW + timedelta(hours=2), NOW + timedelta(hours=2))
        TaskService(source).set_completed(task.id, task.id, True)
        service.drain()
        item = garden.execute(
            uuid4(),
            {"action": "buy", "kind": "daisy"},
            expected_revision=garden.snapshot().revision,
        ).item_id
        garden.execute(
            uuid4(),
            {"action": "buy_fertilizer", "quantity": 4},
            expected_revision=garden.snapshot().revision,
        )
        garden.execute(
            uuid4(),
            {"action": "grow", "id": str(item)},
            expected_revision=garden.snapshot().revision,
        )
        text = profile / "source.txt"
        text.write_text("Книга из копии")
        material = copy_attachment(source, profile, None, None, text)
        source.link_material(task.id, task.id, material.id)
    return task


def change(profile, task):
    with stores(profile) as (source, garden, service):
        source.update_task(
            task.id, lambda t: Task(id=t.id, title="Текущая версия", completed=t.completed)
        )
        service.drain()
        garden.execute(
            uuid4(),
            {"action": "buy", "kind": "stone"},
            expected_revision=garden.snapshot().revision,
        )


def state(profile):
    with stores(profile) as (source, garden, service):
        return (
            source.list_tasks(),
            garden.snapshot(),
            garden.achievement_snapshot(),
            garden.source_snapshot(),
        )


def test_paired_backup_restores_money_growth_achievements_materials_and_rekeys_epoch(tmp_path):
    task = seed(tmp_path)
    expected = state(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    preview = backup.inspect_backup(archive)
    assert preview["garden"]
    with ZipFile(archive) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["format"] == "ritm-profile-2" and "garden.sqlite3" in manifest["files"]
    change(tmp_path, task)
    backup.restore_backup(tmp_path, archive, preview["token"])
    actual = state(tmp_path)
    assert actual[0] == expected[0]
    assert actual[1].garden == expected[1].garden and actual[1].balance == expected[1].balance
    assert actual[2] == expected[2]
    assert actual[3]["identity"] != expected[3]["identity"]
    assert actual[3]["payments"] == expected[3]["payments"]
    with stores(tmp_path) as (source, garden, service):
        assert service.drain() == 0 and garden.snapshot().balance == expected[1].balance
        material = source.list_materials()[0]
        assert saved_attachment_path(tmp_path, material).read_text() == "Книга из копии"
        assert source.list_attachments()[0].id == material.id


def test_pending_events_survive_backup_and_old_in_memory_batch_cannot_apply_after_restore(tmp_path):
    task = seed(tmp_path)
    with stores(tmp_path) as (source, garden, service):
        source.add_focus_record(
            task.id, NOW + timedelta(hours=2), NOW + timedelta(hours=3), NOW + timedelta(hours=3)
        )
        cursor = garden.source_snapshot()
        old_id, batch = source.garden_events_after(
            identity=cursor["identity"], sequence=cursor["seq"], event_id=cursor["event_id"]
        )
        archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
        balance = garden.snapshot().balance
        backup.restore_backup(tmp_path, archive, backup.inspect_backup(archive)["token"])
        with pytest.raises(ValueError, match="не совпадает"):
            garden.apply_source_event(
                old_id,
                batch[0],
                zone="UTC",
                expected_revision=garden.snapshot().revision,
                transition=partial(apply_event, achievement_awards=True),
            )
        assert garden.snapshot().balance == balance
        assert service.drain() == 1 and garden.snapshot().balance == balance + 120
        assert service.drain() == 0


def task_only(paired, target):
    with ZipFile(paired) as origin, ZipFile(target, "w") as archive:
        record = json.loads(origin.read("manifest.json"))
        record["format"] = backup.FORMAT
        record.pop("pair")
        record["files"].pop("garden.sqlite3")
        for name in record["files"]:
            archive.writestr(name, origin.read(name))
        archive.writestr("manifest.json", json.dumps(record))
    return target


def test_task_only_archive_retains_compatible_garden_and_refuses_a_later_cursor(tmp_path):
    task = seed(tmp_path)
    paired = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    old = task_only(paired, tmp_path / "old.zip")
    token = backup.inspect_backup(old)["token"]
    with stores(tmp_path) as (source, garden, service):
        garden.execute(
            uuid4(),
            {"action": "buy", "kind": "stone"},
            expected_revision=garden.snapshot().revision,
        )
    retained = state(tmp_path)
    backup.restore_backup(tmp_path, old, token)
    assert state(tmp_path)[1].garden == retained[1].garden
    assert state(tmp_path)[1].balance == retained[1].balance
    # Once source history and the garden move on, the same old single-DB archive is unsafe.
    change(tmp_path, task)
    current = state(tmp_path)
    with pytest.raises(ValueError, match="не согласованы"):
        backup.restore_backup(tmp_path, old, token)
    assert state(tmp_path) == current


def test_paired_restore_can_create_garden_in_profile_without_one(tmp_path):
    origin, target = tmp_path / "origin", tmp_path / "target"
    seed(origin)
    expected = state(origin)
    with TaskStore(target / "tasks.sqlite3"):
        pass
    archive = backup.create_backup(origin, origin / "copies", NOW)
    backup.restore_backup(target, archive, backup.inspect_backup(archive)["token"])
    actual = state(target)
    assert actual[0] == expected[0] and actual[1].balance == expected[1].balance
    assert actual[1].garden == expected[1].garden and actual[2] == expected[2]


def test_wal_snapshot_waits_for_both_writers_and_captures_valid_pair(tmp_path):
    seed(tmp_path)
    with (
        sqlite3.connect(tmp_path / "tasks.sqlite3") as s,
        sqlite3.connect(tmp_path / "garden.sqlite3") as g,
    ):
        assert s.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        assert g.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
        assert backup.inspect_backup(archive)["garden"]


@pytest.mark.parametrize("point", ["garden.sqlite3", "tasks.sqlite3", "committed"])
def test_killed_process_recovers_old_pair_or_keeps_committed_new_pair(tmp_path, point):
    task = seed(tmp_path)
    archived = state(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    change(tmp_path, task)
    current = state(tmp_path)
    crash_restore(tmp_path, archive, point)
    assert (tmp_path / PENDING_RESTORE).exists()
    with pytest.raises(ValueError, match="не завершено"):
        TaskStore(tmp_path / "tasks.sqlite3")
    with pytest.raises(ValueError, match="не завершено"):
        GardenStore(tmp_path / "garden.sqlite3")
    assert backup.recover_profile(tmp_path)
    assert not backup.recover_profile(tmp_path)
    actual = state(tmp_path)
    expected = archived if point == "committed" else current
    assert actual[0] == expected[0]
    assert actual[1].garden == expected[1].garden and actual[1].balance == expected[1].balance
    assert actual[2] == expected[2]
    if point != "committed":
        assert actual[3] == expected[3]
    with stores(tmp_path) as (source, garden, service):
        assert service.drain() == 0


def crash_restore(profile, archive, point):
    code = """from pathlib import Path
import os,sys
from ritm.storage import backup,profile_backup as p
profile,archive,point=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
publish=p._publish_database; marker=p._marker
def fail_publish(source,destination):
    publish(source,destination)
    if source.parent.name=='after' and destination.name==point: os._exit(77)
def fail_marker(*args):
    marker(*args)
    if args[2]=='committed' and point=='committed': os._exit(77)
p._publish_database=fail_publish; p._marker=fail_marker
backup.restore_backup(profile,archive,backup.inspect_backup(archive)['token'])
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(profile), str(archive), point],
        cwd=profile,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 77, result.stdout + result.stderr


def test_disk_failure_rolls_back_pair_and_new_attachments(tmp_path, monkeypatch):
    task = seed(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    change(tmp_path, task)
    current = state(tmp_path)
    directories = set((tmp_path / "attachments").iterdir())
    publish = profile_backup._publish_database

    def fail(source, destination):
        publish(source, destination)
        if source.parent.name == "after" and destination.name == "garden.sqlite3":
            raise OSError("fake disk full")

    with monkeypatch.context() as patch:
        patch.setattr(profile_backup, "_publish_database", fail)
        with pytest.raises(OSError, match="disk full"):
            backup.restore_backup(tmp_path, archive, backup.inspect_backup(archive)["token"])
    assert state(tmp_path) == current
    assert set((tmp_path / "attachments").iterdir()) == directories
    assert not (tmp_path / PENDING_RESTORE).exists()


def test_failed_recovery_keeps_insurance_and_can_itself_be_restarted(tmp_path):
    task = seed(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    change(tmp_path, task)
    current = state(tmp_path)
    crash_restore(tmp_path, archive, "garden.sqlite3")
    code = """from pathlib import Path
import os,sys
from ritm.storage import backup,profile_backup as p
publish=p._publish_database
def fail(source,destination):
    publish(source,destination)
    if destination.name=='garden.sqlite3': os._exit(78)
p._publish_database=fail
backup.recover_profile(Path(sys.argv[1]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 78, result.stdout + result.stderr
    assert backup.recover_profile(tmp_path)
    assert state(tmp_path) == current


def test_corrupted_plan_does_not_clear_marker_or_touch_outside_files(tmp_path):
    task = seed(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    change(tmp_path, task)
    current = state(tmp_path)
    crash_restore(tmp_path, archive, "garden.sqlite3")
    marker = json.loads((tmp_path / PENDING_RESTORE).read_text())
    plan = tmp_path / (".ritm-restore-" + marker["id"]) / "plan.json"
    original = plan.read_bytes()
    plan.write_text("{}")
    victim = tmp_path / "keep.txt"
    victim.write_text("Не удалять")
    with pytest.raises(ValueError, match="повреждён"):
        backup.recover_profile(tmp_path)
    assert victim.read_text() == "Не удалять" and (tmp_path / PENDING_RESTORE).exists()
    plan.write_bytes(original)
    assert backup.recover_profile(tmp_path)
    assert state(tmp_path) == current


def test_abort_into_profile_without_garden_removes_only_the_new_garden(tmp_path):
    origin, target = tmp_path / "origin", tmp_path / "target"
    seed(origin)
    with TaskStore(target / "tasks.sqlite3") as source:
        original = Task(title="Сохранить")
        source.create_task(original)
    archive = backup.create_backup(origin, origin / "copies", NOW)
    crash_restore(target, archive, "garden.sqlite3")
    assert backup.recover_profile(target)
    assert not (target / "garden.sqlite3").exists()
    with TaskStore(target / "tasks.sqlite3") as source:
        assert source.list_tasks() == (original,)


def test_committed_cleanup_can_resume_after_safety_file_has_already_been_removed(tmp_path):
    task = seed(tmp_path)
    archived = state(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    change(tmp_path, task)
    code = """from pathlib import Path
import os,sys
from ritm.storage import backup,profile_backup as p
def partial_cleanup(profile,stage):
    (stage/'before'/'tasks.sqlite3').unlink()
    os._exit(79)
p._cleanup=partial_cleanup
profile,archive=Path(sys.argv[1]),Path(sys.argv[2])
backup.restore_backup(profile,archive,backup.inspect_backup(archive)['token'])
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), str(archive)],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 79, result.stdout + result.stderr
    assert backup.recover_profile(tmp_path)
    actual = state(tmp_path)
    assert actual[0] == archived[0] and actual[1].balance == archived[1].balance


def test_startup_worker_recovers_before_opening_store(tmp_path):
    task = seed(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    change(tmp_path, task)
    expected = state(tmp_path)
    crash_restore(tmp_path, archive, "garden.sqlite3")
    code = """from pathlib import Path
import sys,time
from PySide6.QtGui import QGuiApplication
from ritm.ui.tasks import TaskController
app=QGuiApplication([])
controller=TaskController(Path(sys.argv[1]),automatic_backup=False)
deadline=time.monotonic()+20
while controller.busy and time.monotonic()<deadline:
    app.processEvents();time.sleep(.01)
assert not controller.busy and not controller.error, controller.error
controller.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert state(tmp_path) == expected


def test_individually_valid_but_mismatched_garden_in_archive_is_rejected(tmp_path):
    origin, other = tmp_path / "origin", tmp_path / "other"
    seed(origin)
    seed(other)
    expected = state(origin)
    archive = backup.create_backup(origin, origin / "copies", NOW)
    corrupted = tmp_path / "mixed.zip"
    content = (other / "garden.sqlite3").read_bytes()
    with ZipFile(archive) as z, ZipFile(corrupted, "w") as dest:
        record = json.loads(z.read("manifest.json"))
        record["files"]["garden.sqlite3"] = hashlib.sha256(content).hexdigest()
        for name in record["files"]:
            dest.writestr(name, content if name == "garden.sqlite3" else z.read(name))
        dest.writestr("manifest.json", json.dumps(record))
    with pytest.raises(ValueError, match="не согласованы"):
        backup.inspect_backup(corrupted)
    assert state(origin) == expected


def test_writer_cannot_change_either_database_between_snapshot_copies(tmp_path, monkeypatch):
    task = seed(tmp_path)
    expected = state(tmp_path)
    copy = backup._copy_database
    attempted = threading.Event()
    future = None
    with ThreadPoolExecutor(max_workers=1) as pool:

        def writer():
            attempted.set()
            change(tmp_path, task)

        def snapshot(source, destination, **options):
            nonlocal future
            copy(source, destination, **options)
            if source == tmp_path / "tasks.sqlite3" and future is None:
                future = pool.submit(writer)
                assert attempted.wait(2)
                assert not future.done()

        with monkeypatch.context() as patch:
            patch.setattr(backup, "_copy_database", snapshot)
            archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
        future.result(timeout=10)
    target = tmp_path / "target"
    with TaskStore(target / "tasks.sqlite3"):
        pass
    backup.restore_backup(target, archive, backup.inspect_backup(archive)["token"])
    actual = state(target)
    assert actual[0] == expected[0] and actual[1].balance == expected[1].balance
    assert state(tmp_path)[1].balance != expected[1].balance


def test_recovery_mutex_does_not_rollback_another_running_operation(tmp_path):
    task = seed(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    change(tmp_path, task)
    expected = state(tmp_path)
    crash_restore(tmp_path, archive, "garden.sqlite3")
    marker = (tmp_path / PENDING_RESTORE).read_bytes()
    with profile_backup.profile_operation(tmp_path):
        with pytest.raises(ValueError, match="ещё не завершена"):
            backup.recover_profile(tmp_path)
    assert (tmp_path / PENDING_RESTORE).read_bytes() == marker
    assert backup.recover_profile(tmp_path)
    assert state(tmp_path) == expected


def test_cleanup_failure_after_commit_does_not_report_rollback_or_lose_new_pair(
    tmp_path, monkeypatch
):
    task = seed(tmp_path)
    expected = state(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    change(tmp_path, task)
    cleanup = profile_backup._cleanup
    calls = 0

    def fail_once(profile, stage):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("fake cleanup failure")
        cleanup(profile, stage)

    with monkeypatch.context() as patch:
        patch.setattr(profile_backup, "_cleanup", fail_once)
        backup.restore_backup(tmp_path, archive, backup.inspect_backup(archive)["token"])
    assert calls == 2
    actual = state(tmp_path)
    assert actual[0] == expected[0] and actual[1].balance == expected[1].balance
    assert not (tmp_path / PENDING_RESTORE).exists()


def test_corrupt_archive_and_pending_marker_are_rejected_before_any_new_restore(tmp_path):
    seed(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    expected = state(tmp_path)
    bad = tmp_path / "bad.zip"
    bad.write_text("bad zip")
    with pytest.raises(ValueError, match="повреждена"):
        backup.restore_backup(tmp_path, bad, "bad")
    assert state(tmp_path) == expected
    crash_restore(tmp_path, archive, "garden.sqlite3")
    marker = (tmp_path / PENDING_RESTORE).read_bytes()
    with pytest.raises(ValueError, match="не завершено"):
        backup.restore_backup(tmp_path, archive, backup.inspect_backup(archive)["token"])
    assert (tmp_path / PENDING_RESTORE).read_bytes() == marker
    assert backup.recover_profile(tmp_path)


def test_paired_backup_confirmation_and_narrow_gui(tmp_path):
    environment = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    script = Path(__file__).with_name("ui_paired_backup_scenario.py")
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in (
        "TypeError",
        "ReferenceError",
        "Cannot assign",
        "Binding loop",
        "failed to load",
    ):
        assert warning not in result.stderr, result.stderr


def test_stale_first_event_cannot_bind_restored_zero_cursor_garden(tmp_path):
    with (
        TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as source,
        GardenStore(tmp_path / "garden.sqlite3", month="2026-11") as garden,
    ):
        archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
        source.begin_garden_source(NOW)
        identity, events = source.garden_events_after()
        backup.restore_backup(tmp_path, archive, backup.inspect_backup(archive)["token"])
        with pytest.raises(ValueError, match="не совпадает"):
            garden.apply_source_event(
                identity,
                events[0],
                zone="UTC",
                expected_revision=garden.snapshot().revision,
                transition=partial(apply_event, achievement_awards=True),
            )
        assert garden.snapshot().balance == 0
        service = GardenEventService(source, garden, award_achievements=True)
        service.begin(NOW)
        assert garden.snapshot().balance == 100 and service.drain() == 0


def test_v5_binding_migration_preserves_wallet_claims_and_achievements(tmp_path):
    task = seed(tmp_path)
    expected = state(tmp_path)
    with sqlite3.connect(tmp_path / "garden.sqlite3") as db:
        db.execute("DROP TABLE garden_favorites")
        db.execute("DROP TABLE garden_source_binding")
        db.execute("PRAGMA user_version=5")
    original = (tmp_path / "garden.sqlite3").read_bytes()
    with pytest.raises(ValueError, match="ротация"):
        GardenStore(tmp_path / "garden.sqlite3", month="2026-12")
    assert (tmp_path / "garden.sqlite3").read_bytes() == original
    assert state(tmp_path) == expected
    with stores(tmp_path) as (source, garden, service):
        assert garden._binding_snapshot() is None
        source.update_task(task.id, lambda t: Task(id=t.id, title="Новое событие", completed=True))
        service.drain()
        assert garden._binding_snapshot() == garden.source_snapshot()["identity"]
        assert garden.snapshot().balance == expected[1].balance


def test_sync_binding_and_queue_excluded_from_paired_archive_only(tmp_path):
    from ritm.storage.sync_intents import SyncBinding, SyncIntent
    from ritm.storage.sync_outbox import SyncBase

    task = seed(tmp_path)
    binding = SyncBinding(owner_id=uuid4(), device_id=uuid4(), server_id=uuid4())
    pending = SyncIntent(
        id=uuid4(), target_kind="task", target_id=task.id, action="delete", payload=None
    )
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.initialize_sync_binding(binding)
        store.enqueue_sync_intent(pending)
        expected_task = store.get_task(task.id)
        frozen = store.freeze_sync_intent(
            pending.id,
            SyncBase(
                server_id=binding.server_id,
                target_kind="task",
                target_id=task.id,
                revision=4,
                delete_epoch=2,
            ),
        )
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        assert (
            store.get_sync_binding() == binding and store.list_sync_intents()[0].intent == pending
        )
    preview = backup.inspect_backup(archive)
    backup.restore_backup(tmp_path, archive, preview["token"])
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        assert store.get_sync_binding() is None and not store.list_sync_intents()
        assert store.get_task(task.id) == expected_task
        if store.get_sync_binding() is None:
            assert store.list_frozen_sync_requests() == ()
        else:
            assert store.get_frozen_sync_request(pending.id).raw == frozen.raw
    assert profile_backup.validate_pair(tmp_path)


def test_failed_pair_restore_keeps_live_sync_queue(tmp_path, monkeypatch):
    from ritm.storage.sync_intents import SyncBinding, SyncIntent
    from ritm.storage.sync_outbox import SyncBase

    task = seed(tmp_path)
    archive = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    binding = SyncBinding(owner_id=uuid4(), device_id=uuid4(), server_id=uuid4())
    pending = SyncIntent(
        id=uuid4(), target_kind="task", target_id=task.id, action="delete", payload=None
    )
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.initialize_sync_binding(binding)
        store.enqueue_sync_intent(pending)
        expected_task = store.get_task(task.id)
        frozen = store.freeze_sync_intent(
            pending.id,
            SyncBase(
                server_id=binding.server_id,
                target_kind="task",
                target_id=task.id,
                revision=4,
                delete_epoch=2,
            ),
        )
    original = profile_backup._publish_database
    calls = []

    def fail_once(source, destination):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError("synthetic failure")
        return original(source, destination)

    preview = backup.inspect_backup(archive)
    with monkeypatch.context() as patch:
        patch.setattr(profile_backup, "_publish_database", fail_once)
        with pytest.raises(OSError):
            backup.restore_backup(tmp_path, archive, preview["token"])
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        assert (
            store.get_sync_binding() == binding and store.list_sync_intents()[0].intent == pending
        )
        assert store.get_task(task.id) == expected_task
        if store.get_sync_binding() is None:
            assert store.list_frozen_sync_requests() == ()
        else:
            assert store.get_frozen_sync_request(pending.id).raw == frozen.raw
    assert profile_backup.validate_pair(tmp_path)
