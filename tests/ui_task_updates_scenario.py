"""Нахождение другой задачи, ручная правка предложения, конфликт, отмена и история."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ritm.app import load_window
from ritm.application.tasks import TaskService
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.integrations.assistant import Connection
from ritm.storage.attachments import copy_attachment
from ritm.storage.sqlite import TaskStore
from ritm.ui.assistant import AssistantController
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
with TaskStore(profile / "tasks.sqlite3") as store:
    if mode == "run":
        target = Task(
            title="Физика",
            description="Исходное описание",
            subtasks=(
                Subtask(
                    title="Расчёты",
                    items=(ChecklistItem(title="Первый шаг"), ChecklistItem(title="Второй шаг")),
                ),
            ),
        )
        other = Task(title="Математика")
        service = TaskService(store)
        service.create_task(target)
        service.create_task(other)
        service.create_task(Task(title="PRIVATE_COMPLETED", completed=True))
        trashed = Task(title="PRIVATE_TRASH")
        service.create_task(trashed)
        service.delete_node(trashed.id, trashed.id)
        source = profile / "hidden.txt"
        source.write_text("PRIVATE_FILE_BYTES")
        copy_attachment(store, profile, target.id, target.id, source)
    target = next(t for t in store.list_tasks() if t.title.startswith("Физика"))
    other = next(t for t in store.list_tasks() if t.title == "Математика")
leaf = target.subtasks[0].items[0]


class Adapter:
    connection = Connection(
        "test", "task-update-demo", "none", frozenset({"create", "split", "update"})
    )

    def __init__(self):
        self.requests = []
        self.phase = "answer"
        self.started, self.release = Event(), Event()

    def generate(self, request, cancelled):
        self.requests.append(request)
        assert request.operation == "update" and request.selected is None
        assert {t.id for t in request.tasks} == {target.id, other.id}
        assert request.files == request.materials == ()
        assert request.current_time == datetime(2026, 11, 16, 12, tzinfo=UTC)
        if self.phase == "answer":
            return json.dumps(
                {
                    "task_id": str(target.id),
                    "answer": "Найдена задача «Физика». Изменения не требуются.",
                }
            )
        if self.phase == "delay":
            self.started.set()
            assert self.release.wait(10)
        return json.dumps(
            {
                "task_id": str(target.id),
                "changes": {
                    "title": "Физика — итог" if self.phase == "fresh" else "Физика — предложение",
                    "deadline": "2026-11-21T15:45:00+00:00",
                    "expected_minutes": 90,
                },
                "nodes": [
                    {"id": str(leaf.id), "title": "Проверенный первый шаг", "completed": True}
                ],
            }
        )


QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
adapter = Adapter()
backend = TaskController(profile)
agent = AssistantController(profile, adapter, clock=lambda: datetime(2026, 11, 16, 12, tzinfo=UTC))
engine = load_window(backend, assistant=agent)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, backend)


def done():
    gui.wait_for(lambda: not agent.state["busy"] and not agent.state["filesLoading"])


try:
    gui.idle()
    backend.selectTask(str(other.id))
    gui.click("assistantButton")
    done()
    gui.choose("assistantOperation", 2)
    if mode == "reopen":
        gui.wait_for(lambda: not agent.state["historyBusy"])
        assert len(adapter.requests) == 0
        gui.click("chatShowHistory")
        index = next(
            i
            for i, row in enumerate(agent.state["messages"])
            if row["role"] == "assistant" and row.get("scope")
        )
        gui.click("chatResume_" + str(index))
        assert agent.state["contextCount"] > 0
        assert not adapter.requests and not agent.state["hasDraft"]
        gui.click("assistantCancel")
    else:
        gui.fill("assistantPrompt", "Есть ли у меня задача по физике?")
        gui.click("assistantGenerate")
        done()
        assert not agent.state["hasDraft"] and not agent.state["canApply"]
        assert "Найдена задача" in agent.state["messages"][-1]["text"]
        adapter.phase = "update"
        gui.fill("assistantPrompt", "Перенеси срок и уточни первый шаг")
        gui.click("assistantGenerate")
        gui.wait_for(lambda: agent.state["hasDraft"])
        assert agent.state["updateMode"] and "Исходное" not in agent.state["preview"]
        assert "Физика → Физика — предложение" in agent.state["preview"]
        assert backend.detail["id"] == str(other.id)
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert store.get_task(target.id).title == "Физика"
            assert not store.get_task(target.id).subtasks[0].items[0].completed
        gui.click("assistantEdit")
        gui.fill("draftTitle", "Физика — вручную")
        gui.item("draftTitle").textEdited.emit()
        gui.fill("draftNodeTitle_0", "Вручную проверенный шаг")
        gui.item("draftNodeTitle_0").textEdited.emit()
        gui.click("assistantCheck")
        assert not agent.state["error"], agent.state["error"]
        assert "Физика — вручную" in agent.state["preview"]
        assert "Вручную проверенный шаг" in agent.state["preview"]
        gui.reveal(gui.item("assistantDraftPreview"))
        gui.screenshot(profile / "task-update-preview.png")
        with TaskStore(profile / "tasks.sqlite3") as store:
            TaskService(store).edit_details(
                target.id,
                title=target.title,
                description="Параллельное изменение",
                difficulty=None,
                expected_duration=None,
                deadline=None,
                category=None,
            )
        gui.click("assistantApply")
        done()
        assert "изменилась" in agent.state["error"]
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert store.get_task(target.id).title == "Физика"
        adapter.phase = "fresh"
        gui.click("chatRefine")
        gui.fill("assistantPrompt", "Сформируй заново по актуальным данным")
        gui.click("assistantGenerate")
        gui.wait_for(lambda: agent.state["hasDraft"])
        assert (
            next(t for t in adapter.requests[-1].tasks if t.id == target.id).description
            == "Параллельное изменение"
        )
        gui.click("assistantApply")
        done()
        gui.idle()
        with TaskStore(profile / "tasks.sqlite3") as store:
            saved = store.get_task(target.id)
            assert saved.title == "Физика — итог" and saved.description == "Параллельное изменение"
            assert saved.expected_duration.total_seconds() == 5400
            assert saved.subtasks[0].items[0].completed and not saved.completed
            assert store.get_task(other.id) == other
            assert not store.list_materials()
        # Late response after explicit cancellation must not become a new draft.
        gui.click("assistantButton")
        done()
        adapter.phase = "delay"
        gui.fill("assistantPrompt", "Запрос, который я отменю")
        gui.click("assistantGenerate")
        gui.wait_for(adapter.started.is_set)
        gui.click("assistantCancel")
        adapter.release.set()
        QTest.qWait(200)
        app.processEvents()
        assert not agent.state["hasDraft"]
        backend.saveAppearance("dark", 150)
        gui.idle()
        window.setWidth(640)
        window.setHeight(480)
        app.processEvents()
        QTest.qWait(100)
        gui.click("assistantButton")
        done()
        adapter.phase = "fresh"
        gui.fill("assistantPrompt", "Уточни ожидаемое время")
        # Change response title so the otherwise already-applied request is not a no-op.
        with TaskStore(profile / "tasks.sqlite3") as store:
            current = store.get_task(target.id)
            TaskService(store).edit_details(
                target.id,
                title="Физика — изменено локально",
                description=current.description,
                difficulty=None,
                expected_duration=None,
                deadline=current.deadline,
                category=None,
            )
        gui.click("assistantGenerate")
        gui.wait_for(lambda: agent.state["hasDraft"])
        gui.reveal(gui.item("assistantDraftPreview"))
        gui.screenshot(profile / "task-update-narrow.png")
        gui.click("assistantEdit")
        gui.reveal(gui.item("draftNodeTitle_0"))
        gui.screenshot(profile / "task-update-editor-narrow.png")
        gui.click("assistantCancel")
        gui.wait_for(lambda: not agent.state["historyBusy"])
        history = (profile / "chat-history.json").read_text()
        assert "PRIVATE_COMPLETED" not in history and "PRIVATE_TRASH" not in history
        assert "PRIVATE_FILE_BYTES" not in history
finally:
    adapter.release.set()
    agent.close()
    backend.close()
    window.hide()
    engine = None
