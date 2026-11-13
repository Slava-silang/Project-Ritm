"""Уточнение в чате, локальная история, отключение и подтверждаемая очистка."""

import json
import sys
from pathlib import Path
from threading import Event

from gui_support import GuiHarness
from PySide6.QtCore import QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ritm.app import load_window
from ritm.integrations.assistant import Connection, MockAdapter
from ritm.ui.assistant import AssistantController
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
mode = sys.argv[2]
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])


class Recorder:
    connection = MockAdapter.connection

    def __init__(self):
        self.requests = []

    def generate(self, request, cancelled):
        self.requests.append(request)
        return json.dumps({"title": request.prompt, "subtasks": [{"title": "Шаг"}]})


adapter = Recorder()
controller = TaskController(profile)
agent = AssistantController(profile, adapter)
engine = load_window(controller, assistant=agent)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def ready():
    gui.wait_for(lambda: not agent.state["historyBusy"])
    gui.wait_for(lambda: gui.item("assistantGenerate").isEnabled())


def send(prompt):
    ready()
    gui.fill("assistantPrompt", prompt)
    gui.click("assistantGenerate")
    gui.wait_for(lambda: agent.state["hasDraft"])
    gui.wait_for(lambda: not agent.state["historyBusy"])


try:
    gui.idle()
    gui.wait_for(lambda: not agent.state["historyBusy"])
    assert agent.state["contextCount"] == 0 and not adapter.requests
    gui.click("assistantButton")
    if mode == "run":
        send("Первое предложение")
        assert not adapter.requests[0].history
        agent.edit(json.dumps({"title": "Ручная правка", "subtasks": [{"title": "Шаг"}]}))
        assert not agent.state["error"]
        gui.click("chatRefine")
        send("Сделай короче")
        history = adapter.requests[1].history
        assert len(history) == 2
        assert history[0].text == "Первое предложение"
        assert "Ручная правка" in history[1].text
        assert not controller.tasks
        gui.click("chatShowHistory")
        gui.screenshot(profile / "chat-history.png")
        gui.click("chatNew")
        assert agent.state["contextCount"] == 0
        gui.click("chatSaveHistory")
        gui.wait_for(lambda: not agent.state["historyBusy"])
        assert not agent.state["historyEnabled"]
        before = (profile / "chat-history.json").read_bytes()
        send("Останется только в памяти")
        assert not adapter.requests[-1].history
        assert (profile / "chat-history.json").read_bytes() == before
        agent.cancel()
        # Смена режима/задачи не должна переслать старый контекст.
        adapter.connection = Connection("mock", "another-model", "none")
        agent.generate("Новая область")
        gui.wait_for(lambda: agent.state["hasDraft"])
        assert not adapter.requests[-1].history
        assert "только в памяти" not in (profile / "chat-history.json").read_text()
    elif mode == "reopen":
        assert not agent.state["historyEnabled"]
        assert len(agent.state["messages"]) == 4
        assert all("только в памяти" not in row["text"] for row in agent.state["messages"])
        gui.click("chatShowHistory")
        gui.click("chatResume_1")
        assert agent.state["contextCount"] == 4
        send("Продолжение после перезапуска")
        assert len(adapter.requests[-1].history) == 4
        before_clear = len(agent.state["messages"])
        gui.click("chatClearHistory")
        gui.click("cancelChatHistoryClear")
        assert len(agent.state["messages"]) == before_clear
        gui.click("chatClearHistory")
        gui.click("confirmChatHistoryClear")
        gui.wait_for(lambda: not agent.state["historyBusy"])
        assert not agent.state["messages"] and agent.state["contextCount"] == 0
        controller.saveAppearance("dark", 150)
        gui.idle()
        window.setWidth(640)
        window.setHeight(480)
        gui.wait_for(lambda: window.width() == 640)
        for name in ("chatSaveHistory", "chatShowHistory"):
            item = gui.item(name)
            assert item.mapToScene(QPointF(item.width(), 0)).x() <= window.width()
        gui.screenshot(profile / "chat-narrow.png")
    else:
        assert not agent.state["messages"] and not agent.state["historyEnabled"]
        release, started = Event(), Event()

        class Slow:
            connection = MockAdapter.connection

            def generate(self, request, cancelled):
                started.set()
                release.wait(3)
                return '{"title":"Запоздалый ответ"}'

        agent.adapter = Slow()
        ready()
        agent.generate("Очистка при активном запросе")
        gui.wait_for(started.is_set)
        agent.clearHistory()
        gui.wait_for(lambda: not agent.state["historyBusy"])
        release.set()
        QTest.qWait(150)
        app.processEvents()
        assert not agent.state["messages"] and not agent.state["hasDraft"]
        assert not json.loads((profile / "chat-history.json").read_text())["messages"]
    assert not controller.tasks
finally:
    agent.close()
    controller.close()
    window.close()
    engine = None
    app.processEvents()
