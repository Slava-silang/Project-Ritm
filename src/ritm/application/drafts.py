"""Ограниченный формат предложений. Не принимает команды, ссылки или чужие ID."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid5

from ritm.domain import completion, weights
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.storage.codec import encode_task

MAX_BYTES = 128_000


def _text(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Нужно непустое название допустимой длины")
    return value.strip()


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError("Ответ содержит недопустимые или отсутствующие поля")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Ответ содержит повторяющиеся поля")
        result[key] = value
    return result


@dataclass(frozen=True)
class Draft:
    id: UUID
    baseline: Task | None
    task: Task
    payload: str

    @property
    def digest(self):
        return hashlib.sha256(encode_task(self.task).encode()).hexdigest()


def parse_draft(identifier: UUID, payload: str, baseline: Task | None = None) -> Draft:
    """Новые UUID выводятся из ID запроса; повторный разбор сохраняет идентичность."""
    if not isinstance(payload, str) or len(payload.encode()) > MAX_BYTES:
        raise ValueError("Ответ помощника слишком большой")
    try:
        data = json.loads(payload, object_pairs_hook=_unique)
        fields = {"subtasks", "weights"}
        if baseline is None:
            fields |= {"title", "description", "difficulty", "expected_minutes", "deadline"}
        _object(data, fields, ("title",) if baseline is None else ("subtasks",))
        children = data.get("subtasks", [])
        if not isinstance(children, list) or len(children) > 100:
            raise ValueError("Допустимо не более 100 новых подзадач")
        if baseline is not None and not children:
            raise ValueError("Предложение разбиения должно содержать подзадачи")
        added = []
        count = 0
        for i, child in enumerate(children):
            _object(child, {"title", "items"}, ("title",))
            items = child.get("items", [])
            if not isinstance(items, list) or len(items) > 100:
                raise ValueError("Допустимо не более 100 пунктов второго уровня в ветке")
            count += 1 + len(items)
            if count > 1000:
                raise ValueError("Предложение содержит слишком много пунктов")
            added.append(
                Subtask(
                    id=uuid5(identifier, f"subtask/{i}"),
                    title=_text(child["title"]),
                    items=tuple(
                        ChecklistItem(
                            id=uuid5(identifier, f"subtask/{i}/item/{j}"), title=_text(item)
                        )
                        for j, item in enumerate(items)
                    ),
                )
            )
        if baseline is None:
            minutes = data.get("expected_minutes")
            if minutes is not None and (type(minutes) is not int or not 1 <= minutes <= 525600):
                raise ValueError("Ожидаемое время — целые минуты от 1 до 525600")
            description = data.get("description", "")
            if not isinstance(description, str) or len(description) > 20000:
                raise ValueError("Описание слишком большое или имеет неверный формат")
            deadline = data.get("deadline")
            if deadline is not None and not isinstance(deadline, str):
                raise ValueError("Неверный формат дедлайна")
            task = Task(
                id=uuid5(identifier, "task"),
                title=_text(data["title"]),
                description=description,
                difficulty=data.get("difficulty"),
                expected_duration=timedelta(minutes=minutes) if minutes else None,
                deadline=datetime.fromisoformat(deadline) if deadline else None,
                subtasks=tuple(added),
            )
            task = weights.initialize_weights(task)
        else:
            task = baseline
            for child in added:
                task = completion.add_subtask(task, child)
        requested = data.get("weights")
        if requested is not None:
            if not isinstance(requested, list) or len(requested) != len(task.subtasks):
                raise ValueError("Укажите вес каждой подзадачи первого уровня")
            if any(
                not isinstance(value, (str, int, float))
                or isinstance(value, bool)
                or len(str(value)) > 16
                for value in requested
            ):
                raise ValueError("Веса должны быть числами с точностью 0,01%")
            task = weights.set_weights(
                task,
                {
                    child.id: Decimal(str(value))
                    for child, value in zip(task.subtasks, requested, strict=True)
                },
            )
        # Нормализованный редактируемый ввод и полный предпросмотр имеют одни веса.
        data["weights"] = [str(child.weight) for child in task.subtasks]
        normalized = json.dumps(data, ensure_ascii=False)
        return Draft(identifier, baseline, task, normalized)
    except (KeyError, TypeError, InvalidOperation, OverflowError, RecursionError) as error:
        raise ValueError("Некорректный ответ помощника. Проверьте структуру и значения.") from error
