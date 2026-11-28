"""Atomic local calendar facts, deferred until task identity dependencies settle."""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from ritm.integrations.sync_codec import BlockRecord, decode_entity, encode_entity
from ritm.storage.sync_intents import _stored_uuid

BLOCK_CAPTURE_OBJECTS = ("sync_block_changes",)
BLOCK_CAPTURE_SCHEMA = (
    "CREATE TABLE sync_block_changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "id TEXT NOT NULL UNIQUE, context_id INTEGER NOT NULL CHECK(context_id=1) "
    "REFERENCES sync_binding(id), after_intent_id TEXT REFERENCES sync_intents(id), "
    "after_regular_id TEXT, kind TEXT NOT NULL CHECK(kind IN ('save','delete','restore')), "
    "block_id TEXT NOT NULL, before_json TEXT, after_json TEXT, "
    "CHECK(before_json IS NOT NULL OR after_json IS NOT NULL))",
)


@dataclass(frozen=True, kw_only=True)
class BlockChange:
    id: UUID
    sequence: int
    kind: str
    block_id: UUID
    after_intent_id: UUID | None
    after_regular_id: UUID | None
    before: BlockRecord | None = field(repr=False)
    after: BlockRecord | None = field(repr=False)


class SyncBlockCaptureMixin:
    def _capture_block_change(self, before, after, *, kind="save"):
        if before == after or not self._sync_capture_active():
            return
        record = after if after is not None else before
        intent = self._db.execute(
            "SELECT id FROM sync_intents ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        regular = self._db.execute(
            "SELECT id FROM sync_regular_changes ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        self._db.execute(
            "INSERT INTO sync_block_changes "
            "(id,context_id,after_intent_id,after_regular_id,kind,block_id,before_json,after_json) "
            "VALUES (?,1,?,?,?,?,?,?)",
            (
                str(uuid4()),
                intent[0] if intent else None,
                regular[0] if regular else None,
                kind,
                str(record.block.id),
                encode_entity(before) if before else None,
                encode_entity(after) if after else None,
            ),
        )

    def _capture_task_blocks(self, task, *, restored=False):
        if not self._sync_capture_active():
            return
        # Do not use list_work_blocks: archived roots are deliberately hidden there.
        rows = self._db.execute(
            "SELECT * FROM work_blocks WHERE task_id=? ORDER BY starts_us,id", (str(task.id),)
        ).fetchall()
        for row in rows:
            record = BlockRecord(block=self._read_work_block(row))
            self._capture_block_change(
                None if restored else record,
                record if restored else None,
                kind="restore" if restored else "delete",
            )

    def list_sync_block_changes(self):
        changes = []
        for row in self._db.execute("SELECT * FROM sync_block_changes ORDER BY seq"):
            before = decode_entity(row["before_json"]) if row["before_json"] else None
            after = decode_entity(row["after_json"]) if row["after_json"] else None
            identifier = _stored_uuid(row["block_id"])
            if any(
                type(record) is not BlockRecord or record.block.id != identifier
                for record in (before, after)
                if record is not None
            ) or (row["kind"] == "delete") != (after is None):
                raise ValueError("Повреждён журнал изменений календаря")
            changes.append(
                BlockChange(
                    id=_stored_uuid(row["id"]),
                    sequence=row["seq"],
                    kind=row["kind"],
                    block_id=identifier,
                    after_intent_id=_stored_uuid(row["after_intent_id"])
                    if row["after_intent_id"]
                    else None,
                    after_regular_id=_stored_uuid(row["after_regular_id"])
                    if row["after_regular_id"]
                    else None,
                    before=before,
                    after=after,
                )
            )
        return tuple(changes)
