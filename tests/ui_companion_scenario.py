"""Проверка временных персонажей и реальных X11-кликов через маску."""

import ctypes
import sys
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.platform.companion import CompanionController
from ritm.storage.sqlite import TaskStore
from ritm.ui.assistant import AssistantController
from ritm.ui.planning import PlanningController
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
QQuickStyle.setStyle("Basic")
app = QApplication([])
original_cursor = QCursor.pos()
app.setQuitOnLastWindowClosed(False)
if mode == "run":
    with TaskStore(profile / "tasks.sqlite3") as store:
        task = Task(title="Проверка фокуса компаньона")
        store.create_task(task)
controller = TaskController(profile)
opened = []
pet = CompanionController(profile, controller, open_main=lambda: opened.append(True))
agent = AssistantController(profile)
planner = PlanningController(profile, agent)
engine = load_window(controller, assistant=agent, planner=planner, companion=pet)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def ready():
    gui.wait_for(lambda: not pet.state["busy"])


class Underlay(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint)
        self.clicks = 0

    def mousePressEvent(self, event):
        self.clicks += 1


underlay = Underlay()
try:
    gui.idle()
    ready()
    if mode == "run":
        assert not pet.window.isVisible()
        gui.click("companionButton")
        gui.click("petEnabled")
        gui.choose("petChoice", 1)
        gui.choose("petSize", 1)
        gui.click("applyCompanion")
        ready()
        assert pet.window.isVisible() and pet.state["pet"] == "cat"
        gui.click("closeCompanionSettings")
        rect = app.primaryScreen().availableGeometry()
        target = rect.topLeft() + QPoint(100, 100)
        underlay.setGeometry(target.x(), target.y(), 450, 450)
        underlay.show()
        underlay.raise_()
        pet.window.setPosition(target + QPoint(70, 70))
        pet.window.show()
        QTest.qWait(200)
        assert not pet.window.mask().contains(QPoint(2, 2))
        assert pet.window.mask().contains(QPoint(112, 112))
        if app.platformName() == "xcb":
            x11 = ctypes.CDLL("libX11.so.6")
            xtest = ctypes.CDLL("libXtst.so.6")
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XFlush.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            xtest.XTestFakeMotionEvent.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_ulong,
            ]
            xtest.XTestFakeButtonEvent.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_int,
                ctypes.c_ulong,
            ]
            display = x11.XOpenDisplay(None)
            assert display
            try:

                def native_click(point):
                    ratio = app.primaryScreen().devicePixelRatio()
                    xtest.XTestFakeMotionEvent(
                        display, -1, round(point.x() * ratio), round(point.y() * ratio), 0
                    )
                    xtest.XTestFakeButtonEvent(display, 1, 1, 0)
                    xtest.XTestFakeButtonEvent(display, 1, 0, 0)
                    x11.XFlush(display)
                    QTest.qWait(100)

                native_click(pet.window.position() + QPoint(2, 2))
                assert underlay.clicks == 1
                native_click(pet.window.position() + QPoint(112, 112))
                assert underlay.clicks == 1
            finally:
                x11.XCloseDisplay(display)
        before = pet.window.position()
        QTest.mousePress(pet.window, Qt.LeftButton, pos=QPoint(112, 112))
        QTest.mouseMove(pet.window, QPoint(140, 140), 50)
        QTest.mouseRelease(pet.window, Qt.LeftButton, pos=QPoint(140, 140))
        assert pet.window.position() != before
        QTest.qWait(250)
        ready()
        pet.window.grabWindow().save(str(profile / "cat-window.png"))
        pet.configure(True, "dog", 288)
        ready()
        assert pet.state["size"] == 288 and pet.state["pet"] == "dog"
        pet.window.grabWindow().save(str(profile / "dog-window.png"))
        controller.selectTask(controller.tasks[0]["id"])
        pet.toggleFocus()
        gui.idle()
        assert controller.focus and not controller.focus["paused"]
        pet.toggleFocus()
        gui.idle()
        assert controller.focus["paused"]
        pet.toggleFocus()
        gui.idle()
        assert not controller.focus["paused"]
        controller.changeFocus("finish")
        gui.idle()
        pet.showMenu(pet.window.position())
        pet.menu.actions()[0].trigger()
        pet.menu.close()
        assert opened
        pet.hideTemporarily()
        assert not pet.window.isVisible() and pet.state["enabled"]
        pet._hide_timer.timeout.emit()
        assert pet.window.isVisible()
        pet.window.close()
        ready()
        assert not pet.state["enabled"] and not pet.window.isVisible()
        pet.showAgain()
        assert not pet.window.isVisible()
        pet.configure(True, "dragon", 224)
        ready()
        pet.window.grabWindow().save(str(profile / "dragon-window.png"))
        gui.click("companionButton")
        gui.screenshot(profile / "companion-settings.png")
        window.resize(640, 480)
        controller.saveAppearance("dark", 150)
        gui.idle()
        gui.screenshot(profile / "companion-settings-narrow.png")
        gui.click("closeCompanionSettings")
    else:
        assert pet.state["pet"] == "dragon" and pet.state["enabled"]
        assert pet.window.isVisible()
        assert pet.state["x"] is not None and pet.state["y"] is not None
        assert not controller.focus
finally:
    QCursor.setPos(original_cursor)
    pet.close()
    planner.close()
    agent.close()
    controller.close()
    underlay.close()
    window.close()
    engine = None
    app.processEvents()
