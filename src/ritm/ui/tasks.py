"""Снимки для QML и последовательные фоновые операции с отдельным соединением SQLite."""

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ritm.application.tasks import TaskService
from ritm.domain.task_list import query_tasks
from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task
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
        "categoryId": str(task.category_id) if task.category_id else "",
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


def select_category(categories, title: str, identifier: str = "") -> Category | None:
    title = title.strip()
    if not title:
        return None
    selected = next((x for x in categories if str(x.id) == identifier), None)
    if selected is not None and selected.title.casefold() == title.casefold():
        return selected
    return next(
        (x for x in categories if x.title.casefold() == title.casefold()), None
    ) or Category(title=title)


class TaskController(QObject):
    changed = Signal()
    stateChanged = Signal()
    listChanged = Signal()
    categoriesChanged = Signal()
    filtersChanged = Signal()
    timeChanged = Signal()
    actionSucceeded = Signal(str)
    _ready = Signal(object, str, str)

    def __init__(self, profile: Path, clock: Callable[[], datetime] | None = None):
        super().__init__()
        self._profile = profile
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-storage")
        self._rows = []
        self._source = []
        self._visible = []
        self._categories = []
        self._trash = []
        self._search, self._state, self._category = "", "active", ""
        self._expanded = set()
        self._selected_overdue = False
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._update_listing)
        self._ticker.start(1000)
        self._selected = ""
        self._busy = False
        self._error = ""
        self._closing = False
        self._ready.connect(self._finish)
        self._submit("refresh", {})

    @Property("QVariantList", notify=listChanged)
    def tasks(self):
        return self._visible

    @Property("QVariantList", notify=categoriesChanged)
    def categories(self):
        return self._categories

    @Property("QVariantList", notify=changed)
    def trash(self):
        return self._trash

    @Property(str, notify=filtersChanged)
    def categoryFilter(self):
        return self._category

    @Property(bool, notify=timeChanged)
    def selectedOverdue(self):
        return self._selected_overdue

    @Slot(str, str, str)
    def setFilters(self, search, state, category):
        self._search, self._state, self._category = search, state, category
        self.filtersChanged.emit()
        self._update_listing()
        if self._selected not in {row["id"] for row in self._visible}:
            self._selected = self._visible[0]["id"] if self._visible else ""
            self._update_listing()
            self.changed.emit()

    @Slot(str)
    def toggleExpanded(self, identifier):
        if identifier in self._expanded:
            self._expanded.remove(identifier)
        else:
            self._expanded.add(identifier)
        self._update_listing()

    def _update_listing(self):
        if self._closing:
            return
        task = next((x for x in self._source if str(x.id) == self._selected), None)
        overdue = bool(
            task and not task.completed and task.deadline and task.deadline < self._clock()
        )
        if overdue != self._selected_overdue:
            self._selected_overdue = overdue
            self.timeChanged.emit()
        selected = query_tasks(
            self._source,
            now=self._clock(),
            search=self._search,
            state=self._state,
            category_id=UUID(self._category) if self._category not in ("", "none") else None,
            uncategorized=self._category == "none",
        )
        by_id = {row["id"]: row for row in self._rows}
        visible = [
            {
                **by_id[str(row.task.id)],
                "group": row.group,
                "overdue": row.overdue,
                "expanded": str(row.task.id) in self._expanded,
            }
            for row in selected
        ]
        for index, row in enumerate(visible):
            row["firstInGroup"] = index == 0 or row["group"] != visible[index - 1]["group"]
        if visible != self._visible:
            self._visible = visible
            self.listChanged.emit()

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
            self._update_listing()
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

    @Slot(str)
    def deleteNode(self, identifier):
        self._submit("delete", {"id": identifier})

    @Slot(str)
    def restoreEntry(self, identifier):
        self._submit("restore", {"id": identifier})

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
                    new_category = None
                    if "category" in data["fields"]:
                        categories = store.list_categories()
                        category = select_category(
                            categories,
                            str(data["fields"]["category"]),
                            str(data["fields"].get("categoryId", "")),
                        )
                        new_category = (
                            category
                            if category is not None
                            and category.id not in {x.id for x in categories}
                            else None
                        )
                        fields["category_id"] = category.id if category else None
                    task = (
                        service.edit_details(current.id, category=new_category, **fields)
                        if current
                        else service.create_task(Task(**fields), category=new_category)
                    )
                    selected = str(task.id)
                elif action == "restore":
                    selected = str(service.restore(UUID(data["id"])).id)
                elif action != "refresh":
                    task_id = UUID(selected)
                    if action == "delete":
                        service.delete_node(task_id, UUID(data["id"]), now=self._clock())
                    elif action == "complete":
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
                raw = store.list_tasks()
                categories = [{"id": str(x.id), "title": x.title} for x in store.list_categories()]
                names = {x["id"]: x["title"] for x in categories}
                rows = [
                    {**task_view(task), "category": names.get(str(task.category_id), "")}
                    for task in raw
                ]
                trash = [
                    {
                        **entry,
                        "deletedText": datetime.fromisoformat(entry["deleted_at"])
                        .astimezone()
                        .strftime(DATE_FORMAT),
                    }
                    for entry in store.list_trash()
                ]
            self._ready.emit((rows, selected, categories, trash, raw), "", action)
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
            self._rows, self._selected, categories, self._trash, self._source = result
            if categories != self._categories:
                self._categories = categories
                self.categoriesChanged.emit()
            self._update_listing()
            if self._selected not in {row["id"] for row in self._rows}:
                self._selected = self._visible[0]["id"] if self._visible else ""
                self._update_listing()
            self.changed.emit()
        self.stateChanged.emit()
        if not error:
            self.actionSucceeded.emit(action)

    def close(self):
        self._closing = True
        self._ticker.stop()
        self._pool.shutdown(wait=True)
