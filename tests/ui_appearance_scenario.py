"""Темы-заготовки, масштаб, клавиатура и повторное открытие профиля."""

import sys
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ritm.app import load_window
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
controller = TaskController(profile)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def key(code, modifiers=Qt.KeyboardModifier.ControlModifier):
    QTest.keyClick(window, code, modifiers)
    app.processEvents()
    gui.idle()


try:
    gui.idle()
    window.requestActivate()
    QTest.qWait(50)
    if sys.argv[2] == "reopen":
        assert controller.appearance == {"theme": "space", "scale": 150}
        assert window.findChild(QObject, "uiAppearance").property("dark")
        assert len(controller.tasks) == 1
    else:
        for index, name in enumerate(("space", "jungle", "light", "dark", "system")):
            gui.click("appearanceButton")
            gui.choose("appearanceTheme", index)
            gui.click("saveAppearance")
            assert controller.appearance["theme"] == name
        key(Qt.Key.Key_N)
        gui.fill("titleInput", "Проверка оформления")
        key(Qt.Key.Key_F)
        assert gui.item("titleInput").isVisible()
        QTest.keyClick(window, Qt.Key.Key_Tab)
        assert window.activeFocusItem() is not None
        gui.click("saveTaskButton")
        assert len(controller.tasks) == 1
        key(Qt.Key.Key_F)
        assert window.activeFocusItem() == gui.item("searchInput")
        key(Qt.Key.Key_2)
        assert window.property("page") == "calendar"
        key(Qt.Key.Key_3)
        assert window.property("page") == "statistics"
        key(Qt.Key.Key_4)
        assert window.property("page") == "materials"
        key(Qt.Key.Key_1)
        assert window.property("page") == "tasks"
        gui.click("appearanceButton")
        gui.choose("appearanceTheme", 3)
        gui.choose("appearanceScale", 2)
        gui.click("saveAppearance")
        assert controller.appearance == {"theme": "dark", "scale": 150}
        window.setWidth(640)
        window.setHeight(480)
        gui.wait_for(lambda: window.width() == 640, "narrow layout")
        gui.screenshot(profile / "dark-narrow-150.png")
        gui.click("appearanceButton")
        gui.screenshot(profile / "appearance-narrow.png")
        gui.click("cancelAppearance")
        key(Qt.Key.Key_0)
        assert controller.appearance["scale"] == 100
        key(Qt.Key.Key_Equal)
        assert controller.appearance["scale"] == 125
        key(Qt.Key.Key_Minus)
        assert controller.appearance["scale"] == 100
        window.setWidth(1120)
        window.setHeight(780)
        for page in (
            "tasksPageButton",
            "calendarPageButton",
            "statisticsPageButton",
            "materialsPageButton",
        ):
            gui.click(page)
            gui.screenshot(profile / ("dark-" + page + ".png"))
        gui.click("appearanceButton")
        gui.choose("appearanceTheme", 0)
        gui.choose("appearanceScale", 2)
        gui.click("saveAppearance")
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
