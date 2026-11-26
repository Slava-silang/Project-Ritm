"""Pure regular-aggregate contract: synthetic records, no storage/profile/network."""

import copy
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from ritm.integrations.sync_codec import (
    RegularRecord,
    RemovalRecord,
    SyncCodecError,
    decode_entity,
    encode_entity,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sync" / "v1"


def document():
    return json.loads((FIXTURES / "regular.json").read_bytes())


def decode(value):
    return decode_entity(json.dumps(value, ensure_ascii=False))


def test_independent_schedule_values_and_unchanged_nested_precision():
    record = decode(document())
    assert isinstance(record, RegularRecord)
    assert record.first_day == date(2026, 11, 26)
    assert record.current_day == date(2026, 12, 5)
    assert record.interval_days == 3
    assert record.current.task.title == "Лабораторная — пример"
    assert record.current.task.deadline is None
    assert record.current.task.expected_duration.days == 104249
    assert record.id != record.current.task.id
    assert document()["data"]["current"]["expected_us"] == "9007199254740993"


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "invalid"),
        ("id", None),
        ("first_day", "2026-02-30"),
        ("first_day", "20261126"),
        ("first_day", "2026-11-26T00:00:00+00:00"),
        ("first_day", None),
        ("current_day", "2026-11-25"),
        ("current_day", "2026-12-04"),
        ("current_day", "2026-12-05Z"),
        ("interval_days", 0),
        ("interval_days", -1),
        ("interval_days", True),
        ("interval_days", "3"),
        ("interval_days", 3.0),
        ("interval_days", 36501),
        ("current", None),
        ("current", []),
    ],
)
def test_malformed_or_inconsistent_schedule_rejected(field, value):
    raw = document()
    raw["data"][field] = value
    with pytest.raises(SyncCodecError):
        decode(raw)


@pytest.mark.parametrize("field", ["id", "first_day", "interval_days", "current_day", "current"])
def test_required_fields(field):
    raw = document()
    del raw["data"][field]
    with pytest.raises(SyncCodecError):
        decode(raw)


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("data",),
        ("data", "current"),
        ("data", "current", "subtasks", 0),
        ("data", "current", "subtasks", 0, "items", 0),
    ],
)
@pytest.mark.parametrize("field", ["attachments", "trash", "token", "source_links"])
def test_local_payload_never_added_to_regular_aggregate(path, field):
    raw = document()
    target = raw
    for key in path:
        target = target[key]
    target[field] = "synthetic-sensitive-marker"
    with pytest.raises(SyncCodecError) as caught:
        decode(raw)
    assert "synthetic-sensitive-marker" not in str(caught.value)


def test_regular_task_deadline_is_forbidden_in_both_directions():
    raw = document()
    raw["data"]["current"]["deadline"] = "2026-12-05T10:00:00+00:00"
    with pytest.raises(SyncCodecError):
        decode(raw)
    record = decode(document())
    task = replace(
        record.current.task, deadline=datetime.fromisoformat(raw["data"]["current"]["deadline"])
    )
    with pytest.raises(SyncCodecError):
        encode_entity(replace(record, current=replace(record.current, task=task)))


def test_schedule_identity_separate_from_every_node_and_task():
    raw = document()
    for identifier in (
        raw["data"]["current"]["id"],
        raw["data"]["current"]["subtasks"][0]["id"],
        raw["data"]["current"]["subtasks"][0]["items"][0]["id"],
    ):
        changed = copy.deepcopy(raw)
        changed["data"]["id"] = identifier
        with pytest.raises(SyncCodecError):
            decode(changed)


def test_future_start_and_completed_current_not_silently_advanced():
    raw = document()
    raw["data"]["first_day"] = raw["data"]["current_day"] = "2099-01-01"
    record = decode(raw)
    assert record.current_day == date(2099, 1, 1)
    from ritm.domain.completion import set_completed

    completed = replace(
        record,
        current=replace(
            record.current, task=set_completed(record.current.task, record.current.task.id, True)
        ),
    )
    assert decode_entity(encode_entity(completed)) == completed
    assert completed.current_day == record.current_day
    assert completed.current.completed_at is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "00000010-0000-4000-8000-000000000000"),
        ("first_day", datetime(2026, 11, 26)),
        ("current_day", "2026-12-05"),
        ("interval_days", False),
        ("current", None),
    ],
)
def test_encoder_rejects_invalid_typed_record(field, value):
    record = decode(document())
    with pytest.raises(SyncCodecError):
        encode_entity(replace(record, **{field: value}))


def test_stop_marker_carries_no_task_or_text_and_is_not_retention():
    record = decode_entity((FIXTURES / "stopped_regular.json").read_bytes())
    assert isinstance(record, RemovalRecord) and record.entity_kind == "regular"
    assert record.root_id is None and record.reason == "user_deleted"
    assert not hasattr(record, "current")
    with pytest.raises(SyncCodecError):
        encode_entity(replace(record, reason="retention"))
    with pytest.raises(SyncCodecError):
        encode_entity(replace(record, root_id=uuid4()))
    raw = json.loads(encode_entity(record))
    raw["data"]["current"] = document()["data"]["current"]
    with pytest.raises(SyncCodecError):
        decode(raw)


def test_duplicate_schedule_keys_rejected_and_repr_has_no_task_text():
    raw = (
        (FIXTURES / "regular.json")
        .read_text()
        .replace('"interval_days":3', '"interval_days":3,"interval_days":3')
    )
    with pytest.raises(SyncCodecError):
        decode_entity(raw)
    record = decode(document())
    assert record.current.task.title not in repr(record)
    assert record.current.task.description not in repr(record)
