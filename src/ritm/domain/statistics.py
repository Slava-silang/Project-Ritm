"""Границы местной недели, прогресс задач и длительность фокуса."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from uuid import UUID

from ritm.domain.calendar import instant_us
from ritm.domain.focus import focus_by_day
from ritm.domain.tasks import Task
from ritm.domain.weights import progress


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


@dataclass(frozen=True)
class WeeklyStatistics:
    start: date
    end: date
    task_count: int
    progress: Decimal | None
    completed_count: int
    undated_count: int
    daily_focus: tuple[int, ...]
    task_focus: dict[UUID, int]
    task_ids: frozenset[UUID]


def weekly_statistics(
    tasks: Sequence[Task],
    completed_at: Mapping[UUID, datetime],
    intervals: Mapping[UUID, Sequence[tuple[int, int]]],
    anchor: date,
    zone: tzinfo | None,
) -> WeeklyStatistics:
    start = week_start(anchor)
    end = start + timedelta(days=7)
    first_us = instant_us(datetime.combine(start, time.min, tzinfo=zone).astimezone(UTC))
    end_us = instant_us(datetime.combine(end, time.min, tzinfo=zone).astimezone(UTC))
    # Allow imaginary midnights at timezone changes to be normalized by UTC conversion.
    due = {
        task.id: progress(task)
        for task in tasks
        if task.deadline and start <= task.deadline.astimezone(zone).date() < end
    }
    completed = {
        task.id
        for task in tasks
        if task.completed
        and task.id in completed_at
        and start <= completed_at[task.id].astimezone(zone).date() < end
    }
    daily: dict[date, int] = defaultdict(int)
    focus = {}
    for task in tasks:
        by_day = focus_by_day(
            (
                (max(first_us, begin), min(end_us, finish))
                for begin, finish in intervals.get(task.id, ())
            ),
            zone,
        )
        for day, value in by_day.items():
            daily[day] += value
        if by_day:
            focus[task.id] = sum(by_day.values())
    return WeeklyStatistics(
        start=start,
        end=end,
        task_count=len(due),
        progress=(sum(due.values()) / len(due)).quantize(Decimal("0.01")) if due else None,
        completed_count=len(completed),
        undated_count=sum(task.completed and task.id not in completed_at for task in tasks),
        daily_focus=tuple(daily[start + timedelta(days=i)] for i in range(7)),
        task_focus=focus,
        task_ids=frozenset(due.keys() | completed | focus.keys()),
    )
