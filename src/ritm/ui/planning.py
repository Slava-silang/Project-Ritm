"""Рабочие часы, запрос расписания и подтверждение вне потока интерфейса."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from threading import Event
from uuid import UUID, uuid4

from PySide6.QtCore import Property, QObject, Signal, Slot

from ritm.application.planning import apply_schedule, parse_schedule, planning_context
from ritm.integrations.assistant import Request
from ritm.integrations.chatgpt import ChatGPTError
from ritm.storage.sqlite import TaskStore
from ritm.storage.working_hours import DEFAULT, load_hours, save_hours
from ritm.ui.calendar import local_instant


def hours_fields(hours):
    def text(value):
        return f"{value // 60:02}:{value % 60:02}"

    return [
        {
            "enabled": slot is not None,
            "start": text(slot[0]) if slot else "09:00",
            "end": text(slot[1]) if slot else "18:00",
        }
        for slot in hours
    ]


def parse_hours_fields(rows):
    def minutes(value):
        if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
            raise ValueError("Часы вводятся в формате ЧЧ:ММ")
        h, m = int(value[:2]), int(value[3:])
        if not 0 <= h <= 24 or not 0 <= m <= 59 or (h == 24 and m):
            raise ValueError("Часы должны быть в диапазоне 00:00–24:00")
        return h * 60 + m

    if not isinstance(rows, list) or len(rows) != 7:
        raise ValueError("Укажите семь дней недели")
    return [
        [minutes(row["start"]), minutes(row["end"])] if row["enabled"] else None for row in rows
    ]


class PlanningController(QObject):
    changed = Signal()
    applied = Signal()
    _ready = Signal(object, str, object, str)

    def __init__(self, profile, assistant):
        super().__init__()
        self.profile, self.assistant = profile, assistant
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-planning")
        self._ready.connect(self._finish)
        self._id = None
        self._cancelled = Event()
        self._busy = self._saving = self._closed = False
        self._error = ""
        self._hours = DEFAULT
        self._tasks = []
        self._draft = None
        self._valid = False

    @Property("QVariantMap", notify=changed)
    def state(self):
        draft = self._draft
        return {
            "busy": self._busy,
            "saving": self._saving,
            "error": self._error,
            "hours": hours_fields(self._hours),
            "tasks": self._tasks,
            "provider": self.assistant.adapter.connection.provider,
            "model": self.assistant.adapter.connection.model,
            "hasDraft": draft is not None,
            "canApply": bool(draft and draft.blocks and self._valid and not self._busy),
            "payload": draft.payload if draft else "",
            "blocks": [
                {
                    "start": b.starts_at.astimezone().strftime("%d.%m.%Y %H:%M"),
                    "end": b.ends_at.astimezone().strftime("%d.%m.%Y %H:%M"),
                }
                for b in draft.blocks
            ]
            if draft
            else [],
            "summary": self._summary() if draft else "",
        }

    def _summary(self):
        draft = self._draft
        available = sum((b - a for a, b in draft.context.free), timedelta())
        lines = [
            f"Нужно запланировать: {draft.context.remaining.total_seconds() / 60:g} мин. "
            f"Свободно до срока в этом периоде: {available.total_seconds() / 60:g} мин."
        ]
        if draft.missing:
            lines.append(
                f"Не распределено: {draft.missing.total_seconds() / 60:g} мин. "
                "Выберите более широкий период, измените рабочие часы или вручную "
                "пересмотрите ожидаемое время/дедлайн в карточке задачи."
            )
        elif not draft.context.remaining:
            lines.append("Ожидаемое время уже покрыто существующими блоками.")
        else:
            lines.append("Оставшееся время распределено. Дедлайн и прежние блоки сохраняются.")
        return "\n".join(lines)

    def _run(self, action, work):
        self._cancelled.set()
        self._cancelled = Event()
        cancelled = self._cancelled
        identifier = self._id = uuid4()
        self._busy, self._error = True, ""
        self.changed.emit()

        def execute():
            try:
                result = work(identifier, cancelled)
                self._ready.emit(identifier, action, result, "")
            except (ValueError, ChatGPTError) as error:
                self._ready.emit(identifier, action, None, str(error))
            except (OSError, sqlite3.Error):
                self._ready.emit(
                    identifier, action, None, "Не удалось прочитать или сохранить расписание."
                )
            except Exception:
                self._ready.emit(
                    identifier,
                    action,
                    None,
                    "Не удалось получить корректное расписание. Повторите запрос.",
                )

        self._pool.submit(execute)

    @Slot()
    def load(self):
        if self._busy or self._closed:
            return
        self._draft = None

        def work(*_):
            with TaskStore(self.profile / "tasks.sqlite3") as store:
                tasks = [
                    {"id": str(t.id), "title": t.title}
                    for t in store.list_tasks()
                    if not t.completed
                ]
            try:
                hours, error = load_hours(self.profile), ""
            except ValueError as failure:
                hours, error = DEFAULT, str(failure)
            return hours, tasks, error

        self._run("load", work)

    @Slot("QVariantList")
    def saveHours(self, rows):
        if self._busy:
            return
        self._draft = None
        self._run("hours", lambda *_: save_hours(self.profile, parse_hours_fields(rows)))

    @Slot(str, str, str, str)
    def generate(self, task_id, first_day, last_day, prompt):
        if self._busy or self._closed:
            return
        self._draft, self._valid = None, False
        adapter = self.assistant.adapter

        def work(identifier, cancelled):
            if "schedule" not in adapter.connection.operations:
                raise ValueError("Подключение не поддерживает планирование")
            if len(prompt) > 20000:
                raise ValueError("Запрос должен быть не длиннее 20000 символов")
            try:
                first, last = date.fromisoformat(first_day), date.fromisoformat(last_day)
                if not 0 <= (last - first).days <= 30:
                    raise ValueError()
                start = datetime.combine(first, time()).astimezone()
                end = datetime.combine(last + timedelta(days=1), time()).astimezone()
                task = UUID(task_id)
            except (ValueError, OverflowError):
                raise ValueError(
                    "Выберите задачу и период до 31 дня; даты в формате ГГГГ-ММ-ДД"
                ) from None
            with TaskStore(self.profile / "tasks.sqlite3") as store:
                context = planning_context(store, task, start, end, load_hours(self.profile))
            if cancelled.is_set():
                return None
            if not context.free or not context.remaining:
                return parse_schedule(identifier, '{"blocks":[]}', context)
            request = Request(
                identifier,
                prompt or "Распредели оставшееся время задачи",
                "schedule",
                context.task,
                planning=context.data(),
            )
            response = adapter.generate(request, cancelled)
            return parse_schedule(identifier, response, context)

        self._run("generate", work)

    @Slot(str)
    def edit(self, payload):
        if self._busy or self._draft is None:
            return
        try:
            self._draft = parse_schedule(self._draft.id, payload, self._draft.context)
            self._valid, self._error = True, ""
        except ValueError as error:
            self._valid, self._error = False, str(error)
        self.changed.emit()

    @Slot()
    def apply(self):
        if not self.state["canApply"]:
            return
        draft = self._draft
        self._saving = True

        def work(*_):
            if load_hours(self.profile) != draft.context.hours:
                raise ValueError("Рабочие часы изменились. Сформируйте расписание заново.")
            with TaskStore(self.profile / "tasks.sqlite3") as store:
                return apply_schedule(store, draft)

        self._run("apply", work)

    @Slot("QVariantList")
    def editBlocks(self, rows):
        if self._busy or self._draft is None:
            return
        try:
            if len(rows) != len(self._draft.blocks):
                raise ValueError("Состав черновика изменился")
            data = {
                "blocks": [
                    {
                        "start": local_instant(row["start"]).isoformat(),
                        "end": local_instant(row["end"]).isoformat(),
                    }
                    for row in rows
                ]
            }
            self.edit(json.dumps(data))
        except (ValueError, KeyError, TypeError) as error:
            self._valid, self._error = False, str(error)
            self.changed.emit()

    @Slot(int)
    def removeBlock(self, index):
        if not self._busy and self._draft and 0 <= index < len(self._draft.blocks):
            data = json.loads(self._draft.payload)
            del data["blocks"][index]
            self.edit(json.dumps(data))

    @Slot()
    def cancel(self):
        if self._saving:
            return
        self._cancelled.set()
        self._id, self._draft = None, None
        self._busy, self._valid, self._error = False, False, ""
        self.changed.emit()

    @Slot(object, str, object, str)
    def _finish(self, identifier, action, result, error):
        if self._closed or identifier != self._id or self._cancelled.is_set():
            return
        self._busy = self._saving = False
        self._error = error
        if not error:
            if action == "load":
                self._hours, self._tasks, self._error = result
            elif action == "hours":
                self._hours = result
            elif action == "generate":
                self._draft, self._valid = result, True
            else:
                self._draft = None
                self.applied.emit()
        self.changed.emit()

    def close(self):
        self._closed = True
        self._cancelled.set()
        self._pool.shutdown(wait=True, cancel_futures=True)
