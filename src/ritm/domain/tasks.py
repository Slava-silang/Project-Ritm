"""Неизменяемые данные задачи. Выполнение и распределение весов — T06/T07."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4


def validate_identity(title: str, identifier: UUID) -> str:
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Название обязательно")
    if not isinstance(identifier, UUID):
        raise ValueError("Идентификатор должен быть UUID")
    return title.strip()


@dataclass(frozen=True, kw_only=True)
class Category:
    title: str
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self):
        object.__setattr__(self, "title", validate_identity(self.title, self.id))


@dataclass(frozen=True, kw_only=True)
class ChecklistItem:
    title: str
    id: UUID = field(default_factory=uuid4)
    completed: bool = False

    def __post_init__(self):
        object.__setattr__(self, "title", validate_identity(self.title, self.id))
        if type(self.completed) is not bool:
            raise ValueError("Отметка выполнения должна быть логической")


@dataclass(frozen=True, kw_only=True)
class Subtask(ChecklistItem):
    items: tuple[ChecklistItem, ...] = ()
    weight: Decimal | None = None

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.items, tuple) or any(
            type(x) is not ChecklistItem for x in self.items
        ):
            raise ValueError("Второй уровень может содержать только конечные пункты")
        if self.weight is not None and (
            not isinstance(self.weight, Decimal)
            or not self.weight.is_finite()
            or not 0 <= self.weight <= 100
        ):
            raise ValueError("Вес должен быть конечным Decimal от 0 до 100")
        ids = [self.id, *(x.id for x in self.items)]
        if len(ids) != len(set(ids)):
            raise ValueError("Повторяющиеся идентификаторы в ветке")


@dataclass(frozen=True, kw_only=True)
class Task(ChecklistItem):
    description: str = ""
    category_id: UUID | None = None
    difficulty: int | None = None
    expected_duration: timedelta | None = None
    deadline: datetime | None = None
    subtasks: tuple[Subtask, ...] = ()

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.description, str):
            raise ValueError("Описание должно быть строкой")
        if self.category_id is not None and not isinstance(self.category_id, UUID):
            raise ValueError("Категория должна ссылаться на UUID")
        if self.difficulty is not None and (
            type(self.difficulty) is not int or not 1 <= self.difficulty <= 10
        ):
            raise ValueError("Сложность должна быть целым числом от 1 до 10")
        if self.expected_duration is not None and (
            not isinstance(self.expected_duration, timedelta)
            or self.expected_duration <= timedelta(0)
        ):
            raise ValueError("Ожидаемая длительность должна быть положительной")
        if self.deadline is not None and (
            not isinstance(self.deadline, datetime) or self.deadline.utcoffset() is None
        ):
            raise ValueError("Дедлайн должен содержать дату, время и часовой пояс")
        if not isinstance(self.subtasks, tuple) or any(
            type(x) is not Subtask for x in self.subtasks
        ):
            raise ValueError("Первый уровень должен содержать подзадачи")
        ids = [self.id]
        for child in self.subtasks:
            ids.extend([child.id, *(item.id for item in child.items)])
        if len(ids) != len(set(ids)):
            raise ValueError("Повторяющиеся идентификаторы в задаче")
