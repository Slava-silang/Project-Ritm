"""Calendar-day recurrence: no elapsed seconds or catch-up backlog."""

from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

from ritm.domain.tasks import Task


def scheduled_day(first: date, interval: int, today: date) -> date:
    if (
        type(first) is not date
        or type(today) is not date
        or type(interval) is not int
        or not 1 <= interval <= 36500
    ):
        raise ValueError("Укажите первый день и интервал от 1 до 36500 дней")
    if today < first:
        return first
    return first + timedelta(days=((today - first).days // interval) * interval)


def next_task(task: Task) -> Task:
    """Fresh identity and completion; old history, files and work remain untouched."""
    return replace(
        task,
        id=uuid4(),
        completed=False,
        deadline=None,
        subtasks=tuple(
            replace(
                child,
                id=uuid4(),
                completed=False,
                items=tuple(replace(item, id=uuid4(), completed=False) for item in child.items),
            )
            for child in task.subtasks
        ),
    )
