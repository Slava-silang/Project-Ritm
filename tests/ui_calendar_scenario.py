"""Календарь в настоящем QML-окне с отдельным профилем."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from ritm.app import load_window
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
profile = Path(sys.argv[1])
now = [datetime(2026, 12, 31, 10, tzinfo=UTC)]
reopen = "--reopen" in sys.argv
if not reopen:
    task = Task(
        title="Лабораторная по механике",
        expected_duration=timedelta(hours=5),
        deadline=datetime(2027, 1, 1, 0, 30, tzinfo=UTC),
    )
    with TaskStore(profile / "tasks.sqlite3") as store:
        TaskService(store).create_task(task)
    task_id = str(task.id)
else:
    task_id = sys.argv[-1]
controller = TaskController(profile, clock=lambda: now[0], zone=UTC)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)

try:
    gui.idle()
    gui.click("calendarPageButton")
    assert not controller.error
    assert controller.calendar["mode"] == "week"
    assert sum(day["deadlineCount"] for day in controller.calendar["days"]) == 1
    if reopen:
        assert controller.detail["plannedText"] == "3 ч"
        assert sum(day["blockCount"] for day in controller.calendar["days"]) == 3
        gui.screenshot(profile / "calendar-reopened.png")
        print(json.dumps({"reopened": True}))
        raise SystemExit(0)
    assert sum(day["blockCount"] for day in controller.calendar["days"]) == 0
    gui.click("calendarNextButton")
    assert controller.calendar["days"][0]["date"] == "2027-01-04"
    gui.click("calendarPrevButton")
    gui.click("calendarMonthButton")
    assert controller.calendar["title"] == "Декабрь 2026"
    gui.click("calendarNextButton")
    assert controller.calendar["title"] == "Январь 2027"
    gui.click("calendarTodayButton")
    assert controller.calendar["title"] == "Декабрь 2026"
    gui.click("calendarWeekButton")
    gui.click("calendarDay_2")
    assert controller.calendar["selected"] == "2026-12-30"
    gui.click("newBlockButton")
    assert gui.item("blockStartInput").property("text") == "30.12.2026 09:00"
    gui.click("cancelBlockButton")
    gui.click("calendarDay_3")
    gui.click("newBlockButton")
    gui.fill("blockStartInput", "31.12.2026 23:00")
    gui.fill("blockEndInput", "31.12.2026 22:00")
    gui.click("saveBlockButton")
    assert controller.error
    gui.screenshot(profile / "calendar-form.png")
    assert gui.item("blockStartInput").property("text") == "31.12.2026 23:00"
    with TaskStore(profile / "tasks.sqlite3") as store:
        assert not store.list_work_blocks()
    gui.fill("blockEndInput", "01.01.2027 01:00")
    gui.click("saveBlockButton")
    assert not controller.error
    assert "позже дедлайна" in controller.notice
    assert controller.detail["plannedText"] == "2 ч"
    assert sum(day["blockCount"] for day in controller.calendar["days"]) == 2
    gui.click("newBlockButton")
    gui.fill("blockStartInput", "01.01.2027 00:00")
    gui.fill("blockEndInput", "01.01.2027 01:00")
    gui.click("saveBlockButton")
    assert "пересечение" in controller.notice
    assert controller.detail["plannedText"] == "3 ч"
    assert controller.detail["minutes"] == "300"
    gui.screenshot(profile / "calendar-week.png")
    event_id = next(
        event["id"]
        for day in controller.calendar["days"]
        for event in day["entries"]
        if event["kind"] == "block"
    )
    gui.click("calendarEvent_" + event_id)
    assert window.property("page") == "tasks"
    assert controller.detail["id"] == task_id
    assert controller.detail["plannedText"] == "3 ч"
    gui.click("calendarPageButton")
    gui.click("calendarMonthButton")
    gui.screenshot(profile / "calendar-month.png")
    gui.click("newBlockButton")
    gui.click("cancelBlockButton")
    assert controller.detail["plannedText"] == "3 ч"
    window.resize(640, 480)
    gui.wait_for(lambda: window.width() < 850, "narrow calendar")
    gui.screenshot(profile / "calendar-narrow.png")
    gui.click("tasksPageButton")
    assert controller.detail["plannedText"] == "3 ч"
    gui.click("deleteTaskButton")
    gui.click("confirmDeleteButton")
    gui.click("calendarPageButton")
    assert not any(day["entries"] for day in controller.calendar["days"])
    gui.click("tasksPageButton")
    gui.click("trashButton")
    gui.click("restore_0")
    gui.click("closeTrashButton")
    gui.click("calendarPageButton")
    assert controller.detail["plannedText"] == "3 ч"
    assert any(day["entries"] for day in controller.calendar["days"])
    gui.click("calendarTodayButton")
    now[0] += timedelta(days=1)
    gui.wait_for(lambda: controller.calendar["today"] == "2027-01-01", "midnight refresh")
    print(json.dumps({"ok": True, "taskId": task_id}))
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
