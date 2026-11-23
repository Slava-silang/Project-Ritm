"""Темы, масштаб, клавиатура и повторное открытие профиля."""

import sys
from pathlib import Path

from garden_support import unlock_themes
from gui_support import GuiHarness, load_offscreen_fonts
from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ritm.app import load_window
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
load_offscreen_fonts(app)
if sys.argv[2] == "run":
    unlock_themes(profile)
controller = TaskController(profile)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def key(code, modifiers=Qt.KeyboardModifier.ControlModifier):
    QTest.keyClick(window, code, modifiers)
    app.processEvents()
    gui.idle()


def check_observatory(look):
    sky = gui.item("observatorySky")
    QTest.mouseMove(window, QPoint(540, 75))
    gui.wait_for(lambda: sky.property("pointerX") > 0, "sky tracks pointer")
    gui.screenshot(profile / "observatory-tasks.png")
    timer = gui.item("observatoryFlightTimer")
    flyby = gui.item("observatoryFlyby")
    saucer = gui.item("observatorySaucer")
    for route in ("rightMeteor", "leftMeteor", "saucer", "rightMeteor"):
        timer.triggered.emit()
        assert flyby.property("route") == route
        gui.wait_for(lambda: 0.25 < flyby.property("progress"), "flyby in motion")
        assert flyby.isVisible()
        if route == "saucer":
            assert saucer.property("sourceSize").width() > 0
            assert saucer.scale() < 1
            assert flyby.x() > flyby.property("startX") and flyby.y() < flyby.property("startY")
            gui.screenshot(profile / "observatory-saucer.png")
        else:
            assert not saucer.isVisible()
            assert flyby.x() < flyby.property("startX") and flyby.y() > flyby.property("startY")
            assert (flyby.x() < sky.width() / 2) == (route == "leftMeteor")
            if route == "leftMeteor":
                gui.screenshot(profile / "observatory-left-meteor.png")
    look.setProperty("reduceMotion", True)
    gui.wait_for(lambda: not sky.property("moving"), "static sky")
    assert not flyby.isVisible() and not timer.property("running")
    look.setProperty("reduceMotion", False)


try:
    gui.idle()
    window.requestActivate()
    QTest.qWait(50)
    look = window.findChild(QObject, "uiAppearance")
    if sys.argv[2] == "reopen":
        assert controller.appearance == {"theme": "space", "scale": 150}
        assert look.property("dark") and look.property("usesSharedStyle")
        assert len(controller.tasks) == 1
    else:
        for index, name in enumerate(("space", "jungle", "light", "dark", "system")):
            gui.click("appearanceButton")
            gui.choose("appearanceTheme", index)
            gui.click("saveAppearance")
            assert controller.appearance["theme"] == name
            assert look.property("usesSharedStyle") == (name != "jungle")
            expected_dark = name in ("space", "dark") or (
                name == "system" and app.styleHints().colorScheme() == Qt.ColorScheme.Dark
            )
            assert look.property("dark") == expected_dark
            if name == "space":
                check_observatory(look)
        gui.click("appearanceButton")
        gui.choose("appearanceTheme", 2)
        gui.click("saveAppearance")
        key(Qt.Key.Key_N)
        title_background = gui.item("titleInput").property("background")
        assert title_background.property("radius") == 12
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
        assert not gui.item("workspaceSidebar").isVisible()
        gui.screenshot(profile / "dark-narrow-150.png")
        gui.click("appearanceButton")
        gui.screenshot(profile / "appearance-narrow.png")
        gui.click("cancelAppearance")
        gui.click("calendarPageButton")
        assert gui.item("calendarScroll").height() > 0
        gui.click("tasksPageButton")
        key(Qt.Key.Key_0)
        assert controller.appearance["scale"] == 100
        key(Qt.Key.Key_Equal)
        assert controller.appearance["scale"] == 125
        key(Qt.Key.Key_Minus)
        assert controller.appearance["scale"] == 100
        window.setWidth(1120)
        window.setHeight(780)
        gui.wait_for(gui.item("workspaceSidebar").isVisible, "wide sidebar")
        for page in (
            "tasksPageButton",
            "calendarPageButton",
            "statisticsPageButton",
            "materialsPageButton",
        ):
            gui.click(page)
            assert gui.item("workspaceSidebar").isVisible()
            gui.screenshot(profile / ("dark-" + page + ".png"))
        gui.click("tasksPageButton")
        gui.click("newTaskButton")
        gui.screenshot(profile / "graphite-task-editor.png")
        key(Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        gui.click("appearanceButton")
        gui.choose("appearanceTheme", 0)
        gui.choose("appearanceScale", 2)
        gui.click("saveAppearance")
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
