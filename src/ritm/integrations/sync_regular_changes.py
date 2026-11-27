"""Shared local regular-change DTO validation, without SQLite or transport."""

from dataclasses import dataclass, field, replace
from datetime import date
from uuid import UUID

from ritm.integrations.sync_codec import (
    OccurrenceRecord,
    RegularRecord,
    TaskRecord,
    decode_entity,
    encode_entity,
)


@dataclass(frozen=True, kw_only=True)
class RegularChange:
    sequence: int
    id: UUID
    after_intent_id: UUID | None
    kind: str
    rule_id: UUID
    root_id: UUID
    before: RegularRecord | OccurrenceRecord | TaskRecord | None = field(repr=False)
    after: RegularRecord | OccurrenceRecord | TaskRecord | None = field(repr=False)
    day: date | None = None


def validate_regular_change(change):
    before, after = change.before, change.after
    if change.kind in ("history", "detach", "root_save", "root_restore", "root_delete"):
        expected = {
            "history": (OccurrenceRecord, OccurrenceRecord),
            "detach": (OccurrenceRecord, TaskRecord),
            "root_save": (TaskRecord, TaskRecord),
            "root_restore": (type(None), (OccurrenceRecord, TaskRecord)),
            "root_delete": (type(None), type(None)),
        }[change.kind]
        valid = isinstance(before, expected[0]) and isinstance(after, expected[1])
        valid = valid and type(change.day) is date
        if not valid:
            raise ValueError("Повреждён переход истории повторов")
        for record in (before, after):
            if record is None:
                continue
            root = record.current.task.id if type(record) is OccurrenceRecord else record.task.id
            valid = valid and root == change.root_id
            if type(record) is OccurrenceRecord:
                valid = valid and record.rule_id == change.rule_id and record.day == change.day
        if not valid:
            raise ValueError("Повреждён переход истории повторов")
        return
    if change.day is not None:
        raise ValueError("Неожиданный день перехода регулярной задачи")
    if change.kind == "delete":
        valid = before is None and after is None
    elif change.kind == "stop":
        valid = type(before) is RegularRecord and type(after) is TaskRecord
        valid = valid and before.id == change.rule_id and after.task.id == change.root_id
        valid = valid and before.current.task.id == change.root_id
    elif change.kind == "rollover":
        valid = type(before) is RegularRecord and type(after) is RegularRecord
        valid = valid and before.id == after.id == change.rule_id
        valid = valid and before.current_day < after.current_day
        valid = valid and before.current.task.id != after.current.task.id
        valid = valid and after.current.task.id == change.root_id
    elif change.kind in ("save", "restore"):
        valid = type(after) is RegularRecord and type(before) in (
            type(None),
            TaskRecord,
            RegularRecord,
            OccurrenceRecord,
        )
        if change.kind == "restore":
            valid = valid and type(before) is RegularRecord
        valid = valid and after.id == change.rule_id and after.current.task.id == change.root_id
        if valid and before is not None:
            root = (
                before.current.task.id
                if type(before) in (RegularRecord, OccurrenceRecord)
                else before.task.id
            )
            valid = root == change.root_id
    else:
        valid = False
    if not valid:
        raise ValueError("Повреждён переход регулярной задачи")


def checked_uuid(value):
    if type(value) is not UUID:
        raise ValueError("Некорректный контекст подготовки истории")


def checked_counter(value, *, positive=False):
    if type(value) is not int or not (int(positive) <= value <= 2**63 - 1):
        raise ValueError("Некорректное основание истории")


def checked_entity(entity):
    if decode_entity(encode_entity(entity)) != entity:
        raise ValueError("Некорректное содержимое истории")


def task_node_ids(task):
    return {
        task.id,
        *(x.id for x in task.subtasks),
        *(y.id for x in task.subtasks for y in x.items),
    }


def validate_regular_journal(changes, *, max_items=10000, max_bytes=32 * 1024 * 1024):
    if type(changes) is not tuple or len(changes) > max_items:
        raise ValueError("Превышены границы журнала истории")
    seen, previous, size = set(), 0, 0
    for change in changes:
        if type(change) is not RegularChange:
            raise ValueError("Некорректный журнал истории")
        checked_uuid(change.id)
        checked_uuid(change.rule_id)
        checked_uuid(change.root_id)
        if change.after_intent_id is not None:
            checked_uuid(change.after_intent_id)
        checked_counter(change.sequence, positive=True)
        if change.id in seen or change.sequence <= previous:
            raise ValueError("Нарушен порядок журнала истории")
        seen.add(change.id)
        previous = change.sequence
        for record in (change.before, change.after):
            if record is not None:
                checked_entity(record)
                size += len(encode_entity(record))
        if size > max_bytes:
            raise ValueError("Журнал истории слишком велик")
        validate_regular_change(change)


def mapped_task_record(record, identities):
    """Map task node identities without forcing an ordinary task into a regular DTO."""
    task = record.task
    if not task_node_ids(task) <= identities.keys():
        return None
    children = tuple(
        replace(
            child,
            id=identities[child.id],
            items=tuple(replace(item, id=identities[item.id]) for item in child.items),
        )
        for child in task.subtasks
    )
    result = replace(record, task=replace(task, id=identities[task.id], subtasks=children))
    checked_entity(result)
    return result
