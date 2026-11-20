"""Подменный Gmail: Qt picker, вход, согласие/фильтр/просмотр, отмена и узкий UI."""

import sys
from pathlib import Path
from threading import Event

from gui_support import GuiHarness
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from test_gmail import message
from test_google_auth import config_file, session

from ritm.app import load_window
from ritm.ui.assistant import AssistantController
from ritm.ui.gmail import GmailController
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
QGuiApplication.setAttribute(Qt.AA_DontUseNativeDialogs)
app = QGuiApplication([])
QQuickStyle.setStyle("Basic")
config_path = config_file(profile)
current = session(profile)


class API:
    def __init__(self):
        self.calls = []
        self.hold, self.started = Event(), Event()
        self.delay = False

    def get(self, path, params, token, cancelled):
        self.calls.append((path, params))
        if path == "profile":
            return {"emailAddress": "student@example.org"}
        if path == "messages":
            return {"messages": [{"id": "a1"}]}
        if self.delay:
            self.started.set()
            self.hold.wait(5)
        return message(value="MAIL_PRIVATE_BODY: Сдать работу в пятницу. <b>Это текст</b>")


current.api = API()
backend = TaskController(profile)
agent = AssistantController(profile)
agent.gmail.close()
browsers = []
agent._gmail = GmailController(profile, current, browser=lambda url: browsers.append(url) or True)
source = agent.gmail
engine = load_window(backend, assistant=agent)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, backend)


def idle():
    gui.wait_for(lambda: not source.state["busy"])


try:
    gui.idle()
    assert not current.api.calls and not current.http.calls
    gui.click("assistantButton")
    gui.click("gmailSourceButton")
    assert not gui.item("gmailLogin").isEnabled()
    picker = window.findChild(QObject, "gmailConfigPicker")
    picker.setProperty("currentFolder", QUrl.fromLocalFile(str(profile)))
    gui.click("gmailImportConfig")

    def file_item():
        listing = gui.item("fileDialogListView")
        return next(
            (
                i
                for i in listing.property("contentItem").childItems()
                if i.property("fileName") == config_path.name
            ),
            None,
        )

    gui.wait_for(file_item)
    target = file_item()
    QTest.mouseClick(
        target.window(),
        Qt.LeftButton,
        Qt.NoModifier,
        target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint(),
    )
    gui.wait_for(lambda: picker.property("selectedFile").toLocalFile() == str(config_path))
    picker.accept()
    idle()
    assert source.state["configured"] and not source.state["connected"]
    assert not current.api.calls and not current.http.calls
    source.browser = lambda _: False
    gui.click("gmailLogin")
    idle()
    assert "браузер" in source.state["error"]
    assert not current.api.calls and not current.http.calls and not current.tokens
    source.browser = lambda url: browsers.append(url) or True
    gui.click("gmailLogin")
    idle()
    assert source.state["connected"] and len(browsers) == 1
    assert current.api.calls == [("profile", {})]
    assert not gui.item("gmailRead").isEnabled()
    source.read("", True, False)
    idle()
    assert "Подтвердите" in source.state["error"] and len(current.api.calls) == 1
    gui.fill("gmailQuery", "newer_than:30d")
    gui.click("gmailReadConsent")
    gui.click("gmailRead")
    idle()
    assert len(source.state["messages"]) == 1
    assert current.api.calls[-2][1]["q"] == "newer_than:30d"
    gui.click("gmailMessage_0")
    assert "MAIL_PRIVATE_BODY" in source.state["body"]
    gui.screenshot(profile / "gmail-preview.png")
    gui.click("gmailClose")
    assert not source.state["messages"] and not source.state["body"]
    gui.click("gmailSourceButton")
    assert not source.state["loaded"]

    current.api.delay = True
    gui.click("gmailReadConsent")
    gui.click("gmailRead")
    gui.wait_for(current.api.started.is_set)
    gui.click("gmailCancel")
    current.api.hold.set()
    idle()
    assert not source.state["loaded"]
    current.api.delay = False
    gui.click("gmailClose")
    gui.click("assistantCancel")
    backend.saveAppearance("dark", 150)
    gui.idle()
    window.setWidth(640)
    window.setHeight(480)
    app.processEvents()
    QTest.qWait(150)
    gui.click("assistantButton")
    gui.click("gmailSourceButton")
    gui.screenshot(profile / "gmail-narrow.png")
    gui.click("gmailReadConsent")
    gui.click("gmailRead")
    idle()
    gui.click("gmailMessage_0")
    gui.reveal(gui.item("gmailMessageBody"))
    footer_position = gui.item("gmailClose").mapToScene(QPointF(0, 0))
    assert 0 <= footer_position.y() < window.height(), (
        footer_position,
        window.height(),
        gui.item("gmailSourceDialog").property("y"),
    )
    gui.screenshot(profile / "gmail-body-narrow.png")
    gui.click("gmailLogout")
    idle()
    assert not source.state["connected"] and not source.state["messages"]
    gui.click("gmailClose")
    assert not current.vault.backend.values
    gui.wait_for(lambda: not agent.state["historyBusy"])
    for path in profile.glob("*.json"):
        if path != config_path:
            content = path.read_text()
            assert all(
                v not in content
                for v in ("MAIL_PRIVATE_BODY", "FAKE_SECRET", "FAKE_ACCESS", "FAKE_REFRESH")
            )
finally:
    current.api.hold.set()
    agent.close()
    backend.close()
    window.hide()
    engine = None
