"""Проверка управления фокусом через QML с управляемыми часами."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
reopen = sys.argv[2] == "reopen"
now = datetime(2026, 11, 9, 10, tzinfo=UTC)
with TaskStore(profile / "tasks.sqlite3") as store:
    if not reopen:
        store.create_task(Task(title="Лабораторная"))
        store.create_task(Task(title="Курсовая"))
    ids = {task.title: str(task.id) for task in store.list_tasks()}

QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
controller = TaskController(profile, clock=lambda: now, zone=UTC)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def advance(seconds):
    global now
    now += timedelta(seconds=seconds)
    controller._on_tick()
    app.processEvents()


try:
    gui.idle()
    controller.selectTask(ids["Лабораторная"])
    if reopen:
        assert not controller.focus
        assert controller.detail["focusTotal"] == "00:01:15"
        assert len(controller.detail["focusHistory"]) == 1
    else:
        gui.click("startFocusButton")
        gui.fill("focusMinutesInput", "1")
        gui.click("confirmFocusButton")
        assert controller.focus["target"] == "00:01:00"
        advance(20)
        gui.click("calendarPageButton")
        gui.click("focusPauseResume")
        advance(360)
        assert controller.focus["elapsed"] == "00:00:20"
        gui.click("focusPauseResume")
        advance(40)
        assert controller.focus["reached"]
        gui.screenshot(profile / "focus-target.png")
        gui.click("focusOvertimeButton")
        assert not controller.focus["reached"]
        advance(15)
        gui.click("tasksPageButton")
        controller.selectTask(ids["Курсовая"])
        gui.click("startFocusButton")
        assert "Завершить текущую" in gui.item("confirmFocusButton").property("text")
        gui.click("confirmFocusButton")
        assert controller.focus["title"] == "Курсовая"
        assert not controller.focus["target"]
        advance(10)
        gui.click("finishFocusButton")
        assert not controller.focus
        controller.selectTask(ids["Лабораторная"])
        assert controller.detail["focusTotal"] == "00:01:15"
        assert controller.detail["progress"] == 0
        gui.click("focusHistoryButton")
        gui.screenshot(profile / "focus-history.png")
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
