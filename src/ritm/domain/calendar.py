"""Рабочие блоки и расчёты планирования без Qt и ввода-вывода."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from ritm.domain.tasks import Task

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def utc_instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Дата блока должна содержать время и часовой пояс")
    try:
        normalized = value.astimezone(UTC)
        local = normalized.astimezone(value.tzinfo)
    except (ValueError, OverflowError) as error:
        raise ValueError("Дата блока выходит за поддерживаемый диапазон UTC") from error
    if local.replace(tzinfo=None) != value.replace(tzinfo=None):
        raise ValueError("Указанное местное время не существует из-за перевода часов")
    return normalized


def instant_us(value: datetime) -> int:
    delta = utc_instant(value) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def from_instant_us(value: int) -> datetime:
    return EPOCH + timedelta(microseconds=value)


@dataclass(frozen=True, kw_only=True)
class WorkBlock:
    task_id: UUID
    starts_at: datetime
    ends_at: datetime
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self):
        if not isinstance(self.id, UUID) or not isinstance(self.task_id, UUID):
            raise ValueError("Идентификаторы блока и задачи должны быть UUID")
        start, end = utc_instant(self.starts_at), utc_instant(self.ends_at)
        if end <= start:
            raise ValueError("Окончание рабочего блока должно быть позже начала")
        object.__setattr__(self, "starts_at", start)
        object.__setattr__(self, "ends_at", end)

    @property
    def duration(self) -> timedelta:
        return self.ends_at - self.starts_at


def overlaps(first: WorkBlock, second: WorkBlock) -> bool:
    return first.starts_at < second.ends_at and second.starts_at < first.ends_at


def planned_duration(blocks: Iterable[WorkBlock]) -> timedelta:
    """Сумма длительностей, включая каждый из пересекающихся блоков."""
    return sum((block.duration for block in blocks), timedelta())


@dataclass(frozen=True)
class BlockWarnings:
    overlapping_ids: tuple[UUID, ...]
    after_deadline: bool


def block_warnings(block: WorkBlock, task: Task, nearby: Iterable[WorkBlock]) -> BlockWarnings:
    if block.task_id != task.id:
        raise ValueError("Рабочий блок должен относиться к указанной главной задаче")
    return BlockWarnings(
        overlapping_ids=tuple(
            other.id for other in nearby if other.id != block.id and overlaps(block, other)
        ),
        after_deadline=task.deadline is not None and block.ends_at > task.deadline,
    )
