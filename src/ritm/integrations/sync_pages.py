"""Bounded full-snapshot staging. No application of records or local deletions."""

import json
import re
from dataclasses import dataclass, field
from uuid import UUID

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
    CategoryRecord,
    Entity,
    RegularRecord,
    RemovalRecord,
    SyncCodecError,
    decode_entity,
    encode_entity,
)

MAX_PAGE_ITEMS = 50
MAX_SNAPSHOT_ITEMS = 10000
MAX_STAGED_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, kw_only=True)
class SnapshotEntry:
    revision: int
    delete_epoch: int
    entity: Entity = field(repr=False)


@dataclass(frozen=True, kw_only=True)
class SnapshotPage:
    server_id: UUID
    snapshot_id: UUID
    set_revision: int
    total: int
    offset: int
    cursor: str | None
    next_cursor: str | None
    entries: tuple[SnapshotEntry, ...] = field(repr=False)


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    server_id: UUID
    snapshot_id: UUID
    set_revision: int
    entries: tuple[SnapshotEntry, ...] = field(repr=False)


def _cursor(value):
    if value is not None and (
        type(value) is not str or re.fullmatch("[A-Za-z0-9_-]{1,512}", value) is None
    ):
        raise SyncCodecError()
    return value


def _validate(page):
    end = page.offset + len(page.entries)
    if not 0 <= page.offset <= end <= page.total <= MAX_SNAPSHOT_ITEMS:
        raise SyncCodecError()
    if (page.offset == 0) != (page.cursor is None):
        raise SyncCodecError()
    if (end == page.total) != (page.next_cursor is None):
        raise SyncCodecError()
    if page.total and not page.entries:
        raise SyncCodecError()
    if page.next_cursor is not None and page.next_cursor == page.cursor:
        raise SyncCodecError()
    for entry in page.entries:
        if not 0 < entry.revision <= page.set_revision:
            raise SyncCodecError()
        if isinstance(entry.entity, RemovalRecord) and (
            entry.entity.revision != entry.revision
            or entry.entity.delete_epoch != entry.delete_epoch
        ):
            raise SyncCodecError()


@_guard
def decode_page(raw: bytes | str) -> SnapshotPage:
    data = _load(raw, "snapshot_page")
    _keys(
        data,
        {
            "server_id",
            "snapshot_id",
            "set_revision",
            "total",
            "offset",
            "cursor",
            "next_cursor",
            "entries",
        },
    )
    if type(data["entries"]) is not list or len(data["entries"]) > MAX_PAGE_ITEMS:
        raise SyncCodecError()
    entries = []
    for value in data["entries"]:
        _keys(value, {"revision", "delete_epoch", "entity"})
        entries.append(
            SnapshotEntry(
                revision=_number(value["revision"]),
                delete_epoch=_number(value["delete_epoch"]),
                entity=decode_entity(_dump(value["entity"])),
            )
        )
    page = SnapshotPage(
        server_id=_id(data["server_id"]),
        snapshot_id=_id(data["snapshot_id"]),
        set_revision=_number(data["set_revision"]),
        total=_number(data["total"]),
        offset=_number(data["offset"]),
        cursor=_cursor(data["cursor"]),
        next_cursor=_cursor(data["next_cursor"]),
        entries=tuple(entries),
    )
    _validate(page)
    return page


@_guard
def encode_page(page: SnapshotPage) -> bytes:
    if (
        type(page) is not SnapshotPage
        or type(page.entries) is not tuple
        or len(page.entries) > MAX_PAGE_ITEMS
    ):
        raise SyncCodecError()
    entries = []
    for entry in page.entries:
        if type(entry) is not SnapshotEntry:
            raise SyncCodecError()
        entries.append(
            {
                "revision": _number_text(entry.revision),
                "delete_epoch": _number_text(entry.delete_epoch),
                "entity": json.loads(encode_entity(entry.entity)),
            }
        )
    raw = _dump(
        {
            "version": 1,
            "kind": "snapshot_page",
            "data": {
                "server_id": _id_text(page.server_id),
                "snapshot_id": _id_text(page.snapshot_id),
                "set_revision": _number_text(page.set_revision),
                "total": _number_text(page.total),
                "offset": _number_text(page.offset),
                "cursor": page.cursor,
                "next_cursor": page.next_cursor,
                "entries": entries,
            },
        }
    )
    decode_page(raw)
    return raw


def _check_graph(entries):
    identities = set()
    categories = set()
    tasks = set()
    references = []
    blocks = []
    for entry in entries:
        entity = entry.entity
        identifiers = []
        if isinstance(entity, RemovalRecord):
            identifiers = [entity.entity_id]
        elif isinstance(entity, CategoryRecord):
            categories.add(entity.category.id)
            identifiers = [entity.category.id]
        elif isinstance(entity, BlockRecord):
            identifiers = [entity.block.id]
            blocks.append(entity)
        else:
            record = entity.current if isinstance(entity, RegularRecord) else entity
            task = record.task
            tasks.add(task.id)
            identifiers = [task.id]
            if isinstance(entity, RegularRecord):
                identifiers.append(entity.id)
            for child in task.subtasks:
                identifiers.append(child.id)
                identifiers.extend(item.id for item in child.items)
            if task.category_id is not None:
                references.append(task.category_id)
        for identifier in identifiers:
            if identifier in identities:
                raise SyncCodecError()
            identities.add(identifier)
    if any(identifier not in categories for identifier in references):
        raise SyncCodecError()
    for entity in blocks:
        if (
            entity.block.task_id is not None
            and entity.block.task_id not in tasks
            and entity.archived_task_title is None
        ):
            raise SyncCodecError()


class SnapshotCollector:
    """One read attempt; an invalid page poisons only this temporary collector."""

    def __init__(self, server_id: UUID):
        if type(server_id) is not UUID:
            raise SyncCodecError()
        self._server_id = server_id
        self._header = None
        self._entries = []
        self._cursor = None
        self._seen = set()
        self._bytes = 0
        self._complete = False
        self._failed = False

    @property
    def complete(self):
        return self._complete and not self._failed

    def add(self, page: SnapshotPage) -> None:
        try:
            raw = encode_page(page)
            page = decode_page(raw)
            if self._failed or self._complete or page.server_id != self._server_id:
                raise SyncCodecError()
            header = (page.snapshot_id, page.set_revision, page.total)
            if self._header is not None and header != self._header:
                raise SyncCodecError()
            if page.offset != len(self._entries) or page.cursor != self._cursor:
                raise SyncCodecError()
            if page.next_cursor is not None and page.next_cursor in self._seen:
                raise SyncCodecError()
            if self._bytes + len(raw) > MAX_STAGED_BYTES:
                raise SyncCodecError("size")
            candidate = [*self._entries, *page.entries]
            if page.next_cursor is None:
                _check_graph(candidate)
            self._header = header
            self._entries = candidate
            self._cursor = page.next_cursor
            if page.next_cursor is not None:
                self._seen.add(page.next_cursor)
            self._bytes += len(raw)
            self._complete = page.next_cursor is None
        except SyncCodecError:
            self._entries = []
            self._seen.clear()
            self._complete = False
            self._failed = True
            raise

    def finish(self) -> Snapshot:
        if not self.complete:
            raise SyncCodecError()
        return Snapshot(
            server_id=self._server_id,
            snapshot_id=self._header[0],
            set_revision=self._header[1],
            entries=tuple(self._entries),
        )
