"""Веса в сотых долях процента; точные расчёты без зависимости от Decimal context."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from uuid import UUID

from ritm.domain.tasks import Subtask, Task

TOTAL = 10_000


def _decimal(units: int) -> Decimal:
    return Decimal(f"{units // 100}.{units % 100:02d}")


def _allocate(shares: Sequence[Fraction]) -> tuple[Decimal, ...]:
    if not shares:
        return ()
    total = sum(shares)
    if total == 0:
        shares = [Fraction(1)] * len(shares)
        total = len(shares)
    exact = [share * TOTAL / total for share in shares]
    units = [value.numerator // value.denominator for value in exact]
    # Largest remainders win; a tie is resolved by the existing sibling order.
    order = sorted(range(len(shares)), key=lambda i: (-(exact[i] - units[i]), i))
    for index in order[: TOTAL - sum(units)]:
        units[index] += 1
    return tuple(_decimal(value) for value in units)


def _apply(task: Task, values: Sequence[Decimal]) -> Task:
    return replace(
        task,
        subtasks=tuple(
            replace(child, weight=value) for child, value in zip(task.subtasks, values, strict=True)
        ),
    )


def equal_weights(task: Task) -> Task:
    """Начальные равные веса или явный сброс ручного распределения."""
    return _apply(task, _allocate([Fraction(1)] * len(task.subtasks)))


def set_weights(task: Task, values: Mapping[UUID, Decimal]) -> Task:
    """Заменить все веса по ID; неверная сумма не исправляется молча."""
    if set(values) != {child.id for child in task.subtasks}:
        raise ValueError("Нужно указать вес каждой подзадачи первого уровня")
    ordered = tuple(values[child.id] for child in task.subtasks)
    for value in ordered:
        if (
            not isinstance(value, Decimal)
            or not value.is_finite()
            or not 0 <= value <= 100
            or (value != 0 and value < Decimal("0.01"))
            or (Fraction(value) * 100).denominator != 1
        ):
            raise ValueError("Вес должен быть Decimal от 0 до 100 с точностью до 0,01%")
    if ordered and sum(Fraction(value) for value in ordered) != 100:
        raise ValueError("Сумма весов должна равняться 100%")
    return _apply(task, ordered)


def initialize_weights(task: Task) -> Task:
    """Без весов — равное распределение; заданные веса должны быть согласованы."""
    if all(child.weight is None for child in task.subtasks):
        return equal_weights(task)
    return set_weights(task, {child.id: child.weight for child in task.subtasks})


def add_subtask(task: Task, subtask: Subtask) -> Task:
    """Новый пункт получает 1/(n+1); существующие сохраняют пропорции до округления."""
    current = initialize_weights(task)
    combined = replace(current, subtasks=(*current.subtasks, subtask))
    count = len(current.subtasks)
    shares = [Fraction(child.weight) * count for child in current.subtasks]
    return _apply(combined, _allocate([*shares, Fraction(100)]))


def remove_subtask(task: Task, subtask_id: UUID) -> Task:
    """Удалить первый уровень. Если остались только нулевые веса — распределить поровну."""
    if not any(child.id == subtask_id for child in task.subtasks):
        raise KeyError(subtask_id)
    current = initialize_weights(task)
    remaining = tuple(child for child in current.subtasks if child.id != subtask_id)
    return _apply(
        replace(current, subtasks=remaining),
        _allocate([Fraction(child.weight) for child in remaining]),
    )


def progress(task: Task) -> Decimal:
    """Вес начисляется за завершённую ветку целиком. У пустой задачи — 0 или 100%."""
    if not task.subtasks:
        return _decimal(TOTAL if task.completed else 0)
    current = initialize_weights(task)
    units = sum(int(Fraction(child.weight) * 100) for child in current.subtasks if child.completed)
    return _decimal(units)
