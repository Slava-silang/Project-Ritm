"""Отдельный источник почты: все операции в worker, письма только в памяти."""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ritm.integrations.gmail import GmailError, MailScope, check_cancelled
from ritm.integrations.google_auth import GmailSession
from ritm.platform.gmail_credentials import GmailVault
from ritm.ui.mail_assistant import MailAssistantController


class GmailController(QObject):
    changed = Signal()
    _ready = Signal(object, object, str)
    _browser = Signal(object, str, object)

    def __init__(self, profile, session=None, *, browser=None, assistant=None):
        super().__init__()
        self.session = session if session is not None else GmailSession(GmailVault(profile))
        self.browser = browser or (lambda url: QDesktopServices.openUrl(QUrl(url)))
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-gmail")
        self._closed, self._busy = False, False
        self._cancelled, self._id = Event(), None
        self._info = {"configured": False, "connected": False, "email": "", "warning": ""}
        self._error, self._batch, self._selected = "", None, -1
        self._ready.connect(self._finish)
        self._browser.connect(self._open_browser)
        self._proposals = MailAssistantController(profile, self, assistant)

    @Property(QObject, constant=True)
    def proposals(self):
        return self._proposals

    def selected_mail(self):
        return self._batch.messages[self._selected] if self._batch and self._selected >= 0 else None

    @Property("QVariantMap", notify=changed)
    def state(self):
        messages = self._batch.messages if self._batch else ()
        selected = messages[self._selected] if 0 <= self._selected < len(messages) else None
        return {
            **self._info,
            "busy": self._busy,
            "error": self._error,
            "loaded": self._batch is not None,
            "messages": [
                {"subject": m.subject or "Без темы", "sender": m.sender, "date": m.date}
                for m in messages
            ],
            "unavailable": self._batch.unavailable if self._batch else 0,
            "selected": self._selected,
            "body": selected.body if selected else "",
            "bodyAvailable": selected.body_available if selected else False,
            "sourceUrl": selected.source_url if selected else "",
        }

    def _run(self, job):
        if self._busy or self._closed or self._proposals.saving:
            return
        self._proposals.cancel()
        self._busy, self._error, self._batch, self._selected = True, "", None, -1
        identifier, cancelled = uuid4(), Event()
        self._id, self._cancelled = identifier, cancelled
        self.changed.emit()

        def work():
            result, error = None, ""
            try:
                result = job(cancelled, identifier)
            except GmailError as failure:
                error = str(failure)
            except Exception:
                error = "Не удалось выполнить действие Gmail. Проверьте подключение и конфигурацию."
            info = {
                "configured": self.session.config is not None,
                "connected": self.session.tokens is not None,
                "email": self.session.tokens["email"] if self.session.tokens else "",
                "warning": self.session.vault.warning,
            }
            self._ready.emit(identifier, (info, result), error)

        self._pool.submit(work)

    @Slot(QUrl)
    def configure(self, url):
        def job(cancelled, _identifier):
            if not url.isLocalFile():
                raise GmailError("Выберите локальный файл конфигурации Google.")
            self.session.configure(Path(url.toLocalFile()), cancelled)

        self._run(job)

    @Slot()
    def restore(self):
        self._run(lambda cancelled, _: self.session.restore(cancelled))

    @Slot()
    def login(self):
        self._run(
            lambda cancelled, identifier: self.session.sign_in(
                cancelled, lambda url: self._launch_browser(identifier, url, cancelled)
            )
        )

    def _launch_browser(self, identifier, url, cancelled):
        gate, result = Event(), {"ok": False}
        self._browser.emit(identifier, url, (gate, result))
        end = time.monotonic() + 10
        while not gate.wait(0.05):
            check_cancelled(cancelled)
            if time.monotonic() >= end:
                raise GmailError("Не удалось открыть браузер Google. Повторите вход.")
        check_cancelled(cancelled)
        if not result["ok"]:
            raise GmailError("Не удалось открыть браузер Google. Повторите вход.")

    @Slot(str, bool, bool)
    def read(self, query, include_spam_trash, consent):
        def job(cancelled, _identifier):
            if not consent:
                raise GmailError("Подтвердите чтение выбранных писем Gmail.")
            return self.session.read(MailScope(query, include_spam_trash), cancelled)

        self._run(job)

    @Slot()
    def logout(self):
        self._run(lambda _cancelled, _: self.session.logout())

    @Slot(int)
    def selectMessage(self, index):
        if (
            not self._busy
            and not self._proposals.saving
            and self._batch
            and 0 <= index < len(self._batch.messages)
        ):
            if index != self._selected:
                self._proposals.cancel()
            self._selected = index
            self.changed.emit()

    @Slot()
    def cancel(self):
        if self._proposals.saving:
            return
        self._proposals.cancel()
        self._cancelled.set()
        self._batch, self._selected = None, -1
        self.changed.emit()

    @Slot(object, str, object)
    def _open_browser(self, identifier, url, gate_result):
        gate, result = gate_result
        try:
            if not self._closed and identifier == self._id and not self._cancelled.is_set():
                result["ok"] = bool(self.browser(url))
        except Exception:
            result["ok"] = False
        finally:
            gate.set()

    @Slot(object, object, str)
    def _finish(self, identifier, value, error):
        if self._closed or identifier != self._id:
            return
        self._busy = False
        self._info = value[0]
        if self._cancelled.is_set():
            self._error = self._error or "Действие Gmail отменено."
        else:
            self._error, self._batch = error, value[1]
        self.changed.emit()

    def close(self):
        self._proposals.close()
        self._closed = True
        self.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)
