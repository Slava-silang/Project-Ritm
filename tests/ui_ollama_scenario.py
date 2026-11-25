"""Real QML and fake Ollama; no server, downloads, browser or credentials."""

import sys
from pathlib import Path
from types import SimpleNamespace

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from test_ollama import Transport

from ritm.app import load_window
from ritm.integrations.chatgpt import DIRECT
from ritm.integrations.ollama import OllamaError
from ritm.ui.assistant import AssistantController
from ritm.ui.connection import ConnectionController
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
controller = TaskController(profile)
agent = AssistantController(profile)


class Local(Transport):
    fail = False
    hold = False

    def json(self, method, path, payload, cancelled, seconds):
        if path == "/api/tags":
            if self.hold:
                cancelled.wait(10)
                raise OllamaError("cancelled")
            if self.fail:
                raise OllamaError("connection")
        if path == "/api/chat":
            self.answer["model"] = payload["model"]
        return super().json(method, path, payload, cancelled, seconds)


local = Local()
local.models = {
    "models": [{"name": "local:latest"}, {"name": "local:second"}, {"name": "remote:cloud"}]
}
chat = SimpleNamespace(
    tokens={"scope": DIRECT},
    models=[{"slug": "chat-model", "display_name": "Chat model"}],
    model="chat-model",
    verified=False,
    vault=SimpleNamespace(warning=""),
)
connection = ConnectionController(profile, agent, chat, ollama_transport=local)
engine = load_window(controller, assistant=agent, connection=connection)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
try:
    gui.idle()
    assert not local.calls
    gui.click("assistantButton")
    gui.click("connectionExpand")
    gui.choose("connectionProvider", 2)
    assert gui.item("ollamaModels").isVisible()
    assert not gui.item("ollamaUse").isEnabled()
    local.fail = True
    gui.click("ollamaModels")
    gui.wait_for(lambda: not connection.state["busy"])
    assert "Нет связи" in connection.state["error"] and agent.state["provider"] == "mock"
    local.fail = False
    local.hold = True
    gui.click("ollamaModels")
    gui.wait_for(lambda: connection.state["busy"])
    assert agent.state["busy"] and not gui.item("ollamaUse").isEnabled()
    gui.click("ollamaCancel")
    gui.wait_for(lambda: not connection.state["busy"])
    assert "отменён" in connection.state["error"]
    local.hold = False
    gui.click("ollamaModels")
    gui.wait_for(lambda: not connection.state["busy"])
    assert len(connection.state["ollama"]["models"]) == 2
    assert not any(c[1] == "/api/chat" for c in local.calls)
    gui.choose("ollamaModel", 0)
    assert connection.state["ollama"]["model"] == "local:latest"
    assert agent.state["provider"] == "mock"  # Selection needs separate activation.
    gui.click("ollamaUse")
    assert agent.state["provider"] == "ollama"
    gui.click("connectionExpand")
    gui.fill("assistantPrompt", "Разбей лабораторную")
    gui.wait_for(lambda: gui.item("assistantGenerate").isEnabled())
    gui.click("assistantGenerate")
    gui.wait_for(lambda: agent.state["hasDraft"])
    assert not controller.tasks and agent.state["draft"]["title"] == "Локальный план"
    assert agent.state["messages"][-1]["label"] == "Локальная Ollama"
    old_chat = agent.state["chatId"]
    gui.click("connectionExpand")
    gui.choose("ollamaModel", 1)
    assert agent.state["model"] == "local:second"
    assert not agent.state["hasDraft"] and agent.state["contextCount"] == 0
    assert agent.state["chatId"] != old_chat
    # Cancelled list loading preserves the previous model/list.
    before = connection.state["ollama"]
    local.hold = True
    gui.click("ollamaModels")
    gui.click("ollamaCancel")
    gui.wait_for(lambda: not connection.state["busy"])
    assert connection.state["ollama"] == before
    local.hold = False
    gui.choose("connectionProvider", 0)
    gui.click("chatgptUse")
    assert agent.state["provider"] == "chatgpt" and agent.state["contextCount"] == 0
    gui.choose("connectionProvider", 2)
    gui.click("ollamaDisconnect")
    assert agent.state["provider"] == "chatgpt"  # Inactive disconnect leaves ChatGPT alone.
    gui.click("ollamaModels")
    gui.wait_for(lambda: not connection.state["busy"])
    gui.choose("ollamaModel", 0)
    gui.click("ollamaUse")
    local.models = {"models": []}
    gui.click("ollamaModels")
    gui.wait_for(lambda: not connection.state["busy"])
    assert agent.state["provider"] == "mock" and connection.state["error"]
    assert not gui.item("ollamaUse").isEnabled()
    local.models = {"models": [{"name": "local:latest"}]}
    gui.click("ollamaModels")
    gui.wait_for(lambda: not connection.state["busy"])
    gui.choose("ollamaModel", 0)
    gui.click("ollamaUse")
    controller.saveAppearance("space", 150)
    gui.idle()
    window.resize(640, 480)
    gui.wait_for(lambda: window.width() == 640)
    gui.click("ollamaDisconnect")
    assert agent.state["provider"] == "mock"
    gui.click("ollamaModels")
    gui.wait_for(lambda: not connection.state["busy"])
    gui.choose("ollamaModel", 0)
    gui.click("ollamaUse")
    assert agent.state["provider"] == "ollama"
    gui.screenshot(profile / "ollama-space-narrow.png")
    assert sum(c[1] == "/api/chat" for c in local.calls) == 1
    local.hold = True
    gui.click("ollamaModels")
    gui.wait_for(lambda: connection.state["busy"])
finally:
    connection.close()
    agent.close()
    controller.close()
    window.close()
    engine = None
