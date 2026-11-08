"""Атомарные изменения сохранённой задачи."""

from uuid import UUID

from ritm.domain import completion
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.storage.sqlite import TaskStore


class TaskService:
    def __init__(self, store: TaskStore):
        self._store = store

    def set_completed(self, task_id: UUID, node_id: UUID, completed: bool) -> Task:
        return self._store.update_task(
            task_id, lambda task: completion.set_completed(task, node_id, completed)
        )

    def add_subtask(self, task_id: UUID, subtask: Subtask) -> Task:
        return self._store.update_task(task_id, lambda task: completion.add_subtask(task, subtask))

    def add_item(self, task_id: UUID, parent_id: UUID, item: ChecklistItem) -> Task:
        return self._store.update_task(
            task_id, lambda task: completion.add_item(task, parent_id, item)
        )
