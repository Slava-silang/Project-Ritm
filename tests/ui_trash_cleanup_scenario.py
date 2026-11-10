"""Подтверждение выборочной очистки корзины."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from ritm.app import load_window
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage.attachments import copy_attachment
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
source = profile / "original.txt"
source.write_text("Оригинал")
now = datetime(2026, 11, 10, 12, tzinfo=UTC)
with TaskStore(profile / "tasks.sqlite3") as store:
    keep = Task(title="Оставить в корзине")
    remove = Task(title="Удалить из корзины")
    store.create_task(keep)
    store.create_task(remove)
    entry = copy_attachment(store, profile, remove.id, remove.id, source)
    service = TaskService(store)
    service.delete_node(keep.id, keep.id, now - timedelta(seconds=1))
    service.delete_node(remove.id, remove.id, now)

QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
controller = TaskController(profile, clock=lambda: now)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)

try:
    gui.idle()
    gui.click("trashButton")
    assert not gui.item("purgeSelectedButton").isEnabled()
    gui.click("selectTrash_0")
    gui.click("purgeSelectedButton")
    gui.click("cancelPurgeButton")
    assert len(controller.trash) == 2
    assert (profile / "attachments" / entry.id.hex).is_dir()
    gui.click("purgeSelectedButton")
    gui.screenshot(profile / "confirm-cleanup.png")
    gui.click("confirmPurgeButton")
    assert not controller.error and controller.pendingCleanup == 0
    assert [row["title"] for row in controller.trash] == [keep.title]
    assert not (profile / "attachments" / entry.id.hex).exists()
    assert source.read_text() == "Оригинал"
    gui.click("restore_0")
    assert not controller.trash and controller.detail["title"] == keep.title
    gui.click("closeTrashButton")
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
