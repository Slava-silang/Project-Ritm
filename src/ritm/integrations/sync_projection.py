"""Pure local selection and proposed cloud retention; never deletes local data."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ritm.integrations._sync_wire import _guard, _id_text, _number_text
from ritm.integrations.sync_codec import (
    BlockRecord,
    CalendarRecord,
    CategoryRecord,
    Entity,
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    SyncCodecError,
    TaskRecord,
    decode_entity,
    encode_entity,
)
from ritm.integrations.sync_operations import target_identity
from ritm.integrations.sync_pages import (
    MAX_SNAPSHOT_ITEMS,
    MAX_STAGED_BYTES,
    Snapshot,
    SnapshotEntry,
    _check_graph,
)

COMPLETED_LIMIT = 50
CALENDAR_DAYS = 30


class CompletionSelectionRequired(SyncCodecError):
    def __init__(self, unknown_ids, suggested_ids):
        self.kind = "selection"
        self.unknown_ids = frozenset(unknown_ids)
        self.suggested_ids = frozenset(suggested_ids)
        ValueError.__init__(
            self, "Выберите завершённые задачи для облака: у части истории неизвестна дата."
        )


@dataclass(frozen=True, kw_only=True)
class LocalData:
    tasks: tuple[TaskRecord, ...] = field(default=(), repr=False)
    regulars: tuple[RegularRecord, ...] = field(default=(), repr=False)
    history: tuple[OccurrenceRecord, ...] = field(default=(), repr=False)
    categories: tuple[CategoryRecord, ...] = field(default=(), repr=False)
    blocks: tuple[BlockRecord, ...] = field(default=(), repr=False)
    calendar: CalendarRecord | None = None
    skipped_ids: frozenset[UUID] = frozenset()
    deleted_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, kw_only=True)
class LocalExclusion:
    kind: str
    id: UUID
    reason: str


@dataclass(frozen=True, kw_only=True)
class Projection:
    entities: tuple[Entity, ...] = field(repr=False)
    exclusions: tuple[LocalExclusion, ...]
    unknown_completed_ids: frozenset[UUID]


@dataclass(frozen=True, kw_only=True)
class RetentionPlan:
    revision: int
    entries: tuple[SnapshotEntry, ...] = field(repr=False)
    effects: tuple[SnapshotEntry, ...] = field(repr=False)


def _moment(moment):
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise SyncCodecError()
    return moment.astimezone(UTC)


def _records(values, expected):
    if type(values) is not tuple:
        raise SyncCodecError()
    result = []
    for value in values:
        if type(value) is not expected:
            raise SyncCodecError()
        result.append(decode_entity(encode_entity(value)))
    return tuple(result)


def _task(record):
    return record.current if isinstance(record, OccurrenceRecord) else record


def _root(record):
    return _task(record).task.id


def _validate_entities(entities):
    if len(entities) > MAX_SNAPSHOT_ITEMS:
        raise SyncCodecError("size")
    size = 0
    for entity in entities:
        size += len(encode_entity(entity))
        if size > MAX_STAGED_BYTES:
            raise SyncCodecError("size")
    _check_graph(tuple(SnapshotEntry(entity=e, revision=1, delete_epoch=0) for e in entities))


@_guard
def project_local(
    data: LocalData, now: datetime, selected_completed: frozenset[UUID] | None = None
) -> Projection:
    if type(data) is not LocalData:
        raise SyncCodecError()
    cutoff = _moment(now) - timedelta(days=CALENDAR_DAYS)
    tasks = _records(data.tasks, TaskRecord)
    regulars = _records(data.regulars, RegularRecord)
    history = _records(data.history, OccurrenceRecord)
    categories = _records(data.categories, CategoryRecord)
    blocks = _records(data.blocks, BlockRecord)
    calendar = () if data.calendar is None else _records((data.calendar,), CalendarRecord)
    for identifiers in (data.skipped_ids, data.deleted_ids):
        if type(identifiers) is not frozenset or any(type(x) is not UUID for x in identifiers):
            raise SyncCodecError()
    if data.skipped_ids & data.deleted_ids:
        raise SyncCodecError()
    excluded = []
    blocked = data.skipped_ids | data.deleted_ids

    def permitted(record, root):
        if root not in blocked:
            return True
        kind, identifier = target_identity(record)
        excluded.append(
            LocalExclusion(
                kind=kind,
                id=identifier,
                reason="user_deleted" if root in data.deleted_ids else "skipped",
            )
        )
        return False

    tasks = tuple(t for t in tasks if permitted(t, t.task.id))
    regulars = tuple(r for r in regulars if permitted(r, r.current.task.id))
    history = tuple(h for h in history if permitted(h, h.current.task.id))
    represented = {}
    for record in (*regulars, *history):
        root = record.current.task.id
        if root in represented:
            raise SyncCodecError()
        represented[root] = record.current
    ordinary = []
    seen = set()
    for record in tasks:
        if record.task.id in seen:
            raise SyncCodecError()
        seen.add(record.task.id)
        if record.task.id in represented:
            if record != represented[record.task.id]:
                raise SyncCodecError()
        else:
            ordinary.append(record)
    # Validate all candidate trees before selection can hide a malformed duplicate.
    _check_graph(
        tuple(
            SnapshotEntry(entity=e, revision=1, delete_epoch=0)
            for e in (*categories, *calendar, *regulars, *ordinary, *history)
        )
    )
    # Keep active rules (including their completed/waiting current templates) outside history cap.
    completed = [r for r in (*ordinary, *history) if _task(r).task.completed]
    active = [r for r in ordinary if not r.task.completed]
    unknown = frozenset(_root(r) for r in completed if _task(r).completed_at is None)
    dated = sorted(
        (r for r in completed if _task(r).completed_at is not None),
        key=lambda r: (_task(r).completed_at, _root(r).int),
        reverse=True,
    )
    if selected_completed is not None:
        roots = {_root(r) for r in completed}
        if (
            type(selected_completed) is not frozenset
            or any(type(x) is not UUID for x in selected_completed)
            or len(selected_completed) > COMPLETED_LIMIT
            or not selected_completed <= roots
        ):
            raise SyncCodecError()
        chosen = selected_completed
    elif len(completed) <= COMPLETED_LIMIT:
        chosen = frozenset(_root(r) for r in completed)
    elif unknown:
        raise CompletionSelectionRequired(unknown, (_root(r) for r in dated[:COMPLETED_LIMIT]))
    else:
        chosen = frozenset(_root(r) for r in dated[:COMPLETED_LIMIT])
    kept = [r for r in completed if _root(r) in chosen]
    for record in completed:
        if _root(record) not in chosen:
            kind, identifier = target_identity(record)
            excluded.append(LocalExclusion(kind=kind, id=identifier, reason="completion_limit"))
    all_roots = {t.task.id: t.task.title for t in tasks}
    all_roots.update({r.current.task.id: r.current.task.title for r in (*regulars, *history)})
    kept_roots = {_root(r) for r in (*active, *kept)} | {r.current.task.id for r in regulars}
    visible_blocks = []
    block_ids = set()
    for record in blocks:
        if record.block.id in block_ids:
            raise SyncCodecError()
        block_ids.add(record.block.id)
        block = record.block
        reason = None
        if block.task_id in data.deleted_ids:
            reason = "user_deleted"
        elif block.task_id in data.skipped_ids:
            reason = "skipped"
        elif block.ends_at < cutoff:
            reason = "calendar_age"
        elif block.task_id is not None and block.task_id not in kept_roots:
            title = all_roots.get(block.task_id, record.archived_task_title)
            if title is None:
                reason = "missing_source"
            else:
                record = replace(record, archived_task_title=title)
        elif block.task_id is not None:
            record = replace(record, archived_task_title=None)
        if reason:
            excluded.append(LocalExclusion(kind="block", id=block.id, reason=reason))
        else:
            visible_blocks.append(record)
    entities = (*categories, *calendar, *regulars, *active, *kept, *visible_blocks)
    _validate_entities(entities)
    return Projection(entities=entities, exclusions=tuple(excluded), unknown_completed_ids=unknown)


@_guard
def retain_cloud(
    snapshot: Snapshot, completion_order: Mapping[UUID, int], now: datetime, revision: int
) -> RetentionPlan:
    if (
        type(snapshot) is not Snapshot
        or type(snapshot.entries) is not tuple
        or not isinstance(completion_order, Mapping)
    ):
        raise SyncCodecError()
    _id_text(snapshot.server_id)
    _id_text(snapshot.snapshot_id)
    _number_text(snapshot.set_revision)
    _number_text(revision)
    if revision <= snapshot.set_revision:
        raise SyncCodecError()
    cutoff = _moment(now) - timedelta(days=CALENDAR_DAYS)
    for entry in snapshot.entries:
        if type(entry) is not SnapshotEntry:
            raise SyncCodecError()
        _number_text(entry.revision)
        _number_text(entry.delete_epoch)
        if not 0 < entry.revision <= snapshot.set_revision:
            raise SyncCodecError()
        if isinstance(entry.entity, RemovalRecord) and (
            entry.revision != entry.entity.revision
            or entry.delete_epoch != entry.entity.delete_epoch
        ):
            raise SyncCodecError()
    _validate_entities(tuple(e.entity for e in snapshot.entries))
    completed = []
    for entry in snapshot.entries:
        if (
            isinstance(entry.entity, (TaskRecord, OccurrenceRecord))
            and _task(entry.entity).task.completed
        ):
            root = _root(entry.entity)
            order = completion_order.get(root)
            _number_text(order)
            if order == 0:
                raise SyncCodecError()
            completed.append((order, root.int, entry))
    completed.sort(key=lambda row: (row[0], row[1]), reverse=True)
    old = {target_identity(row[2].entity)[1]: row[2] for row in completed[COMPLETED_LIMIT:]}
    old_titles = {_root(e.entity): _task(e.entity).task.title for e in old.values()}
    kept_roots = set()
    for entry in snapshot.entries:
        if isinstance(entry.entity, RegularRecord):
            kept_roots.add(entry.entity.current.task.id)
        elif isinstance(entry.entity, (TaskRecord, OccurrenceRecord)):
            if target_identity(entry.entity)[1] not in old:
                kept_roots.add(_root(entry.entity))
    deleted_roots = set()
    for entry in snapshot.entries:
        mark = entry.entity
        if isinstance(mark, RemovalRecord) and mark.reason == "user_deleted":
            if mark.entity_kind == "task":
                deleted_roots.add(mark.entity_id)
            elif mark.entity_kind == "occurrence":
                deleted_roots.add(mark.root_id)
    entries = []
    effects = []
    for entry in snapshot.entries:
        entity = entry.entity
        replacement = None
        if isinstance(entity, (TaskRecord, OccurrenceRecord)) and target_identity(entity)[1] in old:
            kind, identifier = target_identity(entity)
            replacement = RemovalRecord(
                entity_kind=kind,
                entity_id=identifier,
                root_id=_root(entity) if kind == "occurrence" else None,
                revision=revision,
                delete_epoch=entry.delete_epoch,
                reason="retention",
            )
        elif isinstance(entity, BlockRecord):
            if entity.block.task_id in deleted_roots or entity.block.ends_at < cutoff:
                deleted = entity.block.task_id in deleted_roots
                replacement = RemovalRecord(
                    entity_kind="block",
                    entity_id=entity.block.id,
                    revision=revision,
                    delete_epoch=entry.delete_epoch + deleted,
                    reason="user_deleted" if deleted else "retention",
                )
            elif entity.block.task_id in old_titles:
                replacement = replace(entity, archived_task_title=old_titles[entity.block.task_id])
            elif entity.block.task_id in kept_roots and entity.archived_task_title is not None:
                replacement = replace(entity, archived_task_title=None)
        if replacement is not None:
            encode_entity(replacement)
            entry = SnapshotEntry(
                entity=replacement,
                revision=revision,
                delete_epoch=replacement.delete_epoch
                if isinstance(replacement, RemovalRecord)
                else entry.delete_epoch,
            )
            effects.append(entry)
        entries.append(entry)
    _validate_entities(tuple(e.entity for e in entries))
    return RetentionPlan(
        revision=revision if effects else snapshot.set_revision,
        entries=tuple(entries),
        effects=tuple(effects),
    )
