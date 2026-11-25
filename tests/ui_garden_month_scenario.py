"""Переход месяца без фокуса, очистка размещения и отклонение старой команды."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from gui_support import GuiHarness
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
now = [datetime(2026, 11, 30, 23, 59, tzinfo=UTC)]
with TaskStore(profile / "tasks.sqlite3", clock=lambda: now[0]) as source:
    task = TaskService(source).create_task(Task(title="Работа через границу месяца"))
QQuickStyle.setStyle("Basic")
app = QApplication([])
controller = TaskController(profile, clock=lambda: now[0], zone=UTC)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)

try:
    gui.idle()
    gui.click("gardenButton")
    gui.click("gardenEnabled")
    assert controller._garden_timer.isActive()
    assert controller._garden_timer.interval() == 60000
    gui.click("gardenBuy_daisy")
    gui.click("gardenMove")
    pane = gui.item("gardenPane")
    assert pane.property("selectedId") and pane.property("placingId")
    stale = {
        "operation": str(uuid4()),
        "command": {"action": "terrain", "x": 0, "y": 0, "terrain": "water"},
        "revision": controller.gardenBoard["revision"],
        "identity": controller.gardenBoard["identity"],
    }
    now[0] += timedelta(minutes=2)
    controller._on_garden_tick()
    gui.idle()
    assert not controller.error, controller.error
    assert controller.gardenBoard["month"] == "2026-12"
    assert controller.gardenBoard["items"] == []
    assert not pane.property("selectedId") and not pane.property("placingId")
    assert controller.garden["balance"] == "20.00"
    gui.screenshot(profile / "garden-new-month.png")
    controller._submit("garden_command", stale)
    gui.idle()
    assert "изменились" in controller.error
    with GardenStore(profile / "garden.sqlite3") as garden:
        archive = garden.archive_snapshot()
        assert len(archive.history) == 1 and len(archive.history[0].items) == 1
        assert archive.current.garden.terrain[0] == "grass"
    controller.startFocus(str(task.id), "", False)
    gui.idle()
    assert controller._garden_timer.interval() == 5000
    now[0] += timedelta(minutes=10)
    controller._on_garden_tick()
    gui.idle()
    assert controller.garden["balance"] == "40.00"
    controller.setGardenEnabled(False)
    gui.idle()
    assert not controller._garden_timer.isActive()
    now[0] += timedelta(days=32)
    controller._on_garden_tick()
    gui.idle()
    assert controller.garden["currentMonth"] == "2026-12"
    controller.setGardenEnabled(True)
    gui.idle()
    assert controller.garden["currentMonth"] == "2027-01"
    assert controller.garden["balance"] == "40.00"
    assert controller.gardenBoard["items"] == []
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
