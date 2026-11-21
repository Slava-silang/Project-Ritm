"""Периодическое сохранение не блокирует кнопки и не возвращает прокрутку назад."""

import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.storage.appearance import save_appearance
from ritm.storage.sqlite import TaskStore
from ritm.ui import tasks as ui_tasks
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
close_during_checkpoint = mode.endswith("-close")
mode = mode.removesuffix("-close")
closed = False
now = [datetime(2026, 11, 22, 10, tzinfo=UTC)]
save_appearance(profile, "dark", 100)
with TaskStore(profile / "tasks.sqlite3", clock=lambda: now[0]) as store:
    task = Task(title="Проверка фокуса", description="Строка подробного описания.\n" * 80)
    store.create_task(task)
QQuickStyle.setStyle("Basic")
app = QApplication([])
controller = TaskController(profile, clock=lambda: now[0], zone=UTC, automatic_backup=False)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
entered, release = threading.Event(), threading.Event()
original = ui_tasks.sync_garden if mode == "garden" else TaskStore.checkpoint_pomodoro


def held(*args, **kwargs):
    entered.set()
    assert release.wait(8), "test did not release the worker"
    return original(*args, **kwargs)


def idle():
    gui.wait_for(lambda: not controller._busy, "storage idle")
    assert not controller.error, controller.error


def button_snapshot():
    result = []
    for name in ("newTaskButton", "startFocusButton"):
        item = gui.item(name)
        point = item.mapToScene(QPointF())
        result.append(
            (
                point.x(),
                point.y(),
                item.width(),
                item.height(),
                item.property("background").property("color").name(),
                item.isEnabled(),
            )
        )
    return result


def trigger():
    entered.clear()
    release.clear()
    now[0] += timedelta(minutes=5) if mode == "garden" else timedelta(seconds=5)
    if mode == "garden":
        controller._on_garden_tick()
    else:
        controller._on_tick()
    gui.wait_for(entered.is_set, "checkpoint entered")


try:
    idle()
    controller.selectTask(str(task.id))
    if mode == "garden":
        controller.setGardenEnabled(True)
        idle()
        controller.startFocus(str(task.id), "", False)
    else:
        controller.startPomodoro(str(task.id), 25, 5, 15, False)
        idle()
        now[0] += timedelta(minutes=25)
        controller.changePomodoro("break")
    idle()
    controller._ticker.stop()
    controller._garden_timer.stop()
    if mode == "garden":
        ui_tasks.sync_garden = held
    else:
        TaskStore.checkpoint_pomodoro = held
    QTest.mouseMove(window, QPoint(1, 1))
    QTest.qWait(100)
    view = gui.item("detailScroll").property("contentItem")
    view.setProperty("contentY", 80)
    app.processEvents()
    failures = []
    completed = []
    controller.actionSucceeded.connect(completed.append)
    for i in range(2):
        baseline = button_snapshot()
        if i == 0:
            gui.screenshot(profile / "focus-stable-before.png")
        trigger()
        if i == 0:
            gui.screenshot(profile / "focus-stable-during.png")
        if controller.busy or button_snapshot() != baseline:
            failures.append("background checkpoint disabled/recolored/moved buttons")
        # Scrolling while a checkpoint runs must not be reverted on completion.
        view.setProperty("contentY", 140 + i * 40)
        position = view.property("contentY")
        release.set()
        idle()
        app.processEvents()
        QTest.qWait(80)
        if abs(view.property("contentY") - position) > 1:
            failures.append("checkpoint restored an old scroll position")
        if mode == "garden":
            assert controller.garden["balance"] == ("110.00" if i == 0 else "120.00")
    if set(completed) & {"garden_tick", "pomodoro_tick"}:
        failures.append("checkpoint was reported as a user action")
    # An accepted user action is queued once behind the current checkpoint.
    trigger()
    states = []
    controller.stateChanged.connect(lambda: states.append(controller.busy))
    controller.renameNode(str(task.id), "Сохранено во время фоновой записи")
    queued_busy = controller.busy
    release.set()
    if close_during_checkpoint:
        controller.close()
        closed = True
    else:
        idle()
    with TaskStore(profile / "tasks.sqlite3") as store:
        if store.get_task(task.id).title != "Сохранено во время фоновой записи":
            failures.append("user action was lost during checkpoint")
    if not close_during_checkpoint and (
        not queued_busy or not states or states[-1] or not all(states[:-1])
    ):
        failures.append(f"foreground busy transitions: {states}")
    assert not failures, failures
finally:
    release.set()
    if mode == "garden":
        ui_tasks.sync_garden = original
    else:
        TaskStore.checkpoint_pomodoro = original
    if not closed:
        controller.close()
    window.close()
    engine = None
    app.processEvents()
