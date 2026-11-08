"""Снимки для QML и последовательные фоновые операции с отдельным соединением SQLite."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Property, QObject, Signal, Slot

from ritm.application.tasks import TaskService
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.domain.weights import initialize_weights, progress
from ritm.storage.sqlite import TaskStore

DATE_FORMAT = "%d.%m.%Y %H:%M"


def task_view(task: Task) -> dict:
    task = initialize_weights(task)
    duration = task.expected_duration
    minutes = ""
    if duration is not None:
        us = (duration.days * 86400 + duration.seconds) * 1_000_000 + duration.microseconds
        minutes = (
            str(us // 60_000_000)
            if us % 60_000_000 == 0
            else format(Decimal(us) / Decimal(60_000_000), "f").rstrip("0")
        )
    percent = progress(task)
    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "completed": task.completed,
        "progress": float(percent),
        "progressText": str(percent).replace(".", ","),
        "difficulty": str(task.difficulty) if task.difficulty is not None else "",
        "minutes": minutes,
        "deadline": task.deadline.astimezone().strftime(DATE_FORMAT) if task.deadline else "",
        "subtasks": [
            {
                "id": str(child.id),
                "title": child.title,
                "completed": child.completed,
                "weight": str(child.weight).replace(".", ","),
                "items": [
                    {"id": str(item.id), "title": item.title, "completed": item.completed}
                    for item in child.items
                ],
            }
            for child in task.subtasks
        ],
    }


def parse_details(data: dict, current: Task | None = None) -> dict:
    difficulty = str(data.get("difficulty", "")).strip()
    minutes = str(data.get("minutes", "")).strip().replace(",", ".")
    deadline = str(data.get("deadline", "")).strip()
    try:
        rating = int(difficulty) if difficulty else None
        if difficulty and str(rating) != difficulty:
            raise ValueError
    except ValueError as error:
        raise ValueError("Сложность — целое число от 1 до 10 либо пустое поле") from error
    original = task_view(current) if current is not None else None
    duration = None
    if original is not None and minutes == original["minutes"]:
        duration = current.expected_duration
    elif minutes:
        try:
            value = Decimal(minutes)
            if not value.is_finite() or not Decimal("0.00000001") <= value <= Decimal(2**63 - 1):
                raise ValueError
            us = Fraction(value) * 60_000_000
            if us.denominator != 1 or us > 2**63 - 1:
                raise ValueError
            duration = timedelta(microseconds=int(us))
        except (InvalidOperation, ValueError, OverflowError) as error:
            raise ValueError("Укажите положительную длительность в минутах") from error
    moment = None
    if original is not None and deadline == original["deadline"]:
        moment = current.deadline
    elif deadline:
        try:
            local = datetime.strptime(deadline, DATE_FORMAT)
            moment = local.astimezone()
            if moment.replace(tzinfo=None) != local:
                raise ValueError
        except (ValueError, OverflowError) as error:
            raise ValueError("Дедлайн: ДД.ММ.ГГГГ ЧЧ:ММ, в местном времени") from error
    return {
        "title": str(data.get("title", "")),
        "description": str(data.get("description", "")),
        "difficulty": rating,
        "expected_duration": duration,
        "deadline": moment,
    }


class TaskController(QObject):
    changed = Signal()
    stateChanged = Signal()
    actionSucceeded = Signal(str)
    _ready = Signal(object, str, str)

    def __init__(self, profile: Path):
        super().__init__()
        self._profile = profile
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-storage")
        self._rows = []
        self._selected = ""
        self._busy = False
        self._error = ""
        self._closing = False
        self._ready.connect(self._finish)
        self._submit("refresh", {})

    @Property("QVariantList", notify=changed)
    def tasks(self):
        return self._rows

    @Property("QVariantMap", notify=changed)
    def detail(self):
        return next((row for row in self._rows if row["id"] == self._selected), {})

    @Property(bool, notify=stateChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=stateChanged)
    def error(self):
        return self._error

    @Slot(str)
    def selectTask(self, identifier):
        if not self._busy:
            self._selected = identifier
            self.changed.emit()

    @Slot(str, "QVariantMap")
    def saveTask(self, identifier, data):
        self._submit("save", {"id": identifier, "fields": dict(data)})

    @Slot(str, bool)
    def setCompleted(self, identifier, completed):
        self._submit("complete", {"id": identifier, "completed": completed})

    @Slot(str)
    def addSubtask(self, title):
        self._submit("add_subtask", {"title": title})

    @Slot(str, str)
    def addItem(self, parent, title):
        self._submit("add_item", {"parent": parent, "title": title})

    @Slot(str, str)
    def renameNode(self, identifier, title):
        self._submit("rename", {"id": identifier, "title": title})

    @Slot("QVariantMap")
    def saveWeights(self, values):
        self._submit("weights", {"values": dict(values)})

    @Slot()
    def equalWeights(self):
        self._submit("equal_weights", {})

    @Slot()
    def reload(self):
        self._submit("refresh", {})

    def _submit(self, action, data):
        if self._busy or self._closing:
            return
        self._busy, self._error = True, ""
        self.stateChanged.emit()
        self._pool.submit(self._run, action, data, self._selected)

    def _run(self, action, data, selected):
        try:
            with TaskStore(self._profile / "tasks.sqlite3") as store:
                service = TaskService(store)
                if action == "save":
                    identifier = data["id"]
                    current = store.get_task(UUID(identifier)) if identifier else None
                    if identifier and current is None:
                        raise KeyError(identifier)
                    fields = parse_details(data["fields"], current)
                    task = (
                        service.edit_details(current.id, **fields)
                        if current
                        else service.create_task(Task(**fields))
                    )
                    selected = str(task.id)
                elif action != "refresh":
                    task_id = UUID(selected)
                    if action == "complete":
                        service.set_completed(task_id, UUID(data["id"]), data["completed"])
                    elif action == "add_subtask":
                        service.add_subtask(task_id, Subtask(title=data["title"]))
                    elif action == "add_item":
                        service.add_item(
                            task_id, UUID(data["parent"]), ChecklistItem(title=data["title"])
                        )
                    elif action == "rename":
                        service.rename_node(task_id, UUID(data["id"]), data["title"])
                    elif action == "weights":
                        try:
                            values = {
                                UUID(key): Decimal(str(value).replace(",", "."))
                                for key, value in data["values"].items()
                            }
                        except InvalidOperation as error:
                            raise ValueError("Веса должны быть числами от 0 до 100") from error
                        service.set_weights(task_id, values)
                    elif action == "equal_weights":
                        service.equal_weights(task_id)
                rows = [task_view(task) for task in store.list_tasks()]
            self._ready.emit((rows, selected), "", action)
        except (sqlite3.Error, OSError):
            self._ready.emit(
                None,
                "Не удалось открыть или сохранить базу. Проверьте доступ к папке и повторите.",
                action,
            )
        except KeyError:
            self._ready.emit(None, "Задача или пункт больше не найдены. Обновите список.", action)
        except (ValueError, OverflowError) as error:
            self._ready.emit(None, str(error), action)
        except Exception:
            self._ready.emit(
                None, "Не удалось выполнить действие. Повторите после перезапуска.", action
            )

    @Slot(object, str, str)
    def _finish(self, result, error, action):
        if self._closing:
            return
        self._busy, self._error = False, error
        if result is not None:
            self._rows, self._selected = result
            if self._selected not in {row["id"] for row in self._rows} and self._rows:
                self._selected = self._rows[0]["id"]
            self.changed.emit()
        self.stateChanged.emit()
        if not error:
            self.actionSucceeded.emit(action)

    def close(self):
        self._closing = True
        self._pool.shutdown(wait=True)
