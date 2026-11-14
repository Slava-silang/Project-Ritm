"""Реакции от обычных команд задач и настоящий fullscreen на X11."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from ritm.app import load_window
from ritm.domain.tasks import Subtask, Task
from ritm.platform.companion import CompanionController
from ritm.storage.sqlite import TaskStore
from ritm.ui.assistant import AssistantController
from ritm.ui.planning import PlanningController
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
QQuickStyle.setStyle("Basic")
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
now = [datetime(2030, 1, 7, 10, tzinfo=UTC)]
ticks = [0.0]
if mode == "run":
    with TaskStore(profile / "tasks.sqlite3") as store:
        task = Task(title="Проверка реакций", subtasks=(Subtask(title="Последний пункт"),))
        store.create_task(task)
controller = TaskController(profile, clock=lambda: now[0])
pet = CompanionController(profile, controller, clock=lambda: ticks[0])
agent = AssistantController(profile)
planner = PlanningController(profile, agent)
engine = load_window(controller, assistant=agent, planner=planner, companion=pet)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
fullscreen = QWidget()
fullscreen.setWindowTitle("Ритм — временная проверка fullscreen")


def ready():
    gui.wait_for(lambda: not pet.state["busy"])


try:
    gui.idle()
    ready()
    if mode == "run":
        gui.click("companionButton")
        gui.click("petEnabled")
        gui.choose("petFrequency", 2)
        gui.item("petBreak").setProperty("value", 5)
        gui.click("petReducedMotion")
        gui.click("petFullscreen")  # Сначала изолируем реакции от чужих окон рабочего стола.
        gui.click("applyCompanion")
        ready()
        assert pet.state["frequency"] == "often" and pet.state["reduce_motion"]
        gui.click("closeCompanionSettings")
        controller.selectTask(str(task.id))
        pet.toggleFocus()
        gui.idle()
        assert pet.state["mode"] == "working" and pet.state["phrase"]
        window.hide()
        now[0] += timedelta(minutes=5)
        ticks[0] += 300
        controller._update_focus()
        pet._react()
        assert "перерыв" in pet.state["phrase"]
        assert pet.window.isVisible()

        def capture():
            gui.wait_for(lambda: bool(pet.window.property("imageReady")))
            pet.window.requestUpdate()
            QTest.qWait(120)  # Software backend не испускает frameSwapped без нового dirty frame.
            return (
                app.primaryScreen().grabWindow(pet.window.winId()).toImage()
                if app.platformName() == "xcb"
                else pet.window.grabWindow()
            )

        first_frame = capture()
        ratio = pet.window.devicePixelRatio()
        sample = (round(40 * ratio), round(40 * ratio))
        initial_pixel = first_frame.pixelColor(*sample).getRgb()
        assert min(initial_pixel[:3]) > 230
        first_frame.save(str(profile / "pet-break.png"))
        pet.toggleFocus()
        gui.idle()
        assert pet.state["mode"] == "paused"
        now[0] += timedelta(hours=1)
        ticks[0] += 3600
        controller._update_focus()
        pet._react()
        assert pet.state["mode"] == "paused" and not pet.state["phrase"]
        ticks[0] += 40
        controller.setCompleted(str(task.subtasks[0].id), True)
        gui.idle()
        assert pet.state["mode"] == "happy" and pet.state["phrase"]
        assert pet.window.isVisible()
        happy_frame = capture()
        happy_frame.save(str(profile / "pet-happy.png"))
        assert initial_pixel == happy_frame.pixelColor(*sample).getRgb(), (
            "Картинка исчезла при обновлении подписи"
        )
        ticks[0] += 9
        pet._react()
        assert pet.state["mode"] == "paused"
        controller.reload()
        gui.idle()
        assert pet.state["mode"] == "paused"  # Обновление не празднует старые задачи.
        controller.changeFocus("finish")
        gui.idle()
        assert pet.state["mode"] == "idle"
        if app.platformName() == "xcb":
            window.showNormal()
            window.raise_()
            window.requestActivate()
            pet.configure(True, "dragon", 224, False, "often", 5, True, True)
            ready()
            gui.wait_for(lambda: not pet.state["fullscreen"])
            fullscreen.showFullScreen()
            fullscreen.raise_()
            fullscreen.activateWindow()
            gui.wait_for(lambda: pet.state["fullscreen"], "active X11 fullscreen")
            assert not pet.window.isVisible()
            pet.showAgain()
            assert not pet.window.isVisible()
            pet.hideTemporarily()
            fullscreen.showNormal()
            gui.wait_for(lambda: not pet.state["fullscreen"])
            assert not pet.window.isVisible()
            pet.showAgain()
            assert pet.window.isVisible()
            fullscreen.showFullScreen()
            fullscreen.raise_()
            fullscreen.activateWindow()
            gui.wait_for(lambda: pet.state["fullscreen"])
            pet.disable()
            ready()
            fullscreen.showNormal()
            assert not pet.window.isVisible()
        window.showNormal()
        pet.configure(True, "cat", 224, True, "rare", 25, True, False)
        ready()
        assert pet.state["muted"] and not pet.state["phrase"]
        gui.click("companionButton")
        window.resize(640, 480)
        controller.saveAppearance("dark", 150)
        gui.idle()
        gui.reveal(gui.item("petBreak"))
        gui.screenshot(profile / "reactions-settings.png")
        gui.click("closeCompanionSettings")
    else:
        assert pet.state["frequency"] == "rare" and pet.state["break_minutes"] == 25
        assert pet.state["reduce_motion"] and not pet.state["hide_fullscreen"]
        assert pet.state["muted"] and not pet.state["phrase"]
        assert not controller.focus and pet.state["mode"] == "idle"
finally:
    fullscreen.close()
    pet.close()
    planner.close()
    agent.close()
    controller.close()
    window.close()
    engine = None
    app.processEvents()
