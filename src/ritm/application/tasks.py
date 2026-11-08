"""Атомарные изменения сохранённой задачи."""

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from ritm.domain import completion, weights
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.storage.sqlite import TaskStore


class TaskService:
    def __init__(self, store: TaskStore):
        self._store = store

    def set_completed(self, task_id: UUID, node_id: UUID, completed: bool) -> Task:
        return self._store.update_task(
            task_id,
            lambda task: completion.set_completed(
                weights.initialize_weights(task), node_id, completed
            ),
        )

    def add_subtask(self, task_id: UUID, subtask: Subtask) -> Task:
        return self._store.update_task(task_id, lambda task: completion.add_subtask(task, subtask))

    def add_item(self, task_id: UUID, parent_id: UUID, item: ChecklistItem) -> Task:
        return self._store.update_task(
            task_id,
            lambda task: completion.add_item(weights.initialize_weights(task), parent_id, item),
        )

    def remove_subtask(self, task_id: UUID, subtask_id: UUID) -> Task:
        return self._store.update_task(
            task_id, lambda task: completion.remove_subtask(task, subtask_id)
        )

    def set_weights(self, task_id: UUID, values: Mapping[UUID, Decimal]) -> Task:
        return self._store.update_task(task_id, lambda task: weights.set_weights(task, values))

    def equal_weights(self, task_id: UUID) -> Task:
        return self._store.update_task(task_id, weights.equal_weights)
