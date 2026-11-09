"""Диалоги вложений и передача сохранённой копии внешнему обработчику."""

import sys
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QObject, QPointF, Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ritm.app import load_window
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
source = profile / "Пример с пробелом.txt"
source.write_text("Рабочий материал")
leaf = ChecklistItem(title="Пункт")
step = Subtask(title="Шаг", items=(leaf,))
task = Task(title="Лабораторная", subtasks=(step,))
with TaskStore(profile / "tasks.sqlite3") as store:
    store.create_task(task)

QQuickStyle.setStyle("Basic")
QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
app = QGuiApplication([])
controller = TaskController(profile)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


class OpenHandler(QObject):
    received = None

    @Slot(QUrl)
    def receive(self, url):
        self.received = Path(url.toLocalFile())


handler = OpenHandler()
QDesktopServices.setUrlHandler("file", handler, "receive")
try:
    gui.idle()
    picker = window.findChild(QObject, "attachmentPicker")
    assert picker is not None

    def source_item():
        listing = gui.item("fileDialogListView")
        return next(
            (
                child
                for child in listing.property("contentItem").childItems()
                if child.property("fileName") == source.name
            ),
            None,
        )

    def pick_file(button):
        picker.setProperty("currentFolder", QUrl.fromLocalFile(str(profile)))
        gui.click(button)
        gui.wait_for(lambda: picker.property("visible"), "file picker opens")
        gui.wait_for(source_item, "source file listed")
        item = source_item()
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(
            item.window(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
        )
        gui.wait_for(
            lambda: picker.property("selectedFile").toLocalFile() == str(source), "selection"
        )
        picker.accept()
        gui.idle()
        assert not controller.error, (controller.error, picker.property("selectedFile"))

    for button, owner in (
        ("taskAttachments", task),
        ("branchAttachments_0", step),
        ("itemAttachments_0_0", leaf),
    ):
        gui.click(button)
        pick_file("chooseAttachment")
        assert len(controller.attachments[str(owner.id)]) == 1
        gui.click("closeAttachments")
    gui.click("materialsPageButton")
    pick_file("addMaterialButton")
    material = controller.materials[0]
    gui.screenshot(profile / "materials.png")
    source.rename(profile / "перемещён.txt")
    gui.click("openMaterial_" + material["id"])
    library_copy = handler.received
    assert library_copy.read_text() == "Рабочий материал"
    gui.click("tasksPageButton")
    for button, owner in (("taskAttachments", task), ("itemAttachments_0_0", leaf)):
        gui.click(button)
        gui.click("chooseFromMaterials")
        assert gui.item("materialChoice").property("currentValue") == material["id"]
        gui.click("linkMaterialButton")
        assert controller.attachments[str(owner.id)][-1]["shared"]
        gui.click("openAttachment_" + material["id"])
        assert handler.received == library_copy
        gui.click("closeAttachments")
    assert len(list((profile / "attachments").iterdir())) == 4
    gui.click("itemAttachments_0_0")
    entry = controller.attachments[str(leaf.id)][0]
    gui.screenshot(profile / "attachments.png")
    gui.click("openAttachment_" + entry["id"])
    assert handler.received is not None
    assert handler.received.is_relative_to(profile / "attachments")
    assert handler.received.read_text() == "Рабочий материал"
    handler.received.unlink()
    gui.click("openAttachment_" + entry["id"])
    assert "отсутствует" in controller.error
    gui.click("closeAttachments")
finally:
    QDesktopServices.unsetUrlHandler("file")
    controller.close()
    window.close()
    engine = None
    app.processEvents()
