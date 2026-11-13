"""Жизненный цикл окна: трей, потеря трея, подтверждение выхода и XDG-настройка."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QTimer
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.platform.desktop import DesktopIntegration
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
available = sys.argv[2] != "unavailable"
QQuickStyle.setStyle("Basic")
app = QApplication([])
now = datetime(2026, 11, 13, 10, tzinfo=UTC)
with TaskStore(profile / "tasks.sqlite3") as store:
    task = Task(title="Проверка трея")
    store.create_task(task)
controller = TaskController(profile, clock=lambda: now, zone=UTC)
desktop = DesktopIntegration(app, profile, profile / "config", [sys.executable, "-m", "ritm"])
if sys.argv[2] == "native":
    assert desktop.tray.isSystemTrayAvailable(), "Стенд должен иметь действующий трей"
else:
    desktop.tray.isSystemTrayAvailable = lambda: available
desktop.refreshAvailability()
engine = load_window(controller, desktop)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
try:
    gui.idle()
    gui.wait_for(lambda: not desktop.settings["busy"])
    desktop.attach(window, hidden=True)
    assert window.isVisible() != available
    desktop.menu.actions()[0].trigger()
    assert window.isVisible()
    gui.click("systemButton")
    assert not gui.item("autoStart").property("checked")
    # Closing settings is cancellation; no desktop file is created.
    gui.click("autoStart")
    gui.click("closeSystemDialog")
    assert not desktop.path.exists()
    gui.click("systemButton")
    assert not gui.item("autoStart").property("checked")
    gui.click("autoStart")
    gui.click("hiddenStart")
    gui.click("saveAutostart")
    gui.wait_for(lambda: not desktop.settings["busy"])
    assert desktop.settings["enabled"] and desktop.settings["hidden"]
    assert desktop.path.exists()
    gui.screenshot(profile / "system-settings.png")
    gui.click("closeSystemDialog")
    controller.saveAppearance("light", 150)
    gui.idle()
    window.setWidth(640)
    window.setHeight(480)
    gui.wait_for(lambda: window.width() == 640)
    gui.click("systemButton")
    gui.screenshot(profile / "system-settings-narrow.png")
    gui.click("autoStart")
    gui.click("saveAutostart")
    gui.wait_for(lambda: not desktop.settings["busy"])
    assert not desktop.path.exists()
    gui.click("closeSystemDialog")
    window.setWidth(1120)
    window.setHeight(780)
    controller.saveAppearance("light", 100)
    gui.idle()
    gui.click("startFocusButton")
    gui.click("confirmFocusButton")
    now += timedelta(seconds=20)
    if available:
        assert not window.close()
        assert not window.isVisible() and not controller.focus["paused"]
        QTest.qWait(100)
        # Динамическая потеря трея возвращает окно.
        available = False
        desktop.tray.isSystemTrayAvailable = lambda: available
        desktop.refreshAvailability()
        assert window.isVisible()
        available = True
        desktop.refreshAvailability()
        window.close()
        assert not window.isVisible()
        desktop.menu.actions()[-1].trigger()
    else:
        assert not window.close()
    gui.wait_for(lambda: gui.item("exitFocusDialog").property("visible"))
    gui.click("cancelFocusExit")
    assert window.isVisible()
    if available:
        window.close()
        assert not window.isVisible()
        desktop.showWindow()
    # Проверяем настоящий путь выхода через кнопку, а не только close().
    gui.click("systemButton")
    gui.click("exitApplication")
    gui.wait_for(lambda: gui.item("exitFocusDialog").property("visible"))
    QTimer.singleShot(0, lambda: gui.click("confirmFocusExit"))
    QTimer.singleShot(10000, lambda: app.exit(9))
    assert app.exec() == 0
    assert not window.isVisible()
    with TaskStore(profile / "tasks.sqlite3") as store:
        (session,) = store.list_focus_sessions()
        assert session.finished_us is not None and session.worked_us == 20_000_000
finally:
    desktop.close()
    controller.close()
    engine = None
    app.processEvents()
