"""Переходы выполнения по S03, без ввода-вывода и зависимости от Qt."""

from dataclasses import replace
from uuid import UUID

from ritm.domain.tasks import ChecklistItem, Subtask, Task


def _refresh(task: Task) -> Task:
    children = tuple(
        replace(child, completed=all(item.completed for item in child.items))
        if child.items
        else child
        for child in task.subtasks
    )
    return replace(
        task,
        subtasks=children,
        completed=all(child.completed for child in children) if children else task.completed,
    )


def _mark_branch(child: Subtask, completed: bool) -> Subtask:
    return replace(
        child,
        completed=completed,
        items=tuple(replace(item, completed=completed) for item in child.items),
    )


def set_completed(task: Task, node_id: UUID, completed: bool) -> Task:
    """Изменить ветку целиком, затем пересчитать предков. Исходное дерево неизменно."""
    if type(completed) is not bool:
        raise ValueError("Отметка выполнения должна быть логической")
    if node_id == task.id:
        return replace(
            task,
            completed=completed,
            subtasks=tuple(_mark_branch(child, completed) for child in task.subtasks),
        )
    children = []
    found = False
    for child in task.subtasks:
        if child.id == node_id:
            children.append(_mark_branch(child, completed))
            found = True
        else:
            items = tuple(
                replace(item, completed=completed) if item.id == node_id else item
                for item in child.items
            )
            found = found or any(item.id == node_id for item in child.items)
            children.append(replace(child, items=items))
    if not found:
        raise KeyError(node_id)
    return _refresh(replace(task, subtasks=tuple(children)))


def add_subtask(task: Task, subtask: Subtask) -> Task:
    """Добавить первый уровень; перераспределение весов будет реализовано в T07."""
    return _refresh(replace(task, subtasks=(*task.subtasks, subtask)))


def add_item(task: Task, parent_id: UUID, item: ChecklistItem) -> Task:
    """Добавить второй уровень и пересчитать выполнение его предков."""
    if not any(child.id == parent_id for child in task.subtasks):
        raise KeyError(parent_id)
    return _refresh(
        replace(
            task,
            subtasks=tuple(
                replace(child, items=(*child.items, item)) if child.id == parent_id else child
                for child in task.subtasks
            ),
        )
    )
