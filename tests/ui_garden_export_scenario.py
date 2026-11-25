"""Экспорт через меню/SaveFile, frozen месяц, отмена и неизменность профиля."""

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from gui_support import GuiHarness
from PySide6.QtCore import QCoreApplication, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QImage, QInputMethodEvent
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.application.garden_runtime import sync_garden
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore
from ritm.ui.garden_export import garden_png
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
outputs = profile / "Сохранённые сады"
outputs.mkdir()
now = [datetime(2026, 11, 1, 12, tzinfo=UTC)]
with TaskStore(profile / "tasks.sqlite3", clock=lambda: now[0]) as source:
    sync_garden(source, profile, now[0], zone="UTC", enabled=True)
with GardenStore(profile / "garden.sqlite3") as garden:
    item = garden.execute(
        uuid4(), {"action": "buy", "kind": "daisy"}, expected_revision=garden.snapshot().revision
    ).item_id
    garden.execute(
        uuid4(),
        {"action": "place", "id": str(item), "x": 2, "y": 3},
        expected_revision=garden.snapshot().revision,
    )
    old = garden.snapshot().garden
now[0] = datetime(2026, 12, 1, 12, tzinfo=UTC)
QQuickStyle.setStyle("Basic")
QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
# Keep the real file picker entirely inside the synthetic test directory.
QStandardPaths.writableLocation = staticmethod(lambda location: str(outputs))
app = QApplication([])
controller = TaskController(profile, clock=lambda: now[0], zone=UTC)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def hashes():
    return {
        name: hashlib.sha256((profile / name).read_bytes()).hexdigest()
        for name in ("tasks.sqlite3", "garden.sqlite3")
    }


def open_picker(kind):
    gui.click("gardenExport")
    gui.click("gardenExportHtml" if kind == "html" else "gardenExportPng")
    picker = gui.item("gardenExportFile")
    gui.wait_for(lambda: picker.property("visible"), "export picker")
    assert gui.item("gardenPane").property("dialogsOpen")
    assert picker.property("defaultSuffix") == kind
    return picker


def save(picker, path, file_name=None):
    field = gui.item("fileNameTextField")
    field.forceActiveFocus()
    QTest.keyClick(field.window(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    event = QInputMethodEvent()
    name = file_name or path.name
    event.setCommitString(name)
    QCoreApplication.sendEvent(app.focusObject(), event)
    app.processEvents()
    assert field.property("text") == name, field.property("text")
    picker.accept()
    gui.wait_for(path.is_file, "export file written")
    gui.idle()
    assert not controller.error, controller.error
    assert gui.item("gardenExportNotice").isVisible()


try:
    gui.idle()
    controller.saveAppearance("dusk", 100)
    gui.idle()
    gui.click("gardenButton")
    gui.click("gardenArchiveTab")
    assert controller.gardenBoard["month"] == "2026-11"
    before = hashes()
    picker = open_picker("png")
    token = picker.property("token")
    picker.reject()
    gui.idle()
    assert controller._garden_export_offer is None
    controller.exportGarden(QUrl.fromLocalFile(str(outputs / "cancelled.png")), token)
    gui.idle()
    assert not (outputs / "cancelled.png").exists() and hashes() == before
    picker = open_picker("png")
    png = outputs / "Ноябрь #1.png"
    save(picker, png)
    assert png.read_bytes() == garden_png(old)
    assert not QImage(str(png)).isNull() and hashes() == before
    gui.click("gardenCurrentTab")
    picker = open_picker("html")
    assert picker.property("month") == "2026-12"
    # A month transition during SaveFile must not replace the captured garden.
    now[0] += timedelta(days=32)
    controller._on_garden_tick()
    gui.wait_for(lambda: controller.garden["currentMonth"] == "2027-01", "new month")
    # The synthetic date jump also schedules the normal daily calendar refresh.
    # Settle it before testing a later user error, which refresh normally clears.
    gui.wait_for(lambda: controller.calendar["today"] == now[0].date().isoformat(), "calendar day")
    gui.idle()
    before = hashes()
    target = outputs / "Декабрь.html"
    save(picker, target, target.stem)  # SaveFile supplies the selected format's suffix.
    record = json.loads(
        target.read_text().split('type="application/json">')[1].split("</script>")[0]
    )
    assert record["garden"]["month"] == "2026-12"
    assert hashes() == before
    gui.idle()
    offer = controller.prepareGardenExport("png")
    controller.exportGarden(QUrl.fromLocalFile(str(profile / "tasks.sqlite3")), offer["token"])
    gui.wait_for(lambda: bool(controller.error), "wrong export extension rejected")
    gui.idle()
    assert "расширением" in controller.error, controller.error
    assert hashes() == before
    offer = controller.prepareGardenExport("png")
    controller.exportGarden(QUrl("https://invalid.example/garden.png"), offer["token"])
    assert "локальный" in controller.error and hashes() == before
    controller.cancelGardenExport(offer["token"])
    # The compact export tool remains visible at 640×480/150%.
    controller.saveAppearance("dusk", 150)
    gui.idle()
    window.setWidth(640)
    window.setHeight(480)
    app.processEvents()
    gui.click("gardenExport")
    gui.screenshot(profile / "garden-export-narrow.png")
    menu = gui.item("gardenExportMenu")
    menu.close()
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
