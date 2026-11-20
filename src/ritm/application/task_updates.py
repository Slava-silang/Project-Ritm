"""Ограниченные предложения правок одной задачи и публичный снимок активного списка."""

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from ritm.application.drafts import MAX_BYTES, Draft, _object, _text, _unique, parse_draft
from ritm.domain import completion, weights
from ritm.domain.tasks import Task

MAX_CONTEXT_BYTES = 256_000


def active_context(tasks: tuple[Task, ...]):
    active = tuple(task for task in tasks if not task.completed)
    if len(active) > 500:
        raise ValueError(
            "Для поиска доступно не более 500 активных задач. Используйте выбранную задачу."
        )
    result, count = [], 0
    for task in active:
        children = []
        for child in task.subtasks:
            children.append(
                {
                    "id": str(child.id),
                    "title": child.title,
                    "completed": child.completed,
                    "items": [
                        {"id": str(i.id), "title": i.title, "completed": i.completed}
                        for i in child.items
                    ],
                }
            )
            count += 1 + len(child.items)
        result.append(
            {
                "id": str(task.id),
                "title": task.title,
                "description": task.description,
                "deadline": task.deadline.isoformat() if task.deadline else None,
                "completed": task.completed,
                "subtasks": children,
            }
        )
    if count > 5000 or len(json.dumps(result, ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES:
        raise ValueError(
            "Список активных задач слишком большой для поиска. Используйте выбранную задачу."
        )
    return result


def task_nodes(task):
    return {node.id: node for child in task.subtasks for node in (child, *child.items)}


@dataclass(frozen=True)
class TaskAnswer:
    text: str
    task_id: UUID | None = None


def parse_update(
    identifier: UUID, payload: str, snapshot: tuple[Task, ...], *, preserve_completed=False
) -> Draft | TaskAnswer:
    if not isinstance(payload, str) or len(payload.encode()) > MAX_BYTES:
        raise ValueError("Ответ помощника слишком большой")
    try:
        data = json.loads(payload, object_pairs_hook=_unique)
        _object(
            data, {"task_id", "changes", "nodes", "subtasks", "weights", "answer"}, ("task_id",)
        )
        target = UUID(data["task_id"]) if isinstance(data["task_id"], str) else None
        baseline = next(
            (
                task
                for task in snapshot
                if task.id == target and (preserve_completed or not task.completed)
            ),
            None,
        )
        if data["task_id"] is not None and baseline is None:
            raise ValueError("Помощник выбрал задачу вне снимка активного списка.")
        if "answer" in data:
            _object(data, {"task_id", "answer"}, ("task_id", "answer"))
            return TaskAnswer(_text(data["answer"]), target)
        if baseline is None:
            raise ValueError("Для изменения нужно выбрать существующую активную задачу.")
        changes = data.get("changes", {})
        _object(
            changes,
            {"title", "description", "difficulty", "expected_minutes", "deadline", "completed"},
        )
        nodes = data.get("nodes", [])
        if not isinstance(nodes, list) or len(nodes) > 1000:
            raise ValueError("Допустимо до 1000 правок пунктов.")
        known, seen, completions = task_nodes(baseline), set(), set()
        for patch in nodes:
            _object(patch, {"id", "title", "completed"}, ("id",))
            if not isinstance(patch["id"], str):
                raise ValueError("Пункт должен ссылаться на UUID из снимка.")
            node_id = UUID(patch["id"])
            if node_id not in known or node_id in seen or len(patch) == 1:
                raise ValueError("Повторный, чужой или пустой пункт изменения.")
            seen.add(node_id)
            if "completed" in patch:
                if type(patch["completed"]) is not bool:
                    raise ValueError("Выполнение должно быть логическим.")
                completions.add(node_id)
            if "title" in patch:
                patch["title"] = _text(patch["title"])
        if "completed" in changes:
            if type(changes["completed"]) is not bool:
                raise ValueError("Выполнение должно быть логическим.")
            if completions:
                raise ValueError("Отметку всей задачи и её потомков нужно менять отдельно.")
        for child in baseline.subtasks:
            if child.id in completions and any(i.id in completions for i in child.items):
                raise ValueError("Отметки родителя и потомка нужно менять отдельно.")
        task = baseline
        if data.get("subtasks"):
            appended = parse_draft(identifier, json.dumps({"subtasks": data["subtasks"]}), task)
            task = appended.task
            data["subtasks"] = json.loads(appended.payload)["subtasks"]
        elif "subtasks" in data and data["subtasks"] != []:
            raise ValueError("Новые подзадачи должны быть списком.")
        details = {}
        for key in ("title", "description", "difficulty"):
            if key in changes:
                details[key] = _text(changes[key]) if key == "title" else changes[key]
        if "description" in details and (
            not isinstance(details["description"], str) or len(details["description"]) > 20000
        ):
            raise ValueError("Описание должно быть строкой до 20 000 символов.")
        if "expected_minutes" in changes:
            minutes = changes["expected_minutes"]
            if minutes is not None and (type(minutes) is not int or not 1 <= minutes <= 525600):
                raise ValueError("Время — целые минуты от 1 до 525600 либо null.")
            details["expected_duration"] = timedelta(minutes=minutes) if minutes else None
        if "deadline" in changes:
            value = changes["deadline"]
            if value is not None and not isinstance(value, str):
                raise ValueError("Срок должен содержать дату, время и часовой пояс либо null.")
            details["deadline"] = datetime.fromisoformat(value) if value is not None else None
        task = replace(task, **details)
        for patch in nodes:
            node_id = UUID(patch["id"])
            if "title" in patch:
                task = completion.rename_node(task, node_id, patch["title"])
            if "completed" in patch:
                task = completion.set_completed(task, node_id, patch["completed"])
        if "completed" in changes:
            task = completion.set_completed(task, task.id, changes["completed"])
        if data.get("weights") is not None:
            # Reuse the exact existing weight parser without adding a branch.
            requested = data["weights"]
            if not isinstance(requested, list) or len(requested) != len(task.subtasks):
                raise ValueError("Укажите вес каждой подзадачи.")
            if any(type(v) not in (str, int, float) or len(str(v)) > 16 for v in requested):
                raise ValueError("Неверный вес подзадачи.")
            task = weights.set_weights(
                task, {c.id: Decimal(str(v)) for c, v in zip(task.subtasks, requested, strict=True)}
            )
        if preserve_completed and baseline.completed:
            if changes.get("completed", True) is False or any(
                patch.get("completed", True) is False for patch in nodes
            ):
                raise ValueError(
                    "Обновление из письма должно сохранить выполненную задачу выполненной."
                )
            task = completion.set_completed(task, task.id, True)
        if task == baseline:
            raise ValueError("Нет изменений. Для ответа без правок используйте answer.")
        data["weights"] = [str(child.weight) for child in task.subtasks]
        return Draft(identifier, baseline, task, json.dumps(data, ensure_ascii=False), "update")
    except (TypeError, KeyError, OverflowError, RecursionError, InvalidOperation) as error:
        raise ValueError("Некорректное предложение изменения задачи.") from error


def update_preview(draft):
    before, after = draft.baseline, draft.task
    lines = [f"Изменения задачи «{before.title}»", "Запись произойдёт только после подтверждения."]

    def show(value):
        if value is None:
            return "не указано"
        if isinstance(value, bool):
            return "выполнено" if value else "не выполнено"
        if isinstance(value, datetime):
            return value.astimezone().strftime("%d.%m.%Y %H:%M")
        if isinstance(value, timedelta):
            return f"{value.total_seconds() / 60:g} мин"
        return str(value) or "пусто"

    for key, label in (
        ("title", "Название"),
        ("description", "Описание"),
        ("deadline", "Дедлайн"),
        ("difficulty", "Сложность"),
        ("expected_duration", "Время"),
        ("completed", "Выполнение"),
    ):
        old, new = getattr(before, key), getattr(after, key)
        if old != new:
            lines.append(f"{label}: {show(old)} → {show(new)}")
    old_nodes = task_nodes(before)
    for node_id, node in task_nodes(after).items():
        old = old_nodes.get(node_id)
        if old is None:
            lines.append(f"Добавить: {node.title}")
            continue
        for key, label in (("title", "Название пункта"), ("completed", "Выполнение пункта")):
            if getattr(old, key) != getattr(node, key):
                lines.append(
                    f"{label} «{old.title}»: {show(getattr(old, key))} → {show(getattr(node, key))}"
                )
        if hasattr(old, "weight") and old.weight != node.weight:
            lines.append(f"Вес «{old.title}»: {old.weight}% → {node.weight}%")
    return "\n".join(lines)
