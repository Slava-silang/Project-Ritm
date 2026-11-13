"""Создание, проверка даты и подтверждение замены профиля в QML."""

import sys
from datetime import UTC, datetime
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
now = datetime(2026, 11, 10, 12, tzinfo=UTC)
with TaskStore(profile / "tasks.sqlite3") as store:
    store.create_task(Task(title="До копии"))
QQuickStyle.setStyle("Basic")
QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
app = QGuiApplication([])
controller = TaskController(profile, clock=lambda: now, zone=UTC, automatic_backup=True)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)

try:
    gui.idle()
    folder = profile / "backups"
    assert len(list(folder.glob("ritm-auto-2026-11-10-*.zip"))) == 1
    gui.click("backupsButton")
    gui.click("createBackupButton")
    archive = folder / controller.backups["saved"]
    assert archive.is_file()
    controller.saveTask("", {"title": "После копии"})
    gui.idle()

    def choose_archive():
        picker = window.findChild(QObject, "backupFilePicker")
        picker.setProperty("currentFolder", QUrl.fromLocalFile(str(folder)))
        gui.click("chooseBackupArchive")
        gui.wait_for(lambda: picker.property("visible"), "file picker opens")

        def archive_item():
            listing = gui.item("fileDialogListView")
            return next(
                (
                    child
                    for child in listing.property("contentItem").childItems()
                    if child.property("fileName") == archive.name
                ),
                None,
            )

        gui.wait_for(archive_item, "archive listed")
        item = archive_item()
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(
            item.window(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
        )
        gui.wait_for(
            lambda: picker.property("selectedFile").toLocalFile() == str(archive),
            "archive selected",
        )
        picker.accept()
        gui.idle()

    choose_archive()
    assert "10.11.2026 12:00" in gui.item("backupDate").property("text")
    assert len(controller.tasks) == 2
    gui.click("cancelBackupRestore")
    assert len(controller.tasks) == 2
    choose_archive()
    gui.screenshot(profile / "backup-restore.png")
    gui.click("confirmBackupRestore")
    assert not controller.error
    assert [row["title"] for row in controller.tasks] == ["До копии"]
    assert controller.backups["restored"]
    gui.click("closeBackups")
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
