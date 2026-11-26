"""Pure proposed server effects for live regular tasks. No database or authority."""

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ritm.domain.recurrence import scheduled_day
from ritm.integrations._sync_wire import _guard, _number_text
from ritm.integrations.sync_codec import (
    CalendarRecord,
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    SyncCodecError,
    decode_entity,
    encode_entity,
    history_id,
)
from ritm.integrations.sync_operations import Operation, Receipt, rollover_record, verify_receipt
from ritm.integrations.sync_pages import SnapshotEntry


@dataclass(frozen=True, kw_only=True)
class RegularEffects:
    receipt: Receipt
    entries: tuple[SnapshotEntry, ...] = field(repr=False)


@_guard
def calendar_day(settings: CalendarRecord, moment: datetime) -> date:
    if (
        type(settings) is not CalendarRecord
        or not isinstance(moment, datetime)
        or moment.utcoffset() is None
    ):
        raise SyncCodecError()
    checked = decode_entity(encode_entity(settings))
    return moment.astimezone(ZoneInfo(checked.timezone)).date()


@_guard
def history_record(regular: RegularRecord) -> OccurrenceRecord:
    regular = decode_entity(encode_entity(regular))
    if type(regular) is not RegularRecord or not regular.current.task.completed:
        raise SyncCodecError()
    result = OccurrenceRecord(
        id=history_id(regular.id, regular.current_day),
        rule_id=regular.id,
        day=regular.current_day,
        current=regular.current,
    )
    encode_entity(result)
    return result


@_guard
def plan_regular_effects(
    before: SnapshotEntry,
    operation: Operation,
    receipt: Receipt,
    settings: CalendarRecord,
    moment: datetime,
) -> RegularEffects:
    """One CAS transition over a known server aggregate; apply all entries atomically later."""
    if type(before) is not SnapshotEntry or type(before.entity) is not RegularRecord:
        raise SyncCodecError()
    _number_text(before.revision)
    _number_text(before.delete_epoch)
    if before.revision == 0:
        raise SyncCodecError()
    regular = decode_entity(encode_entity(before.entity))
    today = calendar_day(settings, moment)
    verify_receipt(operation, receipt)
    if (
        receipt.status != "applied"
        or operation.target_kind != "regular"
        or operation.target_id != regular.id
        or operation.base_revision != before.revision
        or operation.delete_epoch != before.delete_epoch
        or receipt.revision <= before.revision
    ):
        raise SyncCodecError()
    if operation.action == "rollover":
        day = scheduled_day(regular.first_day, regular.interval_days, today)
        expected = rollover_record(regular, day)
        if operation.payload != expected:
            raise SyncCodecError()
        previous = (
            history_record(regular)
            if regular.current.task.completed
            else RemovalRecord(
                entity_kind="task",
                entity_id=regular.current.task.id,
                revision=receipt.revision,
                delete_epoch=receipt.delete_epoch,
                reason="retention",
            )
        )
        entities = (expected, previous)
    elif operation.action == "stop_regular":
        entities = (
            RemovalRecord(
                entity_kind="regular",
                entity_id=regular.id,
                revision=receipt.revision,
                delete_epoch=receipt.delete_epoch,
                reason="user_deleted",
            ),
            regular.current,
        )
    elif operation.action == "delete":
        entities = tuple(
            RemovalRecord(
                entity_kind=kind,
                entity_id=identifier,
                revision=receipt.revision,
                delete_epoch=receipt.delete_epoch,
                reason="user_deleted",
            )
            for kind, identifier in (("regular", regular.id), ("task", regular.current.task.id))
        )
    else:
        raise SyncCodecError()
    for entity in entities:
        encode_entity(entity)
    return RegularEffects(
        receipt=receipt,
        entries=tuple(
            SnapshotEntry(
                revision=receipt.revision, delete_epoch=receipt.delete_epoch, entity=entity
            )
            for entity in entities
        ),
    )
