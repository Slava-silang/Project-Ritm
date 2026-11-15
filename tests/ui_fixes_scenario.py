"""I34: новые блоки только для активных, старые записи сохраняют владельца."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from ritm.app import load_window
from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
now = datetime(2030, 1, 7, 9, tzinfo=UTC)
with TaskStore(profile / "tasks.sqlite3") as store:
    active, done = Task(title="Активная"), Task(title="Выполненная", completed=True)
    other = Task(title="Другая активная")
    store.create_task(active)
    store.create_task(done)
    store.create_task(other)
    old = WorkBlock(task_id=done.id, starts_at=now, ends_at=now + timedelta(hours=1))
    store.create_work_block(old)
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
backend = TaskController(profile, clock=lambda: now, zone=UTC)
engine = load_window(backend)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, backend)
try:
    gui.idle()
    assert backend.calendarTasks == [
        {"id": str(active.id), "title": active.title},
        {"id": str(other.id), "title": other.title},
    ]
    gui.click("calendarPageButton")
    gui.click("newBlockButton")
    assert gui.item("blockTaskInput").property("count") == 2
    backend.selectTask(str(active.id))
    backend.setCompleted(str(active.id), True)
    gui.idle()
    assert backend.calendarTasks == [{"id": str(other.id), "title": other.title}]
    assert gui.item("blockTaskInput").property("currentIndex") == -1
    assert not gui.item("saveBlockButton").isEnabled()
    backend.selectTask(str(other.id))
    backend.setCompleted(str(other.id), True)
    gui.idle()
    assert not backend.calendarTasks
    gui.click("cancelBlockButton")
    gui.click("editBlock_" + str(old.id))
    assert gui.item("blockTaskInput").property("currentText") == done.title
    gui.fill("blockEndInput", "07.01.2030 11:00")
    gui.click("saveBlockButton")
    assert not backend.error, backend.error
    with TaskStore(profile / "tasks.sqlite3") as store:
        assert store.get_work_block(old.id).task_id == done.id
        assert store.get_work_block(old.id).duration == timedelta(hours=2)
    gui.click("newBlockButton")
    assert gui.item("blockKindInput").property("currentIndex") == 1
    gui.click("cancelBlockButton")
finally:
    backend.close()
    window.close()
    engine = None
    app.processEvents()
