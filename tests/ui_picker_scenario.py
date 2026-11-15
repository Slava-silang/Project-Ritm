"""I35: календарь, стрелки/колесо времени, отмена и сохранение выбранного момента."""

import sys
from datetime import UTC, datetime
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.assistant import AssistantController
from ritm.ui.planning import PlanningController
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
with TaskStore(profile / "tasks.sqlite3") as store:
    task = Task(title="Для проверки календаря")
    store.create_task(task)
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
backend = TaskController(profile, clock=lambda: datetime(2028, 2, 28, tzinfo=UTC), zone=UTC)
agent = AssistantController(profile)
planner = PlanningController(profile, agent)
engine = load_window(backend, assistant=agent, planner=planner)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, backend)

try:
    gui.idle()
    gui.click("calendarPageButton")
    gui.click("newBlockButton")
    gui.fill("blockStartInput", "28.02.2028 09:15 +03:00")
    gui.fill("blockEndInput", "29.02.2028 12:00 +03:00")
    gui.click("blockStartInputPicker")
    gui.click("blockStartInputDay_2028-02-29")
    hour = gui.item("blockStartInputHour")
    gui.reveal(hour)
    before = hour.property("value")
    point = hour.mapToScene(QPointF(hour.width() / 2, hour.height() / 2))
    global_point = QPointF(window.mapToGlobal(point.toPoint()))
    wheel = QWheelEvent(
        point,
        global_point,
        QPoint(),
        QPoint(0, 120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )
    app.sendEvent(window, wheel)
    app.processEvents()
    assert hour.property("value") == before + 1, "Колесо часов не сработало"
    minute = gui.item("blockStartInputMinute")
    minute.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Up)
    assert minute.property("value") == 16
    gui.screenshot(profile / "date-time-picker.png")
    window.resize(640, 480)
    backend.saveAppearance("dark", 150)
    gui.idle()
    gui.screenshot(profile / "date-time-picker-narrow.png")
    gui.click("blockStartInputPickerApply")
    assert gui.item("blockStartInput").property("text") == "29.02.2028 10:16 +03:00"
    gui.click("saveBlockButton")
    assert not backend.error, backend.error
    with TaskStore(profile / "tasks.sqlite3") as store:
        block = store.list_work_blocks()[0]
        assert block.starts_at == datetime(2028, 2, 29, 7, 16, tzinfo=UTC)
        assert block.ends_at == datetime(2028, 2, 29, 9, tzinfo=UTC)
    # Отмена не исправляет и не затирает уже набранный текст.
    gui.click("newBlockButton")
    gui.fill("blockStartInput", "неверный текст")
    gui.click("blockStartInputPicker")
    gui.click("blockStartInputPickerCancel")
    assert gui.item("blockStartInput").property("text") == "неверный текст"
    gui.fill("blockStartInput", "31.12.2028 23:30")
    gui.click("blockStartInputPicker")
    gui.click("blockStartInputNextMonth")
    gui.click("blockStartInputDay_2029-01-01")
    gui.click("blockStartInputPickerApply")
    assert gui.item("blockStartInput").property("text") == "01.01.2029 23:30"
    gui.click("cancelBlockButton")
    gui.click("newTaskButton")
    gui.fill("titleInput", "Задача с датой из календаря")
    gui.fill("deadlineInput", "28.02.2028 11:00")
    gui.click("deadlineInputPicker")
    gui.click("deadlineInputDay_2028-02-29")
    gui.click("deadlineInputPickerApply")
    assert gui.item("deadlineInput").property("text") == "29.02.2028 11:00"
    gui.click("deadlineInputPicker")
    gui.click("deadlineInputPickerClear")
    assert gui.item("deadlineInput").property("text") == ""
    gui.click("saveTaskButton")
    gui.click("calendarPageButton")
    gui.click("planningButton")
    gui.wait_for(lambda: not planner.state["busy"])
    gui.fill("planningFirst", "2028-02-28")
    gui.click("planningFirstPicker")
    gui.click("planningFirstDay_2028-02-29")
    gui.click("planningFirstPickerApply")
    assert gui.item("planningFirst").property("text") == "2028-02-29"
    gui.click("workingEnd_0Picker")
    gui.item("workingEnd_0Hour").setProperty("value", 24)
    assert gui.item("workingEnd_0Minute").property("to") == 0
    gui.click("workingEnd_0PickerApply")
    assert gui.item("workingEnd_0").property("text") == "24:00"
    assert not gui.item("planningGenerate").isEnabled()  # Программный выбор тоже отмечает dirty.
    gui.click("saveWorkingHours")
    gui.wait_for(lambda: not planner.state["busy"])
    assert not planner.state["error"], planner.state["error"]
    gui.click("planningClose")
finally:
    planner.close()
    agent.close()
    backend.close()
    window.close()
    engine = None
    app.processEvents()
