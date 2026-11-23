"""Общая геометрия диалогов и скруглённых элементов в «Графите» и «Мяте»."""

import sys
from itertools import combinations
from pathlib import Path

from gui_support import GuiHarness, load_offscreen_fonts
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
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


def compare_with_mint(gui, percent, destination, *items):
    dark_geometry = [(scene_rect(item), item.isVisible()) for item in items]
    gui.controller.saveAppearance("light", percent)
    gui.idle()
    assert gui.window.color() == QColor("#f4f7f6")
    for item, expected in zip(items, dark_geometry, strict=True):
        assert (scene_rect(item), item.isVisible()) == expected, (item.objectName(), percent)
    if destination is not None:
        gui.screenshot(destination)
    gui.controller.saveAppearance("dark", percent)
    gui.idle()
    assert gui.window.color() == QColor("#171b1e")


def check_dialog(gui, name, action_names, scale, left_edge=0, *, left_shift=0):
    popup = gui.item(name)
    frame = popup.property("background")
    bounds = scene_rect(frame)
    assert frame.property("radius") >= 20 * scale
    expected_center = (gui.window.width() + left_edge) / 2 - left_shift
    assert abs(bounds.center().x() - expected_center) <= 2
    assert abs(bounds.center().y() - gui.window.height() / 2) <= 2
    assert bounds.left() > left_edge and bounds.top() > 0
    if left_edge:
        assert bounds.left() >= left_edge + 24 * scale - 1
    assert bounds.right() < gui.window.width()
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
    return frame, *actions


def check_dropdown(gui, percent, profile):
    name = "petChoice"
    box = gui.item(name)
    gui.click(name)
    popup = gui.item(name + "Popup")
    gui.wait_for(lambda: popup.property("opened"), "companion dropdown opens")
    frame = popup.property("background")
    view = popup.property("contentItem")
    assert frame.property("radius") == 12 * percent / 100
    assert scene_rect(view).left() > scene_rect(frame).left()
    assert scene_rect(view).right() < scene_rect(frame).right()
    compare_with_mint(
        gui, percent, profile / f"mint-companion-menu-{percent}.png", box, frame, view
    )

    QTest.keyClick(gui.window, Qt.Key.Key_Down)
    QTest.keyClick(gui.window, Qt.Key.Key_Return)
    gui.wait_for(lambda: not popup.property("visible"), "companion dropdown accepts keyboard")
    assert box.property("currentIndex") == 1

    gui.click(name)
    gui.wait_for(lambda: popup.property("opened"), "companion dropdown reopens")
    content = view.property("contentItem")
    option = next(item for item in content.childItems() if item.property("index") == 2)
    assert option.property("background").property("radius") == 8 * percent / 100
    point = option.mapToScene(QPointF(option.width() / 2, option.height() / 2)).toPoint()
    QTest.mouseClick(gui.window, Qt.MouseButton.LeftButton, pos=point)
    gui.wait_for(lambda: not popup.property("visible"), "companion dropdown accepts mouse")
    assert box.property("currentIndex") == 2

    gui.click(name)
    QTest.keyClick(gui.window, Qt.Key.Key_Up)
    QTest.keyClick(gui.window, Qt.Key.Key_Escape)
    gui.wait_for(lambda: not popup.property("visible"), "companion dropdown cancels with Escape")
    assert box.property("currentIndex") == 2


def check_detail_scroll(gui, percent, profile):
    bar = gui.item("detailScrollBar")
    assert 0 < bar.property("size") < 1
    assert all(
        scene_rect(gui.item(button)).right() <= scene_rect(bar).left() - 4 * percent / 100
        for button in ("planTaskButton", "startFocusButton", "weightsButton", "addSubtaskButton")
    )
    gui.reveal(gui.item("startFocusButton"))
    bar.setProperty("active", True)
    compare_with_mint(
        gui,
        percent,
        profile / f"mint-detail-scroll-{percent}.png",
        bar,
        *(gui.item(button) for button in ("startFocusButton", "weightsButton")),
    )


profile = Path(sys.argv[1])
save_appearance(profile, "dark", 100)
task = Task(
    title="Отчёт по практике",
    description="Сверить результаты и внести замечания. Подготовить выводы и отправить отчёт. " * 4,
)
with TaskStore(profile / "tasks.sqlite3") as store:
    store.create_task(task)

QQuickStyle.setStyle("Basic")
app = QApplication([])
load_offscreen_fonts(app)
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
    sidebar = gui.item("workspaceSidebar")

    for width, height, percent in ((1120, 780, 100), (1300, 820, 125), (640, 480, 150)):
        scale = percent / 100
        window.resize(width, height)
        controller.saveAppearance("dark", percent)
        gui.idle()
        compare_with_mint(
            gui,
            percent,
            profile / f"mint-tasks-{percent}.png",
            sidebar,
            *(gui.item(name) for name in ("tasksPageButton", "newTaskButton", "detailPanel")),
        )
        check_detail_scroll(gui, percent, profile)
        gui.click("systemButton")
        system_items = check_dialog(
            gui, "systemDialog", ("saveAutostart", "exitApplication", "closeSystemDialog"), scale
        )
        checkbox = gui.item("pauseOnSystem")
        assert checkbox.property("indicator").property("radius") == 5 * scale
        assert not checkbox.property("checked")
        gui.click("pauseOnSystem")
        assert checkbox.property("checked")
        compare_with_mint(
            gui, percent, profile / f"mint-settings-{percent}.png", checkbox, *system_items
        )
        gui.click("closeSystemDialog")

        gui.click("companionButton")
        companion_items = check_dialog(
            gui,
            "companionDialog",
            ("applyCompanion", "showCompanionAgain", "closeCompanionSettings"),
            scale,
        )
        assert gui.item("petBreak").property("background").property("radius") == 12 * scale
        compare_with_mint(gui, percent, None, gui.item("petBreak"), *companion_items)
        check_dropdown(gui, percent, profile)
        gui.click("closeCompanionSettings")

        gui.click("assistantButton")
        assistant_actions = ("assistantApply", "assistantCancel")
        left_edge = scene_rect(sidebar).right() if sidebar.isVisible() else 0
        left_shift = 24 * scale if sidebar.isVisible() else 0
        assistant_items = check_dialog(
            gui, "assistantDialog", assistant_actions, scale, left_edge, left_shift=left_shift
        )
        assert gui.item("chatSaveHistory").property("indicator").property("radius") == 5 * scale
        if percent == 100:
            gui.screenshot(profile / "dark-assistant-100.png")
        compare_with_mint(gui, percent, profile / f"mint-assistant-{percent}.png", *assistant_items)
        if percent == 100:
            window.resize(1040, height)
            gui.wait_for(lambda: gui.window.width() == 1040 and sidebar.isVisible())
            assistant_items = check_dialog(
                gui, "assistantDialog", assistant_actions, scale, left_edge, left_shift=left_shift
            )
            compare_with_mint(gui, percent, None, *assistant_items)
            window.resize(1000, height)
            gui.wait_for(lambda: not sidebar.isVisible(), "sidebar hides on resize")
            check_dialog(gui, "assistantDialog", assistant_actions, scale)
            window.resize(width, height)
            gui.wait_for(sidebar.isVisible, "sidebar returns on resize")
            check_dialog(
                gui, "assistantDialog", assistant_actions, scale, left_edge, left_shift=left_shift
            )
        gui.click("assistantCancel")

        gui.click("gardenButton")
        assert window.property("page") == "garden"
        assert sidebar.isVisible() == (percent < 150)
        assert gui.item("gardenEnabled").property("indicator").property("radius") == 5 * scale
        assert not controller.garden["enabled"]
        assert not (profile / "garden.sqlite3").exists()
        compare_with_mint(
            gui, percent, profile / f"mint-garden-{percent}.png", gui.item("gardenEnabled")
        )

        for page, controls in (
            ("calendar", ("calendarScroll", "calendarWeekButton", "newBlockButton")),
            ("statistics", ("weeklyProgressLabel", "previousStatsWeek")),
            ("materials", ("addMaterialButton",)),
        ):
            gui.click(f"{page}PageButton")
            assert window.property("page") == page
            compare_with_mint(
                gui,
                percent,
                profile / f"mint-{page}-{percent}.png" if percent == 100 else None,
                *(gui.item(name) for name in controls),
            )
        gui.click("tasksPageButton")
        gui.click("newTaskButton")
        compare_with_mint(
            gui,
            percent,
            profile / f"mint-editor-{percent}.png",
            *(gui.item(name) for name in ("titleInput", "formCategory", "saveTaskButton")),
        )
        QTest.keyClick(window, Qt.Key.Key_Escape)
        gui.wait_for(lambda: not gui.item("titleInput").isVisible(), "task editor closes")
finally:
    planner.close()
    agent.close()
    pet.close()
    desktop.close()
    controller.close()
    window.hide()
    engine = None
    app.processEvents()
