"""Явный выбор вложения, PDF → текст → адаптер → подтверждённые подзадачи."""

import json
import sys
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from test_file_context import make_pdf

from ritm.app import load_window
from ritm.domain.tasks import Task
from ritm.integrations.assistant import MockAdapter
from ritm.storage.attachments import copy_attachment, saved_attachment_path
from ritm.storage.sqlite import TaskStore
from ritm.ui.assistant import AssistantController
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
source = profile / "lab.pdf"
make_pdf(source)
with TaskStore(profile / "tasks.sqlite3") as store:
    task, private = Task(title="Лабораторная"), Task(title="Посторонняя")
    store.create_task(task)
    store.create_task(private)
    attached = copy_attachment(store, profile, task.id, task.id, source)
    secret = profile / "private.txt"
    secret.write_text("Do not transmit this file")
    copy_attachment(store, profile, private.id, private.id, secret)
    book = profile / "book.md"
    book.write_text("Background material")
    material = copy_attachment(store, profile, None, None, book)
    image = profile / "scan.png"
    image.write_bytes(b"not readable yet")
    copy_attachment(store, profile, None, None, image)


class RecordingAdapter:
    connection = MockAdapter.connection

    def __init__(self):
        self.requests = []

    def generate(self, request, cancelled):
        self.requests.append(request)
        assert request.materials
        assert "Measure resistance" in request.materials[0].text
        return json.dumps(
            {"subtasks": [{"title": "Measure resistance", "items": ["Calculate error"]}]}
        )


adapter = RecordingAdapter()
controller = TaskController(profile)
agent = AssistantController(profile, adapter)
engine = load_window(controller, assistant=agent)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
try:
    gui.idle()
    assert controller.detail["id"] == str(task.id)
    gui.click("assistantButton")
    gui.wait_for(lambda: len(agent.state["files"]) == 3 and not agent.state["filesLoading"])
    assert all(row["name"] != "private.txt" for row in agent.state["files"])
    assert not gui.item("assistantFile_2").isEnabled()
    gui.choose("assistantOperation", 1)
    gui.click("assistantFile_0")
    gui.fill("assistantPrompt", "Разбей лабораторную по выбранному файлу")
    gui.screenshot(profile / "assistant-files.png")
    gui.click("assistantGenerate")
    gui.wait_for(lambda: agent.state["hasDraft"], "PDF draft")
    assert len(adapter.requests) == 1
    (request,) = adapter.requests
    assert request.selected.id == task.id
    assert [entry.name for entry in request.materials] == ["lab.pdf"]
    assert "Background material" not in request.materials[0].text
    with TaskStore(profile / "tasks.sqlite3") as store:
        assert not store.get_task(task.id).subtasks
    gui.screenshot(profile / "assistant-file-draft.png")
    gui.click("assistantApply")
    gui.wait_for(lambda: not agent.state["busy"])
    gui.idle()
    with TaskStore(profile / "tasks.sqlite3") as store:
        assert store.get_task(task.id).subtasks[0].title == "Measure resistance"
        assert len(store.list_tasks()) == 2
    # Выбор не сохраняется после закрытия; пропажа копии не приводит к запросу модели.
    gui.click("assistantButton")
    QTest.qWait(200)
    gui.wait_for(lambda: not agent.state["filesLoading"])
    assert not gui.item("assistantFile_0").property("checked")
    saved_attachment_path(profile, attached).unlink()
    gui.click("assistantFile_0")
    gui.click("assistantGenerate")
    gui.wait_for(lambda: not agent.state["busy"])
    assert "копия" in agent.state["error"] and len(adapter.requests) == 1
    gui.click("assistantCancel")
    controller.saveAppearance("dark", 150)
    gui.idle()
    window.setWidth(640)
    window.setHeight(480)
    gui.wait_for(lambda: window.width() == 640)
    gui.click("assistantButton")
    QTest.qWait(200)
    gui.wait_for(lambda: not agent.state["filesLoading"])
    gui.reveal(gui.item("assistantFile_1"))
    gui.screenshot(profile / "assistant-files-narrow.png")
    gui.click("assistantCancel")
finally:
    agent.close()
    controller.close()
    window.close()
    engine = None
    app.processEvents()
