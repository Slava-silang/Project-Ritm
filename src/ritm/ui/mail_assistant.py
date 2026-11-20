"""Выбранное письмо → проверенный черновик → явное подтверждение, без истории чата."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from uuid import uuid4

from PySide6.QtCore import Property, QObject, Signal, Slot

from ritm.application.mail_context import mail_prompt, mail_result
from ritm.application.mail_proposals import MailProposalService, mail_preview, parse_mail_proposal
from ritm.domain.calendar import WorkBlock
from ritm.domain.sources import SourceRef
from ritm.domain.tasks import Task
from ritm.integrations.assistant import Request
from ritm.integrations.chatgpt import ChatGPTError
from ritm.storage.sqlite import TaskStore
from ritm.ui.calendar import local_instant


def local_text(value):
    if value is None:
        return ""
    result = value.astimezone().strftime("%d.%m.%Y %H:%M %z")
    return result[:-2] + ":" + result[-2:]


class MailAssistantController(QObject):
    changed = Signal()
    applied = Signal()
    _ready = Signal(object, str, object, str)

    def __init__(self, profile, source, assistant, *, clock=None):
        super().__init__()
        self.profile, self.source, self.assistant = profile, source, assistant
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-mail-ai")
        self._closed, self._busy, self._saving = False, False, False
        self._id, self._cancelled, self._key = None, Event(), self.connection_key()
        self._draft, self._error, self._answer, self._preview = None, "", "", ""
        self._nearby = ()
        self._ready.connect(self._finish)
        if assistant:
            assistant.changed.connect(self.connection_changed)

    @property
    def busy(self):
        return self._busy

    @property
    def saving(self):
        return self._saving

    def connection_key(self):
        if self.assistant is None:
            return None
        adapter = self.assistant.adapter
        session = getattr(adapter, "session", None)
        tokens = getattr(session, "tokens", None)
        return (
            id(adapter),
            adapter.connection.provider,
            adapter.connection.model,
            tokens.get("sub", "") if tokens else "",
            tokens.get("client_id", "") if tokens else "",
            bool(getattr(self.assistant, "_connecting", False)),
        )

    def connection_changed(self):
        current = self.connection_key()
        if current != self._key:
            self._key = current
            self.cancel()

    @Property("QVariantMap", notify=changed)
    def state(self):
        connection = self.assistant.adapter.connection if self.assistant else None
        draft = self._draft
        task = draft.after if draft and isinstance(draft.after, Task) else None
        event = draft.after if draft and isinstance(draft.after, WorkBlock) else None
        return {
            "busy": self._busy,
            "saving": self._saving,
            "error": self._error,
            "answer": self._answer,
            "supported": bool(
                connection
                and "mail" in connection.operations
                and not getattr(self.assistant, "_connecting", False)
            ),
            "recipient": f"{connection.provider} / {connection.model}"
            if connection
            else "Помощник не подключён",
            "hasDraft": draft is not None,
            "preview": self._preview,
            "payload": json.loads(draft.payload) if draft else {},
            "title": task.title if task else "",
            "description": task.description if task else "",
            "difficulty": task.difficulty if task else None,
            "minutes": int(task.expected_duration.total_seconds() / 60)
            if task and task.expected_duration
            else None,
            "deadline": local_text(task.deadline) if task else "",
            "start": local_text(event.starts_at) if event else "",
            "end": local_text(event.ends_at) if event else "",
        }

    def _submit(self, kind, job):
        if self._closed or self._busy:
            return
        self._busy, self._saving, self._error = True, kind == "apply", ""
        identifier, cancelled = uuid4(), Event()
        self._id, self._cancelled = identifier, cancelled
        self.changed.emit()

        def work():
            try:
                result, error = job(cancelled), ""
            except (ValueError, ChatGPTError, KeyError) as failure:
                result, error = (
                    None,
                    str(failure)
                    if not isinstance(failure, KeyError)
                    else "Запись больше недоступна.",
                )
            except Exception:
                result, error = None, "Не удалось подготовить или сохранить предложение из письма."
            self._ready.emit(identifier, kind, result, error)

        self._pool.submit(work)

    @staticmethod
    def preview(draft, store):
        value = mail_preview(draft)
        nearby = ()
        if isinstance(draft.after, WorkBlock):
            nearby = tuple(
                b
                for b in store.list_work_blocks(
                    window_start=draft.after.starts_at, window_end=draft.after.ends_at
                )
                if b.id != draft.after.id
            )
            if nearby:
                value += f"\nВнимание: событие пересекается с записями календаря ({len(nearby)})."
        return value, nearby

    @Slot(str, bool)
    def generate(self, prompt, consent):
        if (
            self._busy
            or self.source.state["busy"]
            or self.assistant is None
            or self.assistant._busy
            or getattr(self.assistant, "_connecting", False)
        ):
            return
        message = self.source.selected_mail()
        self._draft, self._answer, self._preview = None, "", ""
        if not consent or message is None or not message.body_available:
            self._error = "Выберите письмо с текстом и подтвердите передачу выбранной модели."
            self.changed.emit()
            return
        adapter, key = self.assistant.adapter, self.connection_key()
        self._key = key
        if "mail" not in adapter.connection.operations:
            self._error = "Это подключение не поддерживает обработку писем."
            self.changed.emit()
            return
        if not isinstance(prompt, str) or len(prompt) > 16000:
            self._error = "Запрос по письму слишком большой."
            self.changed.emit()
            return

        def job(cancelled):
            ref = SourceRef.from_mail(message)
            with TaskStore(self.profile / "tasks.sqlite3") as store:
                status, link, before = MailProposalService(store).source_state(ref)
                if status in ("unchanged", "unavailable"):
                    raise ValueError(
                        "Это письмо уже обработано."
                        if status == "unchanged"
                        else "Связанная запись удалена. Восстановите её; копия не создаётся."
                    )
                tasks = tuple(t for t in store.list_tasks() if not t.completed)
            mail = {
                "subject": message.subject,
                "sender": message.sender,
                "date": message.date,
                "body": message.body,
            }
            if isinstance(before, WorkBlock):
                mail["linked_event"] = {
                    "id": str(before.id),
                    "title": before.title,
                    "start": before.starts_at.isoformat(),
                    "end": before.ends_at.isoformat(),
                }
            request = Request(
                uuid4(),
                prompt,
                "mail",
                selected=before if isinstance(before, Task) else None,
                mail=mail,
                tasks=tasks,
                current_time=self._clock(),
            )
            mail_prompt(request)  # проверить объём/границы даже у подменного адаптера до вызова
            if cancelled.is_set():
                raise ValueError("Обработка письма отменена.")
            payload = adapter.generate(request, cancelled)
            result = mail_result(request.id, payload, ref, link, before, tasks)
            if isinstance(result, str):
                return key, result, ""
            with TaskStore(self.profile / "tasks.sqlite3") as store:
                preview, nearby = self.preview(result, store)
            return key, result, preview, nearby

        self._submit("generate", job)

    @Slot(str, str, str, str)
    def edit(self, payload, deadline, start, end):
        if not self._draft or self._busy:
            return
        original = self._draft

        def job(_cancelled):
            if len(payload.encode()) > 128000:
                raise ValueError("Предложение слишком большое.")
            data = json.loads(payload)
            if isinstance(original.after, Task) and deadline != local_text(original.after.deadline):
                fields = data["task"].setdefault("changes", {}) if original.before else data["task"]
                fields["deadline"] = (
                    local_instant(deadline).isoformat() if deadline.strip() else None
                )
            elif isinstance(original.after, WorkBlock):
                for field, value, old in (
                    ("start", start, original.after.starts_at),
                    ("end", end, original.after.ends_at),
                ):
                    if value != local_text(old):
                        data["event"][field] = local_instant(value).isoformat()
            checked = parse_mail_proposal(
                original.id,
                json.dumps(data),
                original.source,
                link=original.link,
                before=original.before,
            )
            with TaskStore(self.profile / "tasks.sqlite3") as store:
                preview, nearby = self.preview(checked, store)
                return self.connection_key(), checked, preview, nearby

        self._submit("edit", job)

    @Slot(bool)
    def apply(self, confirmed):
        if self._busy or not self._draft:
            return
        if not confirmed:
            self._error = "Подтвердите сохранение проверенного предложения."
            self.changed.emit()
            return
        draft = self._draft
        nearby = self._nearby

        def job(_cancelled):
            with TaskStore(self.profile / "tasks.sqlite3") as store:
                return MailProposalService(store).apply(draft, expected_overlaps=nearby)

        self._submit("apply", job)

    @Slot()
    def cancel(self):
        if self._saving:
            return
        self._cancelled.set()
        self._draft, self._preview, self._answer, self._error = None, "", "", ""
        self.changed.emit()

    @Slot(object, str, object, str)
    def _finish(self, identifier, kind, result, error):
        if self._closed or identifier != self._id:
            return
        self._busy, self._saving = False, False
        if kind != "apply" and (self._cancelled.is_set() or self.connection_key() != self._key):
            self.changed.emit()
            return
        self._error = error
        if not error:
            if kind == "apply":
                self._draft, self._preview, self._answer = None, "", "Предложение сохранено."
                self.applied.emit()
            elif isinstance(result[1], str):
                self._answer, self._draft, self._preview = result[1], None, ""
            else:
                self._draft, self._preview, self._answer = result[1], result[2], ""
                self._nearby = result[3]
        self.changed.emit()

    def close(self):
        self.cancel()
        self._closed = True
        if self.assistant:
            self.assistant.changed.disconnect(self.connection_changed)
        self._pool.shutdown(wait=True, cancel_futures=True)
