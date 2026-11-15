"""Реальные QML-кнопки Pomodoro и восстановление в новом процессе."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.platform.companion import CompanionController
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
mode = sys.argv[2]
now = datetime(2026, 11, 16, 10, tzinfo=UTC)
if mode == "run":
    with TaskStore(profile / "tasks.sqlite3") as store:
        store.create_task(Task(title="Лабораторная · Pomodoro"))
        store.create_task(Task(title="Курсовая"))

QQuickStyle.setStyle("Basic")
app = QApplication([])
controller = TaskController(profile, clock=lambda: now, zone=UTC)
companion = CompanionController(profile, controller, open_main=lambda: None)
engine = load_window(controller, companion=companion)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
ended = []
controller.pomodoroEnded.connect(ended.append)


def advance(seconds):
    global now
    now += timedelta(seconds=seconds)
    controller._on_tick()
    gui.idle()


try:
    gui.idle()
    if mode == "reopen":
        assert controller.pomodoro["phase"] == "short"
        assert controller.pomodoro["cycle"] == 1
        assert controller.pomodoro["remaining"] == "00:00:40"
        assert not controller.pomodoro["running"]
        assert not controller.focus
        advance(3600)
        assert controller.pomodoro["remaining"] == "00:00:40"
        gui.click("pomodoroBreakPause")
        advance(40)
        assert controller.pomodoro["reached"]
        gui.click("pomodoroWorkButton")
        gui.click("pomodoroConfirmButton")
        assert controller.pomodoro["phase"] == "work"
        assert controller.focus["elapsed"] == "00:00:00"
        gui.click("pomodoroStopButton")
        gui.click("pomodoroConfirmStop")
        assert not controller.focus
        assert not controller.pomodoro["active"]
        assert controller.pomodoro["work"] == 1
    else:
        gui.wait_for(lambda: not companion.state["busy"])
        companion.configure(True, "dragon", 224, False, "normal", 5, False, False)
        gui.wait_for(lambda: not companion.state["busy"])
        gui.wait_for(lambda: bool(companion.window.property("imageReady")))
        controller.selectTask(controller.calendarTasks[0]["id"])
        gui.click("pomodoroOpenButton")
        gui.item("pomodoroWorkMinutes").setProperty("value", 1)
        gui.item("pomodoroShortMinutes").setProperty("value", 1)
        gui.item("pomodoroLongMinutes").setProperty("value", 2)
        gui.click("pomodoroConfirmButton")
        assert controller.pomodoro["active"] and controller.pomodoro["running"]
        assert controller.focus["target"] == ""
        advance(20)
        gui.click("focusPauseResume")
        advance(300)
        assert controller.pomodoro["remaining"] == "00:00:40"
        gui.click("focusPauseResume")
        # Same cycle when another task is chosen, explicit confirmation replaces old focus.
        gui.click("pomodoroOpenButton")
        choice = gui.item("pomodoroTaskChoice")
        other_id = next(
            t["id"] for t in controller.calendarTasks if t["id"] != controller.detail["id"]
        )
        choice.setProperty(
            "currentIndex",
            next(i for i, t in enumerate(controller.calendarTasks) if t["id"] == other_id),
        )
        choice.activated.emit(choice.property("currentIndex"))
        gui.click("pomodoroConfirmButton")
        advance(55)
        assert controller.pomodoro["reached"]
        assert controller.pomodoro["overtime"] == "00:00:15"
        assert ended == ["work"]
        assert "Pomodoro" in companion.state["phrase"]
        controller._on_tick()
        gui.idle()
        assert ended == ["work"]
        gui.screenshot(profile / "pomodoro-overtime.png")
        assert gui.item("pomodoroPane").width() > window.width() - 100
        assert not gui.item("pomodoroPane").property("compact")
        gui.click("pomodoroBreakButton")
        assert not controller.focus
        assert controller.pomodoro["phase"] == "short"
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert sum(s.worked_us for s in store.list_focus_sessions()) == 75_000_000
        advance(10)
        controller.pauseForSystem("lock")
        gui.idle()
        assert not controller.pomodoro["running"]
        advance(300)
        assert controller.pomodoro["remaining"] == "00:00:50"
        gui.click("pomodoroBreakPause")
        advance(10)
        assert controller.pomodoro["remaining"] == "00:00:40"
        controller.saveAppearance("dark", 150)
        gui.idle()
        window.setWidth(640)
        window.setHeight(480)
        app.processEvents()
        gui.screenshot(profile / "pomodoro-narrow.png")
        gui.click("pomodoroOpenButton")
        gui.screenshot(profile / "pomodoro-settings-narrow.png")
        assert not gui.item("pomodoroConfirmButton").isEnabled()
        gui.click("pomodoroDialogPauseButton")
        assert not controller.pomodoro["running"]
finally:
    companion.close()
    controller.close()
    window.hide()
    engine = None
