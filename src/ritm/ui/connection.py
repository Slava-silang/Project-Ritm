"""Управление аккаунтом без передачи токенов в QML или историю задач."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ritm.integrations.assistant import MockAdapter
from ritm.integrations.chatgpt import DIRECT, ChatGPTAdapter, ChatGPTError, ChatGPTSession
from ritm.platform.credentials import CredentialVault


class ConnectionController(QObject):
    changed = Signal()
    _ready = Signal(str)
    _browser = Signal(str)

    def __init__(self, profile, assistant, session=None):
        super().__init__()
        self.assistant = assistant
        self.session = session or ChatGPTSession(CredentialVault(profile))
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-auth")
        self._cancelled = Event()
        self._busy, self._error, self._closed = False, "", False
        self._ready.connect(self._finish)
        self._browser.connect(self._open_browser)

    @Property("QVariantMap", notify=changed)
    def state(self):
        tokens = self.session.tokens
        return {
            "busy": self._busy,
            "error": self._error,
            "connected": tokens is not None,
            "email": tokens.get("email", "") if tokens else "",
            "sharing": bool(tokens and DIRECT in tokens["scope"].split()),
            "models": self.session.models,
            "model": self.session.model,
            "verified": self.session.verified,
            "warning": self.session.vault.warning,
        }

    @Slot()
    def login(self):
        self._run(lambda: self.session.sign_in(self._cancelled, self._browser.emit))

    @Slot()
    def restore(self):
        self._run(self.session.load)

    @Slot()
    def models(self):
        self._run(self.session.list_models)

    @Slot()
    def probe(self):
        # Вызывается отдельной кнопкой после явного согласия; пользовательских данных нет.
        self._run(
            lambda: self.session.infer("Reply exactly: OK", "Reply with OK only.", self._cancelled)
        )

    @Slot()
    def logout(self):
        def operation():
            self.session.logout()

        self.useMock()
        self._run(operation)

    @Slot(str)
    def selectModel(self, slug):
        if self._busy or self.assistant.state["busy"]:
            return
        if slug in {item["slug"] for item in self.session.models}:
            self.session.model = slug
            self.session.verified = False
            self.changed.emit()
            self.assistant.changed.emit()

    @Slot()
    def useChatGPT(self):
        if self._busy or self.assistant.state["busy"]:
            return
        if self.session.model and self.state["sharing"]:
            self.assistant.cancel()
            self.assistant.adapter = ChatGPTAdapter(self.session)
            self.assistant.changed.emit()

    @Slot()
    def useMock(self):
        if self._busy or self.assistant.state["busy"]:
            return
        self.assistant.cancel()
        self.assistant.adapter = MockAdapter()
        self.assistant.changed.emit()

    @Slot()
    def cancel(self):
        self._cancelled.set()

    @Slot(str)
    def _open_browser(self, url):
        if not self._closed and not self._cancelled.is_set():
            if not QDesktopServices.openUrl(QUrl(url)):
                self._cancelled.set()

    def _run(self, operation):
        if self._busy or self.assistant.state["busy"] or self._closed:
            return
        self._busy, self._error = True, ""
        self._cancelled = Event()
        self.changed.emit()

        def work():
            try:
                operation()
                error = ""
            except ChatGPTError as failure:
                error = str(failure)
            except Exception:
                error = "Не удалось выполнить подключение. Повторите попытку позже."
            self._ready.emit(error)

        self._pool.submit(work)

    @Slot(str)
    def _finish(self, error):
        if self._closed:
            return
        self._busy, self._error = False, error
        self.changed.emit()
        self.assistant.changed.emit()

    def close(self):
        self._closed = True
        self._cancelled.set()
        self._pool.shutdown(wait=True, cancel_futures=True)
