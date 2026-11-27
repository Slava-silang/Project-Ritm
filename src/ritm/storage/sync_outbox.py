"""Immutable outbound requests and bound receipts. No transport or incoming application."""

from dataclasses import dataclass, field
from uuid import UUID

from ritm.integrations._sync_wire import _number_text
from ritm.integrations.sync_operations import (
    Operation,
    Receipt,
    decode_operation,
    decode_receipt,
    encode_operation,
    encode_receipt,
    operation_hash,
    verify_receipt,
)
from ritm.storage.sync_intents import _read_intent, _uuid

OUTBOX_OBJECTS = ("sync_outbox",)
OUTBOX_SCHEMA = (
    "CREATE TABLE sync_outbox (intent_id TEXT PRIMARY KEY NOT NULL REFERENCES sync_intents(id), "
    "request BLOB NOT NULL CHECK(typeof(request)='blob'), request_hash TEXT NOT NULL, "
    "receipt BLOB CHECK(receipt IS NULL OR typeof(receipt)='blob'))",
)


@dataclass(frozen=True, kw_only=True)
class SyncBase:
    server_id: UUID
    target_kind: str
    target_id: UUID
    revision: int
    delete_epoch: int


@dataclass(frozen=True, kw_only=True)
class FrozenRequest:
    sequence: int
    intent_id: UUID
    operation: Operation = field(repr=False)
    raw: bytes = field(repr=False)
    receipt: Receipt | None = None
    receipt_bytes: bytes | None = field(default=None, repr=False)


def _validate_base(base, binding, intent):
    if type(base) is not SyncBase:
        raise ValueError("Нужно известное основание изменения")
    _uuid(base.server_id)
    _uuid(base.target_id)
    _number_text(base.revision)
    _number_text(base.delete_epoch)
    if (
        base.server_id != binding.server_id
        or base.target_kind != intent.target_kind
        or base.target_id != intent.target_id
    ):
        raise ValueError("Основание относится к другому серверу или объекту")


class SyncOutboxMixin:
    def _sync_intent_row(self, identifier):
        row = self._db.execute(
            "SELECT * FROM sync_intents WHERE id=?", (_uuid(identifier),)
        ).fetchone()
        if row is None:
            raise KeyError("Намерение не найдено")
        intent = _read_intent(row)
        if row["context_id"] != 1:
            raise ValueError("Неверная привязка очереди")
        if intent.depends_on is not None:
            parent = self._db.execute(
                "SELECT seq FROM sync_intents WHERE id=?", (str(intent.depends_on),)
            ).fetchone()
            if parent is None or parent["seq"] >= row["seq"]:
                raise ValueError("Нарушены зависимости очереди")
        return row, intent

    def _frozen_sync_request(self, identifier):
        queued, intent = self._sync_intent_row(identifier)
        binding = self.get_sync_binding()
        if binding is None:
            raise ValueError("Синхронизация не подключена")
        row = self._db.execute(
            "SELECT * FROM sync_outbox WHERE intent_id=?", (str(identifier),)
        ).fetchone()
        if row is None:
            return None
        raw = row["request"]
        if type(raw) is not bytes:
            raise ValueError("Повреждён сохранённый запрос")
        operation = decode_operation(raw)
        if (
            encode_operation(operation) != raw
            or operation_hash(operation) != row["request_hash"]
            or (operation.op_id, operation.device_id, operation.server_id)
            != (intent.id, binding.device_id, binding.server_id)
            or (operation.target_kind, operation.target_id, operation.action, operation.payload)
            != (intent.target_kind, intent.target_id, intent.action, intent.payload)
        ):
            raise ValueError("Сохранённый запрос не соответствует очереди")
        receipt = None
        receipt_bytes = row["receipt"]
        if receipt_bytes is not None:
            if type(receipt_bytes) is not bytes:
                raise ValueError("Повреждено сохранённое подтверждение")
            receipt = decode_receipt(receipt_bytes)
            if encode_receipt(receipt) != receipt_bytes:
                raise ValueError("Повреждено сохранённое подтверждение")
            verify_receipt(operation, receipt)
        return FrozenRequest(
            sequence=queued["seq"],
            intent_id=intent.id,
            operation=operation,
            raw=raw,
            receipt=receipt,
            receipt_bytes=receipt_bytes,
        )

    def _sync_dependency_base(self, queued, intent):
        latest = self._db.execute(
            "SELECT id FROM sync_intents WHERE seq<? AND target_kind=? AND target_id=? "
            "ORDER BY seq DESC LIMIT 1",
            (queued["seq"], intent.target_kind, str(intent.target_id)),
        ).fetchone()
        required = latest["id"] if latest is not None else None
        found = False
        derived = None
        parent_id = intent.depends_on
        previous_seq = queued["seq"]
        while parent_id is not None:
            parent_row, parent = self._sync_intent_row(parent_id)
            if parent_row["seq"] >= previous_seq:
                raise ValueError("Нарушен порядок зависимостей")
            ack = self._frozen_sync_request(parent_id)
            if ack is None or ack.receipt is None:
                raise ValueError("Дождитесь подтверждения предыдущего изменения")
            if ack.receipt.status != "applied":
                raise ValueError("Предыдущее изменение отклонено; требуется разбор конфликта")
            if str(parent_id) == required:
                found = True
                derived = SyncBase(
                    server_id=ack.receipt.server_id,
                    target_kind=intent.target_kind,
                    target_id=intent.target_id,
                    revision=ack.receipt.revision,
                    delete_epoch=ack.receipt.delete_epoch,
                )
            previous_seq = parent_row["seq"]
            parent_id = parent.depends_on
        if required is not None and not found:
            raise ValueError("Нужно связать намерение с предыдущим изменением этого объекта")
        return derived

    def freeze_sync_intent(self, identifier: UUID, base: SyncBase | None = None) -> FrozenRequest:
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            return self._freeze_sync_intent(identifier, base)

    def _freeze_sync_intent(self, identifier, base=None):
        queued, intent = self._sync_intent_row(identifier)
        binding = self.get_sync_binding()
        if binding is None:
            raise ValueError("Синхронизация не подключена")
        existing = self._frozen_sync_request(identifier)
        if existing is not None:
            if base is not None:
                _validate_base(base, binding, intent)
                if (base.revision, base.delete_epoch) != (
                    existing.operation.base_revision,
                    existing.operation.delete_epoch,
                ):
                    raise ValueError("Основание уже отправляемого запроса нельзя менять")
            return existing
        derived = self._sync_dependency_base(queued, intent)
        if derived is not None:
            if base is not None and base != derived:
                raise ValueError("Основание не совпадает с подтверждённым предшественником")
            base = derived
        if base is None and intent.action == "create":
            base = SyncBase(
                server_id=binding.server_id,
                target_kind=intent.target_kind,
                target_id=intent.target_id,
                revision=0,
                delete_epoch=0,
            )
        _validate_base(base, binding, intent)
        operation = Operation(
            op_id=intent.id,
            device_id=binding.device_id,
            server_id=binding.server_id,
            target_kind=intent.target_kind,
            target_id=intent.target_id,
            base_revision=base.revision,
            delete_epoch=base.delete_epoch,
            action=intent.action,
            payload=intent.payload,
        )
        raw = encode_operation(operation)
        self._db.execute(
            "INSERT INTO sync_outbox(intent_id,request,request_hash,receipt) VALUES (?,?,?,NULL)",
            (str(intent.id), raw, operation_hash(operation)),
        )
        return self._frozen_sync_request(identifier)

    def get_frozen_sync_request(self, identifier: UUID) -> FrozenRequest | None:
        with self._db:
            self._db.execute("BEGIN")
            return self._frozen_sync_request(identifier)

    def list_frozen_sync_requests(self) -> tuple[FrozenRequest, ...]:
        with self._db:
            self._db.execute("BEGIN")
            ids = tuple(
                UUID(row[0])
                for row in self._db.execute(
                    "SELECT q.id FROM sync_intents q JOIN sync_outbox o ON o.intent_id=q.id "
                    "ORDER BY q.seq"
                )
            )
            return tuple(self._frozen_sync_request(identifier) for identifier in ids)

    def save_sync_receipt(self, receipt: Receipt) -> FrozenRequest:
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            return self._save_sync_receipt(receipt)

    def _save_sync_receipt(self, receipt):
        raw = encode_receipt(receipt)
        frozen = self._frozen_sync_request(receipt.op_id)
        if frozen is None:
            raise ValueError("Запрос ещё не подготовлен")
        verify_receipt(frozen.operation, receipt)
        if frozen.receipt_bytes is not None:
            if frozen.receipt_bytes != raw:
                raise ValueError("Противоречащие подтверждения одного запроса")
            return frozen
        self._db.execute(
            "UPDATE sync_outbox SET receipt=? WHERE intent_id=? AND receipt IS NULL",
            (raw, str(receipt.op_id)),
        )
        self._accept_sync_prepared(frozen, receipt)
        return self._frozen_sync_request(receipt.op_id)

    def next_sync_sendable(self) -> FrozenRequest | None:
        """FIFO candidate only; unresolved regular facts also block transport."""
        with self._db:
            self._db.execute("BEGIN")
            if self._db.execute("SELECT 1 FROM sync_regular_changes LIMIT 1").fetchone():
                return None
            ids = tuple(
                UUID(row[0]) for row in self._db.execute("SELECT id FROM sync_intents ORDER BY seq")
            )
            for identifier in ids:
                frozen = self._frozen_sync_request(identifier)
                if frozen is None:
                    return None
                if frozen.receipt is None:
                    return frozen
                if frozen.receipt.status != "applied":
                    return None
            return None
