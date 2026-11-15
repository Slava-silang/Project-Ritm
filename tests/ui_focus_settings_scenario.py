"""Сон/блокировка без настоящего усыпления компьютера и настройка через QML."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtDBus import QDBusConnection
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.platform.desktop import DesktopIntegration
from ritm.platform.power import LinuxPowerMonitor
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
now = datetime(2026, 11, 16, 10, tzinfo=UTC)
QQuickStyle.setStyle("Basic")
app = QApplication([])
with TaskStore(profile / "tasks.sqlite3") as store:
    if mode == "run":
        store.create_task(Task(title="Работа с книгой"))
    task = store.list_tasks()[0]
controller = TaskController(profile, clock=lambda: now, zone=UTC)
bus = QDBusConnection("ritm-no-real-power-events")
power = LinuxPowerMonitor(controller, system_bus=bus, session_bus=bus)
desktop = DesktopIntegration(app, profile, profile / "config", [sys.executable, "-m", "ritm"])
engine = load_window(controller, desktop=desktop)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def advance(seconds):
    global now
    now += timedelta(seconds=seconds)
    controller._on_tick()
    gui.idle()


def set_policy(value):
    gui.click("systemButton")
    assert gui.item("pauseOnSystem").property("checked") == controller.pauseOnSystem
    if gui.item("pauseOnSystem").property("checked") != value:
        gui.click("pauseOnSystem")
    gui.click("saveFocusSettings")
    assert not controller.error
    assert controller.pauseOnSystem is value
    gui.click("closeSystemDialog")


try:
    gui.idle()
    gui.wait_for(lambda: not desktop.settings["busy"])
    controller.selectTask(str(task.id))
    if mode == "reopen":
        assert controller.pauseOnSystem is True
        assert controller.pomodoro["phase"] == "short"
        assert not controller.pomodoro["running"]
        advance(3600)
        assert controller.pomodoro["remaining"] == "00:00:50"
        set_policy(False)
        assert not controller.pomodoro["running"]
    else:
        assert controller.pauseOnSystem is False
        controller.startFocus(str(task.id), "", False)
        gui.idle()
        power.lockChanged(True)
        power.sleepChanged(True)
        advance(3600)
        power.sleepChanged(False)
        assert not controller.focus["paused"]
        assert controller.focus["elapsed"] == "01:00:00"
        assert not power._needs_inhibitor()
        controller.changeFocus("pause")
        gui.idle()
        power.sleepChanged(True)
        advance(3600)
        power.sleepChanged(False)
        assert controller.focus["paused"]
        assert controller.focus["elapsed"] == "01:00:00"
        controller.changeFocus("finish")
        gui.idle()
        controller.startPomodoro(str(task.id), 1, 1, 2, False)
        gui.idle()
        power.sleepChanged(True)
        advance(90)
        power.sleepChanged(False)
        assert controller.pomodoro["phase"] == "work"
        assert controller.pomodoro["overtime"] == "00:00:30"
        assert not controller.focus["paused"]
        controller.changePomodoro("break")
        gui.idle()
        power.lockChanged(True)
        power.sleepChanged(True)
        advance(90)
        power.sleepChanged(False)
        assert controller.pomodoro["phase"] == "short"
        assert controller.pomodoro["reached"]
        assert not controller.focus
        set_policy(True)
        controller.startPomodoro(str(task.id), 1, 1, 2, False)
        gui.idle()
        advance(60)
        controller.changePomodoro("break")
        gui.idle()
        advance(10)
        power.lockChanged(True)
        gui.idle()
        assert not controller.pomodoro["running"]
        advance(3600)
        assert controller.pomodoro["remaining"] == "00:00:50"
        # Disabling auto-pause never resumes an explicitly paused timer.
        set_policy(False)
        assert not controller.pomodoro["running"]
        set_policy(True)
        gui.click("systemButton")
        controller.saveAppearance("dark", 150)
        gui.idle()
        window.setWidth(640)
        window.setHeight(480)
        app.processEvents()
        gui.screenshot(profile / "focus-settings-narrow.png")
        gui.click("saveFocusSettings")
        assert controller.pauseOnSystem and not controller.error
        gui.screenshot(profile / "focus-settings-actions-narrow.png")
finally:
    power.close()
    desktop.close()
    controller.close()
    window.hide()
    engine = None
