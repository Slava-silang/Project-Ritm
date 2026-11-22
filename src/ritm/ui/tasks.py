"""Снимки для QML и последовательные фоновые операции с отдельным соединением SQLite."""

import re
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfoNotFoundError

from PySide6.QtCore import Property, QObject, QTimer, QTimeZone, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ritm.application.calendar import CalendarService
from ritm.application.garden_runtime import empty_view, sync_garden
from ritm.application.tasks import TaskService
from ritm.domain.calendar import from_instant_us, instant_us, planned_duration
from ritm.domain.focus import focus_by_day
from ritm.domain.pomodoro import Pomodoro
from ritm.domain.statistics import week_start, weekly_statistics
from ritm.domain.task_list import is_overdue, query_tasks
from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task
from ritm.domain.weights import initialize_weights, progress
from ritm.platform.garden_assets import load_garden_assets
from ritm.storage.appearance import DEFAULT, load_appearance, save_appearance
from ritm.storage.attachments import (
    clean_deleted_attachments,
    copy_attachment,
    saved_attachment_path,
)
from ritm.storage.backup import (
    backup_folder,
    create_backup,
    daily_backup,
    inspect_backup,
    recover_profile,
    restore_backup,
    set_backup_folder,
)
from ritm.storage.focus_settings import load_focus_settings, save_focus_settings
from ritm.storage.garden import GardenStore
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
from ritm.ui.garden_editor import editor_view, empty_editor, fertilizer_quote, placement_preview
from ritm.ui.garden_scene import cell_at, garden_scene

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
    today = now.astimezone(zone).date()
    week = weekly_statistics(tasks, completed, focus, anchor or today, zone)
    current = (
        week if week.start == week_start(today) else weekly_statistics(tasks, {}, {}, today, zone)
    )
    last = week.end - timedelta(days=1)
    current_last = current.end - timedelta(days=1)
    maximum = max(week.daily_focus, default=0)
    return {
        "start": week.start.isoformat(),
        "period": f"{week.start:%d.%m.%Y} — {last:%d.%m.%Y}",
        "taskCount": week.task_count,
        "percent": float(week.progress or 0),
        "percentText": str(week.progress).replace(".", ",") if week.task_count else "",
        "currentWeek": {
            "period": f"{current.start:%d.%m} — {current_last:%d.%m}",
            "taskCount": current.task_count,
            "percent": float(current.progress or 0),
            "percentText": str(current.progress).replace(".", ",") if current.task_count else "",
        },
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
    backupsChanged = Signal()
    appearanceChanged = Signal()
    focusSettingsChanged = Signal()
    gardenChanged = Signal()
    gardenBoardChanged = Signal()
    gardenSceneChanged = Signal()
    gardenCommandDone = Signal(str)
    _gardenBoardReady = Signal(object)
    _gardenReady = Signal(object)
    pomodoroChanged = Signal()
    pomodoroEnded = Signal(str)
    focusChanged = Signal()
    systemPauseDone = Signal()
    actionSucceeded = Signal(str)
    taskCompleted = Signal(str)
    _ready = Signal(object, str, str)

    def __init__(
        self,
        profile: Path,
        clock: Callable[[], datetime] | None = None,
        zone: tzinfo | None = None,
        *,
        automatic_backup: bool = False,
    ):
        super().__init__()
        self._appearance = dict(DEFAULT)
        self._appearance_error = ""
        self._profile = profile
        self._backups = {"folder": str((profile / "backups").absolute())}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-storage")
        self._rows = []
        self._source = []
        self._visible = []
        self._categories = []
        self._trash = []
        self._pending_cleanup = 0
        self._attachments = {}
        self._materials = []
        self._search, self._state, self._category = "", "active", ""
        self._expanded = set()
        self._selected_overdue = False
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._zone = zone
        self._garden_zone = ("UTC" if zone is UTC else getattr(zone, "key", None)) or bytes(
            QTimeZone.systemTimeZoneId()
        ).decode()
        self._garden = empty_view()
        self._garden_visible = False
        self._garden_wants_view = False
        self._garden_board = empty_editor()
        self._garden_scene = {}
        self._garden_preview_state = None
        self._garden_pending = None
        self._garden_graphics_error = ""
        self._garden_asset_cache = None
        self._garden_shape_key = None
        self._garden_scene_cache = None
        self._gardenBoardReady.connect(self._set_garden_board)
        self._garden_live_focus = False  # Worker-owned; restore invalidates even during close.
        self._garden_timer = QTimer(self)
        self._garden_timer.setInterval(5000)
        self._garden_timer.timeout.connect(self._on_garden_tick)
        self._gardenReady.connect(self._set_garden)
        today = self._clock().astimezone(zone).date().isoformat()
        self._calendar_config = {"mode": "week", "anchor": today, "day": today}
        self._calendar = {}
        self._statistics = {}
        self._statistics_anchor = None
        self._notice = ""
        self._pomodoro = Pomodoro()
        self._pomodoro_view = {}
        self._pomodoro_reached = False
        self._active_focus = None
        self._focus_loaded = False
        self._recovering = None
        self._pause_on_system = False
        self._focus_settings_error = ""
        self._pending_system_pause = None
        self._focus_view = {}
        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._on_tick)
        self._ticker.start(1000)
        self._selected = ""
        self._busy = False
        self._background = False
        self._pending_action = None
        self._error = ""
        self._closing = False
        self._ready.connect(self._finish)
        self._submit("refresh", {"startup": True, "automatic": automatic_backup})

    def _on_tick(self):
        self._update_listing()
        self._update_focus()
        self._update_pomodoro()
        if (
            not self._busy
            and self._pomodoro.running_since_us is not None
            and instant_us(self._clock()) - self._pomodoro.running_since_us >= 5_000_000
        ):
            self._submit("pomodoro_tick", {"moment": self._clock()})
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

    @Property("QVariantMap", notify=gardenChanged)
    def garden(self):
        return self._garden

    @Slot(bool)
    def setGardenEnabled(self, enabled):
        self._submit("garden_settings", {"enabled": enabled})

    @Property("QVariantMap", notify=gardenBoardChanged)
    def gardenBoard(self):
        return self._garden_board

    @Property("QVariantMap", notify=gardenSceneChanged)
    def gardenScene(self):
        return self._garden_scene

    @Property(bool, notify=stateChanged)
    def gardenCommandPending(self):
        return self._garden_pending is not None

    @Slot(bool)
    def setGardenVisible(self, visible):
        if self._garden_visible != visible:
            self._garden_visible = visible
            if not self._busy:
                self._submit("garden_view", {})

    @Slot(str, result="QVariantMap")
    def gardenQuote(self, text):
        return fertilizer_quote(text, self._garden_board["fertilizerMax"])

    @Slot(float, float, result="QVariantMap")
    def gardenCellAt(self, px, py):
        cell = cell_at(self._garden_scene, px, py) if self._garden_scene else None
        if cell is None:
            return {"valid": False}
        return {"valid": True, **self._garden_scene["cells"][cell[1] * 10 + cell[0]]}

    @Slot(str, int, int, result="QVariantMap")
    def gardenPlacement(self, identifier, x, y):
        if self._garden_preview_state is None:
            return {"error": "Сначала откройте сад.", "cells": []}
        message = placement_preview(self._garden_preview_state, identifier, x, y)
        item = next((i for i in self._garden_board["items"] if i["id"] == identifier), None)
        return {
            "error": message,
            "cells": [{"x": x + dx, "y": y + dy} for dx, dy in item["offsets"]] if item else [],
        }

    @Slot("QVariantMap")
    def gardenCommand(self, command):
        if self.busy or self._closing or self._garden_pending is not None:
            return
        if not self._garden_board["ready"]:
            return
        self._garden_pending = {
            "operation": str(uuid4()),
            "command": dict(command),
            "revision": self._garden_board["revision"],
            "identity": self._garden_board["identity"],
        }
        self._submit("garden_command", dict(self._garden_pending))

    @Slot()
    def retryGardenCommand(self):
        if self._garden_pending is not None:
            self._submit("garden_command", dict(self._garden_pending))

    @Slot(object)
    def _set_garden_board(self, payload):
        self._garden_preview_state = payload["snapshot"]
        if self._garden_board != payload["board"]:
            self._garden_board = payload["board"]
            self.gardenBoardChanged.emit()
        if self._garden_scene != payload["scene"]:
            self._garden_scene = payload["scene"]
            self.gardenSceneChanged.emit()

    def _prepare_garden_board(self, state, identity, summary):
        payload = {"board": empty_editor(), "scene": {}, "snapshot": None}
        if (
            self._garden_wants_view
            and not self._closing
            and summary["enabled"]
            and state is not None
        ):
            try:
                if self._garden_graphics_error:
                    raise ValueError(self._garden_graphics_error)
                if self._garden_asset_cache is None:
                    self._garden_asset_cache = load_garden_assets()
                shape = (state.garden.terrain, state.garden.items)
                if shape != self._garden_shape_key:
                    self._garden_scene_cache = garden_scene(state.garden, self._garden_asset_cache)
                    self._garden_shape_key = shape
                payload = {
                    "board": editor_view(state, identity, self._garden_asset_cache),
                    "scene": self._garden_scene_cache,
                    "snapshot": state,
                }
            except Exception:
                self._garden_graphics_error = (
                    "Не удалось загрузить графику сада. Проверьте установку и повторите обновление."
                )
                payload["board"] = empty_editor(self._garden_graphics_error)
        if not payload["board"]["ready"]:
            self._garden_asset_cache = self._garden_shape_key = self._garden_scene_cache = None
        self._gardenBoardReady.emit(payload)

    def _run_garden_command(self, store, data):
        try:
            if not store.garden_source_status()["enabled"]:
                raise ValueError("Включите учёт сада перед изменением участка.")
            # Deliver first: a financial tick may have invalidated the displayed revision.
            summary = self._sync_garden(store)
            if summary["error"]:
                raise ValueError(summary["error"])
            with GardenStore(self._profile / "garden.sqlite3") as garden:
                result = garden.execute(
                    UUID(data["operation"]),
                    data["command"],
                    expected_revision=data["revision"],
                    expected_identity=data["identity"],
                )
            outcome, error = (
                {"resolved": True, "itemId": str(result.item_id) if result.item_id else ""},
                "",
            )
        except ValueError as failure:
            outcome, error = {"resolved": True}, str(failure)
        except Exception:
            outcome, error = (
                {"resolved": False},
                "Не удалось подтвердить действие сада. "
                "Повторите ту же операцию, чтобы проверить результат.",
            )
        self._sync_garden(store)
        self._ready.emit(outcome, error, "garden_command")

    def _on_garden_tick(self):
        if not self._busy:
            self._submit("garden_tick", {})

    def _update_garden_timer(self):
        active = self._active_focus
        running = bool(
            self._garden["enabled"]
            and not self._garden["error"]
            and self._focus_loaded
            and self._recovering is None
            and active is not None
            and active.running_since_us is not None
            and not self._closing
        )
        if running and not self._garden_timer.isActive():
            self._garden_timer.start()
        elif not running:
            self._garden_timer.stop()

    @Slot(object)
    def _set_garden(self, state):
        self._garden = state
        self.gardenChanged.emit()
        self._update_garden_timer()

    def _sync_garden(self, store, *, checkpoint=False, enabled=None):
        try:
            state = sync_garden(
                store,
                self._profile,
                self._clock().astimezone(self._zone),
                zone=self._garden_zone,
                checkpoint=checkpoint,
                enabled=enabled,
                with_state=self._garden_wants_view,
            )
        except Exception as error:
            # Optional delivery must never misreport an already committed task as lost.
            try:
                status = store.garden_source_status()
            except (ValueError, OSError, sqlite3.Error):
                status = {}
            state = {
                **empty_view(),
                **status,
                "error": (
                    "Недоступны данные часовых поясов. Обновите зависимости приложения "
                    "командой uv sync --locked и перезапустите Ритм."
                    if isinstance(error, ZoneInfoNotFoundError)
                    else str(error)
                    if isinstance(error, ValueError)
                    else "Не удалось обновить сад. Проверьте папку профиля и повторите обновление."
                ),
            }
        snapshot, identity = state.pop("_state", None), state.pop("_identity", None)
        self._prepare_garden_board(snapshot, identity, state)
        self._gardenReady.emit(state)
        return state

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

    def _update_pomodoro(self):
        state = self._pomodoro
        elapsed = state.elapsed(
            instant_us(self._clock()), self._active_focus, recovering=self._recovering is not None
        )
        active = state.phase != "idle"
        reached = active and elapsed >= state.target_us
        running = (
            (
                self._active_focus is not None
                and self._active_focus.running_since_us is not None
                and self._recovering is None
            )
            if state.phase == "work"
            else state.running_since_us is not None
        )
        view = {
            "active": active,
            "phase": state.phase,
            "cycle": state.cycle,
            "work": state.work,
            "short": state.short,
            "long": state.long,
            "elapsed": focus_time(elapsed),
            "remaining": focus_time(max(0, state.target_us - elapsed)),
            "overtime": focus_time(max(0, elapsed - state.target_us)),
            "reached": reached,
            "running": running,
            "label": {
                "idle": "Pomodoro",
                "work": "Работа",
                "short": "Короткий перерыв",
                "long": "Длинный перерыв",
            }[state.phase],
        }
        if view != self._pomodoro_view:
            self._pomodoro_view = view
            self.pomodoroChanged.emit()
        if reached and not self._pomodoro_reached and self._recovering is None:
            self.pomodoroEnded.emit(state.phase)
        self._pomodoro_reached = reached

    @Property("QVariantMap", notify=pomodoroChanged)
    def pomodoro(self):
        return self._pomodoro_view

    @Slot(str, int, int, int, bool)
    def startPomodoro(self, task_id, work, short, long, replace_current):
        self._pomodoro_command(
            "start" if self._pomodoro.phase == "idle" else "work",
            task_id=task_id,
            work=work,
            short=short,
            long=long,
            replace_current=replace_current,
        )

    @Slot(str)
    def changePomodoro(self, action):
        self._pomodoro_command(action)

    def _pomodoro_command(self, action, **data):
        if self._recovering is not None:
            return
        active = self._active_focus
        self._submit(
            "pomodoro",
            {
                "command": action,
                "revision": self._pomodoro.revision,
                "expected": (str(active.id), active.revision) if active else None,
                **data,
            },
        )

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

    @Property(bool, notify=focusSettingsChanged)
    def pauseOnSystem(self):
        return self._pause_on_system

    @Property(str, notify=focusSettingsChanged)
    def focusSettingsError(self):
        return self._focus_settings_error

    @Slot(bool)
    def saveFocusSettings(self, pause_on_system):
        self._submit("focus_settings", {"pause_on_system": pause_on_system})

    def pauseForSystem(self, reason: str):
        if not self._pause_on_system:
            self.systemPauseDone.emit()
            return
        if self._pending_system_pause is None:
            self._pending_system_pause = (self._clock(), reason)
        self._drain_system_pause()

    def _drain_system_pause(self):
        if self._pending_system_pause is None or self._busy or self._closing:
            return
        moment, reason = self._pending_system_pause
        self._pending_system_pause = None
        if not self._pause_on_system:
            self.systemPauseDone.emit()
            return
        active = self._active_focus
        if active is None or active.running_since_us is None or self._recovering is not None:
            if self._pomodoro.running_since_us is not None:
                self._submit("pomodoro_system_pause", {"moment": moment})
                return
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
            "start",
            task_id=task_id,
            minutes="" if self._pomodoro.phase == "work" else minutes,
            replace_current=replace_current,
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
        return [
            {"id": row["id"], "title": row["title"]} for row in self._rows if not row["completed"]
        ]

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
        # Worker serialization is separate from blocking user-facing controls.
        return self._pending_action is not None or (self._busy and not self._background)

    @Property(str, notify=stateChanged)
    def error(self):
        return self._error

    @Slot(str)
    def selectTask(self, identifier):
        if not self.busy:
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

    @Property("QVariantList", notify=changed)
    def materials(self):
        return self._materials

    @Slot(QUrl)
    def addMaterial(self, url):
        self._submit("attach", {"task_id": None, "node_id": None, "source": url.toLocalFile()})

    @Slot(str, str, str)
    def linkMaterial(self, task_id, node_id, material_id):
        self._submit("link_material", {"task_id": task_id, "node_id": node_id, "id": material_id})

    @Slot(str, str, QUrl)
    def attachFile(self, task_id, node_id, url):
        self._submit(
            "attach", {"task_id": task_id, "node_id": node_id, "source": url.toLocalFile()}
        )

    @Slot(str)
    def openAttachment(self, identifier):
        self._submit("open_attachment", {"id": identifier})

    @Property(int, notify=changed)
    def pendingCleanup(self):
        return self._pending_cleanup

    @Slot("QVariantList")
    def purgeTrash(self, identifiers):
        self._submit("purge_trash", {"ids": list(identifiers)})

    @Property("QVariantMap", notify=appearanceChanged)
    def appearance(self):
        return self._appearance

    @Property(str, notify=appearanceChanged)
    def appearanceError(self):
        return self._appearance_error

    @Slot(str, int)
    def saveAppearance(self, theme, scale):
        self._submit("appearance", {"theme": theme, "scale": scale})

    @Property("QVariantMap", notify=backupsChanged)
    def backups(self):
        return self._backups

    @Property(QUrl, notify=backupsChanged)
    def backupFolderUrl(self):
        return QUrl.fromLocalFile(self._backups["folder"])

    @Slot(QUrl)
    def setBackupFolder(self, url):
        self._submit("backup_folder", {"path": url.toLocalFile()})

    @Slot()
    def createBackup(self):
        self._submit("create_backup", {})

    @Slot(QUrl)
    def inspectBackup(self, url):
        self._submit("inspect_backup", {"path": url.toLocalFile()})

    @Slot()
    def restoreBackup(self):
        preview = self._backups.get("preview")
        if preview:
            self._submit("restore_backup", dict(preview))

    @Slot(str)
    def restoreEntry(self, identifier):
        self._submit("restore", {"id": identifier})

    @Slot()
    def reload(self):
        self._submit("refresh", {})

    def _submit(self, action, data):
        if self._closing:
            return
        background = action in ("pomodoro_tick", "garden_tick")
        if self._busy:
            if self._background and not background and self._pending_action is None:
                self._pending_action = (action, data)
                self._error, self._notice = "", ""
                self.stateChanged.emit()
            return
        self._busy = True
        self._background = background
        if not background:
            self._error, self._notice = "", ""
            self.stateChanged.emit()
        self._enqueue(action, data)

    def _enqueue(self, action, data):
        self._pool.submit(
            self._run,
            action,
            data,
            self._selected,
            dict(self._calendar_config),
            self._statistics_anchor,
            self._focus_loaded and self._recovering is None,
            self._garden_visible,
        )

    def _run(self, action, data, selected, config, statistics_anchor, live_focus, garden_visible):
        self._garden_wants_view = garden_visible
        if action in ("refresh", "garden_view", "garden_settings", "restore_backup"):
            self._garden_graphics_error = ""
        self._garden_live_focus = (
            live_focus and not data.get("startup") and action != "restore_backup"
        )
        notice = ""
        backup_state = None
        appearance_state = None
        appearance_error = ""
        focus_settings = None
        focus_settings_error = ""
        try:
            if action == "focus_settings":
                try:
                    saved = save_focus_settings(self._profile, data["pause_on_system"])
                except OSError as error:
                    raise ValueError(
                        "Не удалось сохранить настройку таймера. "
                        "Проверьте доступ и место в папке профиля."
                    ) from error
                self._ready.emit(saved, "", action)
                return
            if data.get("startup"):
                recover_profile(self._profile)
                try:
                    focus_settings = load_focus_settings(self._profile)
                except (ValueError, OSError) as error:
                    focus_settings = False
                    focus_settings_error = (
                        str(error)
                        if isinstance(error, ValueError)
                        else ("Не удалось прочитать настройку паузы; автопауза выключена.")
                    )
            if action == "appearance":
                with TaskStore(self._profile / "tasks.sqlite3", clock=self._clock) as store:
                    garden_state = self._sync_garden(store)
                try:
                    previous = load_appearance(self._profile)
                except ValueError:
                    previous = dict(DEFAULT)
                if data["theme"] not in (
                    *garden_state["themes"],
                    "system",
                    "dusk",
                    previous["theme"],
                ):
                    raise ValueError("Эта тема откроется за достижение сада.")
                appearance_state = save_appearance(self._profile, data["theme"], data["scale"])
                self._ready.emit(appearance_state, "", action)
                return
            if action in ("backup_folder", "create_backup", "inspect_backup", "restore_backup"):
                try:
                    backup_state = self._run_backup(action, data)
                except (OSError, sqlite3.Error) as error:
                    raise ValueError(
                        "Не удалось прочитать или записать копию. Проверьте папку и место на диске."
                    ) from error
                if action != "restore_backup":
                    self._ready.emit(backup_state, "", action)
                    return
                selected = ""
            with TaskStore(self._profile / "tasks.sqlite3", clock=self._clock) as store:
                service = TaskService(store)
                if data.get("startup") or action == "restore_backup":
                    store.checkpoint_pomodoro(self._clock(), startup=True)
                    live_focus = False
                    self._garden_live_focus = False
                if action == "garden_command":
                    self._run_garden_command(store, data)
                    return
                if action in ("garden_settings", "garden_tick", "garden_view"):
                    state = self._sync_garden(
                        store,
                        checkpoint=live_focus and action != "garden_view",
                        enabled=data["enabled"] if action == "garden_settings" else None,
                    )
                    self._ready.emit(
                        None, state["error"] if action == "garden_settings" else "", action
                    )
                    return
                if (
                    action == "complete"
                    and data["completed"]
                    and live_focus
                    and store.garden_source_status()["enabled"]
                ):
                    store.checkpoint_garden_focus(self._clock())
                if action == "pomodoro":
                    store.change_pomodoro(
                        data["command"],
                        self._clock(),
                        data["revision"],
                        data["expected"],
                        **{
                            k: v
                            for k, v in data.items()
                            if k not in ("command", "revision", "expected")
                        },
                    )
                elif action in ("pomodoro_tick", "pomodoro_system_pause"):
                    store.checkpoint_pomodoro(
                        data.get("moment", self._clock()), pause=action == "pomodoro_system_pause"
                    )
                    self._ready.emit(store.get_pomodoro(), "", action)
                    return
                if action == "calendar":
                    config = data
                elif action == "attach":
                    if not data["source"]:
                        raise ValueError("Выберите локальный файл")
                    try:
                        copy_attachment(
                            store,
                            self._profile,
                            UUID(data["task_id"]) if data["task_id"] is not None else None,
                            UUID(data["node_id"]) if data["node_id"] is not None else None,
                            Path(data["source"]),
                        )
                    except OSError as error:
                        raise ValueError(
                            "Не удалось скопировать файл. Проверьте доступ и свободное место."
                        ) from error
                elif action == "link_material":
                    store.link_material(
                        UUID(data["task_id"]), UUID(data["node_id"]), UUID(data["id"])
                    )
                elif action == "open_attachment":
                    identifier = UUID(data["id"])
                    matches = store.list_materials(identifier) or store.list_attachments(identifier)
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
                elif action == "purge_trash":
                    store.purge_trash({UUID(identifier) for identifier in data["ids"]})
                elif action == "restore":
                    selected = str(service.restore(UUID(data["id"])).id)
                elif action not in ("refresh", "restore_backup"):
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
                if action in ("refresh", "purge_trash", "restore_backup"):
                    clean_deleted_attachments(store, self._profile)
                self._sync_garden(store)
                if data.get("startup") and action == "refresh":
                    try:
                        appearance_state = load_appearance(self._profile)
                    except (OSError, ValueError) as error:
                        appearance_error = str(error)
                    try:
                        folder = backup_folder(self._profile)
                        backup_state = {"folder": str(folder)}
                        if data["automatic"]:
                            daily_backup(
                                self._profile, folder, self._clock().astimezone(self._zone)
                            )
                    except (ValueError, OSError, sqlite3.Error):
                        backup_state = {
                            **(backup_state or {}),
                            "automaticError": (
                                "Не удалось создать ежедневную копию. "
                                "Проверьте папку и файлы профиля."
                            ),
                        }
                pending_cleanup = len(store.pending_attachment_cleanup())
                raw = store.list_tasks()
                blocks = store.list_work_blocks()
                source_urls = {}
                for link in store.list_source_links():
                    source_urls.setdefault((link.kind, str(link.target_id)), []).append(
                        link.source.url
                    )
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
                    sources=source_urls,
                )
                categories = [{"id": str(x.id), "title": x.title} for x in store.list_categories()]
                names = {x["id"]: x["title"] for x in categories}
                rows = [
                    {
                        **task_view(task),
                        "sourceUrls": source_urls.get(("task", str(task.id)), []),
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
                pomodoro_state = store.get_pomodoro()
                materials = [
                    {"id": str(entry.id), "name": entry.name} for entry in store.list_materials()
                ]
                material_ids = {entry["id"] for entry in materials}
                attachments = {}
                for entry in store.list_attachments():
                    attachments.setdefault(str(entry.node_id), []).append(
                        {
                            "id": str(entry.id),
                            "name": entry.name,
                            "shared": str(entry.id) in material_ids,
                        }
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
                    materials,
                    pending_cleanup,
                    backup_state,
                    appearance_state,
                    appearance_error,
                    pomodoro_state,
                    focus_settings,
                    focus_settings_error,
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

    def _run_backup(self, action, data):
        if action == "backup_folder":
            if not data["path"]:
                raise ValueError("Выберите локальную папку")
            set_backup_folder(self._profile, Path(data["path"]))
            return {"folder": str(backup_folder(self._profile)), "automaticError": ""}
        if action == "create_backup":
            folder = backup_folder(self._profile)
            saved = create_backup(self._profile, folder, self._clock().astimezone(self._zone))
            return {"saved": saved.name, "automaticError": ""}
        if action == "inspect_backup":
            if not data["path"]:
                raise ValueError("Выберите локальный архив")
            archive = Path(data["path"])
            preview = inspect_backup(archive)
            created = datetime.fromisoformat(preview["created"]).astimezone(self._zone)
            return {
                "preview": {
                    **preview,
                    "path": str(archive),
                    "dateText": created.strftime(DATE_FORMAT),
                }
            }
        restore_backup(self._profile, Path(data["path"]), data["token"])
        return {"preview": {}, "restored": True}

    @Slot(object, str, str)
    def _finish(self, result, error, action):
        completed_before = {task.id for task in self._source if task.completed}
        if self._closing:
            return
        if action in ("pomodoro_tick", "garden_tick"):
            if action == "pomodoro_tick" and result is not None:
                self._pomodoro = result
                self._update_pomodoro()
            self._busy = False
            self._background = False
            if error:
                self._error = error
            pending, self._pending_action = self._pending_action, None
            if pending is not None:
                # No intermediate idle notification: queued work must finish before exit.
                self._submit(*pending)
            elif error:
                self.stateChanged.emit()
            self._update_garden_timer()
            self._drain_system_pause()
            if not self._busy and self._garden_wants_view != self._garden_visible:
                self._submit("garden_view", {})
            return
        if action == "garden_command" and result is not None:
            if result["resolved"]:
                self._garden_pending = None
            if not error:
                self.gardenCommandDone.emit(result.get("itemId", ""))
            result = None
        if action == "focus_settings" and result is not None:
            self._pause_on_system = result
            self._focus_settings_error = ""
            self.focusSettingsChanged.emit()
            result = None
        if action == "appearance" and result is not None:
            self._appearance = result
            self._appearance_error = ""
            self.appearanceChanged.emit()
            result = None
        if action in ("backup_folder", "create_backup", "inspect_backup"):
            update = result or ({"preview": {}} if action == "inspect_backup" else {})
            self._backups = {**self._backups, **update}
            self.backupsChanged.emit()
            result = None
        if action == "open_attachment" and result is not None:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.absolute()))):
                error = "Не удалось открыть вложение. Проверьте программу для этого типа файла."
            result = None
        if action in ("pomodoro_tick", "pomodoro_system_pause"):
            if result is not None:
                self._pomodoro = result
                self._update_pomodoro()
            result = None
        self._busy = False
        if error or action not in ("pomodoro_tick", "garden_tick"):
            self._error = error
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
                self._materials,
                self._pending_cleanup,
                backup_state,
                appearance_state,
                appearance_error,
                self._pomodoro,
                focus_settings,
                focus_settings_error,
            ) = result
            if focus_settings is not None:
                self._pause_on_system = focus_settings
                self._focus_settings_error = focus_settings_error
                self.focusSettingsChanged.emit()
            if appearance_state is not None or appearance_error:
                self._appearance = appearance_state or dict(DEFAULT)
                self._appearance_error = appearance_error
                self.appearanceChanged.emit()
            if backup_state is not None:
                self._backups = {**self._backups, **backup_state}
                self.backupsChanged.emit()
            if action == "restore_backup":
                self._focus_loaded = False
            self.statisticsChanged.emit()
            if not self._focus_loaded:
                self._focus_loaded = True
                if self._active_focus is not None:
                    self._recovering = self._active_focus.id
            if self._active_focus is None or self._active_focus.id != self._recovering:
                self._recovering = None
            self._update_focus()
            self._update_pomodoro()
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
        self._update_garden_timer()
        self.stateChanged.emit()
        if action in ("system_pause", "pomodoro_system_pause"):
            self.systemPauseDone.emit()
        self._drain_system_pause()
        if not self._busy and self._garden_wants_view != self._garden_visible:
            self._submit("garden_view", {})
        if not error:
            self.actionSucceeded.emit(action)
            if action == "complete":
                for task in self._source:
                    if task.completed and task.id not in completed_before:
                        self.taskCompleted.emit(str(task.id))

    def close(self):
        self._closing = True
        self._ticker.stop()
        self._garden_timer.stop()
        if self._pending_action is not None:
            pending, self._pending_action = self._pending_action, None
            self._enqueue(*pending)
        # The final break checkpoint is queued behind any outstanding DB command.
        self._pool.submit(self._close_pomodoro).result()
        self._pool.shutdown(wait=True)

    def _close_pomodoro(self):
        try:
            with TaskStore(self._profile / "tasks.sqlite3") as store:
                store.checkpoint_pomodoro(self._clock(), pause=True)
                self._sync_garden(store, checkpoint=self._garden_live_focus)
        except (OSError, sqlite3.Error, ValueError):
            # The last successful checkpoint remains recoverable; never reset damaged data.
            pass
