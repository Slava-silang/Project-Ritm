"""Версия 1 снимка дерева для корзины: без потери UUID, Decimal и времени."""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from ritm.domain.tasks import ChecklistItem, Subtask, Task


def encode_task(task: Task) -> str:
    def node(item):
        result = {"id": str(item.id), "title": item.title, "completed": item.completed}
        if isinstance(item, Subtask):
            result.update(
                weight=str(item.weight) if item.weight is not None else None,
                items=[node(child) for child in item.items],
            )
        return result

    duration = task.expected_duration
    us = (
        None
        if duration is None
        else (duration.days * 86400 + duration.seconds) * 1_000_000 + duration.microseconds
    )
    return json.dumps(
        {
            "version": 1,
            **node(task),
            "description": task.description,
            "category_id": str(task.category_id) if task.category_id else None,
            "difficulty": task.difficulty,
            "expected_us": us,
            "deadline": task.deadline.isoformat() if task.deadline else None,
            "subtasks": [node(child) for child in task.subtasks],
        },
        ensure_ascii=False,
    )


def decode_task(payload: str) -> Task:
    data = json.loads(payload)
    if data.get("version") != 1:
        raise ValueError("Неизвестный формат записи корзины")

    def identity(item):
        return {"id": UUID(item["id"]), "title": item["title"], "completed": item["completed"]}

    return Task(
        **identity(data),
        description=data["description"],
        category_id=UUID(data["category_id"]) if data["category_id"] else None,
        difficulty=data["difficulty"],
        expected_duration=timedelta(microseconds=data["expected_us"])
        if data["expected_us"] is not None
        else None,
        deadline=datetime.fromisoformat(data["deadline"]) if data["deadline"] else None,
        subtasks=tuple(
            Subtask(
                **identity(child),
                weight=Decimal(child["weight"]) if child["weight"] is not None else None,
                items=tuple(ChecklistItem(**identity(item)) for item in child["items"]),
            )
            for child in data["subtasks"]
        ),
    )
