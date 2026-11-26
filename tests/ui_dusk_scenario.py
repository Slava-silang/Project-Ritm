"""Рабочие Сумерки: выбор без сада, вкладки/состояния/масштаб, новый процесс."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import Qt
from PySide6.QtQml import QQmlProperty
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.domain.tasks import Subtask, Task
from ritm.integrations.assistant import MockAdapter
from ritm.platform.companion import CompanionController
from ritm.platform.desktop import DesktopIntegration
from ritm.storage.sqlite import TaskStore
from ritm.ui.assistant import AssistantController
from ritm.ui.planning import PlanningController
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
now = datetime(2026, 11, 22, 10, tzinfo=UTC)
if mode == "view":
    with TaskStore(profile / "tasks.sqlite3") as store:
        task = Task(
            title="Подготовить отчёт по практике",
            description="Собрать результаты практики и оформить выводы.",
            difficulty=6,
            expected_duration=timedelta(hours=2),
            deadline=now + timedelta(hours=8),
            subtasks=(
                Subtask(title="Собрать материалы", completed=True),
                Subtask(title="Оформить результаты"),
            ),
        )
        store.create_task(task)
        store.create_task(Task(title="Сдать план доклада", deadline=now - timedelta(days=2)))
        store.create_task(Task(title="Разобрать конспект лекции", deadline=now + timedelta(days=1)))
        store.create_task(Task(title="Обновить список литературы"))
QQuickStyle.setStyle("Basic")
app = QApplication([])
backend = TaskController(profile, clock=lambda: now, zone=UTC, automatic_backup=False)
desktop = DesktopIntegration(app, profile, profile / "config", [sys.executable, "-m", "ritm"])
pet = CompanionController(profile, backend)
agent = AssistantController(profile, MockAdapter())
planner = PlanningController(profile, agent)
engine = load_window(backend, desktop, agent, planner=planner, companion=pet)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, backend)
appearance = gui.item("uiAppearance")


def colors():
    value = appearance.property("colors")
    return value.toVariant() if hasattr(value, "toVariant") else value


def luminance(color):
    rgb = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    rgb = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return sum(v * w for v, w in zip(rgb, (0.2126, 0.7152, 0.0722), strict=True))


def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b)))
    return (y + 0.05) / (x + 0.05)


def utility(name):
    gui.click(name)


try:
    gui.idle()
    gui.wait_for(lambda: not desktop.settings["busy"] and not pet.state["busy"])
    if mode == "reopen":
        assert backend.appearance == {"theme": "dusk", "scale": 150, "panel": "top"}
        assert appearance.property("dusk")
        assert not gui.item("workspaceSidebar").isVisible()
        assert colors()["accent"] == "#c4ccff"
    else:
        backend.selectTask(str(task.id))
        assert not (profile / "garden.sqlite3").exists()
        gui.click("appearanceButton")
        gui.choose("appearanceTheme", 5)
        gui.choose("appearancePanel", 1)
        assert gui.item("saveAppearance").isEnabled()
        gui.click("saveAppearance")
        assert backend.appearance["theme"] == "dusk"
        assert not (profile / "garden.sqlite3").exists()
        assert colors()["background"] == "#1b2031"
        assert colors()["accent"] == "#c4ccff"
        for key in ("text", "secondary", "muted"):
            for surface in ("background", "surface", "field", "selection"):
                assert contrast(colors()[key], colors()[surface]) >= 4.5, (key, surface)
        assert contrast(colors()["onAccent"], colors()["accentFill"]) >= 4.5
        for width, height, scale in ((1160, 800, 100), (1300, 850, 125), (640, 480, 150)):
            window.resize(width, height)
            backend.saveAppearance("dusk", scale)
            gui.idle()
            QTest.qWait(120)
            assert not gui.item("workspaceSidebar").isVisible()
            assert gui.item("duskNavigation").isVisible()
            gui.item("parametersButton").forceActiveFocus()
            QTest.keyClick(window, Qt.Key.Key_Space)
            gui.wait_for(lambda: gui.item("parametersMenu").property("opened"))
            assert gui.item("parametersMenu").property("count") == 4
            gui.screenshot(profile / f"parameters-menu-{scale}.png")
            QTest.keyClick(window, Qt.Key.Key_Escape)
            gui.wait_for(lambda: not gui.item("parametersMenu").property("visible"))
            assert gui.item("parametersButton").hasActiveFocus()
            gui.click("parametersButton")
            gui.wait_for(lambda: gui.item("parametersMenu").property("opened"))
            gui.click("parametersButton")
            gui.wait_for(lambda: not gui.item("parametersMenu").property("visible"))
            # Keyboard navigation reaches the same appearance dialog as a mouse click.
            gui.item("parametersButton").forceActiveFocus()
            QTest.keyClick(window, Qt.Key.Key_Space)
            gui.wait_for(lambda: gui.item("parametersMenu").property("opened"))
            QTest.keyClick(window, Qt.Key.Key_Home)
            QTest.keyClick(window, Qt.Key.Key_Down)
            QTest.keyClick(window, Qt.Key.Key_Down)
            QTest.keyClick(window, Qt.Key.Key_Return)
            gui.wait_for(lambda: gui.item("cancelAppearance").isVisible())
            assert gui.item("appearanceTheme").property("currentIndex") == 5
            gui.click("cancelAppearance")
            for button, page in (
                ("tasksPageButton", "tasks"),
                ("calendarPageButton", "calendar"),
                ("statisticsPageButton", "statistics"),
                ("materialsPageButton", "materials"),
                ("gardenButton", "garden"),
            ):
                gui.click(button)
                assert window.property("page") == page
                if page == "garden":
                    if not backend.garden["enabled"]:
                        gui.click("gardenEnabled")
                    gui.wait_for(lambda: backend.gardenBoard["ready"], "styled garden ready")
                gui.screenshot(profile / f"dusk-{page}-{scale}.png")
                if page == "garden":
                    gui.click(
                        "gardenProgressWhenOff"
                        if window.property("narrow")
                        else "gardenProgressButton"
                    )
                    gui.screenshot(profile / f"dusk-garden-progress-{scale}.png")
                    gui.click("closeGardenProgress")
            assert (
                gui.item("gardenEnabled").property("indicator").property("radius")
                == 5 * scale / 100
            )
            gui.click("tasksPageButton")
            gui.click("newTaskButton")
            field = gui.item("titleInput")
            assert field.hasActiveFocus()
            assert field.property("background").property("radius") == 14 * scale / 100
            gui.click("saveTaskButton")
            assert backend.error and field.property("invalid")
            assert QQmlProperty.read(field, "background.border.color").name() == colors()["warning"]
            gui.screenshot(profile / f"dusk-form-error-{scale}.png")
            gui.fill("titleInput", "Проверка нового оформления")
            assert not field.property("invalid")
            gui.screenshot(profile / f"dusk-form-{scale}.png")
            QTest.keyClick(window, Qt.Key.Key_Escape)
            app.processEvents()
            backend.reload()
            gui.idle()
            utility("assistantButton")
            gui.screenshot(profile / f"dusk-assistant-{scale}.png")
            gui.click("assistantCancel")
            utility("systemButton")
            gui.screenshot(profile / f"dusk-settings-{scale}.png")
            gui.click("closeSystemDialog")
        # Check switching back is safe; the existing Graphite variant remains available.
        backend.saveAppearance("dark", 100, "left")
        gui.idle()
        window.resize(1160, 800)
        QTest.qWait(100)
        assert gui.item("workspaceSidebar").isVisible()
        backend.saveAppearance("dusk", 150, "top")
        gui.idle()
        assert not gui.item("workspaceSidebar").isVisible()
finally:
    planner.close()
    agent.close()
    pet.close()
    desktop.close()
    backend.close()
    window.close()
    engine = None
    app.processEvents()
