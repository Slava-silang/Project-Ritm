"""Durable local regular transitions; not prepared network operations."""

from datetime import date
from uuid import uuid4

from ritm.domain.calendar import from_instant_us
from ritm.integrations.sync_codec import (
    OccurrenceRecord,
    RegularRecord,
    TaskRecord,
    decode_entity,
    encode_entity,
    history_id,
)
from ritm.integrations.sync_regular_changes import RegularChange
from ritm.integrations.sync_regular_changes import validate_regular_change as _validate
from ritm.storage.sync_intents import _stored_uuid

REGULAR_CAPTURE_OBJECTS_V18 = ("sync_regular_changes",)
REGULAR_CAPTURE_SCHEMA_V18 = (
    "CREATE TABLE sync_regular_changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "id TEXT NOT NULL UNIQUE, context_id INTEGER NOT NULL CHECK(context_id=1) "
    "REFERENCES sync_binding(id), after_intent_id TEXT REFERENCES sync_intents(id), "
    "kind TEXT NOT NULL CHECK(kind IN ('save','rollover','stop','delete')), "
    "rule_id TEXT NOT NULL, root_id TEXT NOT NULL, before_json TEXT, after_json TEXT)",
)


REGULAR_CAPTURE_OBJECTS = ("sync_regular_changes", "sync_regular_roots")
REGULAR_CAPTURE_SCHEMA = (
    REGULAR_CAPTURE_SCHEMA_V18[0]
    .replace(
        "'stop','delete'",
        "'stop','delete','restore','history','detach','root_save','root_restore','root_delete'",
    )
    .replace("after_json TEXT)", "after_json TEXT, occurrence_day TEXT)"),
    "CREATE TABLE sync_regular_roots (root_id TEXT PRIMARY KEY NOT NULL, "
    "context_id INTEGER NOT NULL CHECK(context_id=1) REFERENCES sync_binding(id), "
    "rule_id TEXT NOT NULL, day TEXT NOT NULL)",
)


def migrate_regular_capture(db):
    db.execute("ALTER TABLE sync_regular_changes RENAME TO sync_regular_changes_v18")
    for statement in REGULAR_CAPTURE_SCHEMA:
        db.execute(statement)
    db.execute(
        "INSERT INTO sync_regular_changes "
        "(seq,id,context_id,after_intent_id,kind,rule_id,root_id,before_json,after_json) "
        "SELECT seq,id,context_id,after_intent_id,kind,rule_id,root_id,before_json,after_json "
        "FROM sync_regular_changes_v18"
    )
    # Recover lineage for stopped/deleted roots whose membership was intentionally removed.
    for row in db.execute("SELECT * FROM sync_regular_changes ORDER BY seq").fetchall():
        for key in ("before_json", "after_json"):
            record = decode_entity(row[key]) if row[key] is not None else None
            if type(record) is RegularRecord:
                db.execute(
                    "INSERT INTO sync_regular_roots VALUES (?,1,?,?) "
                    "ON CONFLICT(root_id) DO UPDATE SET rule_id=excluded.rule_id,day=excluded.day",
                    (str(record.current.task.id), str(record.id), record.current_day.isoformat()),
                )
    db.execute("DROP TABLE sync_regular_changes_v18")


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

    def _remember_regular_root(self, root, rule, day):
        self._db.execute(
            "INSERT INTO sync_regular_roots VALUES (?,1,?,?) "
            "ON CONFLICT(root_id) DO UPDATE SET rule_id=excluded.rule_id,day=excluded.day",
            (str(root), str(rule), day.isoformat()),
        )

    def _regular_lineage(self, root):
        row = self._db.execute(
            "SELECT rule_id,day FROM sync_regular_roots WHERE root_id=?", (str(root),)
        ).fetchone()
        if row is None:
            row = self._db.execute(
                "SELECT rule_id,day FROM recurrence_occurrences WHERE task_id=?", (str(root),)
            ).fetchone()
        return (_stored_uuid(row[0]), date.fromisoformat(row[1])) if row else None

    def _regular_source_record(self, task):
        if task is None:
            return None
        current = self._regular_sync_record(task)
        if current is not None:
            return current
        slot = self._db.execute(
            "SELECT rule_id,day FROM recurrence_occurrences WHERE task_id=?", (str(task.id),)
        ).fetchone()
        if slot is not None and task.completed and task.deadline is None:
            rule, day = _stored_uuid(slot[0]), date.fromisoformat(slot[1])
            return OccurrenceRecord(
                id=history_id(rule, day),
                rule_id=rule,
                day=day,
                current=self._sync_task_record(task),
            )
        return self._sync_task_record(task)

    def _capture_history_write(self, before, task, *, restored):
        lineage = self._regular_lineage(task.id)
        if lineage is None:
            return False
        if before is None and not restored:
            raise ValueError("Повтор можно вернуть только явным восстановлением")
        self._remember_regular_root(task.id, *lineage)
        if type(before) is TaskRecord or (
            type(before) is OccurrenceRecord and (not task.completed or task.deadline is not None)
        ):
            self._db.execute("DELETE FROM recurrence_occurrences WHERE task_id=?", (str(task.id),))
        after = self._regular_source_record(task)
        kind = (
            "root_restore"
            if restored
            else (
                "history"
                if type(after) is OccurrenceRecord
                else ("detach" if type(before) is OccurrenceRecord else "root_save")
            )
        )
        self._record_regular_change(kind, None if restored else before, after, lineage=lineage)
        return True

    def _record_regular_change(self, kind, before, after, *, lineage=None):
        if not self._sync_capture_active() or before == after:
            return
        record = after if after is not None else before
        root = (
            record.current.task.id
            if type(record) in (RegularRecord, OccurrenceRecord)
            else record.task.id
        )
        if lineage is None:
            regular = after if type(after) is RegularRecord else before
            rule, day = regular.id, None
            for candidate in (before, after):
                if type(candidate) is RegularRecord:
                    self._remember_regular_root(
                        candidate.current.task.id, candidate.id, candidate.current_day
                    )
        else:
            rule, day = lineage
        anchor = self._db.execute(
            "SELECT id FROM sync_intents ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        change = RegularChange(
            sequence=0,
            id=uuid4(),
            after_intent_id=_stored_uuid(anchor[0]) if anchor else None,
            kind=kind,
            rule_id=rule,
            day=day,
            root_id=root,
            before=None if kind in ("delete", "root_delete") else before,
            after=None if kind == "root_delete" else after,
        )
        _validate(change)
        self._db.execute(
            "INSERT INTO sync_regular_changes "
            "(id,context_id,after_intent_id,kind,rule_id,root_id,"
            "before_json,after_json,occurrence_day) "
            "VALUES (?,1,?,?,?,?,?,?,?)",
            (
                str(change.id),
                str(change.after_intent_id) if change.after_intent_id else None,
                kind,
                str(change.rule_id),
                str(change.root_id),
                encode_entity(change.before).decode() if change.before else None,
                encode_entity(change.after).decode() if change.after else None,
                day.isoformat() if day is not None else None,
            ),
        )

    def _read_sync_regular_changes(self, binding):
        result = []
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
                day=date.fromisoformat(row["occurrence_day"])
                if row["occurrence_day"] is not None
                else None,
                before=decode_entity(row["before_json"])
                if row["before_json"] is not None
                else None,
                after=decode_entity(row["after_json"]) if row["after_json"] is not None else None,
            )
            _validate(change)
            result.append(change)
        return tuple(result)

    def list_sync_regular_changes(self):
        """Read immutable local facts. Never freeze or send these as Operation DTOs."""
        if self._db.in_transaction:
            raise ValueError("Журнал повторов нельзя читать внутри другой транзакции")
        with self._db:
            self._db.execute("BEGIN")
            return self._read_sync_regular_changes(self.get_sync_binding())

    def prepare_sync_history(self, context):
        """Read binding/facts in one snapshot, then prepare pure history proposals."""
        from ritm.integrations.sync_history_plan import plan_history

        if self._db.in_transaction:
            raise ValueError("Историю нельзя готовить внутри другой транзакции")
        with self._db:
            self._db.execute("BEGIN")
            binding = self.get_sync_binding()
            if binding is None:
                raise ValueError("Синхронизация не подключена")
            changes = self._read_sync_regular_changes(binding)
        return plan_history(changes, context, server_id=binding.server_id)

    def prepare_sync_regular(self, context):
        """Read-only next-step regular proposal, never enqueue or consume facts."""
        from ritm.integrations.sync_regular_plan import plan_regular

        if self._db.in_transaction:
            raise ValueError("Повторы нельзя готовить внутри другой транзакции")
        with self._db:
            self._db.execute("BEGIN")
            binding = self.get_sync_binding()
            if binding is None:
                raise ValueError("Синхронизация не подключена")
            changes = self._read_sync_regular_changes(binding)
        return plan_regular(changes, context, server_id=binding.server_id)
