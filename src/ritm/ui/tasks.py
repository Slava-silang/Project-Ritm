"""Снимки для QML и последовательные фоновые операции с отдельным соединением SQLite."""

import re
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ritm.application.calendar import CalendarService
from ritm.application.tasks import TaskService
from ritm.domain.calendar import from_instant_us, instant_us, planned_duration
from ritm.domain.focus import focus_by_day
from ritm.domain.statistics import week_start, weekly_statistics
from ritm.domain.task_list import is_overdue, query_tasks
from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task
from ritm.domain.weights import initialize_weights, progress
from ritm.storage.attachments import copy_attachment, saved_attachment_path
from ritm.storage.sqlite import TaskStore
from ritm.ui.calendar import (
    WEEKDAYS,
    block_from_fields,
    calendar_view,
    change_block,
    duration_text,
    local_instant,
    shift_period,
)

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


def focus_time(microseconds: int) -> str:
    minutes, seconds = divmod(microseconds // 1_000_000, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def focus_duration_input(value: str) -> int:
    match = re.fullmatch(
        r"([0-9]{1,9}):([0-5][0-9]):([0-5][0-9])(?:\.([0-9]{1,6}))?", value.strip()
    )
    if match is None:
        raise ValueError("Длительность: ЧЧ:ММ:СС")
    hours, minutes, seconds, fraction = match.groups()
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1_000_000 + int(
        (fraction or "").ljust(6, "0")
    )


def focus_history_view(session, intervals, zone):
    days = focus_by_day(intervals, zone)
    fraction = session.worked_us % 1_000_000
    elapsed = focus_time(session.worked_us)
    return {
        "id": str(session.id),
        "revision": session.revision,
        "started": from_instant_us(session.started_us).astimezone(zone).strftime(DATE_FORMAT),
        "elapsed": elapsed,
        "durationInput": f"{elapsed}.{fraction:06d}" if fraction else elapsed,
        "days": " · ".join(
            f"{day:%d.%m}: {focus_time(value)}" for day, value in sorted(days.items())
        )
        if len(days) > 1
        else "",
    }


def statistics_view(tasks, rows, completed, focus, anchor, now, zone):
    week = weekly_statistics(tasks, completed, focus, anchor or now.astimezone(zone).date(), zone)
    last = week.end - timedelta(days=1)
    maximum = max(week.daily_focus, default=0)
    return {
        "start": week.start.isoformat(),
        "period": f"{week.start:%d.%m.%Y} — {last:%d.%m.%Y}",
        "taskCount": week.task_count,
        "percent": float(week.progress or 0),
        "percentText": str(week.progress).replace(".", ",") if week.task_count else "",
        "completedCount": week.completed_count,
        "undatedCount": week.undated_count,
        "overdueCount": sum(is_overdue(task, now) for task in tasks),
        "days": [
            {
                "label": f"{WEEKDAYS[i]} {(week.start + timedelta(days=i)):%d.%m}",
                "time": focus_time(value),
                "fraction": value / maximum if maximum else 0.0,
            }
            for i, value in enumerate(week.daily_focus)
        ],
        "totalFocus": focus_time(sum(week.daily_focus)),
        "tasks": [
            {
                "id": row["id"],
                "title": row["title"],
                "expected": row["minutes"],
                "time": focus_time(week.task_focus.get(UUID(row["id"]), 0)),
            }
            for row in rows
            if UUID(row["id"]) in week.task_ids
        ],
    }


class TaskController(QObject):
    changed = Signal()
    stateChanged = Signal()
    listChanged = Signal()
    categoriesChanged = Signal()
    filtersChanged = Signal()
    timeChanged = Signal()
    calendarChanged = Signal()
    statisticsChanged = Signal()
    focusChanged = Signal()
    systemPauseDone = Signal()
    actionSucceeded = Signal(str)
    _ready = Signal(object, str, str)

    def __init__(
        self, profile: Path, clock: Callable[[], datetime] | None = None, zone: tzinfo | None = None
    ):
        super().__init__()
        self._profile = profile
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-storage")
        self._rows = []
        self._source = []
        self._visible = []
        self._categories = []
        self._trash = []
        self._attachments = {}
        self._search, self._state, self._category = "", "active", ""
        self._expanded = set()
        self._selected_overdue = False
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._zone = zone
        today = self._clock().astimezone(zone).date().isoformat()
        self._calendar_config = {"mode": "week", "anchor": today, "day": today}
        self._calendar = {}
        self._statistics = {}
        self._statistics_anchor = None
        self._notice = ""
        self._active_focus = None
        self._focus_loaded = False
        self._recovering = None
        self._pending_system_pause = None
        self._focus_view = {}
        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._on_tick)
        self._ticker.start(1000)
        self._selected = ""
        self._busy = False
        self._error = ""
        self._closing = False
        self._ready.connect(self._finish)
        self._submit("refresh", {})

    def _on_tick(self):
        self._update_listing()
        self._update_focus()
        if self._statistics:
            now = self._clock()
            overdue = sum(is_overdue(task, now) for task in self._source)
            if overdue != self._statistics["overdueCount"]:
                self._statistics = {**self._statistics, "overdueCount": overdue}
                self.statisticsChanged.emit()
        if (
            self._calendar
            and not self._busy
            and self._calendar["today"] != self._clock().astimezone(self._zone).date().isoformat()
        ):
            self._submit("refresh", {})

    def _update_focus(self):
        session = self._active_focus
        view = {}
        if session is not None:
            now_us = instant_us(self._clock())
            recovery = self._recovering == session.id
            view = {
                "title": next(
                    (row["title"] for row in self._rows if row["id"] == str(session.task_id)),
                    "Задача в корзине",
                ),
                "elapsed": focus_time(session.worked_us if recovery else session.elapsed(now_us)),
                "recovery": recovery,
                "recoveryId": str(session.id) if recovery else "",
                "lastStart": (
                    from_instant_us(session.running_since_us)
                    .astimezone(self._zone)
                    .strftime(DATE_FORMAT + ":%S %z")
                    if recovery and session.running_since_us is not None
                    else ""
                ),
                "target": focus_time(session.target_us) if session.target_us else "",
                "paused": session.running_since_us is None,
                "reached": not recovery and session.target_reached(now_us),
            }
        if view != self._focus_view:
            self._focus_view = view
            self.focusChanged.emit()

    @Property("QVariantMap", notify=focusChanged)
    def focus(self):
        return self._focus_view

    def _focus_command(self, action, **data):
        active = self._active_focus
        if self._recovering is not None and action != "recover":
            return
        expected = (str(active.id), active.revision) if active else None
        self._submit("focus", {"command": action, "expected": expected, **data})

    @Slot()
    def finishFocusForExit(self):
        active = self._active_focus
        if active is not None and self._recovering is None:
            self._submit("focus_exit", {"expected": (str(active.id), active.revision)})

    def pauseForSystem(self, reason: str):
        if self._pending_system_pause is None:
            self._pending_system_pause = (self._clock(), reason)
        self._drain_system_pause()

    def _drain_system_pause(self):
        if self._pending_system_pause is None or self._busy or self._closing:
            return
        moment, reason = self._pending_system_pause
        self._pending_system_pause = None
        active = self._active_focus
        if active is None or active.running_since_us is None or self._recovering is not None:
            self.systemPauseDone.emit()
            return
        self._submit(
            "system_pause",
            {
                "expected": (str(active.id), active.revision),
                "moment": moment,
                "reason": reason,
            },
        )

    @Slot(str, str, bool)
    def startFocus(self, task_id, minutes, replace_current):
        self._focus_command(
            "start", task_id=task_id, minutes=minutes, replace_current=replace_current
        )

    @Slot(str)
    def changeFocus(self, action):
        self._focus_command(action)

    @Slot(str, str, bool)
    def recoverFocus(self, identifier, end, last_known):
        if self._recovering is not None and str(self._recovering) == identifier:
            self._focus_command("recover", end=end, last_known=last_known)

    @Slot(str, str, str)
    def addFocusRecord(self, task_id, start, end):
        self._submit("focus_history", {"task_id": task_id, "start": start, "end": end})

    @Slot(str, int, str)
    def editFocusRecord(self, identifier, revision, duration):
        self._submit(
            "focus_history", {"id": identifier, "revision": revision, "duration": duration}
        )

    @Property("QVariantMap", notify=statisticsChanged)
    def statistics(self):
        return self._statistics

    @Slot(int)
    def navigateStatistics(self, direction):
        if not self._statistics or direction not in (-1, 0, 1):
            return
        anchor = date.fromisoformat(self._statistics["start"]) + timedelta(days=7 * direction)
        self._submit("statistics", {"anchor": week_start(anchor) if direction else None})

    @Property("QVariantMap", notify=calendarChanged)
    def calendar(self):
        return self._calendar

    @Property("QVariantList", notify=changed)
    def calendarTasks(self):
        return [{"id": row["id"], "title": row["title"]} for row in self._rows]

    @Property(str, notify=stateChanged)
    def notice(self):
        return self._notice

    @Slot(str)
    def setCalendarMode(self, mode):
        self._submit(
            "calendar",
            {**self._calendar_config, "mode": mode, "anchor": self._calendar_config["day"]},
        )

    @Slot(int)
    def navigateCalendar(self, direction):
        try:
            anchor = shift_period(
                date.fromisoformat(self._calendar_config["anchor"]),
                self._calendar_config["mode"],
                direction,
            )
            self._submit(
                "calendar",
                {**self._calendar_config, "anchor": anchor.isoformat(), "day": anchor.isoformat()},
            )
        except (ValueError, OverflowError):
            self._error = "Этот период выходит за поддерживаемый диапазон дат"
            self.stateChanged.emit()

    @Slot()
    def calendarToday(self):
        today = self._clock().astimezone(self._zone).date().isoformat()
        self._submit("calendar", {**self._calendar_config, "anchor": today, "day": today})

    @Slot(str)
    def selectCalendarDay(self, day):
        self._submit("calendar", {**self._calendar_config, "day": day})

    @Slot("QVariantMap")
    def createBlock(self, fields):
        self._submit("block_create", dict(fields))

    @Slot("QVariantMap")
    def changeBlock(self, fields):
        self._submit("block_change", dict(fields))

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

    @Property("QVariantMap", notify=changed)
    def attachments(self):
        return self._attachments

    @Slot(str, str, QUrl)
    def attachFile(self, task_id, node_id, url):
        self._submit(
            "attach", {"task_id": task_id, "node_id": node_id, "source": url.toLocalFile()}
        )

    @Slot(str)
    def openAttachment(self, identifier):
        self._submit("open_attachment", {"id": identifier})

    @Slot(str)
    def restoreEntry(self, identifier):
        self._submit("restore", {"id": identifier})

    @Slot()
    def reload(self):
        self._submit("refresh", {})

    def _submit(self, action, data):
        if self._busy or self._closing:
            return
        self._busy, self._error, self._notice = True, "", ""
        self.stateChanged.emit()
        self._pool.submit(
            self._run,
            action,
            data,
            self._selected,
            dict(self._calendar_config),
            self._statistics_anchor,
        )

    def _run(self, action, data, selected, config, statistics_anchor):
        notice = ""
        try:
            with TaskStore(self._profile / "tasks.sqlite3", clock=self._clock) as store:
                service = TaskService(store)
                if action == "calendar":
                    config = data
                elif action == "attach":
                    if not data["source"]:
                        raise ValueError("Выберите локальный файл")
                    try:
                        copy_attachment(
                            store,
                            self._profile,
                            UUID(data["task_id"]),
                            UUID(data["node_id"]),
                            Path(data["source"]),
                        )
                    except OSError as error:
                        raise ValueError(
                            "Не удалось скопировать файл. Проверьте доступ и свободное место."
                        ) from error
                elif action == "open_attachment":
                    matches = store.list_attachments(UUID(data["id"]))
                    if not matches:
                        raise ValueError("Вложение больше не доступно")
                    path = saved_attachment_path(self._profile, matches[0])
                    self._ready.emit(path, "", action)
                    return
                elif action == "statistics":
                    statistics_anchor = data["anchor"]
                elif action == "system_pause":
                    store.change_focus("pause", data["moment"], data["expected"])
                    notice = (
                        "Фокус-сессия на паузе после сна компьютера"
                        if data["reason"] == "sleep"
                        else "Фокус-сессия на паузе после блокировки экрана"
                    )
                elif action == "focus_exit":
                    store.change_focus("finish", self._clock(), data["expected"])
                elif action == "focus_history":
                    if "task_id" in data:
                        store.add_focus_record(
                            UUID(data["task_id"]),
                            local_instant(data["start"], self._zone),
                            local_instant(data["end"], self._zone),
                            self._clock(),
                        )
                    else:
                        store.edit_focus_duration(
                            UUID(data["id"]),
                            data["revision"],
                            focus_duration_input(data["duration"]),
                            self._clock(),
                        )
                elif action == "focus":
                    command, expected = data["command"], data["expected"]
                    if command == "start":
                        if expected and not data["replace_current"]:
                            raise ValueError("Подтвердите завершение текущей сессии")
                        minutes = data["minutes"].strip()
                        if minutes and (
                            not minutes.isascii()
                            or not minutes.isdecimal()
                            or len(minutes) > 12
                            or int(minutes) == 0
                        ):
                            raise ValueError("Укажите целое положительное число минут")
                        store.change_focus(
                            command,
                            self._clock(),
                            expected,
                            task_id=UUID(data["task_id"]),
                            target_us=int(minutes) * 60_000_000 if minutes else None,
                        )
                    elif command == "recover":
                        end = None if data["last_known"] else local_instant(data["end"], self._zone)
                        store.change_focus(command, self._clock(), expected, recovered_end=end)
                    else:
                        store.change_focus(command, self._clock(), expected)
                elif action in ("block_create", "block_change"):
                    expected = None
                    if action == "block_change":
                        expected = store.get_work_block(UUID(data["id"]))
                        if expected is None:
                            raise KeyError(data["id"])
                        if (
                            expected.starts_at.isoformat() != data["expectedStart"]
                            or expected.ends_at.isoformat() != data["expectedEnd"]
                            or expected.title != data.get("expectedTitle", expected.title)
                        ):
                            raise ValueError(
                                "Блок уже изменился. Обновите календарь и повторите действие"
                            )
                        block = change_block(expected, data, self._zone)
                    else:
                        block = block_from_fields(data, self._zone)
                    day = block.starts_at.astimezone(self._zone).date()
                    # Validate the destination period before committing a new record.
                    calendar_view(
                        store.list_tasks(),
                        (),
                        mode=config["mode"],
                        anchor=day,
                        selected=day,
                        today=self._clock().astimezone(self._zone).date(),
                        zone=self._zone,
                    )
                    result = (
                        CalendarService(store).update_block(block, expected=expected)
                        if expected is not None
                        else CalendarService(store).create_block(block)
                    )
                    messages = []
                    if result.warnings.overlapping_ids:
                        messages.append("есть пересечение с другими блоками")
                    if result.warnings.after_deadline:
                        messages.append("окончание позже дедлайна")
                    if block.task_id is None:
                        notice = "Событие изменено" if expected is not None else "Событие сохранено"
                    else:
                        notice = "Блок изменён" if expected is not None else "Блок сохранён"
                    if messages:
                        notice += ": " + "; ".join(messages)
                    day = block.starts_at.astimezone(self._zone).date().isoformat()
                    config = {**config, "anchor": day, "day": day}
                elif action == "save":
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
                blocks = store.list_work_blocks()
                active = None
                history = {}
                for session in store.list_focus_sessions():
                    if session.finished_us is None:
                        active = session
                    else:
                        history.setdefault(session.task_id, []).append(session)
                intervals = store.list_finished_focus_intervals()
                planned = {
                    task.id: duration_text(
                        planned_duration(b for b in blocks if b.task_id == task.id)
                    )
                    for task in raw
                }
                calendar = calendar_view(
                    raw,
                    blocks,
                    mode=config["mode"],
                    anchor=date.fromisoformat(config["anchor"]),
                    selected=date.fromisoformat(config["day"]),
                    today=self._clock().astimezone(self._zone).date(),
                    zone=self._zone,
                )
                categories = [{"id": str(x.id), "title": x.title} for x in store.list_categories()]
                names = {x["id"]: x["title"] for x in categories}
                rows = [
                    {
                        **task_view(task),
                        "category": names.get(str(task.category_id), ""),
                        "plannedText": planned[task.id],
                        "focusTotal": focus_time(
                            sum(x.worked_us for x in history.get(task.id, ()))
                        ),
                        "focusHistory": [
                            focus_history_view(x, intervals.get(x.id, ()), self._zone)
                            for x in history.get(task.id, ())
                        ],
                    }
                    for task in raw
                ]
                statistics = statistics_view(
                    raw,
                    rows,
                    store.list_completion_dates(),
                    {
                        task_id: [
                            pair for session in sessions for pair in intervals.get(session.id, ())
                        ]
                        for task_id, sessions in history.items()
                    },
                    statistics_anchor,
                    self._clock(),
                    self._zone,
                )
                attachments = {}
                for entry in store.list_attachments():
                    attachments.setdefault(str(entry.node_id), []).append(
                        {"id": str(entry.id), "name": entry.name}
                    )
                trash = [
                    {
                        **entry,
                        "deletedText": datetime.fromisoformat(entry["deleted_at"])
                        .astimezone()
                        .strftime(DATE_FORMAT),
                    }
                    for entry in store.list_trash()
                ]
            self._ready.emit(
                (
                    rows,
                    selected,
                    categories,
                    trash,
                    raw,
                    calendar,
                    notice,
                    active,
                    statistics,
                    statistics_anchor,
                    attachments,
                ),
                "",
                action,
            )
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
        if action == "open_attachment" and result is not None:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.absolute()))):
                error = "Не удалось открыть вложение. Проверьте программу для этого типа файла."
            result = None
        self._busy, self._error = False, error
        if result is not None:
            (
                self._rows,
                self._selected,
                categories,
                self._trash,
                self._source,
                self._calendar,
                self._notice,
                self._active_focus,
                self._statistics,
                self._statistics_anchor,
                self._attachments,
            ) = result
            self.statisticsChanged.emit()
            if not self._focus_loaded:
                self._focus_loaded = True
                if self._active_focus is not None:
                    self._recovering = self._active_focus.id
            if self._active_focus is None or self._active_focus.id != self._recovering:
                self._recovering = None
            self._update_focus()
            self._calendar_config = {
                "mode": self._calendar["mode"],
                "anchor": self._calendar["anchor"],
                "day": self._calendar["selected"],
            }
            self.calendarChanged.emit()
            if categories != self._categories:
                self._categories = categories
                self.categoriesChanged.emit()
            self._update_listing()
            if self._selected not in {row["id"] for row in self._rows}:
                self._selected = self._visible[0]["id"] if self._visible else ""
                self._update_listing()
            self.changed.emit()
        self.stateChanged.emit()
        if action == "system_pause":
            self.systemPauseDone.emit()
        self._drain_system_pause()
        if not error:
            self.actionSucceeded.emit(action)

    def close(self):
        self._closing = True
        self._ticker.stop()
        self._pool.shutdown(wait=True)
