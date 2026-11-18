"""Полный Gmail → модель-подмена → редактор/подтверждение и новый процесс."""

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from gui_support import GuiHarness
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from test_gmail import message
from test_google_auth import config_file, session

from ritm.app import load_window
from ritm.application.tasks import TaskService
from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Task
from ritm.integrations.assistant import Connection
from ritm.storage.sqlite import TaskStore
from ritm.ui.assistant import AssistantController
from ritm.ui.gmail import GmailController
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
app = QGuiApplication([])
QQuickStyle.setStyle("Basic")


class Adapter:
    def __init__(self):
        self.requests, self.mode = [], "create"
        self.model = "selected-model"
        self.session = SimpleNamespace(tokens={"sub": "account-one", "client_id": "client-one"})
        self.started, self.release = Event(), Event()
        self.slow = False

    @property
    def connection(self):
        return Connection("test", self.model, "none", frozenset({"mail"}))

    def generate(self, request, cancelled):
        self.requests.append(request)
        if self.slow:
            self.started.set()
            self.release.wait(5)
        if self.mode == "answer":
            return '{"answer":"Уточните время встречи"}'
        if self.mode == "event":
            return json.dumps(
                {
                    "kind": "event",
                    "event": {
                        "title": "Встреча",
                        "start": "2026-11-20T10:00:00+03:00",
                        "end": "2026-11-20T11:00:00+03:00",
                    },
                }
            )
        if self.mode == "update":
            return json.dumps(
                {
                    "kind": "task",
                    "task": {
                        "task_id": str(request.selected.id),
                        "changes": {"title": "Уточнённая работа", "completed": True},
                        "subtasks": [{"title": "Новый пункт", "items": ["Проверить"]}],
                    },
                }
            )
        return json.dumps(
            {
                "kind": "task",
                "task": {
                    "title": "Работа из письма",
                    "description": "Проверить условие",
                    "difficulty": 6,
                    "expected_minutes": 90,
                    "subtasks": [
                        {"title": "Первая часть", "items": ["Расчёт"]},
                        {"title": "Проверка", "items": ["Ответ"]},
                    ],
                },
            }
        )


class API:
    version = "changed" if mode == "reopen" else "original"

    def get(self, path, params, token, cancelled):
        if path == "profile":
            return {"emailAddress": "student@example.org"}
        if path == "messages":
            return {"messages": [{"id": "a1"}, {"id": "a2"}]}
        if path == "messages/a1":
            return message(value="SELECTED_MAIL_BODY " + self.version)
        data = message("a2", "OTHER_MAIL_BODY")
        data["payload"]["headers"].append({"name": "X-Private", "value": "NEVER_SEND_HEADER"})
        return data


if mode == "run":
    with TaskStore(profile / "tasks.sqlite3") as store:
        store.create_task(Task(title="Активная работа"))
        done = Task(title="PRIVATE_DONE", completed=True)
        trash = Task(title="PRIVATE_TRASH")
        store.create_task(done)
        store.create_task(trash)
        TaskService(store).delete_node(trash.id, trash.id)
        store.create_work_block(
            WorkBlock(
                title="Занято",
                starts_at=datetime(2026, 11, 20, 7, tzinfo=UTC),
                ends_at=datetime(2026, 11, 20, 8, tzinfo=UTC),
            )
        )

adapter = Adapter()
backend = TaskController(profile)
agent = AssistantController(profile, adapter)
agent.gmail.close()
current = session(profile)
current.api = API()
current.configure(config_file(profile), Event())
current.sign_in(Event(), lambda _: None)
agent._gmail = GmailController(profile, current, browser=lambda _: True, assistant=agent)
agent.gmail.proposals.changed.connect(agent.changed.emit)
agent.gmail.proposals.applied.connect(agent.mailApplied.emit)
source, helper = agent.gmail, agent.gmail.proposals
helper._clock = lambda: datetime(2026, 11, 18, 12, tzinfo=UTC)
engine = load_window(backend, assistant=agent)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, backend)


def idle():
    gui.wait_for(lambda: not helper.busy and not source.state["busy"])


def read():
    gui.click("gmailReadConsent")
    gui.click("gmailRead")
    idle()
    gui.click("gmailMessage_0")


def generate():
    gui.click("mailSendConsent")
    gui.click("mailGenerate")
    idle()


def type_text(name, value):
    target = gui.item(name)
    gui.reveal(target)
    target.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    for char in value:
        QTest.keyClick(
            window, Qt.Key(ord(char.upper())), Qt.ShiftModifier if char.isupper() else Qt.NoModifier
        )
    app.processEvents()


try:
    gui.idle()
    assert not adapter.requests and not helper.state["hasDraft"]
    gui.click("assistantButton")
    gui.click("gmailSourceButton")
    gui.click("gmailRestore")
    idle()
    read()
    assert not gui.item("mailGenerate").isEnabled()
    helper.generate("Без согласия", False)
    assert not adapter.requests
    generate()
    if mode == "reopen":
        assert not adapter.requests and "уже обработано" in helper.state["error"]
    else:
        assert helper.state["hasDraft"]
        request = adapter.requests[-1]
        assert request.mail["body"] == "SELECTED_MAIL_BODY original"
        assert request.current_time == datetime(2026, 11, 18, 12, tzinfo=UTC)
        assert not request.files and not request.materials and not request.history
        assert {t.title for t in request.tasks} == {"Активная работа"}
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert len(store.list_tasks()) == 2
        gui.click("mailCancelProposal")
        assert gui.item("mailProposalPane").property("form").toVariant() == {}
        assert not gui.item("mailGenerate").isEnabled()
        generate()
        gui.click("mailEdit")
        type_text("mailEditTitle", "revised task")
        type_text("mailEditBranch_0", "revised step")
        type_text("mailEditLeaf_0_0", "revised leaf")
        type_text("mailEditWeights", "60 40")
        gui.screenshot(profile / "mail-editor.png")
        gui.click("mailCheckEdits")
        idle()
        assert (
            "revised task" in helper.state["preview"] and "revised leaf" in helper.state["preview"]
        ), helper.state
        helper.apply(False)
        assert "Подтвердите" in helper.state["error"]
        gui.reveal(gui.item("mailPreview"))
        gui.screenshot(profile / "mail-task-preview.png")
        gui.click("mailApply")
        idle()
        gui.idle()
        with TaskStore(profile / "tasks.sqlite3") as store:
            created = next(t for t in store.list_tasks() if t.title == "revised task")
            assert created.subtasks[0].title == "revised step"
            assert created.subtasks[0].weight == 60
            TaskService(store).set_completed(created.id, created.id, True)
        backend.selectTask(str(created.id))
        gui.idle()
        assert backend.detail["sourceUrls"] and backend.detail["sourceUrls"][0].endswith("#all/a1")
        current.api.version = "changed"
        read()
        adapter.mode = "update"
        generate()
        assert adapter.requests[-1].selected.completed
        gui.click("mailEdit")
        gui.click("mailCheckEdits")
        idle()
        with TaskStore(profile / "tasks.sqlite3") as store:
            original = store.get_task(created.id)
            store.update_task(
                created.id,
                lambda t: replace(t, description="Ручная правка"),
            )
        gui.click("mailApply")
        idle()
        assert "изменилась" in helper.state["error"]
        generate()
        gui.click("mailApply")
        idle()
        with TaskStore(profile / "tasks.sqlite3") as store:
            updated = store.get_task(created.id)
            assert updated.completed and updated.description == "Ручная правка"
            assert all(c.completed for c in updated.subtasks)
        count = len(adapter.requests)
        generate()
        assert len(adapter.requests) == count and "уже обработано" in helper.state["error"]
        gui.click("gmailMessage_1")
        adapter.mode = "answer"
        generate()
        assert helper.state["answer"] and not helper.state["hasDraft"]
        for change in ("cancel", "model", "account", "message"):
            adapter.mode, adapter.slow = "event", True
            adapter.release.clear()
            adapter.started.clear()
            gui.click("mailSendConsent")
            gui.click("mailGenerate")
            gui.wait_for(adapter.started.is_set)
            if change == "cancel":
                gui.click("mailCancelProposal")
            elif change == "model":
                adapter.model = "another-model"
                agent.changed.emit()
            elif change == "account":
                adapter.session.tokens["sub"] = "another-account"
                agent.changed.emit()
            else:
                gui.click("gmailMessage_0")
            adapter.release.set()
            idle()
            assert not helper.state["hasDraft"]
            gui.click("gmailMessage_1")
        adapter.slow, adapter.mode = False, "event"
        generate()
        assert "пересекается" in helper.state["preview"]
        gui.click("mailEdit")
        type_text("mailEditTitle", "revised event")
        gui.fill("mailEditEnd", "20.11.2026 07:45 +00:00")
        gui.click("mailCheckEdits")
        idle()
        assert not helper.state["error"]
        gui.reveal(gui.item("mailPreview"))
        gui.screenshot(profile / "mail-event-preview.png")
        gui.click("mailApply")
        idle()
        with TaskStore(profile / "tasks.sqlite3") as store:
            events = store.list_work_blocks()
            assert len(events) == 2 and any(e.title == "revised event" for e in events)
        gui.click("gmailClose")
        gui.click("assistantCancel")
        backend.saveAppearance("dark", 150)
        gui.idle()
        window.setWidth(640)
        window.setHeight(480)
        app.processEvents()
        QTest.qWait(100)
        gui.click("assistantButton")
        gui.click("gmailSourceButton")
        current.api.version = "third"
        read()
        adapter.mode = "update"
        generate()
        # Вторая правка должна отличаться; ask mode gives answer if no difference.
        gui.reveal(gui.item("mailError" if helper.state["error"] else "mailPreview"))
        gui.screenshot(profile / "mail-narrow.png")
        gui.click("mailEdit")
        gui.reveal(gui.item("mailCheckEdits"))
        gui.screenshot(profile / "mail-editor-narrow.png")
        gui.wait_for(lambda: not agent.state["historyBusy"])
        for path in profile.glob("*.json"):
            if path.name != "synthetic-client.json":
                assert (
                    "SELECTED_MAIL_BODY" not in path.read_text()
                    and "OTHER_MAIL_BODY" not in path.read_text()
                )
finally:
    adapter.release.set()
    agent.close()
    backend.close()
    window.hide()
    engine = None
