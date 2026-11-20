"""QML-подтверждение парной копии: состав, отмена, узкое окно и восстановление."""

import sys
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from test_profile_backup import NOW, seed

from ritm.app import load_window
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
seed(profile)
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
controller = TaskController(profile, clock=lambda: NOW, automatic_backup=False)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
try:
    gui.idle()
    gui.click("backupsButton")
    gui.click("createBackupButton")
    archive = profile / "backups" / controller.backups["saved"]
    with GardenStore(profile / "garden.sqlite3") as garden:
        before = garden.snapshot()
    controller.saveTask("", {"title": "После копии"})
    gui.idle()
    with GardenStore(profile / "garden.sqlite3") as garden:
        assert garden.snapshot().balance == before.balance + 300
    controller.inspectBackup(QUrl.fromLocalFile(str(archive)))
    gui.idle()
    assert controller.backups["preview"]["garden"]
    assert "монеты и достижения" in gui.item("backupScope").property("text")
    gui.screenshot(profile / "paired-backup.png")
    gui.click("cancelBackupRestore")
    with TaskStore(profile / "tasks.sqlite3") as source:
        assert len(source.list_tasks()) == 2
    with GardenStore(profile / "garden.sqlite3") as garden:
        assert garden.snapshot().balance == before.balance + 300
    controller.saveAppearance("dark", 150)
    gui.idle()
    window.setWidth(640)
    window.setHeight(480)
    controller.inspectBackup(QUrl.fromLocalFile(str(archive)))
    gui.idle()
    gui.screenshot(profile / "paired-backup-narrow.png")
    gui.click("confirmBackupRestore")
    assert not controller.error, controller.error
    assert controller.backups["restored"]
    with TaskStore(profile / "tasks.sqlite3") as source:
        assert len(source.list_tasks()) == 1
    with GardenStore(profile / "garden.sqlite3") as garden:
        assert garden.snapshot().garden == before.garden
        assert garden.snapshot().balance == before.balance
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
