"""Synthetic sync-v1 contract and rejection vectors. No profile or network."""

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, Inexact, localcontext
from pathlib import Path
from uuid import uuid4

import pytest

from ritm.domain.completion import set_completed
from ritm.domain.tasks import Task
from ritm.integrations.sync_codec import (
    MAX_BYTES,
    MAX_INTEGER,
    BlockRecord,
    RemovalRecord,
    SyncCodecError,
    TaskRecord,
    decode_entity,
    encode_entity,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sync" / "v1"


def document(name="task"):
    return json.loads((FIXTURES / f"{name}.json").read_bytes())


def wire(value):
    return json.dumps(value, ensure_ascii=False).encode()


def target(value, path):
    for key in path:
        value = value[key]
    return value


@pytest.mark.parametrize("name", sorted(p.stem for p in FIXTURES.glob("*.json")))
def test_canonical_cross_client_vectors(name):
    raw = (FIXTURES / f"{name}.json").read_bytes().rstrip(b"\n")
    assert encode_entity(decode_entity(raw)) == raw
    assert decode_entity(raw.decode()) == decode_entity(raw)


def test_precision_and_order_are_wire_contract_not_json_numbers():
    raw = document()
    record = decode_entity(wire(raw))
    assert isinstance(record, TaskRecord)
    assert raw["data"]["expected_us"] == "9007199254740993"
    assert record.task.expected_duration == timedelta(microseconds=9007199254740993)
    assert raw["data"]["deadline"] == "2026-11-26T09:30:00.123456+00:00"
    assert record.task.deadline == datetime(2026, 11, 26, 9, 30, 0, 123456, tzinfo=UTC)
    assert [s.title for s in record.task.subtasks] == ["Подготовка", "Отчёт"]
    assert [s.weight for s in record.task.subtasks] == [Decimal("40.25"), Decimal("59.75")]
    assert document("deleted_task")["data"]["revision"] == "9007199254740993"
    block = decode_entity(wire(document("event"))).block
    assert block.ends_at - block.starts_at == timedelta(hours=1, microseconds=1)


@pytest.mark.parametrize(
    "path", [(), ("data",), ("data", "subtasks", 0), ("data", "subtasks", 0, "items", 0)]
)
@pytest.mark.parametrize(
    "key",
    [
        "attachments",
        "trash",
        "before",
        "after",
        "password",
        "token",
        "focus",
        "garden",
        "owner",
        "source_links",
    ],
)
def test_private_or_unknown_fields_are_rejected_at_every_level(path, key):
    value = document()
    target(value, path)[key] = "synthetic-private-content"
    with pytest.raises(SyncCodecError) as error:
        decode_entity(wire(value))
    assert "synthetic-private-content" not in str(error.value)


MISSING = [
    (name, path, key)
    for name, path in (
        ("task", ("data",)),
        ("task", ("data", "subtasks", 0)),
        ("task", ("data", "subtasks", 0, "items", 0)),
        ("category", ("data",)),
        ("event", ("data",)),
        ("deleted_task", ("data",)),
    )
    for key in target(document(name), path)
]


@pytest.mark.parametrize(("name", "path", "key"), MISSING)
def test_missing_fields_are_not_filled_or_guessed(name, path, key):
    value = document(name)
    del target(value, path)[key]
    with pytest.raises(SyncCodecError):
        decode_entity(wire(value))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("version",), True),
        (("version",), 2),
        (("kind",), "unknown"),
        (("data", "completed"), 1),
        (("data", "difficulty"), True),
        (("data", "difficulty"), 0),
        (("data", "expected_us"), 9007199254740993),
        (("data", "expected_us"), "0"),
        (("data", "expected_us"), "01"),
        (("data", "expected_us"), str(MAX_INTEGER + 1)),
        (("data", "expected_us"), "-1"),
        (("data", "expected_us"), "1e3"),
        (("data", "title"), " "),
        (("data", "title"), " leading"),
        (("data", "description"), None),
        (("data", "subtasks"), {}),
        (("data", "id"), "not-a-uuid"),
        (("data", "deadline"), "2026-11-26T00:00:00"),
        (("data", "deadline"), "2026-02-30T00:00:00Z"),
        (("data", "deadline"), "0001-01-01T00:00:00+14:00"),
        (("data", "completed_at"), "2026-11-26T00:00:00Z"),
        (("data", "subtasks", 0, "weight"), "40.250"),
        (("data", "subtasks", 0, "weight"), "40.00"),
        (("data", "subtasks", 0, "weight"), "NaN"),
        (("data", "subtasks", 0, "weight"), "40.٢٥"),
        (("data", "subtasks", 0, "completed"), True),
        (("data", "completed"), True),
    ],
)
def test_invalid_scalar_and_inconsistent_tree_values(path, value):
    data = document()
    target(data, path[:-1])[path[-1]] = value
    with pytest.raises(SyncCodecError):
        decode_entity(wire(data))


def test_duplicate_node_ids_rejected():
    value = document()
    value["data"]["subtasks"][0]["items"][0]["id"] = value["data"]["id"]
    with pytest.raises(SyncCodecError):
        decode_entity(wire(value))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ends_at", "2026-11-26T10:00:00.123456Z"),
        ("starts_at", "2026-11-26T10:00:00"),
        ("title", ""),
        ("archived_task_title", "must-not-attach-to-event"),
    ],
)
def test_calendar_constraints(key, value):
    data = document("event")
    data["data"][key] = value
    with pytest.raises(SyncCodecError):
        decode_entity(wire(data))


def test_linked_block_cannot_be_an_event_and_retains_only_its_caption():
    data = document("archived_block")
    value = decode_entity(wire(data))
    assert isinstance(value, BlockRecord) and value.block.task_id is not None
    assert value.archived_task_title == "Старая завершённая задача"
    data["data"]["title"] = "not-an-independent-event"
    with pytest.raises(SyncCodecError):
        decode_entity(wire(data))
    assert "Старая завершённая задача" not in repr(value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("revision", 43),
        ("revision", "0"),
        ("revision", "0043"),
        ("delete_epoch", "-1"),
        ("reason", "purge-everything"),
        ("entity_kind", "attachment"),
        ("root_id", str(uuid4())),
    ],
)
def test_removal_constraints(key, value):
    data = document("deleted_task")
    data["data"][key] = value
    with pytest.raises(SyncCodecError):
        decode_entity(wire(data))


@pytest.mark.parametrize("name", ["deleted_task", "deleted_node", "expired_block"])
def test_removal_has_no_payload_and_rejects_deleted_text(name):
    value = document(name)
    assert set(value["data"]) == {
        "entity_kind",
        "entity_id",
        "root_id",
        "revision",
        "delete_epoch",
        "reason",
    }
    for key in ("title", "description", "payload", "before", "after", "trash"):
        invalid = copy.deepcopy(value)
        invalid["data"][key] = "synthetic-removed-text"
        with pytest.raises(SyncCodecError):
            decode_entity(wire(invalid))
    assert isinstance(decode_entity(wire(value)), RemovalRecord)


def test_node_marker_requires_root_and_retention_is_only_task_or_block():
    value = document("deleted_node")
    for patch in (
        {"root_id": None},
        {"root_id": value["data"]["entity_id"]},
        {"reason": "retention"},
    ):
        invalid = copy.deepcopy(value)
        invalid["data"].update(patch)
        with pytest.raises(SyncCodecError):
            decode_entity(wire(invalid))


@pytest.mark.parametrize(
    "payload",
    [
        b'{"version":1,"version":1,"kind":"task","data":{}}',
        b'{"version":1,"kind":"category","data":{"id":null,"id":null,"title":"x"}}',
        b'{"version":1.0}',
        b'{"version":NaN}',
        b'{"version":Infinity}',
        b"[]",
        b"null",
        b"{}",
        b"{} {}",
        b"\xff",
        (b"[" * 1200) + (b"]" * 1200),
    ],
)
def test_bad_json_and_duplicate_keys(payload):
    with pytest.raises(SyncCodecError):
        decode_entity(payload)


def test_size_limit_checks_utf8_bytes_and_encode_too():
    with pytest.raises(SyncCodecError) as error:
        decode_entity(b" " * (MAX_BYTES + 1))
    assert error.value.kind == "size"
    value = document()
    value["data"]["description"] = "Я" * (MAX_BYTES // 2)
    with pytest.raises(SyncCodecError):
        decode_entity(json.dumps(value, ensure_ascii=False))
    record = decode_entity(wire(document()))
    with pytest.raises(SyncCodecError):
        encode_entity(replace(record, task=replace(record.task, description="x" * (MAX_BYTES + 1))))


def test_encoder_validates_and_does_not_repair_or_round():
    record = decode_entity(wire(document()))
    branch = record.task.subtasks[0]
    for weight in (None, Decimal("40.251")):
        invalid = replace(
            record.task, subtasks=(replace(branch, weight=weight), record.task.subtasks[1])
        )
        with pytest.raises(SyncCodecError):
            encode_entity(TaskRecord(task=invalid))
    with pytest.raises(SyncCodecError):
        encode_entity(TaskRecord(task=replace(record.task, completed=True)))
    assert "Лабораторная" not in repr(record)


def test_completed_timestamp_and_large_duration_roundtrip_without_decimal_context():
    record = decode_entity(wire(document()))
    task = set_completed(record.task, record.task.id, True)
    when = datetime(2026, 11, 26, 11, 12, 13, 999999, tzinfo=UTC)
    value = TaskRecord(
        task=replace(task, expected_duration=timedelta(microseconds=MAX_INTEGER)), completed_at=when
    )
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        assert decode_entity(encode_entity(value)) == value


def test_empty_task_and_unknown_completion_date_are_not_fabricated():
    task = Task(title="Готово", completed=True)
    value = TaskRecord(task=task)
    assert decode_entity(encode_entity(value)) == value
    assert json.loads(encode_entity(value))["data"]["completed_at"] is None
    assert (
        decode_entity(encode_entity(TaskRecord(task=replace(task, completed=False)))).task.completed
        is False
    )


@pytest.mark.parametrize("revision", [True, -1, 0, MAX_INTEGER + 1])
def test_encoder_rejects_invalid_typed_counters(revision):
    record = decode_entity((FIXTURES / "deleted_task.json").read_bytes())
    with pytest.raises(SyncCodecError):
        encode_entity(replace(record, revision=revision))


def test_deadline_offsets_are_normalized_without_losing_microseconds():
    value = document()
    value["data"]["deadline"] = "2026-11-26T15:00:00.123456+05:30"
    assert (
        json.loads(encode_entity(decode_entity(wire(value))))["data"]["deadline"]
        == "2026-11-26T09:30:00.123456+00:00"
    )


def test_automatic_null_weights_roundtrip_without_assigning_them():
    record = decode_entity((FIXTURES / "auto_task.json").read_bytes())
    assert all(child.weight is None for child in record.task.subtasks)
    assert decode_entity(encode_entity(record)) == record
    assert all(
        child["weight"] is None for child in json.loads(encode_entity(record))["data"]["subtasks"]
    )


def test_typed_removal_uuid_is_not_silently_coerced():
    record = decode_entity((FIXTURES / "deleted_node.json").read_bytes())
    for invalid in (
        replace(record, entity_id=str(record.entity_id)),
        replace(record, root_id=str(record.root_id)),
    ):
        with pytest.raises(SyncCodecError):
            encode_entity(invalid)
