"""Живой worker/фокус/restore/совместимость через настоящий Qt, временный профиль."""

import os
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from gui_support import GuiHarness
from PySide6.QtCore import QPointF, QUrl
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.application.garden_runtime import sync_garden
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage.appearance import save_appearance
from ritm.storage.backup import create_backup
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore
from ritm.ui import tasks as ui_tasks
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
now = datetime(2026, 11, 19, 9, tzinfo=UTC)
with TaskStore(profile / "tasks.sqlite3", clock=lambda: now) as store:
    task = TaskService(store).create_task(Task(title="Учёба", difficulty=5))
    if mode in ("recovery", "closing_restore"):
        sync_garden(store, profile, now, zone="UTC", enabled=True)
        store.change_focus("start", now, None, task_id=task.id)
        now += timedelta(minutes=5)
        store.checkpoint_garden_focus(now)
        if mode == "closing_restore":
            closing_archive = create_backup(profile, profile / "backups", now)
            active = store.active_focus()
            store.change_focus("finish", now, (str(active.id), active.revision))
        now += timedelta(hours=4)  # unconfirmed absence is not a live checkpoint
if mode == "legacy":
    save_appearance(profile, "space", 125)

QQuickStyle.setStyle("Basic")
app = QApplication([])
main_thread = threading.get_ident()
worker_threads = []
original_sync = ui_tasks.sync_garden


def checked_sync(*args, **kwargs):
    worker_threads.append(threading.get_ident())
    assert worker_threads[-1] != main_thread
    return original_sync(*args, **kwargs)


ui_tasks.sync_garden = checked_sync
controller = TaskController(
    profile, clock=lambda: now, zone=None if os.environ.get("RITM_TEST_SYSTEM_ZONE") else UTC
)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def idle():
    gui.idle()
    assert not controller.error, controller.error


def advance(minutes):
    global now
    now += timedelta(minutes=minutes)


closed = False
try:
    idle()
    controller.selectTask(str(task.id))
    if mode == "live":
        assert not controller.garden["enabled"] and not (profile / "garden.sqlite3").exists()
        controller.saveAppearance("jungle", 100)
        gui.idle()
        assert "достижение" in controller.error
        assert controller.appearance["theme"] == "light"
        gui.click("gardenButton")
        gui.click("gardenEnabled")
        assert controller.garden["enabled"] and controller.garden["balance"] == "100.00"
        gui.screenshot(profile / "garden-settings.png")
        gui.click("closeGarden")
        gui.click("newTaskButton")
        gui.fill("titleInput", "Новая после включения")
        gui.click("saveTaskButton")
        assert controller.garden["balance"] == "400.00"
        controller.selectTask(str(task.id))
        controller.startFocus(str(task.id), "", False)
        idle()
        assert controller._garden_timer.isActive()
        advance(120)
        controller.setCompleted(str(task.id), True)
        idle()
        # Completion includes the live two hours BEFORE the one-time 10% bonus.
        assert controller.garden["balance"] == "664.00", controller.garden
        assert controller.garden["roots"] == 1
        controller.setGardenEnabled(False)
        idle()
        assert not controller._garden_timer.isActive()
        advance(120)
        controller.setGardenEnabled(True)
        idle()
        assert controller.garden["balance"] == "664.00"
        advance(5)
        controller._on_garden_tick()
        idle()
        assert controller.garden["balance"] == "674.00"
        controller.setGardenEnabled(False)
        idle()
        # A delivery failure never reports a committed rename as a failed task write.
        original_apply = GardenStore.apply_source_event

        failure = [OSError]

        def unavailable(*args, **kwargs):
            raise failure[0]("test receiver unavailable")

        GardenStore.apply_source_event = unavailable
        controller.selectTask(str(task.id))
        controller.renameNode(str(task.id), "Сохранённое изменение")
        idle()
        assert controller.garden["error"] and not controller._garden_timer.isActive()
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert store.get_task(task.id).title == "Сохранённое изменение"
        failure[0] = RuntimeError
        controller.renameNode(str(task.id), "Сохранено при ошибке получателя")
        idle()
        assert controller.garden["error"]
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert store.get_task(task.id).title == "Сохранено при ошибке получателя"
        failure[0] = ZoneInfoNotFoundError
        controller.reload()
        idle()
        assert "часовых поясов" in controller.garden["error"]
        assert "uv sync --locked" in controller.garden["error"]
        GardenStore.apply_source_event = original_apply
        controller.reload()
        idle()
        assert not controller.garden["error"]
        controller.saveAppearance("dark", 150)
        idle()
        window.setWidth(640)
        window.setHeight(480)
        gui.wait_for(
            lambda: gui.item("gardenButton").mapToScene(QPointF()).x() < 600,
            "wrapped garden button",
        )
        gui.click("gardenButton")
        gui.screenshot(profile / "garden-settings-narrow.png")
        gui.click("closeGarden")
        gui.click("appearanceButton")
        gui.choose("appearanceTheme", 1)
        assert not gui.item("saveAppearance").isEnabled()
        assert "15" in gui.item("themeUnlockHint").property("text")
        gui.screenshot(profile / "garden-theme-locked.png")
        gui.click("cancelAppearance")
    elif mode == "recovery":
        assert controller.focus["recovery"]
        assert controller.garden["balance"] == "110.00"
        assert not controller._garden_timer.isActive()
        controller.reload()
        idle()
        assert controller.garden["balance"] == "110.00"
        # Closing with an unreviewed session must also leave absence unpaid.
    elif mode == "closing_restore":
        assert not controller.focus
        controller.inspectBackup(QUrl.fromLocalFile(str(closing_archive)))
        idle()
        entered, release = threading.Event(), threading.Event()
        original_restore = ui_tasks.restore_backup

        def delayed_restore(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original_restore(*args, **kwargs)

        ui_tasks.restore_backup = delayed_restore
        controller.restoreBackup()
        assert entered.wait(5)
        # Quit marks the controller closing before the restore's _ready is handled.
        timer = threading.Timer(0.1, release.set)
        timer.start()
        controller.close()
        closed = True
        timer.join()
        with GardenStore(profile / "garden.sqlite3") as store:
            assert store.snapshot().balance == 110
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert store.active_focus() is not None
    elif mode == "legacy":
        assert controller.appearance == {"theme": "space", "scale": 125}
        assert "space" not in controller.garden["themes"]
        controller.saveAppearance("space", 150)
        idle()
        controller.saveAppearance("dark", 100)
        idle()
        gui.click("appearanceButton")
        gui.choose("appearanceTheme", 0)
        gui.click("saveAppearance")
        assert controller.appearance["theme"] == "space"
        assert "space" not in controller.garden["themes"]
        assert not (profile / "garden.sqlite3").exists()
    else:
        controller.setGardenEnabled(True)
        idle()
        archive = create_backup(profile, profile / "backups", now)
        with TaskStore(profile / "tasks.sqlite3", clock=lambda: now) as store:
            service = TaskService(store)
            for i in range(15):
                extra = service.create_task(Task(title=f"Подготовка {i}"))
                service.set_completed(extra.id, extra.id, True)
            for _ in range(3):
                start = now
                now += timedelta(hours=10)
                store.add_focus_record(task.id, start, now, now)
                now += timedelta(hours=14)
        controller.reload()
        idle()
        assert set(controller.garden["themes"]) == {"space", "jungle", "dark", "light"}
        controller.saveAppearance("space", 100)
        idle()
        with GardenStore(profile / "garden.sqlite3") as store:
            previous_revision = store.snapshot().revision
        controller.inspectBackup(QUrl.fromLocalFile(str(archive)))
        idle()
        controller.restoreBackup()
        idle()
        assert controller.garden["balance"] == "100.00" and controller.garden["roots"] == 0
        assert controller.garden["themes"] == ["dark", "light"]
        with GardenStore(profile / "garden.sqlite3") as store:
            assert store.snapshot().revision < previous_revision
        controller.reload()
        idle()
        assert controller.garden["balance"] == "100.00"
    assert worker_threads
finally:
    if not closed:
        controller.close()
    window.close()
    engine = None
    app.processEvents()
if mode == "recovery":
    with GardenStore(profile / "garden.sqlite3") as store:
        assert store.snapshot().balance == 110
