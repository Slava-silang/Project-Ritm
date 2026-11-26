"""Pure operation/receipt boundary. No HTTP, identity verification or replay database."""

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date
from functools import wraps
from uuid import UUID, uuid5

from ritm.domain.recurrence import scheduled_day
from ritm.integrations.sync_codec import (
    MAX_BYTES,
    MAX_INTEGER,
    BlockRecord,
    CategoryRecord,
    RegularRecord,
    SyncCodecError,
    TaskRecord,
    decode_entity,
    encode_entity,
)

PAYLOADS = {
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
    payload: TaskRecord | CategoryRecord | BlockRecord | RegularRecord | None = field(repr=False)


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


def _keys(value, keys):
    if type(value) is not dict or value.keys() != set(keys):
        raise SyncCodecError("schema")


def _id(value):
    if type(value) is not str:
        raise SyncCodecError()
    result = UUID(value)
    if str(result) != value:
        raise SyncCodecError()
    return result


def _id_text(value):
    if type(value) is not UUID:
        raise SyncCodecError()
    return str(value)


def _number(value):
    if type(value) is not str or re.fullmatch(r"0|[1-9][0-9]{0,18}", value) is None:
        raise SyncCodecError()
    result = int(value)
    if result > MAX_INTEGER:
        raise SyncCodecError()
    return result


def _number_text(value):
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise SyncCodecError()
    return str(value)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SyncCodecError("schema")
        result[key] = value
    return result


def _reject(_value):
    raise SyncCodecError("json")


def _dump(value):
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise SyncCodecError("size")
    return raw


def _load(raw, kind):
    if type(raw) not in (str, bytes):
        raise SyncCodecError("json")
    if len(raw) > MAX_BYTES:
        raise SyncCodecError("size")
    raw = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(raw) > MAX_BYTES:
        raise SyncCodecError("size")
    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=_pairs, parse_float=_reject, parse_constant=_reject
    )
    _keys(value, {"version", "kind", "data"})
    if type(value["version"]) is not int or value["version"] != 1:
        raise SyncCodecError("version")
    if value["kind"] != kind:
        raise SyncCodecError("schema")
    return value["data"]


def _guard(function):
    @wraps(function)
    def boundary(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SyncCodecError:
            raise
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError, RecursionError):
            raise SyncCodecError() from None

    return boundary


def target_identity(record):
    if type(record) is TaskRecord:
        return "task", record.task.id
    if type(record) is CategoryRecord:
        return "category", record.category.id
    if type(record) is BlockRecord:
        return "block", record.block.id
    if type(record) is RegularRecord:
        return "regular", record.id
    raise SyncCodecError()


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
    ):
        raise SyncCodecError()
    if operation.action == "create":
        if operation.base_revision != 0 or operation.delete_epoch != 0:
            raise SyncCodecError()
    elif operation.base_revision == 0:
        raise SyncCodecError()
    if operation.action == "prune" and operation.target_kind not in ("task", "regular"):
        raise SyncCodecError()
    if operation.action in ("rollover", "stop_regular") and operation.target_kind != "regular":
        raise SyncCodecError()
    if operation.action in ("delete", "stop_regular"):
        if operation.payload is not None:
            raise SyncCodecError("schema")
    else:
        if type(operation.payload) is not PAYLOADS[operation.target_kind]:
            raise SyncCodecError()
        if target_identity(operation.payload) != (operation.target_kind, operation.target_id):
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
        increment = operation.action in ("delete", "restore", "stop_regular", "prune")
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
