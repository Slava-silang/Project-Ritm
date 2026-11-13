"""Асинхронный цикл запроса и проверяемого черновика, отдельно от списка задач."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

from PySide6.QtCore import Property, QObject, Signal, Slot

from ritm.application.drafts import parse_draft
from ritm.application.file_context import file_catalog, selected_texts
from ritm.application.tasks import TaskService
from ritm.integrations.assistant import Adapter, MockAdapter, Request
from ritm.integrations.chatgpt import ChatGPTError
from ritm.integrations.file_text import FileTextError
from ritm.storage.sqlite import TaskStore


class AssistantController(QObject):
    changed = Signal()
    applied = Signal(str)
    _ready = Signal(object, str, object, str)
    _filesReady = Signal(object, object, str)

    def __init__(self, profile: Path, adapter: Adapter | None = None):
        super().__init__()
        self.profile = profile
        self.adapter = adapter or MockAdapter()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ritm-assistant")
        self._ready.connect(self._finish)
        self._filesReady.connect(self._finish_files)
        self._files = []
        self._files_id = None
        self._files_context = ""
        self._files_loading = False
        self._files_error = ""
        self._id = None
        self._cancelled = Event()
        self._draft = None
        self._valid = False
        self._busy = False
        self._saving = False
        self._error = ""
        self._closed = False

    @Property("QVariantMap", notify=changed)
    def state(self):
        draft = self._draft
        return {
            "files": self._files,
            "filesLoading": self._files_loading,
            "filesError": self._files_error,
            "busy": self._busy,
            "saving": self._saving,
            "error": self._error,
            "provider": self.adapter.connection.provider,
            "model": self.adapter.connection.model,
            "authentication": self.adapter.connection.authentication,
            "operations": sorted(self.adapter.connection.operations),
            "draft": json.loads(draft.payload) if draft else {},
            "hasDraft": draft is not None,
            "canApply": bool(draft and self._valid and not self._busy),
            "deadlineText": draft.task.deadline.astimezone().strftime("%d.%m.%Y %H:%M")
            if draft and draft.task.deadline
            else "",
            "split": bool(draft and draft.baseline),
            "preview": self._preview() if draft else "",
        }

    def _preview(self):
        task = self._draft.task
        lines = [task.title]
        if self._draft.baseline is not None:
            lines.append("Существующие пункты и поля сохраняются. Добавляются новые ветки.")
        if task.description:
            lines.append(task.description)
        lines.append(f"Сложность: {task.difficulty or 'не указана'}")
        minutes = task.expected_duration.total_seconds() / 60 if task.expected_duration else None
        lines.append(f"Ожидаемое время: {minutes:g} мин" if minutes else "Время не указано")
        lines.append(f"Дедлайн: {task.deadline.isoformat()}" if task.deadline else "Без дедлайна")
        for child in task.subtasks:
            lines.append(f"• {child.title} — {child.weight}%")
            lines.extend(f"    □ {item.title}" for item in child.items)
        return "\n".join(lines)

    @Slot(str)
    def loadFiles(self, context=""):
        if self._busy or self._closed:
            return
        identifier = self._files_id = uuid4()
        self._files_context = context
        self._files, self._files_error, self._files_loading = [], "", True
        self.changed.emit()

        def work():
            try:
                with TaskStore(self.profile / "tasks.sqlite3") as store:
                    result = file_catalog(store, context)
                self._filesReady.emit(identifier, result, "")
            except (ValueError, OSError, sqlite3.Error):
                self._filesReady.emit(identifier, [], "Не удалось загрузить список файлов.")

        self._pool.submit(work)

    @Slot(object, object, str)
    def _finish_files(self, identifier, result, error):
        if self._closed or identifier != self._files_id:
            return
        self._files, self._files_error, self._files_loading = result, error, False
        self.changed.emit()

    @Slot(str, str)
    @Slot(str, str, "QVariantList")
    def generate(self, prompt, target_id="", file_ids=None):
        if self._busy or self._files_loading:
            return
        identifiers = tuple(file_ids or ())
        if any(not isinstance(value, str) for value in identifiers) or len(identifiers) > 5:
            self._error = "Выберите не более пяти файлов из списка."
            self.changed.emit()
            return
        file_context = self._files_context
        operation = "split" if target_id else "create"
        if not prompt.strip() or len(prompt) > 20000:
            self._error = "Введите запрос длиной до 20 000 символов."
            self.changed.emit()
            return
        if operation not in self.adapter.connection.operations:
            self._error = "Это подключение не поддерживает выбранное действие."
            self.changed.emit()
            return
        self._cancelled.set()
        self._cancelled = Event()
        cancelled = self._cancelled
        identifier = self._id = uuid4()
        self._draft = None
        self._busy, self._error = True, ""
        self.changed.emit()
        adapter = self.adapter

        def work():
            try:
                with TaskStore(self.profile / "tasks.sqlite3") as store:
                    selected = store.get_task(UUID(target_id)) if target_id else None
                if target_id and selected is None:
                    raise ValueError("Выбранная задача удалена. Обновите список.")
                materials = (
                    selected_texts(self.profile, file_context, identifiers, cancelled)
                    if identifiers
                    else ()
                )
                if cancelled.is_set():
                    return
                request = Request(identifier, prompt, operation, selected, materials)
                response = adapter.generate(request, cancelled)
                if cancelled.is_set():
                    return
                draft = parse_draft(identifier, response, selected)
                self._ready.emit(identifier, "generate", draft, "")
            except (ChatGPTError, FileTextError) as error:
                self._ready.emit(identifier, "generate", None, str(error))
            except Exception:
                self._ready.emit(
                    identifier,
                    "generate",
                    None,
                    "Не удалось получить корректное предложение. Попробуйте снова.",
                )

        self._pool.submit(work)

    @Slot()
    def cancel(self):
        if self._saving:
            return
        self._cancelled.set()
        self._files_id = None
        self._files_loading = False
        self._id = None
        self._draft = None
        self._busy = False
        self._error = ""
        self.changed.emit()

    @Slot(str)
    @Slot(str, str)
    def edit(self, payload, deadline_local=None):
        if self._busy or self._draft is None:
            return
        try:
            draft = self._draft
            if deadline_local is not None and draft.baseline is None:
                data = json.loads(payload)
                if not isinstance(data, dict):
                    raise ValueError("Неверный формат черновика")
                for field in ("difficulty", "expected_minutes"):
                    if isinstance(data.get(field), str):
                        try:
                            data[field] = int(data[field])
                        except ValueError as error:
                            raise ValueError(
                                "Сложность и минуты должны быть целыми числами"
                            ) from error
                data["deadline"] = (
                    (
                        datetime.strptime(deadline_local.strip(), "%d.%m.%Y %H:%M")
                        .astimezone()
                        .isoformat()
                    )
                    if deadline_local.strip()
                    else None
                )
                payload = json.dumps(data)
            self._draft = parse_draft(draft.id, payload, draft.baseline)
            self._error = ""
            self._valid = True
        except ValueError as error:
            self._error = str(error)
            self._valid = False
        self.changed.emit()

    @Slot()
    def apply(self):
        if self._busy or self._draft is None or not self._valid:
            return
        draft = self._draft
        self._error = ""
        self._busy = self._saving = True
        self.changed.emit()

        def work():
            try:
                with TaskStore(self.profile / "tasks.sqlite3") as store:
                    identifier = TaskService(store).apply_proposal(draft)
                self._ready.emit(draft.id, "apply", identifier, "")
            except (ValueError, KeyError) as error:
                self._ready.emit(draft.id, "apply", None, str(error))
            except (OSError, sqlite3.Error):
                self._ready.emit(
                    draft.id,
                    "apply",
                    None,
                    "Не удалось сохранить предложение. Можно повторить попытку.",
                )

        self._pool.submit(work)

    @Slot(object, str, object, str)
    def _finish(self, identifier, action, result, error):
        if self._closed or identifier != self._id or self._cancelled.is_set():
            return
        self._busy = self._saving = False
        self._error = error
        if not error:
            if action == "generate":
                self._draft = result
                self._valid = True
            else:
                self._draft = None
                self.applied.emit(str(result))
        self.changed.emit()

    def close(self):
        self._closed = True
        self._cancelled.set()
        self._pool.shutdown(wait=True, cancel_futures=True)
