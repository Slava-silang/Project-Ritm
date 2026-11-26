"""History identity, account calendar and atomic-effect candidates; no live server."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ritm.domain.completion import set_completed
from ritm.integrations.sync_codec import (
    OccurrenceRecord,
    RemovalRecord,
    SyncCodecError,
    decode_entity,
    encode_entity,
    history_id,
)
from ritm.integrations.sync_operations import (
    Operation,
    Receipt,
    encode_operation,
    operation_hash,
    rollover_record,
)
from ritm.integrations.sync_pages import SnapshotCollector, SnapshotEntry, SnapshotPage
from ritm.integrations.sync_transitions import calendar_day, history_record, plan_regular_effects

FIXTURES = Path(__file__).parent / "fixtures" / "sync" / "v1"
NOW = datetime(2026, 12, 7, 22, tzinfo=UTC)  # Moscow is already on the next scheduled day.
SERVER = UUID(int=600)


def entity(name):
    return decode_entity((FIXTURES / f"{name}.json").read_bytes())


def baseline(completed=False):
    regular = entity("regular")
    if completed:
        regular = replace(
            regular,
            current=replace(
                regular.current,
                task=set_completed(regular.current.task, regular.current.task.id, True),
            ),
        )
    return SnapshotEntry(revision=4, delete_epoch=2, entity=regular)


def request(before, action="rollover"):
    op = Operation(
        op_id=UUID(int=601),
        device_id=UUID(int=602),
        server_id=SERVER,
        target_kind="regular",
        target_id=before.entity.id,
        base_revision=4,
        delete_epoch=2,
        action=action,
        payload=rollover_record(before.entity, date(2026, 12, 8)) if action == "rollover" else None,
    )
    ack = Receipt(
        op_id=op.op_id,
        device_id=op.device_id,
        server_id=SERVER,
        request_hash=operation_hash(op),
        status="applied",
        revision=5,
        delete_epoch=2 + (action in ("stop_regular", "delete")),
        error=None,
    )
    return op, ack


def collect(entries):
    complete = (
        SnapshotEntry(revision=1, delete_epoch=0, entity=entity("category")),
        SnapshotEntry(revision=1, delete_epoch=0, entity=entity("calendar_settings")),
        *entries,
    )
    page = SnapshotPage(
        server_id=SERVER,
        snapshot_id=UUID(int=603),
        set_revision=5,
        total=len(complete),
        offset=0,
        cursor=None,
        next_cursor=None,
        entries=complete,
    )
    collector = SnapshotCollector(SERVER)
    collector.add(page)
    return collector.finish()


def test_common_account_day_not_utc_or_machine_day():
    assert calendar_day(entity("calendar_settings"), NOW) == date(2026, 12, 8)
    utc = replace(entity("calendar_settings"), timezone="UTC")
    assert calendar_day(utc, NOW) == date(2026, 12, 7)
    berlin = replace(utc, timezone="Europe/Berlin")
    assert calendar_day(berlin, datetime(2026, 3, 28, 23, 30, tzinfo=UTC)) == date(2026, 3, 29)
    with pytest.raises(SyncCodecError):
        calendar_day(berlin, datetime(2026, 3, 29))


@pytest.mark.parametrize(
    "zone", ["", "/etc/passwd", "../Europe/Moscow", "Missing/Zone", None, True]
)
def test_invalid_calendar_zone_rejected_without_fallback(zone):
    record = replace(entity("calendar_settings"), timezone=zone)
    with pytest.raises(SyncCodecError):
        encode_entity(record)


def test_history_stable_by_rule_and_scheduled_day_keeps_unknown_completion_date():
    before = baseline(True)
    a, b = history_record(before.entity), history_record(before.entity)
    assert a == b and a.id == history_id(a.rule_id, a.day)
    assert a.day == date(2026, 12, 5) and a.current.completed_at is None
    assert a.current.task.id == before.entity.current.task.id
    assert decode_entity(encode_entity(a)) == a
    with pytest.raises(SyncCodecError):
        history_record(baseline().entity)


@pytest.mark.parametrize(
    "changes",
    [
        {"id": uuid4()},
        {"rule_id": uuid4()},
        {"day": date(2026, 12, 6)},
        {"day": "2026-12-05"},
    ],
)
def test_history_identity_cannot_be_forged_by_independent_fields(changes):
    record = entity("occurrence")
    with pytest.raises(SyncCodecError):
        encode_entity(replace(record, **changes))


@pytest.mark.parametrize("completed", [False, True])
def test_rollover_has_two_consistent_effects(completed):
    before = baseline(completed)
    op, ack = request(before)
    effects = plan_regular_effects(before, op, ack, entity("calendar_settings"), NOW)
    assert effects.receipt == ack and len(effects.entries) == 2
    assert all(e.revision == 5 and e.delete_epoch == 2 for e in effects.entries)
    current, previous = effects.entries
    assert current.entity == op.payload
    if completed:
        assert isinstance(previous.entity, OccurrenceRecord)
        assert previous.entity.current == before.entity.current
    else:
        assert isinstance(previous.entity, RemovalRecord)
        assert previous.entity.entity_id == before.entity.current.task.id
        assert previous.entity.reason == "retention"
        assert before.entity.current.task.title not in repr(previous)
    assert len(collect(effects.entries).entries) == 4
    assert before.entity.current_day == date(2026, 12, 5)


def test_stop_keeps_current_tree_ordinary_delete_has_only_metadata():
    before = baseline(True)
    for action in ("stop_regular", "delete"):
        op, ack = request(before, action)
        effects = plan_regular_effects(before, op, ack, entity("calendar_settings"), NOW)
        assert all(e.delete_epoch == 3 for e in effects.entries)
        assert effects.entries[0].entity.entity_kind == "regular"
        if action == "stop_regular":
            assert effects.entries[1].entity == before.entity.current
        else:
            assert all(isinstance(e.entity, RemovalRecord) for e in effects.entries)
            assert effects.entries[1].entity.entity_kind == "task"
        collect(effects.entries)


@pytest.mark.parametrize(
    "field,value", [("revision", 3), ("delete_epoch", 1), ("revision", 6), ("revision", True)]
)
def test_stale_or_wrong_baseline_has_no_effects(field, value):
    before = baseline()
    op, ack = request(before)
    with pytest.raises(SyncCodecError):
        plan_regular_effects(
            replace(before, **{field: value}), op, ack, entity("calendar_settings"), NOW
        )


def test_rollover_checks_server_day_and_exact_template_not_just_wire_shape():
    before = baseline()
    op, ack = request(before)
    utc = replace(entity("calendar_settings"), timezone="UTC")
    with pytest.raises(SyncCodecError):
        plan_regular_effects(before, op, ack, utc, NOW)
    changed = replace(
        op,
        payload=replace(
            op.payload,
            current=replace(
                op.payload.current, task=replace(op.payload.current.task, title="Unconfirmed edit")
            ),
        ),
    )
    altered = replace(ack, request_hash=operation_hash(changed))
    with pytest.raises(SyncCodecError):
        plan_regular_effects(before, changed, altered, entity("calendar_settings"), NOW)
    rejected = replace(ack, status="rejected", error="stale_revision")
    with pytest.raises(SyncCodecError):
        plan_regular_effects(before, op, rejected, entity("calendar_settings"), NOW)


def test_history_not_duplicated_as_current_or_plain_task():
    before = baseline(True)
    history = SnapshotEntry(revision=4, delete_epoch=2, entity=history_record(before.entity))
    with pytest.raises(SyncCodecError):
        collect((before, history))
    with pytest.raises(SyncCodecError):
        collect((history, SnapshotEntry(revision=4, delete_epoch=2, entity=history.entity.current)))
    op, ack = request(before)
    effects = plan_regular_effects(before, op, ack, entity("calendar_settings"), NOW)
    with pytest.raises(SyncCodecError):
        collect((*effects.entries, history))


def test_history_day_cannot_be_off_schedule_for_active_rule():
    before = baseline(True)
    op, ack = request(before)
    effects = plan_regular_effects(before, op, ack, entity("calendar_settings"), NOW)
    history = effects.entries[1].entity
    day = date(2026, 12, 6)
    history = replace(history, day=day, id=history_id(history.rule_id, day))
    with pytest.raises(SyncCodecError):
        collect((effects.entries[0], replace(effects.entries[1], entity=history)))


def test_occurrence_removal_has_root_without_text_and_supports_retention():
    history = entity("occurrence")
    mark = RemovalRecord(
        entity_kind="occurrence",
        entity_id=history.id,
        root_id=history.current.task.id,
        reason="retention",
        revision=5,
        delete_epoch=2,
    )
    assert decode_entity(encode_entity(mark)) == mark
    with pytest.raises(SyncCodecError):
        encode_entity(replace(mark, root_id=None))
    with pytest.raises(SyncCodecError):
        collect(
            (
                SnapshotEntry(entity=mark, revision=5, delete_epoch=2),
                SnapshotEntry(entity=history.current, revision=4, delete_epoch=2),
            )
        )


def test_calendar_only_one_and_required_for_active_rules():
    rule = baseline()
    page = SnapshotPage(
        server_id=SERVER,
        snapshot_id=UUID(int=604),
        set_revision=5,
        total=2,
        offset=0,
        cursor=None,
        next_cursor=None,
        entries=(SnapshotEntry(entity=entity("category"), revision=1, delete_epoch=0), rule),
    )
    with pytest.raises(SyncCodecError):
        SnapshotCollector(SERVER).add(page)
    duplicate = replace(entity("calendar_settings"), id=uuid4())
    with pytest.raises(SyncCodecError):
        collect((SnapshotEntry(entity=duplicate, revision=2, delete_epoch=0),))


@pytest.mark.parametrize("kind", ["calendar_settings", "occurrence"])
def test_new_entities_used_by_operations_and_unknown_fields_still_rejected(kind):
    record = entity(kind)
    op = Operation(
        op_id=uuid4(),
        device_id=uuid4(),
        server_id=SERVER,
        target_kind=kind,
        target_id=record.id,
        base_revision=4,
        delete_epoch=0,
        action="put",
        payload=record,
    )
    assert encode_operation(op)
    if kind == "calendar_settings":
        with pytest.raises(SyncCodecError):
            encode_operation(replace(op, action="delete", payload=None))
    raw = json.loads(encode_entity(record))
    raw["data"]["password"] = "synthetic-sensitive"
    with pytest.raises(SyncCodecError) as caught:
        decode_entity(json.dumps(raw))
    assert "synthetic-sensitive" not in str(caught.value)


def test_completed_history_edits_keep_structural_removal_explicit():
    from ritm.domain.completion import remove_subtask

    history = entity("occurrence")
    current = replace(
        history.current,
        task=remove_subtask(history.current.task, history.current.task.subtasks[1].id),
    )
    edited = replace(history, current=current)
    op = Operation(
        op_id=uuid4(),
        device_id=uuid4(),
        server_id=SERVER,
        target_kind="occurrence",
        target_id=history.id,
        base_revision=4,
        delete_epoch=2,
        action="prune",
        payload=edited,
    )
    assert encode_operation(op)
    for invalid in (
        replace(
            history,
            current=replace(
                history.current, task=replace(history.current.task, completed=False, subtasks=())
            ),
        ),
        replace(
            history,
            current=replace(history.current, task=replace(history.current.task, deadline=NOW)),
        ),
    ):
        with pytest.raises(SyncCodecError):
            encode_entity(invalid)


def test_plan_is_repeatable_and_does_not_mutate_old_state_or_history_date():
    before = baseline(True)
    completed = datetime(2026, 12, 7, 12, tzinfo=UTC)
    before = replace(
        before,
        entity=replace(
            before.entity, current=replace(before.entity.current, completed_at=completed)
        ),
    )
    op, ack = request(before)
    a = plan_regular_effects(before, op, ack, entity("calendar_settings"), NOW)
    b = plan_regular_effects(before, op, ack, entity("calendar_settings"), NOW)
    assert a == b
    assert a.entries[1].entity.day == date(2026, 12, 5)
    assert a.entries[1].entity.current.completed_at == completed
    assert before.entity.current.completed_at == completed
    assert before.entity.current.task.title not in repr(a)
