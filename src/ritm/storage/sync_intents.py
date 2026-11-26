"""Durable unsent intents. No credentials, frozen operation, transport or inbox."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from ritm.domain.tasks import Task
from ritm.integrations.sync_codec import Entity, TaskRecord, decode_entity, encode_entity
from ritm.integrations.sync_operations import PAYLOADS, target_identity

SYNC_OBJECTS = ("sync_binding", "sync_intents", "sync_intents_target")
SYNC_SCHEMA = (
    "CREATE TABLE sync_binding (id INTEGER PRIMARY KEY CHECK(id=1), "
    "owner_id TEXT NOT NULL, device_id TEXT NOT NULL, server_id TEXT NOT NULL)",
    "CREATE TABLE sync_intents (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "id TEXT NOT NULL UNIQUE, context_id INTEGER NOT NULL CHECK(context_id=1) "
    "REFERENCES sync_binding(id), depends_on TEXT REFERENCES sync_intents(id), "
    "target_kind TEXT NOT NULL, target_id TEXT NOT NULL, action TEXT NOT NULL, "
    "desired_json TEXT)",
    "CREATE INDEX sync_intents_target ON sync_intents(target_kind,target_id,seq)",
)


@dataclass(frozen=True, kw_only=True)
class SyncBinding:
    owner_id: UUID
    device_id: UUID
    server_id: UUID


@dataclass(frozen=True, kw_only=True)
class SyncIntent:
    id: UUID
    target_kind: str
    target_id: UUID
    action: str
    payload: Entity | None = field(repr=False)
    depends_on: UUID | None = None


@dataclass(frozen=True, kw_only=True)
class QueuedIntent:
    sequence: int
    intent: SyncIntent = field(repr=False)


def _uuid(value):
    if type(value) is not UUID:
        raise ValueError("Некорректные идентификаторы синхронизации")
    return str(value)


def _stored_uuid(value):
    if type(value) is not str:
        raise ValueError("Повреждены служебные данные синхронизации")
    result = UUID(value)
    if str(result) != value:
        raise ValueError("Повреждены служебные данные синхронизации")
    return result


def _binding(binding):
    if type(binding) is not SyncBinding:
        raise ValueError("Некорректная привязка синхронизации")
    return tuple(_uuid(value) for value in (binding.owner_id, binding.device_id, binding.server_id))


def _intent(intent):
    if type(intent) is not SyncIntent:
        raise ValueError("Некорректное намерение синхронизации")
    identifier, target = _uuid(intent.id), _uuid(intent.target_id)
    dependency = _uuid(intent.depends_on) if intent.depends_on is not None else None
    if identifier == dependency:
        raise ValueError("Намерение не может зависеть от себя")
    if (
        type(intent.target_kind) is not str
        or intent.target_kind not in PAYLOADS
        or type(intent.action) is not str
        or intent.action
        not in ("create", "put", "restore", "delete", "prune", "stop_regular", "rollover")
    ):
        raise ValueError("Неизвестное действие синхронизации")
    if intent.target_kind == "calendar_settings" and intent.action not in ("create", "put"):
        raise ValueError("Общую настройку нельзя удалить этим действием")
    if intent.action in ("stop_regular", "rollover") and intent.target_kind != "regular":
        raise ValueError("Действие требует регулярное расписание")
    if intent.action == "prune" and intent.target_kind not in ("task", "regular", "occurrence"):
        raise ValueError("Действие требует дерево задачи")
    payload = None
    if intent.action in ("delete", "stop_regular"):
        if intent.payload is not None:
            raise ValueError("Намерение удаления не должно содержать прежний текст")
    else:
        if type(intent.payload) is not PAYLOADS[intent.target_kind]:
            raise ValueError("Тип данных не соответствует намерению")
        payload = encode_entity(intent.payload).decode("utf-8")
        if target_identity(intent.payload) != (intent.target_kind, intent.target_id):
            raise ValueError("Идентификатор данных не соответствует намерению")
    return (identifier, dependency, intent.target_kind, target, intent.action, payload)


class SyncIntentMixin:
    def get_sync_binding(self) -> SyncBinding | None:
        row = self._db.execute(
            "SELECT owner_id,device_id,server_id FROM sync_binding WHERE id=1"
        ).fetchone()
        if row is None:
            return None
        return SyncBinding(
            owner_id=_stored_uuid(row["owner_id"]),
            device_id=_stored_uuid(row["device_id"]),
            server_id=_stored_uuid(row["server_id"]),
        )

    def initialize_sync_binding(self, binding: SyncBinding):
        values = _binding(binding)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            previous = self.get_sync_binding()
            if previous is not None:
                if previous != binding:
                    raise ValueError("Сначала явно отключите прежний аккаунт или поколение сервера")
                return
            self._db.execute("INSERT INTO sync_binding VALUES (1,?,?,?)", values)

    def _enqueue_sync_intent(self, intent: SyncIntent) -> int:
        values = _intent(intent)
        if self.get_sync_binding() is None:
            raise ValueError("Синхронизация не подключена")
        existing = self._db.execute(
            "SELECT * FROM sync_intents WHERE id=?", (values[0],)
        ).fetchone()
        if existing is not None:
            previous = tuple(
                existing[key]
                for key in (
                    "id",
                    "depends_on",
                    "target_kind",
                    "target_id",
                    "action",
                    "desired_json",
                )
            )
            if previous != values:
                raise ValueError("Идентификатор намерения уже используется с другими данными")
            return existing["seq"]
        if (
            intent.depends_on is not None
            and self._db.execute("SELECT 1 FROM sync_intents WHERE id=?", (values[1],)).fetchone()
            is None
        ):
            raise ValueError("Предшествующее намерение не найдено")
        cursor = self._db.execute(
            "INSERT INTO sync_intents(id,context_id,depends_on,target_kind,"
            "target_id,action,desired_json) "
            "VALUES (?,1,?,?,?,?,?)",
            values,
        )
        return cursor.lastrowid

    def enqueue_sync_intent(self, intent: SyncIntent) -> int:
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            return self._enqueue_sync_intent(intent)

    def list_sync_intents(self) -> tuple[QueuedIntent, ...]:
        with self._db:
            self._db.execute("BEGIN")
            binding = self.get_sync_binding()
            result = []
            seen = set()
            for row in self._db.execute("SELECT * FROM sync_intents ORDER BY seq"):
                intent = SyncIntent(
                    id=_stored_uuid(row["id"]),
                    depends_on=_stored_uuid(row["depends_on"])
                    if row["depends_on"] is not None
                    else None,
                    target_kind=row["target_kind"],
                    target_id=_stored_uuid(row["target_id"]),
                    action=row["action"],
                    payload=decode_entity(row["desired_json"])
                    if row["desired_json"] is not None
                    else None,
                )
                _intent(intent)
                if (
                    binding is None
                    or row["context_id"] != 1
                    or (intent.depends_on is not None and intent.depends_on not in seen)
                ):
                    raise ValueError("Нарушены зависимости очереди синхронизации")
                seen.add(intent.id)
                result.append(QueuedIntent(sequence=row["seq"], intent=intent))
            return tuple(result)

    def update_task_and_enqueue(self, identifier: UUID, transform, make_intent) -> Task:
        """Supported ordinary-root edit + unsent intent in one existing SQLite transaction."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self.get_sync_binding() is None:
                raise ValueError("Синхронизация не подключена")
            if self._db.execute(
                "SELECT 1 FROM recurrence_occurrences WHERE task_id=?", (str(identifier),)
            ).fetchone():
                raise ValueError("Для повторов требуется отдельный адаптер истории")
            before = self.get_task(identifier)
            if before is None:
                raise KeyError(identifier)
            updated = transform(before)
            if type(updated) is not Task or updated.id != identifier:
                raise ValueError("Преобразование должно сохранить задачу и её ID")
            self._write_task(updated)
            record = TaskRecord(
                task=updated, completed_at=self.list_completion_dates().get(identifier)
            )
            intent = make_intent(record)
            _intent(intent)
            if (
                intent.target_kind != "task"
                or intent.target_id != identifier
                or intent.action not in ("put", "prune")
                or intent.payload != record
            ):
                raise ValueError("Намерение не соответствует сохранённой задаче")
            # Retrying this combined local edit is not a network retry: never replay the transform.
            if self._db.execute(
                "SELECT 1 FROM sync_intents WHERE id=?", (str(intent.id),)
            ).fetchone():
                raise ValueError("Локальное изменение с таким намерением уже записано")
            self._enqueue_sync_intent(intent)
            return updated


def sanitize_sync_backup(path: Path):
    """Only a validated staged copy: erase queue/context and compact freed pages."""
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA secure_delete=ON")
        db.execute("DELETE FROM sync_intents")
        db.execute("DELETE FROM sync_binding")
        db.execute("DELETE FROM sqlite_sequence WHERE name='sync_intents'")
        db.commit()
        db.execute("VACUUM")
