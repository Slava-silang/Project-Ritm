"""Regular task UI, calendar-day rollover and restart using a disposable profile."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from ritm.app import load_window
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
mode = sys.argv[2]
now = [datetime(2026, 11, 26, 10, tzinfo=UTC)]
if mode == "reopen":
    now[0] += timedelta(days=9)
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
controller = TaskController(profile, clock=lambda: now[0], zone=UTC, automatic_backup=False)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
try:
    gui.idle()
    if mode == "reopen":
        assert len(controller._source) == 2
        assert len(controller.regularTasks) == 1
        assert controller.regularTasks[0]["repeatDay"] == "2026-12-05"
        assert not controller.error
    else:
        gui.click("newTaskButton")
        gui.fill("titleInput", "Читать каждый третий день")
        gui.click("repeatEnabled")
        gui.fill("repeatInterval", "0")
        gui.click("saveTaskButton")
        assert controller.error and not controller.regularTasks
        gui.fill("repeatInterval", "3")
        gui.fill("repeatStart", "2026-11-26")
        gui.item("formCategory").setProperty("editText", "Чтение")
        app.processEvents()
        gui.click("saveTaskButton")
        assert not controller.error and len(controller.regularTasks) == 1
        task_id = controller.regularTasks[0]["id"]
        assert controller.detail["repeatDays"] == "3"
        assert controller.detail["deadline"] == ""
        controller.setCompleted(task_id, True)
        gui.idle()
        now[0] += timedelta(days=3)
        controller.reload()
        gui.idle()
        assert len(controller._source) == 2
        assert controller.regularTasks[0]["id"] != task_id
        now[0] += timedelta(days=6)
        controller.reload()
        gui.idle()
        assert len(controller._source) == 2
        assert controller.statistics["overdueCount"] == 0
        gui.click("parametersButton")
        gui.click("regularTasksButton")
        gui.click("editRegularTaskButton")
        assert gui.item("repeatEnabled").property("checked")
        assert gui.item("formCategory").property("editText") == "Чтение"
        gui.screenshot(profile / "regular-editor.png")
        window.resize(640, 480)
        app.processEvents()
        gui.reveal(gui.item("repeatInterval"), full=True)
        gui.screenshot(profile / "regular-editor-compact.png")
        gui.click("repeatEnabled")
        gui.click("saveTaskButton")
        assert not controller.regularTasks and not controller.error
        gui.click("editTaskButton")
        gui.click("repeatEnabled")
        gui.fill("repeatInterval", "3")
        gui.fill("repeatStart", "2026-11-26")
        gui.click("saveTaskButton")
        assert len(controller.regularTasks) == 1 and not controller.error
        assert controller.regularTasks[0]["category"] == "Чтение"
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
