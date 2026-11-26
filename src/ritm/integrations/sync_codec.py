"""Versioned sync entities. Pure DTO codec: no network, profile, tokens or Qt."""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task
from ritm.domain.weights import set_weights

VERSION = 1
MAX_BYTES = 2 * 1024 * 1024
MAX_INTEGER = 2**63 - 1
COUNTER = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
STAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
WEIGHT = re.compile(r"(?:0|[1-9][0-9]?|100)\.[0-9]{2}\Z")


class SyncCodecError(ValueError):
    def __init__(self, kind="value"):
        self.kind = kind
        super().__init__(
            {
                "value": "Некорректные значения данных синхронизации.",
                "schema": "Неизвестные или отсутствующие поля синхронизации.",
                "version": "Версия формата синхронизации не поддерживается.",
                "json": "Некорректный JSON синхронизации.",
                "size": "Запись синхронизации превышает допустимый размер.",
            }[kind]
        )


@dataclass(frozen=True, kw_only=True)
class TaskRecord:
    task: Task = field(repr=False)
    completed_at: datetime | None = field(default=None, repr=False)


@dataclass(frozen=True, kw_only=True)
class CategoryRecord:
    category: Category = field(repr=False)


@dataclass(frozen=True, kw_only=True)
class BlockRecord:
    block: WorkBlock = field(repr=False)
    archived_task_title: str | None = field(default=None, repr=False)


@dataclass(frozen=True, kw_only=True)
class RemovalRecord:
    entity_kind: Literal["task", "node", "block", "category"]
    entity_id: UUID
    revision: int
    delete_epoch: int
    reason: Literal["user_deleted", "retention"]
    root_id: UUID | None = None


Entity = TaskRecord | CategoryRecord | BlockRecord | RemovalRecord


def _object(value, keys):
    if type(value) is not dict or value.keys() != set(keys):
        raise SyncCodecError("schema")
    return value


def _text(value, *, title=False):
    if type(value) is not str or (title and (not value or value.strip() != value)):
        raise SyncCodecError()
    if len(value) > MAX_BYTES:
        raise SyncCodecError("size")
    value.encode("utf-8")
    return value


def _uuid(value):
    if type(value) is not str:
        raise SyncCodecError()
    result = UUID(value)
    if str(result) != value:
        raise SyncCodecError()
    return result


def _counter(value, *, positive=False):
    if type(value) is not str or COUNTER.fullmatch(value) is None:
        raise SyncCodecError()
    result = int(value)
    if result > MAX_INTEGER or (positive and result == 0):
        raise SyncCodecError()
    return result


def _counter_text(value):
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise SyncCodecError()
    return str(value)


def _date(value):
    if type(value) is not str or STAMP.fullmatch(value) is None:
        raise SyncCodecError()
    return datetime.fromisoformat(value).astimezone(UTC)


def _date_text(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise SyncCodecError()
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _identity(value):
    if type(value["completed"]) is not bool:
        raise SyncCodecError()
    return {
        "id": _uuid(value["id"]),
        "title": _text(value["title"], title=True),
        "completed": value["completed"],
    }


def _task(value):
    _object(
        value,
        {
            "id",
            "title",
            "completed",
            "description",
            "category_id",
            "difficulty",
            "expected_us",
            "deadline",
            "subtasks",
            "completed_at",
        },
    )
    if type(value["subtasks"]) is not list:
        raise SyncCodecError()
    branches = []
    for branch in value["subtasks"]:
        _object(branch, {"id", "title", "completed", "weight", "items"})
        if type(branch["items"]) is not list or (
            branch["weight"] is not None
            and (type(branch["weight"]) is not str or WEIGHT.fullmatch(branch["weight"]) is None)
        ):
            raise SyncCodecError()
        items = []
        for item in branch["items"]:
            _object(item, {"id", "title", "completed"})
            items.append(ChecklistItem(**_identity(item)))
        child = Subtask(
            **_identity(branch),
            weight=Decimal(branch["weight"]) if branch["weight"] is not None else None,
            items=tuple(items),
        )
        if child.items and child.completed != all(item.completed for item in child.items):
            raise SyncCodecError()
        branches.append(child)
    task = Task(
        **_identity(value),
        description=_text(value["description"]),
        category_id=_uuid(value["category_id"]) if value["category_id"] is not None else None,
        difficulty=value["difficulty"],
        expected_duration=timedelta(microseconds=_counter(value["expected_us"], positive=True))
        if value["expected_us"] is not None
        else None,
        deadline=_date(value["deadline"]) if value["deadline"] is not None else None,
        subtasks=tuple(branches),
    )
    if any(branch.weight is None for branch in task.subtasks):
        if not all(branch.weight is None for branch in task.subtasks):
            raise SyncCodecError()
    else:
        set_weights(task, {branch.id: branch.weight for branch in task.subtasks})
    if task.subtasks and task.completed != all(child.completed for child in task.subtasks):
        raise SyncCodecError()
    completed = _date(value["completed_at"]) if value["completed_at"] is not None else None
    if completed is not None and not task.completed:
        raise SyncCodecError()
    return TaskRecord(task=task, completed_at=completed)


def _parse(value):
    _object(value, {"version", "kind", "data"})
    if type(value["version"]) is not int or value["version"] != VERSION:
        raise SyncCodecError("version")
    kind, data = value["kind"], value["data"]
    if kind == "task":
        return _task(data)
    if kind == "category":
        _object(data, {"id", "title"})
        return CategoryRecord(
            category=Category(id=_uuid(data["id"]), title=_text(data["title"], title=True))
        )
    if kind == "block":
        _object(data, {"id", "task_id", "title", "starts_at", "ends_at", "archived_task_title"})
        block = WorkBlock(
            id=_uuid(data["id"]),
            task_id=_uuid(data["task_id"]) if data["task_id"] is not None else None,
            title=_text(data["title"]),
            starts_at=_date(data["starts_at"]),
            ends_at=_date(data["ends_at"]),
        )
        if block.title != data["title"]:
            raise SyncCodecError()
        title = (
            _text(data["archived_task_title"], title=True)
            if data["archived_task_title"] is not None
            else None
        )
        if title is not None and block.task_id is None:
            raise SyncCodecError()
        return BlockRecord(block=block, archived_task_title=title)
    if kind == "removed":
        _object(data, {"entity_kind", "entity_id", "root_id", "revision", "delete_epoch", "reason"})
        entity_kind, reason = data["entity_kind"], data["reason"]
        if entity_kind not in ("task", "node", "block", "category") or reason not in (
            "user_deleted",
            "retention",
        ):
            raise SyncCodecError()
        root = _uuid(data["root_id"]) if data["root_id"] is not None else None
        identifier = _uuid(data["entity_id"])
        if (
            (entity_kind == "node") != (root is not None)
            or root == identifier
            or (reason == "retention" and entity_kind not in ("task", "block"))
        ):
            raise SyncCodecError()
        return RemovalRecord(
            entity_kind=entity_kind,
            entity_id=identifier,
            root_id=root,
            revision=_counter(data["revision"], positive=True),
            delete_epoch=_counter(data["delete_epoch"]),
            reason=reason,
        )
    raise SyncCodecError("schema")


def _weight_text(value):
    if value is None:
        return None
    if type(value) is not Decimal or not value.is_finite() or not 0 <= value <= 100:
        raise SyncCodecError()
    text = "0.00" if value == 0 else format(value, ".2f")
    if Decimal(text) != value:
        raise SyncCodecError()
    return text


def _wire(record):
    def identity(item):
        return {"id": str(item.id), "title": item.title, "completed": item.completed}

    if type(record) is TaskRecord and type(record.task) is Task:
        task, duration = record.task, record.task.expected_duration
        if len(task.subtasks) + sum(len(child.items) for child in task.subtasks) > MAX_BYTES // 32:
            raise SyncCodecError("size")
        data = {
            **identity(task),
            "description": task.description,
            "category_id": str(task.category_id) if task.category_id is not None else None,
            "difficulty": task.difficulty,
            "expected_us": _counter_text(
                (duration.days * 86400 + duration.seconds) * 1000000 + duration.microseconds
            )
            if duration is not None
            else None,
            "deadline": _date_text(task.deadline) if task.deadline is not None else None,
            "completed_at": _date_text(record.completed_at)
            if record.completed_at is not None
            else None,
            "subtasks": [
                {
                    **identity(child),
                    "weight": _weight_text(child.weight),
                    "items": [identity(item) for item in child.items],
                }
                for child in task.subtasks
            ],
        }
        kind = "task"
    elif type(record) is CategoryRecord and type(record.category) is Category:
        kind, data = "category", {"id": str(record.category.id), "title": record.category.title}
    elif type(record) is BlockRecord and type(record.block) is WorkBlock:
        block = record.block
        kind, data = (
            "block",
            {
                "id": str(block.id),
                "task_id": str(block.task_id) if block.task_id is not None else None,
                "title": block.title,
                "starts_at": _date_text(block.starts_at),
                "ends_at": _date_text(block.ends_at),
                "archived_task_title": record.archived_task_title,
            },
        )
    elif type(record) is RemovalRecord:
        if not isinstance(record.entity_id, UUID) or (
            record.root_id is not None and not isinstance(record.root_id, UUID)
        ):
            raise SyncCodecError()
        kind, data = (
            "removed",
            {
                "entity_kind": record.entity_kind,
                "entity_id": str(record.entity_id),
                "root_id": str(record.root_id) if record.root_id is not None else None,
                "revision": _counter_text(record.revision),
                "delete_epoch": _counter_text(record.delete_epoch),
                "reason": record.reason,
            },
        )
    else:
        raise SyncCodecError()
    return {"version": VERSION, "kind": kind, "data": data}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SyncCodecError("schema")
        result[key] = value
    return result


def _reject_number(_value):
    raise SyncCodecError("json")


def encode_entity(record: Entity) -> bytes:
    """Return deterministic UTF-8. Invalid states are rejected, never repaired."""
    try:
        value = _wire(record)
        _parse(value)
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(payload) > MAX_BYTES:
            raise SyncCodecError("size")
        return payload
    except SyncCodecError:
        raise
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        raise SyncCodecError() from None


def decode_entity(payload: bytes | str) -> Entity:
    """Strict envelope, duplicate/unknown field rejection and domain validation."""
    try:
        if type(payload) not in (bytes, str):
            raise SyncCodecError("json")
        if len(payload) > MAX_BYTES:
            raise SyncCodecError("size")
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        if len(raw) > MAX_BYTES:
            raise SyncCodecError("size")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        return _parse(value)
    except SyncCodecError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SyncCodecError("json") from None
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        raise SyncCodecError() from None
