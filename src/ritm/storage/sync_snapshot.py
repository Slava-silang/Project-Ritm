"""Bounded durable remote knowledge. Never applies or removes local user data."""

import json

from ritm.integrations.sync_codec import RemovalRecord, encode_entity
from ritm.integrations.sync_operations import target_identity
from ritm.integrations.sync_pages import (
    MAX_PAGE_ITEMS,
    MAX_STAGED_BYTES,
    Snapshot,
    SnapshotCollector,
    SnapshotPage,
    decode_page,
    encode_page,
)

SNAPSHOT_OBJECTS = ("sync_remote_snapshot",)
SNAPSHOT_SCHEMA = (
    "CREATE TABLE sync_remote_snapshot (context_id INTEGER PRIMARY KEY CHECK(context_id=1) "
    "REFERENCES sync_binding(id), pages BLOB NOT NULL CHECK(typeof(pages)='blob'))",
)


def _pack(snapshot):
    if type(snapshot) is not Snapshot or type(snapshot.entries) is not tuple:
        raise ValueError("Нужен полный проверяемый снимок облачного состояния")
    pages = []
    for start in range(0, max(len(snapshot.entries), 1), MAX_PAGE_ITEMS):
        end = min(start + MAX_PAGE_ITEMS, len(snapshot.entries))
        pages.append(
            encode_page(
                SnapshotPage(
                    server_id=snapshot.server_id,
                    snapshot_id=snapshot.snapshot_id,
                    set_revision=snapshot.set_revision,
                    total=len(snapshot.entries),
                    offset=start,
                    cursor=str(start) if start else None,
                    next_cursor=str(end) if end < len(snapshot.entries) else None,
                    entries=snapshot.entries[start:end],
                )
            ).decode()
        )
    raw = json.dumps(pages, ensure_ascii=False, separators=(",", ":")).encode()
    # JSON escaping adds overhead; entity bounds are checked again by the collector.
    if len(raw) > MAX_STAGED_BYTES * 2:
        raise ValueError("Облачный снимок слишком велик")
    _unpack(raw, snapshot.server_id)
    return raw


def _unpack(raw, server_id):
    if type(raw) is not bytes or len(raw) > MAX_STAGED_BYTES * 2:
        raise ValueError("Повреждено облачное состояние")
    pages = json.loads(raw)
    if type(pages) is not list or not 1 <= len(pages) <= 201:
        raise ValueError("Повреждены страницы облачного состояния")
    collector = SnapshotCollector(server_id)
    for page in pages:
        if type(page) is not str:
            raise ValueError("Повреждена страница облачного состояния")
        collector.add(decode_page(page))
    return collector.finish()


def _contents(snapshot):
    return sorted(
        (entry.revision, entry.delete_epoch, encode_entity(entry.entity))
        for entry in snapshot.entries
    )


def _key(entry):
    entity = entry.entity
    return (
        (entity.entity_kind, entity.entity_id)
        if type(entity) is RemovalRecord
        else target_identity(entity)
    )


def _check_advancement(before, after):
    previous = {_key(entry): entry for entry in before.entries}
    for entry in after.entries:
        old = previous.get(_key(entry))
        if old is None:
            continue
        if entry.revision < old.revision or entry.delete_epoch < old.delete_epoch:
            raise ValueError("Облачная сущность вернулась к прежней версии")
        if entry.revision == old.revision and (
            entry.delete_epoch != old.delete_epoch
            or encode_entity(entry.entity) != encode_entity(old.entity)
        ):
            raise ValueError("Противоречащие данные одной версии сущности")
        if (
            type(old.entity) is RemovalRecord
            and type(entry.entity) is not RemovalRecord
            and entry.delete_epoch <= old.delete_epoch
        ):
            raise ValueError("Возврат удалённой сущности требует нового поколения")


class SyncSnapshotMixin:
    def _read_sync_snapshot(self):
        binding = self.get_sync_binding()
        row = self._db.execute(
            "SELECT pages FROM sync_remote_snapshot WHERE context_id=1"
        ).fetchone()
        if row is None:
            return None
        if binding is None:
            raise ValueError("Облачное состояние не связано с аккаунтом")
        return _unpack(row[0], binding.server_id)

    def read_sync_snapshot(self):
        if self._db.in_transaction:
            raise ValueError("Чтение требует отдельную транзакцию")
        with self._db:
            self._db.execute("BEGIN")
            return self._read_sync_snapshot()

    def _remember_sync_snapshot(self, snapshot):
        binding = self.get_sync_binding()
        if (
            type(snapshot) is not Snapshot
            or binding is None
            or snapshot.server_id != binding.server_id
        ):
            raise ValueError("Снимок относится к другой привязке сервера")
        raw = _pack(snapshot)
        before = self._read_sync_snapshot()
        if before is not None:
            if snapshot.set_revision < before.set_revision:
                raise ValueError("Нельзя заменить облачное состояние более старым снимком")
            if snapshot.set_revision == before.set_revision and _contents(snapshot) != _contents(
                before
            ):
                raise ValueError("Противоречащие снимки одной ревизии")
            _check_advancement(before, snapshot)
        self._db.execute(
            "INSERT INTO sync_remote_snapshot VALUES (1,?) "
            "ON CONFLICT(context_id) DO UPDATE SET pages=excluded.pages",
            (raw,),
        )

    def remember_sync_snapshot(self, snapshot):
        """Cache an authenticated transport's complete snapshot, not an inbound merge."""
        if self._db.in_transaction:
            raise ValueError("Сохранение требует отдельную транзакцию")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._remember_sync_snapshot(snapshot)

    def save_sync_receipt_and_snapshot(self, receipt, snapshot):
        """Persist verified receipt, phase mappings and full remote knowledge atomically.

        A newer snapshot may supersede the operation (including cloud retention).
        It must cover the receipt watermark; it never overwrites local pending data.
        """
        if type(snapshot) is not Snapshot:
            raise ValueError("Нужен полный облачный снимок")
        if self._db.in_transaction:
            raise ValueError("Подтверждение требует отдельную транзакцию")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            frozen = self._save_sync_receipt(receipt)
            if receipt.status != "applied" or snapshot.set_revision < receipt.revision:
                raise ValueError("Снимок не покрывает подтверждённое изменение")
            self._remember_sync_snapshot(snapshot)
            return frozen
