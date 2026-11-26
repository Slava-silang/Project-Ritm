"""Durable local regular transitions; not prepared network operations."""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID, uuid4

from ritm.domain.calendar import from_instant_us
from ritm.integrations.sync_codec import RegularRecord, TaskRecord, decode_entity, encode_entity
from ritm.storage.sync_intents import _stored_uuid

REGULAR_CAPTURE_OBJECTS = ("sync_regular_changes",)
REGULAR_CAPTURE_SCHEMA = (
    "CREATE TABLE sync_regular_changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "id TEXT NOT NULL UNIQUE, context_id INTEGER NOT NULL CHECK(context_id=1) "
    "REFERENCES sync_binding(id), after_intent_id TEXT REFERENCES sync_intents(id), "
    "kind TEXT NOT NULL CHECK(kind IN ('save','rollover','stop','delete')), "
    "rule_id TEXT NOT NULL, root_id TEXT NOT NULL, before_json TEXT, after_json TEXT)",
)


@dataclass(frozen=True, kw_only=True)
class RegularChange:
    sequence: int
    id: UUID
    after_intent_id: UUID | None
    kind: str
    rule_id: UUID
    root_id: UUID
    before: RegularRecord | TaskRecord | None = field(repr=False)
    after: RegularRecord | TaskRecord | None = field(repr=False)


def _validate(change):
    before, after = change.before, change.after
    if change.kind == "delete":
        valid = before is None and after is None
    elif change.kind == "stop":
        valid = type(before) is RegularRecord and type(after) is TaskRecord
        valid = valid and before.id == change.rule_id and after.task.id == change.root_id
        valid = valid and before.current.task.id == change.root_id
    elif change.kind == "rollover":
        valid = type(before) is RegularRecord and type(after) is RegularRecord
        valid = valid and before.id == after.id == change.rule_id
        valid = valid and before.current_day < after.current_day
        valid = valid and before.current.task.id != after.current.task.id
        valid = valid and after.current.task.id == change.root_id
    elif change.kind == "save":
        valid = type(after) is RegularRecord and type(before) in (
            type(None),
            TaskRecord,
            RegularRecord,
        )
        valid = valid and after.id == change.rule_id and after.current.task.id == change.root_id
        if valid and before is not None:
            root = before.current.task.id if type(before) is RegularRecord else before.task.id
            valid = root == change.root_id
    else:
        valid = False
    if not valid:
        raise ValueError("Повреждён переход регулярной задачи")


class SyncRegularCaptureMixin:
    def _sync_task_record(self, task):
        row = self._db.execute(
            "SELECT completed_us FROM task_completion_dates WHERE task_id=?", (str(task.id),)
        ).fetchone()
        return TaskRecord(task=task, completed_at=from_instant_us(row[0]) if row else None)

    def _regular_sync_record(self, task):
        if task is None:
            return None
        row = self._db.execute(
            "SELECT * FROM recurrence_rules WHERE task_id=?", (str(task.id),)
        ).fetchone()
        if row is None:
            return None
        return RegularRecord(
            id=_stored_uuid(row["id"]),
            first_day=date.fromisoformat(row["first_day"]),
            interval_days=row["interval_days"],
            current_day=date.fromisoformat(row["current_day"]),
            current=self._sync_task_record(task),
        )

    def _record_regular_change(self, kind, before, after):
        if not self._sync_capture_active() or before == after:
            return
        regular = after if type(after) is RegularRecord else before
        root = after.current.task.id if type(after) is RegularRecord else regular.current.task.id
        anchor = self._db.execute(
            "SELECT id FROM sync_intents ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        change = RegularChange(
            sequence=0,
            id=uuid4(),
            after_intent_id=_stored_uuid(anchor[0]) if anchor else None,
            kind=kind,
            rule_id=regular.id,
            root_id=root,
            before=None if kind == "delete" else before,
            after=after,
        )
        _validate(change)
        self._db.execute(
            "INSERT INTO sync_regular_changes "
            "(id,context_id,after_intent_id,kind,rule_id,root_id,before_json,after_json) "
            "VALUES (?,1,?,?,?,?,?,?)",
            (
                str(change.id),
                str(change.after_intent_id) if change.after_intent_id else None,
                kind,
                str(change.rule_id),
                str(change.root_id),
                encode_entity(change.before).decode() if change.before else None,
                encode_entity(change.after).decode() if change.after else None,
            ),
        )

    def list_sync_regular_changes(self):
        """Read immutable local facts. Never freeze or send these as Operation DTOs."""
        if self._db.in_transaction:
            raise ValueError("Журнал повторов нельзя читать внутри другой транзакции")
        result = []
        with self._db:
            self._db.execute("BEGIN")
            binding = self.get_sync_binding()
            for row in self._db.execute("SELECT * FROM sync_regular_changes ORDER BY seq"):
                if binding is None or row["context_id"] != 1:
                    raise ValueError("Повреждён контекст переходов регулярных задач")
                change = RegularChange(
                    sequence=row["seq"],
                    id=_stored_uuid(row["id"]),
                    after_intent_id=_stored_uuid(row["after_intent_id"])
                    if row["after_intent_id"]
                    else None,
                    kind=row["kind"],
                    rule_id=_stored_uuid(row["rule_id"]),
                    root_id=_stored_uuid(row["root_id"]),
                    before=decode_entity(row["before_json"])
                    if row["before_json"] is not None
                    else None,
                    after=decode_entity(row["after_json"])
                    if row["after_json"] is not None
                    else None,
                )
                _validate(change)
                result.append(change)
        return tuple(result)
