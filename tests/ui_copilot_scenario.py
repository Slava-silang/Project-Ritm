"""Real QML, fake GitHub account/client; no browser, credentials or network."""

import sys
from pathlib import Path
from types import SimpleNamespace

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from test_copilot_account import AccountClient

from ritm.app import load_window
from ritm.integrations.chatgpt import DIRECT
from ritm.integrations.copilot import CopilotError
from ritm.integrations.copilot_account import CopilotAccount
from ritm.ui.assistant import AssistantController
from ritm.ui.connection import ConnectionController
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
controller = TaskController(profile)
agent = AssistantController(profile)
clients = []


def factory(options):
    client = AccountClient(
        options, content='{"title":"Copilot proposal","subtasks":[],"weights":null}'
    )
    clients.append(client)
    return client


class Account(CopilotAccount):
    fail = False
    wait_login = True
    available = True

    @property
    def installed(self):
        return self.available

    def sign_in(self, cancelled, callback):
        callback("ABCD-1234")
        if self.wait_login:
            cancelled.wait(10)
            raise CopilotError("cancelled")
        if self.fail:
            raise CopilotError("connection")
        self.check(cancelled)


class ChatGPT:
    tokens = {"scope": DIRECT}
    models = [{"slug": "chat-model", "display_name": "Chat model"}]
    model = "chat-model"
    verified = False

    def logout(self):
        self.tokens = None

    vault = SimpleNamespace(warning="")

    def infer(self, *_):
        return '{"title":"ChatGPT proposal","subtasks":[],"weights":null}'


account = Account(profile, client_factory=factory)
connection = ConnectionController(profile, agent, ChatGPT(), account)
engine = load_window(controller, assistant=agent, connection=connection)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
try:
    gui.idle()
    assert not clients and not (profile / "copilot-cli").exists()
    gui.click("assistantButton")
    gui.click("connectionExpand")
    gui.choose("connectionProvider", 1)
    assert gui.item("copilotLogin").isVisible()
    assert not gui.item("copilotUse").isEnabled()
    gui.click("copilotLogin")
    gui.wait_for(lambda: "ABCD-1234" in gui.item("copilotCode").property("text"))
    assert agent.state["busy"] and not gui.item("copilotUse").isEnabled()
    gui.click("copilotCancel")
    gui.wait_for(lambda: not connection.state["busy"])
    assert "отменён" in connection.state["error"]
    assert not connection.state["copilot"]["code"]
    account.wait_login = False
    account.fail = True
    gui.click("copilotLogin")
    gui.wait_for(lambda: not connection.state["busy"])
    assert connection.state["error"] and not account.connected
    account.fail = False
    gui.click("copilotLogin")
    gui.wait_for(lambda: not connection.state["busy"])
    assert account.connected and account.model == "model-a"
    assert agent.state["provider"] == "mock"  # Login does not activate a provider.
    assert not any("send" in client.calls for client in clients)
    gui.click("copilotUse")
    assert agent.state["provider"] == "copilot"
    old_chat = agent.state["chatId"]
    gui.choose("copilotModel", 1)
    assert agent.state["model"] == "model-b"
    assert agent.state["chatId"] != old_chat
    gui.click("connectionExpand")
    gui.fill("assistantPrompt", "Подготовь план")
    gui.wait_for(lambda: gui.item("assistantGenerate").isEnabled())
    gui.click("assistantGenerate")
    gui.wait_for(lambda: agent.state["hasDraft"])
    assert not controller.tasks and agent.state["draft"]["title"] == "Copilot proposal"
    assert len([c for c in clients if "send" in c.calls]) == 1
    assert agent.state["messages"][-1]["label"] == "GitHub Copilot"
    gui.click("connectionExpand")
    copilot_chat = agent.state["chatId"]
    gui.choose("connectionProvider", 0)
    gui.click("chatgptUse")
    assert agent.state["provider"] == "chatgpt" and not agent.state["hasDraft"]
    assert agent.state["chatId"] != copilot_chat and agent.state["contextCount"] == 0
    gui.choose("connectionProvider", 1)
    gui.click("copilotUse")
    assert agent.state["provider"] == "copilot" and agent.state["contextCount"] == 0
    inactive_chat = agent.state["chatId"]
    connection.session.models.append({"slug": "other-chat-model", "display_name": "Other"})
    connection.selectModel("other-chat-model")
    assert agent.state["chatId"] == inactive_chat
    gui.choose("connectionProvider", 0)
    gui.click("chatgptLogout")
    gui.wait_for(lambda: not connection.state["busy"])
    assert agent.state["provider"] == "copilot" and agent.state["chatId"] == inactive_chat
    gui.choose("connectionProvider", 1)
    gui.click("copilotDisconnect")
    assert agent.state["provider"] == "mock" and not account.connected
    gui.click("copilotRestore")
    gui.wait_for(lambda: not connection.state["busy"])
    assert account.connected
    gui.click("copilotUse")
    gui.click("copilotLogin")
    # A non-blocking fake login finishes here; close-while-pending is checked below.
    gui.wait_for(lambda: not connection.state["busy"])
    controller.saveAppearance("dark", 150)
    gui.idle()
    window.setWidth(640)
    window.setHeight(480)
    gui.wait_for(lambda: window.width() == 640)
    gui.click("copilotDisconnect")
    assert not account.connected
    gui.click("copilotRestore")
    gui.wait_for(lambda: not connection.state["busy"])
    gui.click("copilotUse")
    gui.screenshot(profile / "copilot-narrow.png")
    account.available = False
    connection.changed.emit()
    app.processEvents()
    assert not gui.item("copilotLogin").isEnabled()
    account.available = True
    connection.changed.emit()
    app.processEvents()
    account.wait_login = True
    gui.click("copilotLogin")
    gui.wait_for(lambda: connection.state["busy"])
finally:
    connection.close()
    agent.close()
    controller.close()
    window.close()
    engine = None
