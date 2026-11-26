"""Operation identity, strict request/receipt schema and regular candidates."""

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ritm.domain.completion import remove_subtask
from ritm.integrations.sync_codec import SyncCodecError, decode_entity
from ritm.integrations.sync_operations import (
    Operation,
    Receipt,
    decode_operation,
    decode_receipt,
    encode_operation,
    encode_receipt,
    operation_hash,
    rollover_record,
    verify_receipt,
)

ENTITIES = Path(__file__).parent / "fixtures" / "sync" / "v1"
FIXTURES = Path(__file__).parent / "fixtures" / "sync_operations" / "v1"


def entity(name="task"):
    return decode_entity((ENTITIES / f"{name}.json").read_bytes())


def operation(name="task", action="put"):
    payload = entity(name)
    target = (
        payload.id
        if name == "regular"
        else payload.task.id
        if name == "task"
        else payload.category.id
        if name == "category"
        else payload.block.id
    )
    return Operation(
        op_id=UUID(int=100),
        device_id=UUID(int=101),
        server_id=UUID(int=102),
        target_kind="block" if name == "event" else name,
        target_id=target,
        base_revision=4,
        delete_epoch=2,
        action=action,
        payload=payload,
    )


def receipt(op, **changes):
    return Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=op.server_id,
        request_hash=operation_hash(op),
        status="applied",
        revision=5,
        delete_epoch=op.delete_epoch + (op.action in ("delete", "restore", "stop_regular")),
        error=None,
        **changes,
    )


@pytest.mark.parametrize("name", ["task", "category", "event", "regular"])
@pytest.mark.parametrize("action", ["create", "put", "restore"])
def test_typed_requests_roundtrip_target_and_receipt(name, action):
    op = operation(name, action)
    if action == "create":
        op = replace(op, base_revision=0, delete_epoch=0)
    assert decode_operation(encode_operation(op)) == op
    ack = receipt(op)
    assert decode_receipt(encode_receipt(ack)) == ack
    verify_receipt(op, ack)
    assert operation_hash(op) == operation_hash(
        decode_operation(json.dumps(json.loads(encode_operation(op)), indent=2))
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("op_id", uuid4()),
        ("device_id", uuid4()),
        ("server_id", uuid4()),
        ("request_hash", "a" * 64),
        ("revision", 4),
        ("delete_epoch", 3),
    ],
)
def test_ack_must_match_operation_and_counter_transition(field, value):
    op = operation()
    with pytest.raises(SyncCodecError):
        verify_receipt(op, replace(receipt(op), **{field: value}))


def test_same_id_changed_payload_has_different_hash_and_old_receipt_rejected():
    op = operation()
    changed = replace(
        op, payload=replace(op.payload, task=replace(op.payload.task, title="Другой текст"))
    )
    assert operation_hash(changed) != operation_hash(op)
    with pytest.raises(SyncCodecError):
        verify_receipt(changed, receipt(op))
    assert operation_hash(replace(op, op_id=uuid4())) != operation_hash(op)


@pytest.mark.parametrize("kind", ["task", "category", "event", "regular"])
def test_delete_has_no_payload_or_prior_text_and_epoch_advances(kind):
    op = replace(operation(kind), action="delete", payload=None)
    assert decode_operation(encode_operation(op)) == op
    verify_receipt(op, receipt(op))
    assert entity("task").task.title not in repr(op)
    raw = json.loads(encode_operation(op))
    raw["data"]["before"] = "synthetic-sensitive"
    with pytest.raises(SyncCodecError):
        decode_operation(json.dumps(raw))
    with pytest.raises(SyncCodecError):
        encode_operation(replace(op, payload=entity(kind)))


def test_stop_regular_has_no_body_and_rejected_receipt_remains_rejected():
    op = replace(operation("regular"), action="stop_regular", payload=None)
    verify_receipt(op, receipt(op))
    reject = replace(receipt(op), status="rejected", error="stale_revision", revision=4)
    verify_receipt(op, reject)
    assert decode_receipt(encode_receipt(reject)).status == "rejected"
    with pytest.raises(SyncCodecError):
        encode_operation(replace(operation(), action="stop_regular", payload=None))


@pytest.mark.parametrize(
    "field,value",
    [
        ("action", "unknown"),
        ("target_kind", "node"),
        ("target_id", uuid4()),
        ("payload", None),
        ("op_id", "invalid"),
        ("base_revision", True),
        ("base_revision", -1),
        ("delete_epoch", 2**63),
        ("payload", entity("category")),
    ],
)
def test_invalid_typed_operation_rejected(field, value):
    with pytest.raises(SyncCodecError):
        encode_operation(replace(operation(), **{field: value}))


@pytest.mark.parametrize("action", ["put", "restore", "delete", "rollover", "stop_regular"])
def test_non_create_requires_known_base(action):
    op = replace(operation(), action=action, base_revision=0)
    with pytest.raises(SyncCodecError):
        encode_operation(op)


def test_create_requires_zero_epoch_and_base():
    for base, epoch in [(1, 0), (0, 1)]:
        with pytest.raises(SyncCodecError):
            encode_operation(
                replace(operation(), action="create", base_revision=base, delete_epoch=epoch)
            )


def test_deterministic_rollover_preserves_template_and_does_not_mutate_history():
    previous = entity("regular")
    day = date(2026, 12, 8)
    a, b = rollover_record(previous, day), rollover_record(previous, day)
    assert a == b and a.current.task.id != previous.current.task.id
    assert a.current_day == day and a.first_day == previous.first_day
    assert a.current.task.title == previous.current.task.title
    assert a.current.completed_at is None and not a.current.task.completed
    for child in a.current.task.subtasks:
        assert not child.completed and not any(i.completed for i in child.items)
    op = replace(operation("regular"), action="rollover", payload=a)
    assert decode_operation(encode_operation(op)) == op
    verify_receipt(op, receipt(op))
    assert previous.current.task.subtasks[1].completed
    assert rollover_record(previous, date(2026, 12, 11)).current.task.id != a.current.task.id


@pytest.mark.parametrize(
    "day", [date(2026, 12, 5), date(2026, 12, 6), datetime(2026, 12, 8), "2026-12-08"]
)
def test_rollover_day_must_be_later_and_on_schedule(day):
    with pytest.raises(SyncCodecError):
        rollover_record(entity("regular"), day)


def test_rollover_rejects_random_root_or_completed_state():
    current = rollover_record(entity("regular"), date(2026, 12, 8))
    op = replace(operation("regular"), action="rollover", payload=current)
    for task in (
        replace(current.current.task, id=uuid4()),
        replace(current.current.task, completed=True, subtasks=()),
    ):
        with pytest.raises(SyncCodecError):
            encode_operation(
                replace(op, payload=replace(current, current=replace(current.current, task=task)))
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "unknown"),
        ("revision", 0),
        ("error", "not_found"),
        ("request_hash", "A" * 64),
        ("request_hash", "short"),
        ("revision", True),
    ],
)
def test_receipt_status_hash_or_counter_rejected(field, value):
    with pytest.raises(SyncCodecError):
        encode_receipt(replace(receipt(operation()), **{field: value}))


@pytest.mark.parametrize("kind", ["operation", "receipt"])
@pytest.mark.parametrize("path", [(), ("data",)])
@pytest.mark.parametrize("field", ["owner", "token", "password", "before", "payload_text"])
def test_unknown_fields_rejected_without_echo(kind, path, field):
    raw = json.loads(
        encode_operation(operation())
        if kind == "operation"
        else encode_receipt(receipt(operation()))
    )
    target = raw if not path else raw["data"]
    target[field] = "synthetic-sensitive"
    decode = decode_operation if kind == "operation" else decode_receipt
    with pytest.raises(SyncCodecError) as caught:
        decode(json.dumps(raw))
    assert "synthetic-sensitive" not in str(caught.value)


@pytest.mark.parametrize("kind", ["operation", "receipt"])
@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"null",
        b"[]",
        b"{",
        b'{"version":1,"version":1,"kind":"operation","data":{}}',
        b'{"version":1.0,"kind":"operation","data":{}}',
        b'{"version":NaN,"kind":"operation","data":{}}',
        b" " * (2 * 1024 * 1024 + 1),
    ],
)
def test_invalid_json_and_size(kind, raw):
    with pytest.raises(SyncCodecError):
        (decode_operation if kind == "operation" else decode_receipt)(raw)


@pytest.mark.parametrize("kind", ["operation", "receipt"])
def test_missing_fields_and_version(kind):
    raw = json.loads(
        encode_operation(operation())
        if kind == "operation"
        else encode_receipt(receipt(operation()))
    )
    decode = decode_operation if kind == "operation" else decode_receipt
    for field in list(raw["data"]):
        changed = json.loads(json.dumps(raw))
        del changed["data"][field]
        with pytest.raises(SyncCodecError):
            decode(json.dumps(changed))
    raw["version"] = True
    with pytest.raises(SyncCodecError):
        decode(json.dumps(raw))


def test_big_revision_keeps_string_precision():
    op = replace(operation(), base_revision=9007199254740993)
    assert json.loads(encode_operation(op))["data"]["base_revision"] == "9007199254740993"
    assert decode_operation(encode_operation(op)).base_revision == 9007199254740993


@pytest.mark.parametrize("name", sorted(p.stem for p in FIXTURES.glob("*.json")))
def test_canonical_synthetic_examples(name):
    raw = (FIXTURES / f"{name}.json").read_bytes().rstrip(b"\n")
    decoder, encoder = (
        (decode_receipt, encode_receipt)
        if "receipt" in name
        else (decode_operation, encode_operation)
    )
    assert encoder(decoder(raw)) == raw


def test_subtask_prune_carries_only_remaining_tree_and_advances_barrier():
    op = operation()
    task = op.payload.task
    after = remove_subtask(task, task.subtasks[1].id)
    prune = replace(op, action="prune", payload=replace(op.payload, task=after))
    ack = replace(receipt(prune), delete_epoch=prune.delete_epoch + 1)
    verify_receipt(prune, ack)
    assert task.subtasks[1].title.encode() not in encode_operation(prune)
    with pytest.raises(SyncCodecError):
        verify_receipt(prune, replace(ack, delete_epoch=prune.delete_epoch))
    with pytest.raises(SyncCodecError):
        encode_operation(replace(operation("category"), action="prune"))


@pytest.mark.parametrize("text", ["01", "-1", "1.0", "90071992547409930x", "9223372036854775808"])
def test_noncanonical_wire_counters_rejected(text):
    raw = json.loads(encode_operation(operation()))
    raw["data"]["base_revision"] = text
    with pytest.raises(SyncCodecError):
        decode_operation(json.dumps(raw))


def test_fixture_receipts_match_their_requests():
    for path in FIXTURES.glob("*_receipt.json"):
        op = decode_operation(path.with_name(path.name.replace("_receipt", "")).read_bytes())
        verify_receipt(op, decode_receipt(path.read_bytes()))
