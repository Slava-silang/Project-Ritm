"""Сценарии календаря; предупреждения не запрещают сохранение."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from ritm.domain.calendar import BlockWarnings, WorkBlock, block_warnings, planned_duration
from ritm.storage.sqlite import TaskStore


@dataclass(frozen=True)
class BlockChange:
    block: WorkBlock
    warnings: BlockWarnings


class CalendarService:
    def __init__(self, store: TaskStore):
        self._store = store

    def create_block(self, block: WorkBlock) -> BlockChange:
        task, nearby = self._store.create_work_block(block)
        return BlockChange(block, block_warnings(block, task, nearby))

    def update_block(self, block: WorkBlock) -> BlockChange:
        task, nearby = self._store.update_work_block(block)
        return BlockChange(block, block_warnings(block, task, nearby))

    def planned_time(self, task_id: UUID) -> timedelta:
        return planned_duration(self._store.list_work_blocks(task_id=task_id))

    def warnings(self, identifier: UUID) -> BlockWarnings:
        task, block, nearby = self._store.work_block_context(identifier)
        return block_warnings(block, task, nearby)
