"""Локальные периоды и снимки календаря. Не обращается к Qt или базе."""

import calendar
from collections.abc import Sequence
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


def local_instant(text: str, zone: tzinfo | None = None) -> datetime:
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
        if block.task_id not in by_id:
            continue
        task = by_id[block.task_id]
        warnings = block_warnings(block, task, blocks)
        messages = []
        if warnings.overlapping_ids:
            messages.append("Пересекается с другим блоком")
        if warnings.after_deadline:
            messages.append("Выходит за дедлайн")
        events[block.id] = {
            "id": str(block.id),
            "taskId": str(task.id),
            "kind": "block",
            "title": task.title,
            "warning": " · ".join(messages),
            "duration": duration_text(block.duration),
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
                "deadlineCount": sum(x["kind"] == "deadline" for x in entries),
            }
        )
    title = (
        MONTHS[anchor.month - 1].capitalize() + f" {anchor.year}"
        if mode == "month"
        else days[0].strftime("%d.%m.%Y") + " — " + days[-1].strftime("%d.%m.%Y")
    )
    chosen = next((cell for cell in cells if cell["date"] == selected.isoformat()), cells[0])
    return {
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
    return WorkBlock(
        task_id=UUID(str(data.get("taskId", ""))),
        starts_at=local_instant(str(data.get("start", "")), zone),
        ends_at=local_instant(str(data.get("end", "")), zone),
    )
