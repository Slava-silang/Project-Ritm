"""Недельная статистика в QML с фиксированным местным временем."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
now = datetime(2026, 11, 9, 12, tzinfo=UTC)
deadline = now + timedelta(minutes=1)
first = Task(title="Первая", deadline=deadline, expected_duration=timedelta(minutes=20))
second = Task(title="Вторая", deadline=deadline)
with TaskStore(profile / "tasks.sqlite3", clock=lambda: now) as store:
    store.create_task(first)
    store.create_task(second)
    store.add_focus_record(first.id, now - timedelta(minutes=15), now, now)
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
controller = TaskController(profile, clock=lambda: now, zone=UTC)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)

try:
    gui.idle()
    gui.click("statisticsPageButton")
    assert controller.statistics["taskCount"] == 2
    assert controller.statistics["totalFocus"] == "00:15:00"
    assert "0,00%" in gui.item("weeklyProgressLabel").property("text")
    now += timedelta(minutes=2)
    gui.wait_for(lambda: controller.statistics["overdueCount"] == 2, "deadline passes")
    controller.selectTask(str(first.id))
    controller.setCompleted(str(first.id), True)
    gui.idle()
    assert controller.statistics["completedCount"] == 1
    assert controller.statistics["overdueCount"] == 1
    assert "50,00%" in gui.item("weeklyProgressLabel").property("text")
    gui.screenshot(profile / "weekly-statistics.png")
    controller.saveTask(str(second.id), {"title": second.title, "deadline": "16.11.2026 10:00"})
    gui.idle()
    gui.click("statisticsPageButton")
    assert controller.statistics["taskCount"] == 1
    gui.click("nextStatsWeek")
    assert controller.statistics["taskCount"] == 1
    assert controller.statistics["completedCount"] == 0
    gui.click("nextStatsWeek")
    assert "нет задач" in gui.item("weeklyProgressLabel").property("text")
    gui.click("previousStatsWeek")
    gui.click("currentStatsWeek")
    assert controller.statistics["start"] == "2026-11-09"
    assert "100,00%" in gui.item("weeklyProgressLabel").property("text")
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
