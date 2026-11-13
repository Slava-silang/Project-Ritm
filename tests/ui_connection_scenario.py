"""Настоящий QML, подменный OpenAI и keyring; без внешнего браузера и сети."""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from test_chatgpt import Keyring, Server

from ritm.app import load_window
from ritm.integrations.chatgpt import ChatGPTError, ChatGPTSession
from ritm.platform.credentials import CredentialVault
from ritm.ui.assistant import AssistantController
from ritm.ui.connection import ConnectionController
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
controller = TaskController(profile)
agent = AssistantController(profile)
server = Server()
keyring = Keyring()
session = ChatGPTSession(CredentialVault(profile, keyring), server)
connection = ConnectionController(profile, agent, session)
engine = load_window(controller, assistant=agent, connection=connection)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
pool = ThreadPoolExecutor()
connection._browser.disconnect()


def browser(url):
    query = parse_qs(urlsplit(url).query)
    server.nonce = query["nonce"][0]

    def callback():
        with urlopen(
            query["redirect_uri"][0]
            + "?"
            + urlencode(
                {"state": query["state"][0], "code": "test-code", "client_id": "issued-client"}
            ),
            timeout=2,
        ):
            pass

    pool.submit(callback)


connection._browser.connect(browser)

try:
    gui.idle()
    assert server.requests == []  # запуск приложения не вызывает OpenAI
    gui.click("assistantButton")
    gui.click("connectionExpand")
    assert not gui.item("chatgptProbe").isEnabled()
    server.failure = ChatGPTError("tls", stage="Загрузка настроек входа")
    gui.click("chatgptLogin")
    gui.wait_for(lambda: not connection.state["busy"])
    assert "TLS" in connection.state["error"] and "входа" in connection.state["error"]
    assert not connection.state["connected"]
    gui.screenshot(profile / "chatgpt-network-error.png")
    server.failure = None
    gui.click("chatgptLogin")
    gui.wait_for(lambda: not connection.state["busy"])
    assert connection.state["connected"] and connection.state["sharing"], connection.state
    assert not connection.state["verified"]
    gui.click("chatgptModels")
    gui.wait_for(lambda: not connection.state["busy"])
    assert connection.state["model"] == "gpt-6.1-sol"
    assert not gui.item("chatgptProbe").isEnabled()
    gui.click("chatgptProbeConsent")
    gui.click("chatgptProbe")
    gui.wait_for(lambda: not connection.state["busy"])
    assert connection.state["verified"]
    gui.screenshot(profile / "chatgpt-connected.png")
    assert not gui.item("chatgptProbe").isEnabled()
    gui.click("chatgptUse")
    assert agent.state["provider"] == "chatgpt"
    gui.click("connectionExpand")
    gui.fill("assistantPrompt", "Пример выбранного запроса")
    gui.wait_for(lambda: gui.item("assistantGenerate").isEnabled(), "file catalog ready")
    gui.click("assistantGenerate")
    gui.wait_for(lambda: agent.state["hasDraft"])
    assert not controller.tasks
    assert agent.state["draft"]["title"] == "Получено"
    gui.click("assistantApply")
    gui.idle()
    gui.wait_for(lambda: not agent.state["busy"])
    gui.idle()
    assert len(controller.tasks) == 1
    gui.click("assistantButton")
    gui.click("connectionExpand")
    gui.click("chatgptLogout")
    gui.wait_for(lambda: not connection.state["busy"])
    assert not connection.state["connected"] and keyring.value is None
    assert agent.state["provider"] == "mock"
    assert not connection.state["error"]
    assert "test-access-secret" not in str(connection.state)
    assert "rotated-refresh-secret" not in str(agent.state)
    controller.saveAppearance("dark", 150)
    gui.idle()
    window.setWidth(640)
    window.setHeight(480)
    gui.wait_for(lambda: window.width() == 640)
    gui.screenshot(profile / "chatgpt-narrow.png")
    gui.click("assistantCancel")
finally:
    connection.close()
    agent.close()
    controller.close()
    pool.shutdown(wait=True)
    window.close()
    engine = None
    app.processEvents()
