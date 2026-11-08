"""События календаря без задач и без влияния на плановую сумму задач."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from ritm.app import load_window
from ritm.application.calendar import CalendarService
from ritm.application.tasks import TaskService
from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
profile = Path(sys.argv[1])
path = profile / "tasks.sqlite3"
clock = datetime(2026, 11, 9, 9, tzinfo=UTC)
controller = TaskController(profile, clock=lambda: clock, zone=UTC)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def saved(identifier):
    with TaskStore(path) as store:
        return store.get_work_block(UUID(identifier))


try:
    gui.idle()
    gui.click("calendarPageButton")
    if "--reopen" in sys.argv:
        identifier = sys.argv[-1]
        current = saved(identifier)
        assert current.task_id is None and current.title == "Пара по физике"
        assert current.starts_at == datetime(2026, 11, 10, 10, 15, tzinfo=UTC)
        assert current.ends_at == datetime(2026, 11, 10, 11, 45, tzinfo=UTC)
        assert not controller.tasks and not controller.calendarTasks
        assert sum(x["eventCount"] for x in controller.calendar["days"]) == 2
        print(json.dumps({"reopened": True}))
        raise SystemExit(0)
    assert not controller.tasks
    gui.click("newBlockButton")
    assert gui.item("blockKindInput").property("currentIndex") == 1
    gui.fill("blockStartInput", "09.11.2026 10:00")
    gui.fill("blockEndInput", "09.11.2026 11:00")
    gui.click("saveBlockButton")
    assert "Название события" in controller.error
    gui.screenshot(profile / "event-form.png")
    gui.fill("eventTitleInput", "Пара по математике")
    gui.click("saveBlockButton")
    assert not controller.error
    assert not controller.tasks
    identifier = controller.calendar["timeline"][0]["id"]
    assert saved(identifier).task_id is None
    task = Task(title="Лабораторная", deadline=datetime(2026, 11, 9, 10, 30, tzinfo=UTC))
    with TaskStore(path) as store:
        TaskService(store).create_task(task)
        CalendarService(store).create_block(
            WorkBlock(
                task_id=task.id,
                starts_at=datetime(2026, 11, 9, 10, 30, tzinfo=UTC),
                ends_at=datetime(2026, 11, 9, 11, 30, tzinfo=UTC),
            )
        )
    controller.reload()
    gui.idle()
    assert controller.detail["plannedText"] == "1 ч"
    entry = next(x for x in controller.calendar["timeline"] if x["id"] == identifier)
    assert entry["isEvent"] and "Пересекается" in entry["warning"]
    gui.click("calendarEvent_" + identifier)
    assert window.property("page") == "calendar"
    assert not gui.item("blockKindInput").isEnabled()
    gui.fill("eventTitleInput", "Пара по физике")
    gui.fill("blockEndInput", "09.11.2026 11:15")
    gui.click("saveBlockButton")
    assert saved(identifier).title == "Пара по физике"
    assert controller.detail["plannedText"] == "1 ч"
    gui.drag("weekDayDrag_" + identifier, target_name="calendarDay_1")
    assert saved(identifier).starts_at.day == 10
    gui.drag("timelineMove_" + identifier, dy=18)
    gui.drag("timelineResize_" + identifier, dy=18)
    assert saved(identifier).starts_at == datetime(2026, 11, 10, 10, 15, tzinfo=UTC)
    assert saved(identifier).ends_at == datetime(2026, 11, 10, 11, 45, tzinfo=UTC)
    gui.screenshot(profile / "events-week.png")
    gui.click("tasksPageButton")
    assert controller.detail["plannedText"] == "1 ч"
    gui.click("deleteTaskButton")
    gui.click("confirmDeleteButton")
    gui.click("calendarPageButton")
    assert sum(x["eventCount"] for x in controller.calendar["days"]) == 1
    assert sum(x["blockCount"] for x in controller.calendar["days"]) == 0
    gui.click("newBlockButton")
    assert gui.item("blockKindInput").property("currentIndex") == 1
    gui.fill("eventTitleInput", "Прогулка с друзьями")
    gui.fill("blockStartInput", "10.11.2026 12:00")
    gui.fill("blockEndInput", "10.11.2026 13:00")
    gui.click("saveBlockButton")
    assert not controller.error and not controller.tasks
    gui.click("calendarMonthButton")
    gui.screenshot(profile / "events-month.png")
    window.resize(640, 480)
    gui.wait_for(lambda: window.width() < 850, "narrow events")
    gui.click("newBlockButton")
    gui.screenshot(profile / "events-narrow-form.png")
    gui.click("cancelBlockButton")
    print(json.dumps({"ok": True, "eventId": identifier, "platform": app.platformName()}))
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
