"""Геометрия диалогов и скруглённые контролы в рабочем QML «Графита»."""

import os
import sys
from itertools import combinations
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.platform.companion import CompanionController
from ritm.platform.desktop import DesktopIntegration
from ritm.storage.appearance import save_appearance
from ritm.storage.sqlite import TaskStore
from ritm.ui.assistant import AssistantController
from ritm.ui.planning import PlanningController
from ritm.ui.tasks import TaskController


def scene_rect(item):
    return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))


def check_dialog(gui, name, action_names, scale):
    popup = gui.item(name)
    frame = popup.property("background")
    bounds = scene_rect(frame)
    assert frame.property("radius") >= 20 * scale
    assert abs(bounds.center().x() - gui.window.width() / 2) <= 2
    assert abs(bounds.center().y() - gui.window.height() / 2) <= 2
    assert bounds.left() > 0 and bounds.top() > 0
    assert popup.property("header").property("background") is None
    actions = [gui.item(action) for action in action_names]
    assert all(action.isVisible() for action in actions)
    rectangles = [scene_rect(action) for action in actions]
    for action, rect in zip(action_names, rectangles, strict=True):
        assert rect.left() >= bounds.left() + 12 * scale, (action, rect, bounds)
        assert rect.right() <= bounds.right() - 12 * scale, (action, rect, bounds)
        assert rect.bottom() <= bounds.bottom() - 10 * scale, (action, rect, bounds)
    for first, second in combinations(rectangles, 2):
        x_gap = max(first.left() - second.right(), second.left() - first.right())
        y_gap = max(first.top() - second.bottom(), second.top() - first.bottom())
        assert max(x_gap, y_gap) >= 8 * scale - 1, (name, first, second)


profile = Path(sys.argv[1])
save_appearance(profile, "dark", 100)
task = Task(title="Отчёт по практике")
with TaskStore(profile / "tasks.sqlite3") as store:
    store.create_task(task)

QQuickStyle.setStyle("Basic")
app = QApplication([])
if sys.platform == "win32" and app.platformName() == "offscreen":
    fonts = Path(os.environ["WINDIR"]) / "Fonts"
    for name in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "seguisym.ttf"):
        QFontDatabase.addApplicationFont(str(fonts / name))
    app.setFont(QFont("Segoe UI"))
controller = TaskController(profile)
desktop = DesktopIntegration(app, profile, profile / "config", [sys.executable, "-m", "ritm"])
pet = CompanionController(profile, controller)
agent = AssistantController(profile)
planner = PlanningController(profile, agent)
engine = load_window(controller, desktop, agent, planner=planner, companion=pet)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)

try:
    gui.idle()
    gui.wait_for(lambda: not desktop.settings["busy"] and not pet.state["busy"])
    controller.selectTask(str(task.id))
    gui.idle()
    assert gui.item("deleteTaskButton").property("background").property("radius") == 12
    assert gui.item("taskComplete").property("indicator").property("radius") == 5
    gui.screenshot(profile / "tasks-100.png")

    for width, height, percent in ((1120, 780, 100), (900, 650, 125), (640, 480, 150)):
        scale = percent / 100
        window.resize(width, height)
        controller.saveAppearance("dark", percent)
        gui.idle()
        gui.click("systemButton")
        check_dialog(
            gui, "systemDialog", ("saveAutostart", "exitApplication", "closeSystemDialog"), scale
        )
        checkbox = gui.item("pauseOnSystem")
        assert checkbox.property("indicator").property("radius") == 5 * scale
        assert not checkbox.property("checked")
        gui.click("pauseOnSystem")
        assert checkbox.property("checked")
        gui.screenshot(profile / f"settings-{percent}.png")
        gui.click("closeSystemDialog")

        gui.click("companionButton")
        check_dialog(
            gui,
            "companionDialog",
            ("applyCompanion", "showCompanionAgain", "closeCompanionSettings"),
            scale,
        )
        assert gui.item("petBreak").property("background").property("radius") == 12 * scale
        gui.screenshot(profile / f"companion-{percent}.png")
        gui.click("closeCompanionSettings")

        gui.click("assistantButton")
        check_dialog(gui, "assistantDialog", ("assistantApply", "assistantCancel"), scale)
        assert gui.item("chatSaveHistory").property("indicator").property("radius") == 5 * scale
        gui.screenshot(profile / f"assistant-{percent}.png")
        gui.click("assistantCancel")

        gui.click("gardenButton")
        assert window.property("page") == "garden"
        assert gui.item("uiAppearance").property("graphite")
        assert gui.item("graphiteSidebar").isVisible() == (percent == 100)
        assert gui.item("gardenEnabled").property("indicator").property("radius") == 5 * scale
        assert not controller.garden["enabled"]
        assert not (profile / "garden.sqlite3").exists()
        gui.screenshot(profile / f"garden-{percent}.png")
        gui.click("tasksPageButton")
finally:
    planner.close()
    agent.close()
    pet.close()
    desktop.close()
    controller.close()
    window.hide()
    engine = None
    app.processEvents()
