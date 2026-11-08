"""Переходы выполнения по S03, без ввода-вывода и зависимости от Qt."""

from dataclasses import replace
from uuid import UUID

from ritm.domain import weights
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
    """Добавить первый уровень, распределить веса и пересчитать предков."""
    return _refresh(weights.add_subtask(task, subtask))


def remove_subtask(task: Task, subtask_id: UUID) -> Task:
    """Удалить ветку, перераспределить веса и пересчитать выполнение."""
    return _refresh(weights.remove_subtask(task, subtask_id))


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


def remove_node(task: Task, node_id: UUID) -> Task | None:
    if node_id == task.id:
        return None
    if any(child.id == node_id for child in task.subtasks):
        return remove_subtask(task, node_id)
    if not any(item.id == node_id for child in task.subtasks for item in child.items):
        raise KeyError(node_id)
    return _refresh(
        replace(
            task,
            subtasks=tuple(
                replace(child, items=tuple(item for item in child.items if item.id != node_id))
                for child in task.subtasks
            ),
        )
    )


def restore_node(current: Task | None, before: Task, after: Task | None, node_id: UUID) -> Task:
    if node_id == before.id:
        if current is not None:
            raise ValueError("Задача с таким ID уже существует; запись оставлена в корзине")
        return before
    if current is None:
        raise ValueError("Сначала восстановите главную задачу из корзины")
    for position, child in enumerate(before.subtasks):
        if child.id == node_id:
            result = add_subtask(current, child)
            children = list(result.subtasks)
            children.insert(min(position, len(children) - 1), children.pop())
            result = replace(result, subtasks=tuple(children))

            # Restore exact weights when the surviving set/weights have not changed.
            def signature(task):
                return {x.id: x.weight for x in task.subtasks}

            if after is not None and signature(current) == signature(after):
                result = weights.set_weights(result, signature(weights.initialize_weights(before)))
            return _refresh(result)
        for item_position, item in enumerate(child.items):
            if item.id == node_id:
                parent = next((x for x in current.subtasks if x.id == child.id), None)
                if parent is None:
                    raise ValueError("Сначала восстановите родительскую подзадачу из корзины")
                items = list(parent.items)
                items.insert(min(item_position, len(items)), item)
                return _refresh(
                    replace(
                        current,
                        subtasks=tuple(
                            replace(x, items=tuple(items)) if x.id == parent.id else x
                            for x in current.subtasks
                        ),
                    )
                )
    raise KeyError(node_id)
