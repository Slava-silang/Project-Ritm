"""Локальные периоды и снимки календаря. Не обращается к Qt или базе."""

import calendar
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from uuid import UUID

from ritm.domain.calendar import WorkBlock, block_warnings, utc_instant
from ritm.domain.tasks import Task

DATE_FORMAT = "%d.%m.%Y %H:%M"
MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def local_instant(
    text: str, zone: tzinfo | None = None, preferred_offset: timedelta | None = None
) -> datetime:
    text = text.strip()
    try:
        if len(text) > 16:
            return utc_instant(datetime.strptime(text, DATE_FORMAT + " %z"))
        naive = datetime.strptime(text, DATE_FORMAT)
    except ValueError as error:
        raise ValueError("Укажите дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ") from error
    candidates = set()
    for fold in (0, 1):
        aware = (
            naive.replace(tzinfo=zone, fold=fold) if zone else naive.replace(fold=fold).astimezone()
        )
        try:
            value = utc_instant(aware)
            if value.astimezone(zone).replace(tzinfo=None) == naive:
                candidates.add(value)
        except ValueError:
            continue
    if not candidates:
        raise ValueError("Указанное местное время не существует из-за перевода часов")
    if len(candidates) > 1 and preferred_offset is not None:
        matched = [x for x in candidates if x.astimezone(zone).utcoffset() == preferred_offset]
        if len(matched) == 1:
            return matched[0]
    if len(candidates) > 1:
        raise ValueError(
            "Этот час повторяется дважды. Добавьте смещение, например: " + text + " +02:00"
        )
    return candidates.pop()


def period_days(anchor: date, mode: str) -> tuple[date, ...]:
    if mode == "week":
        first, count = anchor - timedelta(days=anchor.weekday()), 7
    elif mode == "month":
        first = anchor.replace(day=1)
        count = ((first.weekday() + calendar.monthrange(anchor.year, anchor.month)[1] + 6) // 7) * 7
        first -= timedelta(days=first.weekday())
    else:
        raise ValueError("Неизвестный вид календаря")
    return tuple(first + timedelta(days=index) for index in range(count))


def shift_period(anchor: date, mode: str, direction: int) -> date:
    if direction not in (-1, 1):
        raise ValueError("Направление перехода должно быть -1 или 1")
    if mode == "week":
        return anchor + timedelta(days=direction * 7)
    if mode != "month":
        raise ValueError("Неизвестный вид календаря")
    year, month = divmod(anchor.year * 12 + anchor.month - 1 + direction, 12)
    return date(year, month + 1, 1)


def duration_text(duration: timedelta) -> str:
    if duration <= timedelta():
        return "0 мин"
    minutes = duration // timedelta(minutes=1)
    if not minutes:
        return "меньше 1 мин"
    hours, rest = divmod(minutes, 60)
    return f"{hours} ч {rest} мин" if hours and rest else f"{hours} ч" if hours else f"{rest} мин"


def calendar_view(
    tasks: Sequence[Task],
    blocks: Sequence[WorkBlock],
    *,
    mode: str,
    anchor: date,
    selected: date,
    today: date,
    zone: tzinfo | None = None,
) -> dict:
    days = period_days(anchor, mode)
    by_id = {task.id: task for task in tasks}
    events = {}
    for block in blocks:
        if block.task_id is not None and block.task_id not in by_id:
            continue
        task = by_id.get(block.task_id)
        warnings = block_warnings(block, task, blocks)
        messages = []
        if warnings.overlapping_ids:
            messages.append("Пересекается с другим блоком")
        if warnings.after_deadline:
            messages.append("Выходит за дедлайн")
        events[block.id] = {
            "id": str(block.id),
            "taskId": str(task.id) if task is not None else "",
            "kind": "block" if task is not None else "event",
            "isEvent": task is None,
            "eventTitle": block.title,
            "title": task.title if task is not None else block.title,
            "warning": " · ".join(messages),
            "duration": duration_text(block.duration),
            "startsAt": block.starts_at.isoformat(),
            "endsAt": block.ends_at.isoformat(),
            "startText": block.starts_at.astimezone(zone).strftime(DATE_FORMAT + " %z"),
            "endText": block.ends_at.astimezone(zone).strftime(DATE_FORMAT + " %z"),
        }
    cells = []
    for day in days:
        start = local_instant(day.strftime("%d.%m.%Y") + " 00:00", zone)
        end = local_instant((day + timedelta(days=1)).strftime("%d.%m.%Y") + " 00:00", zone)
        entries = []
        for block in blocks:
            if block.id not in events or not (block.starts_at < end and block.ends_at > start):
                continue
            clipped_start, clipped_end = max(block.starts_at, start), min(block.ends_at, end)
            end_text = (
                "24:00" if clipped_end == end else clipped_end.astimezone(zone).strftime("%H:%M")
            )
            entries.append(
                {
                    **events[block.id],
                    "time": clipped_start.astimezone(zone).strftime("%H:%M") + "–" + end_text,
                    "sort": clipped_start.isoformat(),
                    "segmentDuration": duration_text(clipped_end - clipped_start),
                    "day": day.isoformat(),
                    "offsetMinutes": (clipped_start - start) / timedelta(minutes=1),
                    "segmentMinutes": (clipped_end - clipped_start) / timedelta(minutes=1),
                    "canMove": clipped_start == block.starts_at,
                    "canResize": clipped_end == block.ends_at,
                }
            )
        for task in tasks:
            if task.deadline is None or task.deadline.astimezone(zone).date() != day:
                continue
            entries.append(
                {
                    "id": "deadline_" + str(task.id),
                    "taskId": str(task.id),
                    "kind": "deadline",
                    "title": task.title,
                    "time": task.deadline.astimezone(zone).strftime("%H:%M"),
                    "warning": "",
                    "duration": "",
                    "sort": task.deadline.astimezone(UTC).isoformat(),
                }
            )
        entries.sort(key=lambda item: (item["sort"], item["kind"], item["id"]))
        cells.append(
            {
                "date": day.isoformat(),
                "number": day.day,
                "label": WEEKDAYS[day.weekday()] + " " + day.strftime("%d.%m"),
                "today": day == today,
                "selected": day == selected,
                "inMonth": day.month == anchor.month,
                "entries": entries,
                "blockCount": sum(x["kind"] == "block" for x in entries),
                "eventCount": sum(x["kind"] == "event" for x in entries),
                "deadlineCount": sum(x["kind"] == "deadline" for x in entries),
            }
        )
    title = (
        MONTHS[anchor.month - 1].capitalize() + f" {anchor.year}"
        if mode == "month"
        else days[0].strftime("%d.%m.%Y") + " — " + days[-1].strftime("%d.%m.%Y")
    )
    chosen = next((cell for cell in cells if cell["date"] == selected.isoformat()), cells[0])
    chosen_day = date.fromisoformat(chosen["date"])
    day_start = local_instant(chosen_day.strftime("%d.%m.%Y") + " 00:00", zone)
    day_end = local_instant((chosen_day + timedelta(days=1)).strftime("%d.%m.%Y") + " 00:00", zone)
    hour_points = [
        day_start + timedelta(hours=i)
        for i in range(int((day_end - day_start) / timedelta(hours=1)) + 1)
    ]
    labels = [point.astimezone(zone).strftime("%H:%M") for point in hour_points]
    labels[-1] = "24:00"
    ticks = [
        {
            "minute": (point - day_start) / timedelta(minutes=1),
            "label": label
            + (point.astimezone(zone).strftime(" %z") if labels.count(label) > 1 else ""),
        }
        for point, label in zip(hour_points, labels, strict=True)
    ]
    timeline = []
    lane_ends = []
    for entry in chosen["entries"]:
        if entry["kind"] not in ("block", "event"):
            continue
        start_minute = entry["offsetMinutes"]
        lane = next((i for i, end in enumerate(lane_ends) if end <= start_minute), len(lane_ends))
        end_minute = start_minute + max(38, entry["segmentMinutes"])
        if lane == len(lane_ends):
            lane_ends.append(end_minute)
        else:
            lane_ends[lane] = end_minute
        timeline.append({**entry, "lane": lane})
    return {
        "timeline": timeline,
        "timelineLanes": max(1, len(lane_ends)),
        "timelineMinutes": (day_end - day_start) / timedelta(minutes=1),
        "ticks": ticks,
        "today": today.isoformat(),
        "mode": mode,
        "anchor": anchor.isoformat(),
        "selected": chosen["date"],
        "days": cells,
        "title": title,
        "selectedLabel": chosen["label"],
        "selectedEvents": chosen["entries"],
        "formDate": date.fromisoformat(chosen["date"]).strftime("%d.%m.%Y"),
    }


def block_from_fields(data: dict, zone: tzinfo | None = None) -> WorkBlock:
    if data.get("kind", "task") not in ("task", "event"):
        raise ValueError("Неизвестный тип записи календаря")
    event = data.get("kind") == "event"
    return WorkBlock(
        task_id=None if event else UUID(str(data.get("taskId", ""))),
        title=str(data.get("title", "")) if event else "",
        starts_at=local_instant(str(data.get("start", "")), zone),
        ends_at=local_instant(str(data.get("end", "")), zone),
    )


def change_block(block: WorkBlock, data: dict, zone: tzinfo | None = None) -> WorkBlock:
    """Перенос/изменение использует прежний ID и владельца, не меняя данные задачи."""
    if data["operation"] == "day":
        local = block.starts_at.astimezone(zone)
        source_day = date.fromisoformat(data.get("sourceDay", local.date().isoformat()))
        target = local.date() + (date.fromisoformat(data["day"]) - source_day)
        text = target.strftime("%d.%m.%Y") + local.strftime(" %H:%M")
        start = local_instant(text, zone, preferred_offset=local.utcoffset())
        start += timedelta(seconds=local.second, microseconds=local.microsecond)
        return replace(block, starts_at=start, ends_at=start + block.duration)
    if data["operation"] in ("move", "resize"):
        minutes = data["minutes"]
        if type(minutes) is not int or minutes % 15 != 0:
            raise ValueError("Шаг мышиного изменения — 15 минут")
        delta = timedelta(minutes=minutes)
        if data["operation"] == "move":
            return replace(block, starts_at=block.starts_at + delta, ends_at=block.ends_at + delta)
        return replace(block, ends_at=block.ends_at + delta)
    if data["operation"] == "fields":
        start = (
            block.starts_at
            if data["start"] == block.starts_at.astimezone(zone).strftime(DATE_FORMAT + " %z")
            else local_instant(data["start"], zone)
        )
        end = (
            block.ends_at
            if data["end"] == block.ends_at.astimezone(zone).strftime(DATE_FORMAT + " %z")
            else local_instant(data["end"], zone)
        )
        return replace(
            block,
            starts_at=start,
            ends_at=end,
            title=str(data.get("title", block.title)) if block.task_id is None else "",
        )
    raise ValueError("Неизвестное изменение рабочего блока")
