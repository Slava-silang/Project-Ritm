"""Проверка сигналов через отдельную D-Bus шину и подтверждения выхода."""

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtDBus import QDBusConnection, QDBusMessage
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.platform.power import (
    CINNAMON,
    CINNAMON_PATH,
    LOGIN,
    LOGIN_PATH,
    MANAGER,
    LinuxPowerMonitor,
)
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
now = datetime(2026, 11, 9, 10, tzinfo=UTC)
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
direct = "--direct" in sys.argv
bus = QDBusConnection("ritm-test-disconnected") if direct else QDBusConnection.sessionBus()
if not direct:
    assert bus.registerService(LOGIN) and bus.registerService(CINNAMON)
with TaskStore(profile / "tasks.sqlite3") as store:
    task = Task(title="Лабораторная")
    store.create_task(task)
controller = TaskController(profile, clock=lambda: now, zone=UTC)
power = LinuxPowerMonitor(controller, system_bus=bus, session_bus=bus)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def emit(path, interface, member, value):
    if direct:
        handler = power.sleepChanged if interface == MANAGER else power.lockChanged
        handler(value)
        return
    message = QDBusMessage.createSignal(path, interface, member)
    message.setArguments([value])
    assert bus.send(message)


def flush():
    loop = QEventLoop()
    QTimer.singleShot(100, loop.quit)
    loop.exec()


try:
    gui.idle()
    gui.click("startFocusButton")
    gui.click("confirmFocusButton")
    now += timedelta(seconds=20)
    emit(CINNAMON_PATH, CINNAMON, "ActiveChanged", True)
    gui.wait_for(lambda: controller.focus.get("paused"), "screen lock pauses focus")
    assert controller.focus["elapsed"] == "00:00:20"
    now += timedelta(minutes=5)
    emit(CINNAMON_PATH, CINNAMON, "ActiveChanged", False)
    flush()
    assert controller.focus["paused"]
    gui.click("focusPauseResume")

    now += timedelta(seconds=15)
    # Hold a database command in flight when the sleep signal arrives.

    with sqlite3.connect(profile / "tasks.sqlite3") as writer:
        writer.execute("BEGIN IMMEDIATE")
        controller.reload()
        assert controller.busy
        emit(LOGIN_PATH, MANAGER, "PrepareForSleep", True)
        gui.wait_for(lambda: controller._pending_system_pause is not None, "queued sleep")
        now += timedelta(hours=1)
        writer.commit()
    gui.wait_for(lambda: controller.focus.get("paused"), "sleep pauses focus")
    gui.idle()
    assert controller.focus["elapsed"] == "00:00:35"
    emit(LOGIN_PATH, MANAGER, "PrepareForSleep", False)
    flush()
    assert controller.focus["paused"]

    assert not window.close()
    gui.click("cancelFocusExit")
    assert window.isVisible() and controller.focus["paused"]
    assert not window.close()
    gui.screenshot(profile / "focus-exit.png")
    gui.click("confirmFocusExit")
    gui.wait_for(lambda: not window.isVisible(), "window closes after save")
    with TaskStore(profile / "tasks.sqlite3") as store:
        (session,) = store.list_focus_sessions()
        assert session.finished_us is not None and session.worked_us == 35_000_000
finally:
    power.close()
    controller.close()
    engine = None
    app.processEvents()
