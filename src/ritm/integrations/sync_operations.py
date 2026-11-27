"""Pure operation/receipt boundary. No HTTP, identity verification or replay database."""

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date
from uuid import UUID, uuid5

from ritm.domain.recurrence import scheduled_day
from ritm.integrations._sync_wire import (
    _dump,
    _guard,
    _id,
    _id_text,
    _keys,
    _load,
    _number,
    _number_text,
)
from ritm.integrations.sync_codec import (
    BlockRecord,
    CalendarRecord,
    CategoryRecord,
    OccurrenceRecord,
    RegularRecord,
    SyncCodecError,
    TaskRecord,
    decode_entity,
    encode_entity,
)

PAYLOADS = {
    "calendar_settings": CalendarRecord,
    "occurrence": OccurrenceRecord,
    "task": TaskRecord,
    "category": CategoryRecord,
    "block": BlockRecord,
    "regular": RegularRecord,
}
ERRORS = {
    "stale_epoch",
    "stale_revision",
    "deleted",
    "not_found",
    "already_exists",
    "invalid_transition",
    "quota",
    "generation_changed",
    "operation_conflict",
}


@dataclass(frozen=True, kw_only=True)
class Operation:
    op_id: UUID
    device_id: UUID
    server_id: UUID
    target_kind: str
    target_id: UUID
    base_revision: int
    delete_epoch: int
    action: str
    payload: (
        TaskRecord
        | CategoryRecord
        | BlockRecord
        | RegularRecord
        | OccurrenceRecord
        | CalendarRecord
        | None
    ) = field(repr=False)


@dataclass(frozen=True, kw_only=True)
class Receipt:
    op_id: UUID
    device_id: UUID
    server_id: UUID
    request_hash: str
    status: str
    revision: int
    delete_epoch: int
    error: str | None


def target_identity(record):
    if type(record) is CalendarRecord:
        return "calendar_settings", record.id
    if type(record) is OccurrenceRecord:
        return "occurrence", record.id
    if type(record) is TaskRecord:
        return "task", record.task.id
    if type(record) is CategoryRecord:
        return "category", record.category.id
    if type(record) is BlockRecord:
        return "block", record.block.id
    if type(record) is RegularRecord:
        return "regular", record.id
    raise SyncCodecError()


def payload_matches_target(action, kind, identifier, payload):
    if action == "reschedule_regular":
        return kind == "regular" and type(payload) is RegularRecord and payload.id != identifier
    return target_identity(payload) == (kind, identifier)


def occurrence_id(rule_id: UUID, day: date) -> UUID:
    if type(rule_id) is not UUID or type(day) is not date:
        raise SyncCodecError()
    return uuid5(rule_id, "occurrence:" + day.isoformat())


def _validate(operation):
    if operation.target_kind not in PAYLOADS or operation.action not in (
        "create",
        "put",
        "restore",
        "delete",
        "prune",
        "stop_regular",
        "rollover",
        "reschedule_regular",
    ):
        raise SyncCodecError()
    if operation.target_kind == "calendar_settings" and operation.action not in ("create", "put"):
        raise SyncCodecError()
    if operation.action == "create":
        if operation.base_revision != 0 or operation.delete_epoch != 0:
            raise SyncCodecError()
    elif operation.base_revision == 0:
        raise SyncCodecError()
    if operation.action == "prune" and operation.target_kind not in (
        "task",
        "regular",
        "occurrence",
    ):
        raise SyncCodecError()
    if (
        operation.action in ("rollover", "stop_regular", "reschedule_regular")
        and operation.target_kind != "regular"
    ):
        raise SyncCodecError()
    if operation.action in ("delete", "stop_regular"):
        if operation.payload is not None:
            raise SyncCodecError("schema")
    else:
        if type(operation.payload) is not PAYLOADS[operation.target_kind]:
            raise SyncCodecError()
        if not payload_matches_target(
            operation.action, operation.target_kind, operation.target_id, operation.payload
        ):
            raise SyncCodecError()
    if operation.action == "rollover":
        record = operation.payload
        task = record.current.task
        if (
            task.id != occurrence_id(record.id, record.current_day)
            or task.completed
            or record.current.completed_at is not None
            or any(
                child.completed or any(item.completed for item in child.items)
                for child in task.subtasks
            )
        ):
            raise SyncCodecError()


@_guard
def decode_operation(raw: bytes | str) -> Operation:
    data = _load(raw, "operation")
    _keys(
        data,
        {
            "op_id",
            "device_id",
            "server_id",
            "target_kind",
            "target_id",
            "base_revision",
            "delete_epoch",
            "action",
            "payload",
        },
    )
    operation = Operation(
        op_id=_id(data["op_id"]),
        device_id=_id(data["device_id"]),
        server_id=_id(data["server_id"]),
        target_kind=data["target_kind"],
        target_id=_id(data["target_id"]),
        base_revision=_number(data["base_revision"]),
        delete_epoch=_number(data["delete_epoch"]),
        action=data["action"],
        payload=decode_entity(_dump(data["payload"])) if data["payload"] is not None else None,
    )
    _validate(operation)
    return operation


@_guard
def encode_operation(operation: Operation) -> bytes:
    if type(operation) is not Operation:
        raise SyncCodecError()
    raw = _dump(
        {
            "version": 1,
            "kind": "operation",
            "data": {
                "op_id": _id_text(operation.op_id),
                "device_id": _id_text(operation.device_id),
                "server_id": _id_text(operation.server_id),
                "target_kind": operation.target_kind,
                "target_id": _id_text(operation.target_id),
                "base_revision": _number_text(operation.base_revision),
                "delete_epoch": _number_text(operation.delete_epoch),
                "action": operation.action,
                "payload": json.loads(encode_entity(operation.payload))
                if operation.payload is not None
                else None,
            },
        }
    )
    decode_operation(raw)
    return raw


@_guard
def decode_receipt(raw: bytes | str) -> Receipt:
    data = _load(raw, "receipt")
    _keys(
        data,
        {
            "op_id",
            "device_id",
            "server_id",
            "request_hash",
            "status",
            "revision",
            "delete_epoch",
            "error",
        },
    )
    if (
        type(data["request_hash"]) is not str
        or re.fullmatch("[0-9a-f]{64}", data["request_hash"]) is None
    ):
        raise SyncCodecError()
    if data["status"] == "applied":
        if data["error"] is not None or _number(data["revision"]) == 0:
            raise SyncCodecError()
    elif data["status"] == "rejected":
        if data["error"] not in ERRORS:
            raise SyncCodecError()
    else:
        raise SyncCodecError()
    return Receipt(
        op_id=_id(data["op_id"]),
        device_id=_id(data["device_id"]),
        server_id=_id(data["server_id"]),
        request_hash=data["request_hash"],
        status=data["status"],
        revision=_number(data["revision"]),
        delete_epoch=_number(data["delete_epoch"]),
        error=data["error"],
    )


@_guard
def encode_receipt(receipt: Receipt) -> bytes:
    if type(receipt) is not Receipt:
        raise SyncCodecError()
    raw = _dump(
        {
            "version": 1,
            "kind": "receipt",
            "data": {
                "op_id": _id_text(receipt.op_id),
                "device_id": _id_text(receipt.device_id),
                "server_id": _id_text(receipt.server_id),
                "request_hash": receipt.request_hash,
                "status": receipt.status,
                "revision": _number_text(receipt.revision),
                "delete_epoch": _number_text(receipt.delete_epoch),
                "error": receipt.error,
            },
        }
    )
    decode_receipt(raw)
    return raw


def operation_hash(operation: Operation) -> str:
    return hashlib.sha256(encode_operation(operation)).hexdigest()


def verify_receipt(operation: Operation, receipt: Receipt) -> None:
    """Verify association and success counters; not ownership or actual application."""
    encode_receipt(receipt)
    digest = operation_hash(operation)
    if (receipt.op_id, receipt.device_id, receipt.server_id, receipt.request_hash) != (
        operation.op_id,
        operation.device_id,
        operation.server_id,
        digest,
    ):
        raise SyncCodecError()
    if receipt.status == "applied":
        increment = operation.action in (
            "delete",
            "restore",
            "stop_regular",
            "prune",
            "reschedule_regular",
        )
        if (
            receipt.revision <= operation.base_revision
            or receipt.delete_epoch != operation.delete_epoch + increment
        ):
            raise SyncCodecError()


def rollover_record(previous: RegularRecord, day: date) -> RegularRecord:
    """Pure deterministic candidate; server CAS/day/epoch checks are still required."""
    previous = decode_entity(encode_entity(previous))
    if type(previous) is not RegularRecord or type(day) is not date:
        raise SyncCodecError()
    if (
        day <= previous.current_day
        or scheduled_day(previous.first_day, previous.interval_days, day) != day
    ):
        raise SyncCodecError()
    root = occurrence_id(previous.id, day)
    task = replace(
        previous.current.task,
        id=root,
        completed=False,
        deadline=None,
        subtasks=tuple(
            replace(
                child,
                id=uuid5(root, f"child:{i}"),
                completed=False,
                items=tuple(
                    replace(item, id=uuid5(root, f"child:{i}:item:{j}"), completed=False)
                    for j, item in enumerate(child.items)
                ),
            )
            for i, child in enumerate(previous.current.task.subtasks)
        ),
    )
    result = replace(previous, current_day=day, current=TaskRecord(task=task))
    encode_entity(result)
    return result
