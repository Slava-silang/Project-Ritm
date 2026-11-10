"""Атомарные изменения сохранённой задачи."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from ritm.domain import completion, weights
from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task
from ritm.storage.sqlite import TaskStore

_UNCHANGED = object()


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

    def create_task(self, task: Task, category: Category | None = None) -> Task:
        task = weights.initialize_weights(task)
        self._store.create_task(task, category)
        return task

    def edit_details(
        self,
        task_id: UUID,
        *,
        title: str,
        description: str,
        difficulty: int | None,
        expected_duration: timedelta | None,
        deadline: datetime | None,
        category_id: UUID | None | object = _UNCHANGED,
        category: Category | None = None,
    ) -> Task:
        return self._store.update_task(
            task_id,
            lambda task: replace(
                task,
                title=title,
                description=description,
                difficulty=difficulty,
                expected_duration=expected_duration,
                deadline=deadline,
                **({"category_id": category_id} if category_id is not _UNCHANGED else {}),
            ),
            category=category,
        )

    def rename_node(self, task_id: UUID, node_id: UUID, title: str) -> Task:
        def rename(task):
            if task.id == node_id:
                return replace(task, title=title)
            found = any(
                child.id == node_id or any(item.id == node_id for item in child.items)
                for child in task.subtasks
            )
            if not found:
                raise KeyError(node_id)
            return replace(
                task,
                subtasks=tuple(
                    replace(child, title=title)
                    if child.id == node_id
                    else replace(
                        child,
                        items=tuple(
                            replace(item, title=title) if item.id == node_id else item
                            for item in child.items
                        ),
                    )
                    for child in task.subtasks
                ),
            )

        return self._store.update_task(task_id, rename)

    def delete_node(self, task_id: UUID, node_id: UUID, now: datetime | None = None) -> str:
        return self._store.archive_node(
            task_id,
            node_id,
            lambda task: completion.remove_node(task, node_id),
            now or datetime.now(UTC),
        )

    def restore(self, identifier: UUID) -> Task:
        return self._store.restore_entry(identifier, completion.restore_node)
