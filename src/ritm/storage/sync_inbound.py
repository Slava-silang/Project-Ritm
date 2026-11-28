"""Coherent ordinary inbound writes and a separate applied baseline. No transport."""

from dataclasses import dataclass, field, replace

from ritm.domain.calendar import instant_us
from ritm.integrations.sync_codec import (
    BlockRecord,
    CalendarRecord,
    CategoryRecord,
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    TaskRecord,
)
from ritm.integrations.sync_inbound import InboundPlan, _protected, plan_inbound
from ritm.integrations.sync_regular_changes import mapped_task_record, task_node_ids
from ritm.storage.codec import decode_task
from ritm.storage.sync_intents import _stored_uuid
from ritm.storage.sync_snapshot import _contents, _pack, _unpack

INBOUND_OBJECTS = ("sync_applied_snapshot",)
INBOUND_SCHEMA = (
    "CREATE TABLE sync_applied_snapshot (context_id INTEGER PRIMARY KEY CHECK(context_id=1) "
    "REFERENCES sync_binding(id), pages BLOB NOT NULL CHECK(typeof(pages)='blob'))",
)


@dataclass(frozen=True, kw_only=True)
class InboundApplyResult:
    status: str
    plan: InboundPlan = field(repr=False)
    reasons: tuple[str, ...] = ()
    changed: int = 0


def _nodes(task):
    return (task, *task.subtasks, *(i for c in task.subtasks for i in c.items))


class SyncInboundMixin:
    def _read_sync_applied_snapshot(self):
        row = self._db.execute(
            "SELECT pages FROM sync_applied_snapshot WHERE context_id=1"
        ).fetchone()
        if row is None:
            return None
        binding = self.get_sync_binding()
        if binding is None:
            raise ValueError("Применённый снимок потерял привязку")
        return _unpack(row[0], binding.server_id)

    def read_sync_applied_snapshot(self):
        if self._db.in_transaction:
            raise ValueError("Чтение требует отдельную транзакцию")
        with self._db:
            self._db.execute("BEGIN")
            return self._read_sync_applied_snapshot()

    def _inbound_context(self, incoming, protected_inputs=frozenset()):
        binding = self.get_sync_binding()
        if binding is None or not self.is_sync_task_capture_enabled():
            raise ValueError("Нужно явное подключение синхронизации")
        _pack(incoming)  # Full bounded validation, before inspecting caller-controlled records.
        if incoming.server_id != binding.server_id:
            raise ValueError("Снимок относится к другому серверу")
        applied = self._read_sync_applied_snapshot()
        identities = dict(self._read_sync_prepared_state().identities)
        inverse = {b: a for a, b in identities.items()}
        settings = next(
            (e.entity for e in (applied or incoming).entries if type(e.entity) is CalendarRecord),
            None,
        )
        if settings is None:
            raise ValueError("В снимке нет общего часового пояса")
        local = [settings, *(CategoryRecord(category=c) for c in self.list_categories())]
        hidden = set()
        for row in self._db.execute("SELECT id FROM nodes WHERE kind=0 ORDER BY id").fetchall():
            identifier = _stored_uuid(row[0])
            if self.is_skipped_occurrence(identifier):
                hidden.add(identities.get(identifier, identifier))
                continue
            task = self.get_task(identifier)
            record = self._regular_source_record(task)
            source = record.current if type(record) in (RegularRecord, OccurrenceRecord) else record
            mapping = {i: identities.get(i, i) for i in task_node_ids(task)}
            mapped = mapped_task_record(source, mapping)
            local.append(
                replace(record, current=mapped)
                if type(record) in (RegularRecord, OccurrenceRecord)
                else mapped
            )
        old_blocks = (
            {e.entity.block.id: e.entity for e in applied.entries if type(e.entity) is BlockRecord}
            if applied
            else {}
        )
        for block in self.list_work_blocks():
            root = identities.get(block.task_id, block.task_id)
            if root in hidden:
                continue
            previous = old_blocks.get(block.id)
            caption = (
                previous.archived_task_title
                if previous and root == previous.block.task_id
                else None
            )
            local.append(
                BlockRecord(block=replace(block, task_id=root), archived_task_title=caption)
            )
        current = {}
        from ritm.integrations.sync_operations import target_identity

        for record in local:
            current[target_identity(record)] = record
        protected = {("task", root) for root in hidden}
        for kind, identifier in _protected(protected_inputs):
            protected.add(
                (
                    kind,
                    identities.get(identifier, identifier)
                    if kind in ("task", "node")
                    else identifier,
                )
            )
        latest = {}
        for row in self._db.execute("SELECT id FROM sync_intents ORDER BY seq").fetchall():
            _, intent = self._sync_intent_row(_stored_uuid(row[0]))
            target = (
                identities.get(intent.target_id, intent.target_id)
                if intent.target_kind == "task"
                else intent.target_id
            )
            key = (intent.target_kind, target)
            frozen = self._frozen_sync_request(intent.id)
            latest[key] = frozen
            if frozen is None or frozen.receipt is None or frozen.receipt.status != "applied":
                protected.add(key)
            elif incoming.set_revision < frozen.receipt.revision:
                protected.add(key)
        acknowledged = set()
        remote = {
            (e.entity.entity_kind, e.entity.entity_id)
            if type(e.entity) is RemovalRecord
            else target_identity(e.entity): e
            for e in incoming.entries
        }
        for key, frozen in latest.items():
            if key in protected or frozen is None or frozen.receipt is None:
                continue
            record = current.get(key)
            if record is not None and record == frozen.operation.payload:
                acknowledged.add(key)
            elif record is None and frozen.operation.action == "delete":
                entry = remote.get(key)
                if entry is None or (
                    type(entry.entity) is not RemovalRecord
                    and entry.delete_epoch <= frozen.receipt.delete_epoch
                ):
                    protected.add(key)
                else:
                    acknowledged.add(key)
        for change in self._read_sync_regular_changes(binding):
            protected.add(("regular", change.rule_id))
            protected.add(("task", identities.get(change.root_id, change.root_id)))
        for change in self.list_sync_block_changes():
            protected.add(("block", change.block_id))
        # A local trash root is not an empty slot for bootstrap to overwrite.
        for row in self._db.execute(
            "SELECT i.id FROM task_identities i LEFT JOIN nodes n ON n.id=i.id WHERE n.id IS NULL"
        ).fetchall():
            root = identities.get(_stored_uuid(row[0]), _stored_uuid(row[0]))
            old = (
                next(
                    (
                        e
                        for e in applied.entries
                        if type(e.entity) is RemovalRecord
                        and e.entity.entity_kind == "task"
                        and e.entity.entity_id == root
                    ),
                    None,
                )
                if applied
                else None
            )
            fresh = remote.get(("task", root))
            restored = (
                old is not None
                and fresh is not None
                and type(fresh.entity) is TaskRecord
                and fresh.delete_epoch > old.delete_epoch
            )
            known_absent = (
                old is not None and fresh is not None and type(fresh.entity) is RemovalRecord
            )
            if not (restored or known_absent) and ("task", root) not in acknowledged:
                protected.add(("task", root))
        # Retained local file owners must never be reassigned to a foreign root.
        reserved = {}
        for row in self._db.execute("SELECT before_json FROM trash"):
            task = decode_task(row[0])
            for node in _nodes(task):
                reserved[identities.get(node.id, node.id)] = identities.get(task.id, task.id)
        for table in ("attachments", "material_links"):
            for row in self._db.execute(f"SELECT task_id,node_id FROM {table}"):
                root, node = _stored_uuid(row[0]), _stored_uuid(row[1])
                reserved[identities.get(node, node)] = identities.get(root, root)
        for entry in incoming.entries:
            record = entry.entity
            if type(record) is TaskRecord and any(
                identifier in reserved and reserved[identifier] != record.task.id
                for identifier in task_node_ids(record.task)
            ):
                protected.add(("task", record.task.id))
        plan = plan_inbound(
            local=tuple(local),
            incoming=incoming,
            accepted=applied,
            server_id=binding.server_id,
            protected=frozenset(protected),
            acknowledged_local=frozenset(acknowledged),
        )
        return plan, applied, identities, inverse

    def preview_sync_incoming(self, incoming, *, protected_inputs=frozenset()):
        if self._db.in_transaction:
            raise ValueError("Предпросмотр требует отдельную транзакцию")
        with self._db:
            self._db.execute("BEGIN")
            return self._inbound_context(incoming, protected_inputs)[0]

    def apply_sync_incoming(self, incoming, *, bootstrap=False, protected_inputs=frozenset()):
        """Recompute under lock; bootstrap flag is caller intent, not a UI approval token.

        Runtime must arrange first-link backup/preview/approval before this API.
        Unsupported destructive/role/completion changes keep the WHOLE batch pending.
        """
        if type(bootstrap) is not bool or self._db.in_transaction:
            raise ValueError("Нужны отдельная транзакция и явный режим объединения")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            plan, applied, identities, inverse = self._inbound_context(incoming, protected_inputs)
            if applied is None and not bootstrap:
                return InboundApplyResult(status="bootstrap_required", plan=plan)
            if (
                applied is not None
                and incoming.set_revision == applied.set_revision
                and _contents(applied) == _contents(incoming)
            ):
                return InboundApplyResult(status="unchanged", plan=plan)
            if plan.needs_resolution:
                return InboundApplyResult(
                    status="blocked",
                    plan=plan,
                    reasons=tuple(
                        sorted(
                            {d.reason for d in plan.decisions if d.action in ("conflict", "defer")}
                        )
                    ),
                )
            cache = self._read_sync_snapshot()
            if cache is not None and incoming.set_revision < cache.set_revision:
                return InboundApplyResult(status="needs_snapshot", plan=plan)
            reasons = set()
            available_roots = {
                identities.get(_stored_uuid(row[0]), _stored_uuid(row[0]))
                for row in self._db.execute("SELECT id FROM nodes WHERE kind=0")
            }
            available_roots.update(
                e.entity.task.id for e in incoming.entries if type(e.entity) is TaskRecord
            )
            for d in plan.decisions:
                record = d.incoming.entity if d.incoming else None
                if d.action in ("archive", "transition", "retire_rule"):
                    reasons.add("inbound_destructive_or_role_pending")
                if type(record) in (RegularRecord, OccurrenceRecord) or type(d.local) in (
                    RegularRecord,
                    OccurrenceRecord,
                ):
                    reasons.add("inbound_regular_pending")
                if (
                    d.action in ("insert", "update")
                    and type(record) is BlockRecord
                    and record.block.task_id is not None
                    and record.block.task_id not in available_roots
                ):
                    reasons.add("inbound_archived_calendar_pending")
                if d.action in ("insert", "update") and type(record) is TaskRecord:
                    old = d.local.task if type(d.local) is TaskRecord else None
                    if old is not None and not task_node_ids(old) <= task_node_ids(record.task):
                        reasons.add("inbound_prune_pending")
                    before_flags = {n.id: n.completed for n in _nodes(old)} if old else {}
                    if applied is not None and any(
                        n.completed and not before_flags.get(n.id, False)
                        for n in _nodes(record.task)
                    ):
                        reasons.add("inbound_completion_pending")
            if reasons:
                return InboundApplyResult(
                    status="blocked", plan=plan, reasons=tuple(sorted(reasons))
                )
            changed = 0
            with self.suppress_sync_capture("incoming"):
                for kind in (CategoryRecord, TaskRecord, BlockRecord):
                    for d in plan.decisions:
                        record = d.incoming.entity if d.incoming else None
                        if d.action not in ("insert", "update") or type(record) is not kind:
                            continue
                        if kind is CategoryRecord:
                            self._write_category(record.category)
                        elif kind is TaskRecord:
                            mapping = {i: inverse.get(i, i) for i in task_node_ids(record.task)}
                            actual = mapped_task_record(record, mapping)
                            previous = self.get_task(actual.task.id)
                            self._write_task(
                                actual.task, restore_root=previous is None, restore_event=True
                            )
                            if actual.completed_at is not None:
                                self._db.execute(
                                    "INSERT INTO task_completion_dates VALUES (?,?) "
                                    "ON CONFLICT(task_id) DO UPDATE SET "
                                    "completed_us=excluded.completed_us",
                                    (str(actual.task.id), instant_us(actual.completed_at)),
                                )
                            self._set_sync_task_presence(actual.task.id, "live")
                        else:
                            block = replace(
                                record.block,
                                task_id=inverse.get(record.block.task_id, record.block.task_id),
                            )
                            self._write_work_block(block, before=self.get_work_block(block.id))
                        changed += 1
                for d in plan.decisions:
                    record = d.incoming.entity if d.incoming else None
                    if d.action == "keep" and type(record) is TaskRecord:
                        self._set_sync_task_presence(
                            inverse.get(record.task.id, record.task.id), "live"
                        )
                    if (
                        type(record) is RemovalRecord
                        and record.entity_kind == "task"
                        and record.reason in ("user_deleted", "retention")
                    ):
                        root = inverse.get(record.entity_id, record.entity_id)
                        self._db.execute(
                            "INSERT OR IGNORE INTO task_identities VALUES (?)", (str(root),)
                        )
                        self._set_sync_task_presence(
                            root, "retained" if record.reason == "retention" else "deleted"
                        )
            self._remember_sync_snapshot(incoming)
            self._db.execute(
                "INSERT INTO sync_applied_snapshot VALUES (1,?) "
                "ON CONFLICT(context_id) DO UPDATE SET pages=excluded.pages",
                (_pack(incoming),),
            )
            return InboundApplyResult(status="applied", plan=plan, changed=changed)
