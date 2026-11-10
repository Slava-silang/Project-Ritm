"""Реальные Qt-жесты: перенос между днями, шкала, растягивание и ошибка записи."""

import json
import sqlite3
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
now = datetime(2026, 11, 9, 9, tzinfo=UTC)
reopen = "--reopen" in sys.argv
if not reopen:
    task = Task(title="Механика", deadline=datetime(2026, 11, 10, 11, 15, tzinfo=UTC))
    first = WorkBlock(
        task_id=task.id,
        starts_at=datetime(2026, 11, 9, 10, tzinfo=UTC),
        ends_at=datetime(2026, 11, 9, 11, tzinfo=UTC),
    )
    other = WorkBlock(
        task_id=task.id,
        starts_at=datetime(2026, 11, 10, 10, 30, tzinfo=UTC),
        ends_at=datetime(2026, 11, 10, 11, 30, tzinfo=UTC),
    )
    with TaskStore(path) as store:
        TaskService(store).create_task(task)
        CalendarService(store).create_block(first)
        CalendarService(store).create_block(other)
    identifier = str(first.id)
else:
    identifier = sys.argv[-1]
controller = TaskController(profile, clock=lambda: now, zone=UTC)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def saved():
    with TaskStore(path) as store:
        return store.get_work_block(UUID(identifier))


drag = gui.drag


try:
    gui.idle()
    gui.click("calendarPageButton")
    if reopen:
        result = saved()
        assert result.starts_at == datetime(2026, 11, 10, 10, 30, tzinfo=UTC)
        assert result.ends_at == datetime(2026, 11, 10, 11, 30, tzinfo=UTC)
        assert controller.detail["plannedText"] == "2 ч"
        print(json.dumps({"reopened": True}))
        raise SystemExit(0)
    drag("weekDayDrag_" + identifier, target_name="calendarDay_1")
    assert not controller.error, controller.error
    assert saved().starts_at == datetime(2026, 11, 10, 10, tzinfo=UTC), saved()
    assert "пересечение" in controller.notice
    drag("timelineMove_" + identifier, dy=36)
    assert saved().starts_at == datetime(2026, 11, 10, 10, 30, tzinfo=UTC), saved()
    assert "позже дедлайна" in controller.notice
    drag("timelineResize_" + identifier, dy=18)
    assert saved().ends_at == datetime(2026, 11, 10, 11, 45, tzinfo=UTC), saved()
    assert controller.detail["plannedText"] == "2 ч 15 мин"
    before = saved()
    drag("timelineMove_" + identifier, dy=36, escape=True)
    assert saved() == before
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER fail_pointer_write BEFORE UPDATE ON work_blocks
                      BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
    drag("timelineResize_" + identifier, dy=18)
    assert controller.error
    assert saved() == before
    resize = gui.item("timelineResize_" + identifier)
    assert resize.parentItem().parentItem().property("previewDelta") == 0
    gui.screenshot(profile / "drag-error.png")
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER fail_pointer_write")
    controller.reload()
    gui.idle()
    gui.click("timelineEdit_" + identifier)
    assert not gui.item("blockTaskInput").isEnabled()
    gui.fill("blockEndInput", "10.11.2026 11:30")
    gui.click("saveBlockButton")
    assert not controller.error
    assert saved().ends_at == datetime(2026, 11, 10, 11, 30, tzinfo=UTC)
    source = saved()
    entry = next(x for x in controller.calendar["timeline"] if x["id"] == identifier)
    with TaskStore(path) as store:
        CalendarService(store).update_block(
            replace(source, ends_at=source.ends_at + timedelta(hours=1))
        )
    controller.changeBlock(
        {
            "id": identifier,
            "operation": "move",
            "minutes": 15,
            "expectedStart": entry["startsAt"],
            "expectedEnd": entry["endsAt"],
        }
    )
    gui.idle()
    assert "уже изменился" in controller.error
    assert saved().ends_at == source.ends_at + timedelta(hours=1)
    with TaskStore(path) as store:
        CalendarService(store).update_block(source)
    controller.reload()
    gui.idle()
    gui.reveal(gui.item("timelineMove_" + identifier))
    gui.screenshot(profile / "drag-timeline.png")
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task
        assert store.get_work_block(first.id).task_id == task.id
    print(json.dumps({"ok": True, "blockId": identifier, "platform": app.platformName()}))
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
