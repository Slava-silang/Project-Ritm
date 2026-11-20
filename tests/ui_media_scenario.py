"""Выбор файла, изображение/копия, согласие повторной передачи и подтверждение задачи."""

import json
import sys
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_media import RAW, make_image

from ritm.app import load_window
from ritm.application.media import image_from_bytes
from ritm.domain.tasks import Task
from ritm.storage.attachments import copy_attachment, saved_attachment_path
from ritm.storage.sqlite import TaskStore
from ritm.ui.assistant import AssistantController
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
QGuiApplication.setAttribute(Qt.AA_DontUseNativeDialogs)
QQuickStyle.setStyle("Basic")
app = QApplication([])
image_path = profile / "scheme.png"
original = make_image(image_path)
source = profile / "notes.docx"
source.write_bytes(b"Example DOCX content passed through unchanged")
with TaskStore(profile / "tasks.sqlite3") as store:
    task = Task(title="Задание по фотографии")
    other = Task(title="Посторонняя задача")
    store.create_task(task)
    store.create_task(other)
    picture = copy_attachment(store, profile, None, None, image_path)
    secret = profile / "private.txt"
    secret.write_text("UNSELECTED_PRIVATE_FILE")
    copy_attachment(store, profile, other.id, other.id, secret)
    bad = profile / "archive.zip"
    bad.write_bytes(b"not supported")
    copy_attachment(store, profile, None, None, bad)


class Adapter:
    connection = RAW

    def __init__(self):
        self.requests = []

    def generate(self, request, cancelled):
        self.requests.append(request)
        return json.dumps(
            {"subtasks": [{"title": "Разобрать схему", "items": ["Выписать данные"]}]}
        )


adapter = Adapter()
backend = TaskController(profile)
agent = AssistantController(profile, adapter)
engine = load_window(backend, assistant=agent)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, backend)


def catalog_ready():
    gui.wait_for(lambda: not agent.state["filesLoading"] and not agent.media.state["busy"])


def selected():
    return gui.item("assistantDialog").property("selectedFiles").toVariant()


def select_file(identifier):
    index = next(i for i, row in enumerate(agent.state["files"]) if row["id"] == identifier)
    gui.click("assistantFile_" + str(index))


try:
    gui.idle()
    backend.selectTask(str(task.id))
    gui.click("assistantButton")
    catalog_ready()
    gui.choose("assistantOperation", 1)
    assert all(row["name"] != "private.txt" for row in agent.state["files"])
    assert not gui.item("assistantFile_1").isEnabled()
    gui.click("assistantPreview_0")
    gui.wait_for(lambda: bool(agent.media.state.get("url")) and not agent.media.state["busy"])
    assert agent.media.state["width"] == 60
    gui.click("mediaRotate")
    gui.wait_for(lambda: not agent.media.state["busy"])
    assert (agent.media.state["width"], agent.media.state["height"]) == (40, 60)
    gui.item("mediaCropWidth").setProperty("value", 50)
    gui.item("mediaCropWidth").valueModified.emit()
    assert not gui.item("mediaSaveCopy").isEnabled()
    gui.click("mediaRefreshPreview")
    gui.wait_for(lambda: not agent.media.state["busy"])
    assert agent.media.state["width"] == 20
    gui.screenshot(profile / "media-preview.png")
    gui.click("mediaSaveCopy")
    catalog_ready()
    with TaskStore(profile / "tasks.sqlite3") as store:
        edited = next(
            row for row in store.list_materials() if row.name == "Изображение — копия.png"
        )
        assert saved_attachment_path(profile, picture).read_bytes() == original
    assert selected() == [str(edited.id)]
    # Use the actual Qt file picker to import a document.
    picker = window.findChild(QObject, "assistantFilePicker")
    picker.setProperty("currentFolder", QUrl.fromLocalFile(str(profile)))
    gui.click("assistantAddFile")

    def source_item():
        listing = gui.item("fileDialogListView")
        return next(
            (
                item
                for item in listing.property("contentItem").childItems()
                if item.property("fileName") == source.name
            ),
            None,
        )

    gui.wait_for(source_item)
    item = source_item()
    QTest.mouseClick(
        item.window(),
        Qt.LeftButton,
        Qt.NoModifier,
        item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint(),
    )
    gui.wait_for(lambda: picker.property("selectedFile").toLocalFile() == str(source))
    picker.accept()
    catalog_ready()
    with TaskStore(profile / "tasks.sqlite3") as store:
        document = next(row for row in store.list_materials() if row.name == "notes.docx")
    gui.fill("assistantPrompt", "Разбей задание по изображению и документу")
    gui.click("assistantGenerate")
    gui.wait_for(lambda: agent.state["hasDraft"])
    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    assert not request.materials and len(request.files) == 2
    assert request.files[1].data == source.read_bytes()
    assert image_from_bytes(request.files[0].data).width() == 20
    with TaskStore(profile / "tasks.sqlite3") as store:
        assert not store.get_task(task.id).subtasks
        assert not [a for a in store.list_attachments() if a.task_id == task.id]
    gui.click("assistantCancel")
    gui.click("assistantButton")
    catalog_ready()
    assert not selected()
    select_file(str(edited.id))
    select_file(str(document.id))
    agent.generate("Без подтверждения", str(task.id), [str(edited.id)])
    assert "Подтвердите повторную" in agent.state["error"]
    assert len(adapter.requests) == 1
    gui.click("assistantGenerate")
    assert gui.item("assistantResendDialog").property("visible")
    assert len(adapter.requests) == 1
    gui.click("assistantCancelResend")
    assert len(adapter.requests) == 1
    gui.click("assistantGenerate")
    gui.click("assistantConfirmResend")
    gui.wait_for(lambda: agent.state["hasDraft"])
    assert len(adapter.requests) == 2 and adapter.requests[-1].history
    gui.click("assistantApply")
    gui.wait_for(lambda: not agent.state["busy"])
    gui.idle()
    with TaskStore(profile / "tasks.sqlite3") as store:
        assert store.get_task(task.id).subtasks[0].title == "Разобрать схему"
        assert {a.id for a in store.list_attachments() if a.task_id == task.id} == {
            edited.id,
            document.id,
        }
    gui.click("assistantButton")
    catalog_ready()
    gui.fill("assistantPrompt", "Уточни без файлов")
    gui.click("assistantGenerate")
    gui.wait_for(lambda: agent.state["hasDraft"])
    assert adapter.requests[-1].files == ()
    gui.click("assistantCancel")
    backend.saveAppearance("dark", 150)
    gui.idle()
    window.setWidth(640)
    window.setHeight(480)
    app.processEvents()
    QTest.qWait(100)
    gui.click("assistantButton")
    catalog_ready()
    photo_index = next(
        i for i, row in enumerate(agent.state["files"]) if row["id"] == str(picture.id)
    )
    gui.reveal(gui.item("assistantPreview_" + str(photo_index)))
    gui.screenshot(profile / "media-files-narrow.png")
    gui.click("assistantPreview_" + str(photo_index))
    gui.wait_for(lambda: bool(agent.media.state.get("url")) and not agent.media.state["busy"])
    gui.screenshot(profile / "media-preview-narrow.png")
    gui.reveal(gui.item("mediaCropWidth"))
    gui.screenshot(profile / "media-crop-narrow.png")
    gui.wait_for(lambda: not agent.state["historyBusy"])
    history = (profile / "chat-history.json").read_text()
    assert "data:image" not in history and "file_data" not in history
finally:
    agent.close()
    backend.close()
    window.hide()
    engine = None
