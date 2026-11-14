"""Ограниченный контекст планирования и строгий черновик новых блоков."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid5

from ritm.domain.calendar import WorkBlock, from_instant_us, instant_us, overlaps, utc_instant
from ritm.domain.tasks import Task


@dataclass(frozen=True)
class PlanningContext:
    task: Task
    starts_at: datetime
    ends_at: datetime
    hours: tuple[tuple[int, int] | None, ...]
    busy: tuple[WorkBlock, ...]
    free: tuple[tuple[datetime, datetime], ...]
    remaining: timedelta

    def data(self):
        return {
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "working_hours": self.hours,
            "remaining_minutes": self.remaining.total_seconds() / 60,
            "busy": [
                {"start": b.starts_at.isoformat(), "end": b.ends_at.isoformat()} for b in self.busy
            ],
            "free": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in self.free],
        }


def validate_hours(value):
    if not isinstance(value, (list, tuple)) or len(value) != 7:
        raise ValueError("Укажите часы для семи дней недели")
    result = []
    for slot in value:
        if slot is None:
            result.append(None)
        elif (
            isinstance(slot, (list, tuple))
            and len(slot) == 2
            and all(type(x) is int for x in slot)
            and 0 <= slot[0] < slot[1] <= 1440
        ):
            result.append(tuple(slot))
        else:
            raise ValueError("Рабочий интервал должен быть внутри одного дня, начало раньше конца")
    return tuple(result)


def planning_context(store, task_id, start, end, hours, *, zone=None, now=None):
    start, end = utc_instant(start), utc_instant(end)
    now = utc_instant(now or datetime.now(UTC))
    step = 15 * 60 * 1_000_000
    now = from_instant_us(((instant_us(now) + step - 1) // step) * step)
    if not start < end or end - start > timedelta(days=32):
        raise ValueError("Выберите период до 31 календарного дня")
    if end <= now:
        raise ValueError("Период планирования уже закончился")
    task = store.get_task(task_id)
    if task is None or task.completed:
        raise ValueError("Выберите существующую незавершённую задачу")
    if task.expected_duration is None:
        raise ValueError("Сначала укажите ожидаемое время в карточке задачи")
    hours = validate_hours(hours)
    busy = store.list_work_blocks(window_start=start, window_end=end)
    planned = sum((b.duration for b in store.list_work_blocks(task_id=task_id)), timedelta())
    remaining = max(timedelta(), task.expected_duration - planned)
    free = []
    day = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    if (last - day).days > 31:
        raise ValueError("Выберите период до 31 календарного дня")
    while day <= last:
        slot = hours[day.weekday()]
        if slot:
            midnight = datetime.combine(day, time())

            # astimezone(None) uses system rules for this date, including DST.
            def boundary(minutes, midnight=midnight):
                local = midnight + timedelta(minutes=minutes)
                aware = local.replace(tzinfo=zone) if zone else local.astimezone()
                if aware.replace(tzinfo=None) != local:
                    raise ValueError("Граница рабочих часов не существует из-за перевода времени")
                return utc_instant(aware)

            left = max(start, now, boundary(slot[0]))
            right = min(end, task.deadline or end, boundary(slot[1]))
            for block in sorted(busy, key=lambda b: b.starts_at):
                if block.ends_at <= left or block.starts_at >= right:
                    continue
                if left < block.starts_at:
                    free.append((left, block.starts_at))
                left = max(left, block.ends_at)
            if left < right:
                free.append((left, right))
        day += timedelta(days=1)
    context = PlanningContext(task, start, end, hours, busy, tuple(free), remaining)
    if len(json.dumps(context.data()).encode()) > 128000:
        raise ValueError("Слишком много записей в периоде. Выберите более короткий период")
    return context


@dataclass(frozen=True)
class ScheduleDraft:
    id: UUID
    context: PlanningContext
    blocks: tuple[WorkBlock, ...]
    payload: str

    @property
    def missing(self):
        return self.context.remaining - sum((b.duration for b in self.blocks), timedelta())

    @property
    def digest(self):
        return hashlib.sha256(self.payload.encode()).hexdigest()


def parse_schedule(identifier, payload, context):
    if not isinstance(payload, str) or len(payload.encode()) > 128000:
        raise ValueError("Слишком большой ответ расписания")
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or set(data) != {"blocks"}:
            raise ValueError()
        entries = data["blocks"]
        if not isinstance(entries, list) or len(entries) > 200:
            raise ValueError()
        blocks = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != {"start", "end"}:
                raise ValueError()
            block = WorkBlock(
                id=uuid5(identifier, f"schedule/{i}"),
                task_id=context.task.id,
                starts_at=datetime.fromisoformat(entry["start"]),
                ends_at=datetime.fromisoformat(entry["end"]),
            )
            if not any(a <= block.starts_at < block.ends_at <= b for a, b in context.free):
                raise ValueError("Блок вне свободного рабочего времени или после дедлайна")
            if any(overlaps(block, other) for other in blocks):
                raise ValueError("Предложенные блоки пересекаются")
            blocks.append(block)
        if sum((b.duration for b in blocks), timedelta()) > context.remaining:
            raise ValueError("Предложено больше времени, чем осталось запланировать")
    except (TypeError, KeyError, OverflowError) as error:
        raise ValueError(
            "Неверный формат расписания: нужны blocks с start/end и часовым поясом"
        ) from error
    except ValueError as error:
        raise ValueError(str(error) or "Неверный формат расписания") from error
    normalized = json.dumps(
        {
            "blocks": [
                {"start": b.starts_at.isoformat(), "end": b.ends_at.isoformat()} for b in blocks
            ]
        },
        sort_keys=True,
    )
    return ScheduleDraft(identifier, context, tuple(blocks), normalized)


def apply_schedule(store, draft):
    checked = parse_schedule(draft.id, draft.payload, draft.context)
    if checked != draft:
        raise ValueError("Черновик расписания не прошёл повторную проверку")
    if not draft.blocks:
        raise ValueError("Нет блоков для сохранения")
    return store.apply_schedule(draft)
