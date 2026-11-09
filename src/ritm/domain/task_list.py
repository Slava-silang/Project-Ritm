"""Поиск, группы сроков и сортировка с явно переданными часами."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from ritm.domain.tasks import Task


@dataclass(frozen=True)
class ListedTask:
    task: Task
    group: str
    overdue: bool


def is_overdue(task: Task, now: datetime) -> bool:
    return bool(
        not task.completed
        and task.deadline is not None
        and task.deadline.astimezone(UTC) < now.astimezone(UTC)
    )


def query_tasks(
    tasks: Sequence[Task],
    *,
    now: datetime,
    search: str = "",
    state: str = "active",
    category_id: UUID | None = None,
    uncategorized: bool = False,
) -> tuple[ListedTask, ...]:
    if now.utcoffset() is None:
        raise ValueError("Часы должны содержать часовой пояс")
    if state not in ("active", "completed", "all"):
        raise ValueError("Неизвестный фильтр выполнения")
    query = search.strip().casefold()
    rows = []
    for task in tasks:
        if state == "active" and task.completed or state == "completed" and not task.completed:
            continue
        if uncategorized and task.category_id is not None:
            continue
        if category_id is not None and task.category_id != category_id:
            continue
        if query:
            text = "\n".join(
                (
                    task.title,
                    task.description,
                    *(child.title for child in task.subtasks),
                    *(item.title for child in task.subtasks for item in child.items),
                )
            )
            if query not in text.casefold():
                continue
        overdue = is_overdue(task, now)
        if task.completed:
            group = "Выполненные"
        elif overdue:
            group = "Просроченные"
        elif task.deadline:
            group = "Предстоящие"
        else:
            group = "Без дедлайна"
        rows.append(ListedTask(task, group, overdue))
    ranks = {"Просроченные": 0, "Предстоящие": 1, "Без дедлайна": 2, "Выполненные": 3}
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                ranks[row.group],
                row.task.deadline.astimezone(UTC)
                if row.task.deadline
                else datetime.max.replace(tzinfo=UTC),
            ),
        )
    )
