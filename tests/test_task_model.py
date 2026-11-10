from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task


def test_lab_structure_and_stable_identity():
    category = Category(title=" Программирование ")
    point = ChecklistItem(title="Расчёт")
    task = Task(
        title="Лабораторная",
        category_id=category.id,
        difficulty=8,
        expected_duration=timedelta(hours=3),
        deadline=datetime(2026, 11, 28, tzinfo=UTC),
        subtasks=(Subtask(title="Задание 1", items=(point,), weight=Decimal("40")),),
    )
    assert replace(task, title="Отчёт").id == task.id
    assert task.subtasks[0].items[0].id == point.id
    assert category.title == "Программирование"
    with pytest.raises(FrozenInstanceError):
        task.title = "Изменение"


def test_minimal_task_has_unknown_optional_values():
    task = Task(title=" Работа ")
    assert task.title == "Работа"
    assert task.deadline is task.difficulty is task.expected_duration is None
    assert Task(title="Работа").id != task.id


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "  "},
        {"difficulty": 0},
        {"difficulty": 11},
        {"difficulty": True},
        {"difficulty": 1.5},
        {"expected_duration": timedelta(0)},
        {"expected_duration": timedelta(minutes=-1)},
        {"deadline": datetime(2026, 11, 28)},
        {"category_id": "not-a-uuid"},
        {"id": "bad"},
        {"description": None},
        {"subtasks": [Subtask(title="пункт")]},
        {"subtasks": (ChecklistItem(title="не первый уровень"),)},
    ],
)
def test_reject_invalid_main_task(fields):
    with pytest.raises(ValueError):
        Task(**({"title": "Работа"} | fields))


def test_duplicate_ids_across_branches_are_rejected():
    identifier = uuid4()
    with pytest.raises(ValueError):
        Task(
            title="Работа",
            subtasks=(
                Subtask(title="Один", items=(ChecklistItem(title="А", id=identifier),)),
                Subtask(title="Два", items=(ChecklistItem(title="Б", id=identifier),)),
            ),
        )


def test_second_level_cannot_have_children_or_deadline():
    with pytest.raises(ValueError):
        Subtask(title="Первый", items=(Subtask(title="Вложенный первый"),))
    with pytest.raises(TypeError):
        ChecklistItem(title="Второй", deadline=datetime.now(UTC))


@pytest.mark.parametrize(
    "weight", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), Decimal("101"), 25.0]
)
def test_reject_invalid_weight(weight):
    with pytest.raises(ValueError):
        Subtask(title="Задание", weight=weight)
