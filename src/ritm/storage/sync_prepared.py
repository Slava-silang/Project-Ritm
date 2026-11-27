"""Atomic regular proposal preparation and receipt-backed identity ledger."""

import json
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

from ritm.integrations.sync_codec import Entity, OccurrenceRecord, RemovalRecord, TaskRecord
from ritm.integrations.sync_history_plan import plan_history
from ritm.integrations.sync_regular_changes import task_node_ids
from ritm.integrations.sync_regular_plan import plan_regular
from ritm.storage.sync_intents import SyncIntent, _stored_uuid
from ritm.storage.sync_outbox import SyncBase

PREPARED_OBJECTS = ("sync_prepared", "sync_identities", "sync_phase_acks")
PREPARED_SCHEMA = (
    "CREATE TABLE sync_prepared (intent_id TEXT PRIMARY KEY NOT NULL "
    "REFERENCES sync_intents(id), metadata TEXT NOT NULL)",
    "CREATE TABLE sync_identities (local_id TEXT PRIMARY KEY NOT NULL, "
    "remote_id TEXT NOT NULL UNIQUE, context_id INTEGER NOT NULL CHECK(context_id=1) "
    "REFERENCES sync_binding(id))",
    "CREATE TABLE sync_phase_acks (change_id TEXT NOT NULL, phase TEXT NOT NULL, "
    "intent_id TEXT NOT NULL REFERENCES sync_prepared(intent_id), "
    "PRIMARY KEY(change_id,phase))",
)


@dataclass(frozen=True, kw_only=True)
class PreparedState:
    identities: tuple[tuple[UUID, UUID], ...] = field(repr=False)
    barriers: frozenset[UUID]
    reschedules: frozenset[UUID]
    promotions: frozenset[UUID]
    detaches: frozenset[UUID]
    stops: frozenset[UUID]


@dataclass(frozen=True, kw_only=True)
class _PreparedProjection:
    id: UUID
    target_kind: str
    action: str
    base_revision: int
    delete_epoch: int
    payload: Entity | None = field(repr=False)
    source_changes: tuple[UUID, ...]
    barrier_changes: tuple[UUID, ...]
    transition_change: UUID | None
    identities: tuple[tuple[UUID, UUID], ...] = field(repr=False)


def _metadata(proposal):
    return json.dumps(
        {
            "sources": [str(x) for x in proposal.source_changes],
            "identities": [[str(a), str(b)] for a, b in proposal.identities],
            "barriers": [str(x) for x in proposal.barrier_changes],
            "transition": str(proposal.transition_change) if proposal.transition_change else None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _read_metadata(raw):
    value = json.loads(raw)
    if type(value) is not dict or set(value) != {"sources", "identities", "barriers", "transition"}:
        raise ValueError("Повреждены основания подготовленного изменения")
    for name in ("sources", "barriers"):
        if type(value[name]) is not list or len(set(value[name])) != len(value[name]):
            raise ValueError("Повреждены ссылки на локальные изменения")
        value[name] = tuple(_stored_uuid(x) for x in value[name])
    if not value["sources"] or not set(value["barriers"]) <= set(value["sources"]):
        raise ValueError("Повреждены фазы локального изменения")
    if value["transition"] is not None:
        value["transition"] = _stored_uuid(value["transition"])
        if value["transition"] not in value["sources"]:
            raise ValueError("Переход не принадлежит исходным изменениям")
    if type(value["identities"]) is not list:
        raise ValueError("Повреждены соответствия идентификаторов")
    pairs = []
    for pair in value["identities"]:
        if type(pair) is not list or len(pair) != 2:
            raise ValueError("Повреждено соответствие идентификаторов")
        pairs.append(tuple(_stored_uuid(x) for x in pair))
    if len({a for a, b in pairs}) != len(pairs) or len({b for a, b in pairs}) != len(pairs):
        raise ValueError("Неоднозначное соответствие идентификаторов")
    value["identities"] = tuple(pairs)
    return value


class SyncPreparedMixin:
    def enqueue_sync_regular(self, context, target_id):
        """Recalculate against locked facts, persist proposal and exact request together.

        Does not retire facts, authorize transport, or apply remote entities.
        Caller supplies verified server bases; an unknown base is never invented.
        """
        if self._db.in_transaction:
            raise ValueError("Подготовка требует отдельную транзакцию")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            return self._enqueue_sync_regular(context, target_id)

    def _enqueue_sync_regular(self, context, target_id):
        binding = self.get_sync_binding()
        if binding is None:
            raise ValueError("Синхронизация не подключена")
        changes = self._read_sync_regular_changes(binding)
        # Validate the original context before merging; never hide duplicate IDs.
        plan_regular(changes, context, server_id=binding.server_id)
        state = self._read_sync_prepared_state()
        supplied = dict(context.identities)
        for local, remote in state.identities:
            if local in supplied and supplied[local] != remote:
                raise ValueError("Контекст меняет подтверждённое соответствие")
            supplied[local] = remote
        context = replace(
            context,
            identities=tuple(supplied.items()),
            acknowledged_barriers=context.acknowledged_barriers | state.barriers,
            acknowledged_reschedules=context.acknowledged_reschedules | state.reschedules,
            acknowledged_promotions=context.acknowledged_promotions | state.promotions,
            acknowledged_stops=context.acknowledged_stops | state.stops,
        )
        plan = plan_regular(changes, context, server_id=binding.server_id)
        matches = [p for p in plan.proposals if p.id == target_id]
        if len(matches) != 1:
            raise ValueError("Нет однозначного готового изменения")
        proposal = matches[0]
        return self._persist_sync_proposal(proposal, binding, state)

    def enqueue_sync_history(self, context, target_id):
        """Prepare one history projection atomically; never consume other completions."""
        if self._db.in_transaction:
            raise ValueError("Подготовка требует отдельную транзакцию")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            return self._enqueue_sync_history(context, target_id)

    def _enqueue_sync_history(self, context, target_id):
        binding = self.get_sync_binding()
        if binding is None:
            raise ValueError("Синхронизация не подключена")
        changes = self._read_sync_regular_changes(binding)
        plan_history(changes, context, server_id=binding.server_id)
        state = self._read_sync_prepared_state()
        supplied = dict(context.identities)
        for local, remote in state.identities:
            if local in supplied and supplied[local] != remote:
                raise ValueError("Контекст меняет подтверждённое соответствие")
            supplied[local] = remote
        context = replace(
            context,
            identities=tuple(supplied.items()),
            acknowledged_detaches=context.acknowledged_detaches | state.detaches,
            acknowledged_barriers=context.acknowledged_barriers | state.barriers,
        )
        plan = plan_history(changes, context, server_id=binding.server_id)
        matches = [p for p in plan.proposals if p.id == target_id]
        if len(matches) != 1:
            raise ValueError("Нет однозначного готового изменения")
        proposal = matches[0]
        used = set()
        for record in (
            proposal.payload,
            next(b.entry.entity for b in context.bases if b.id == target_id and b.entry is not None)
            if any(b.id == target_id and b.entry is not None for b in context.bases)
            else None,
        ):
            if type(record) is OccurrenceRecord:
                used.update(task_node_ids(record.current.task))
            elif type(record) is TaskRecord:
                used.update(task_node_ids(record.task))
            elif type(record) is RemovalRecord and record.root_id is not None:
                used.add(record.root_id)
        adapted = _PreparedProjection(
            id=proposal.id,
            target_kind="occurrence",
            action=proposal.action,
            base_revision=proposal.base_revision,
            delete_epoch=proposal.delete_epoch,
            payload=proposal.payload,
            source_changes=proposal.source_changes,
            barrier_changes=proposal.barrier_changes,
            transition_change=proposal.transition_change,
            identities=tuple((a, b) for a, b in context.identities if b in used),
        )
        return self._persist_sync_proposal(adapted, binding, state)

    def _persist_sync_proposal(self, proposal, binding, state):
        raw = _metadata(proposal)
        metadata = _read_metadata(raw)
        confirmed = dict(state.identities)
        reverse = {remote: local for local, remote in state.identities}
        for local, remote in metadata["identities"]:
            if (local in confirmed and confirmed[local] != remote) or (
                remote in reverse and reverse[remote] != local
            ):
                raise ValueError("Подготовка меняет подтверждённое соответствие")
        for row in self._db.execute("SELECT * FROM sync_prepared"):
            previous = _read_metadata(row["metadata"])
            frozen = self._frozen_sync_request(_stored_uuid(row["intent_id"]))
            if frozen is None:
                raise ValueError("Подготовленное изменение потеряло запрос")
            op = frozen.operation
            if (
                row["metadata"] == raw
                and op.target_kind == proposal.target_kind
                and op.target_id == proposal.id
                and op.action == proposal.action
                and op.payload == proposal.payload
                and op.base_revision == proposal.base_revision
                and op.delete_epoch == proposal.delete_epoch
            ):
                return frozen
            if set(previous["sources"]) & set(metadata["sources"]) and (
                frozen.receipt is None or frozen.receipt.status != "applied"
            ):
                raise ValueError("Дождитесь подтверждения подготовленного изменения")
        last = self._db.execute("SELECT id FROM sync_intents ORDER BY seq DESC LIMIT 1").fetchone()
        item = SyncIntent(
            id=uuid4(),
            target_kind=proposal.target_kind,
            target_id=proposal.id,
            action=proposal.action,
            payload=proposal.payload,
            depends_on=_stored_uuid(last[0]) if last else None,
        )
        self._enqueue_sync_intent(item)
        self._db.execute("INSERT INTO sync_prepared VALUES (?,?)", (str(item.id), raw))
        return self._freeze_sync_intent(
            item.id,
            SyncBase(
                server_id=binding.server_id,
                target_kind=item.target_kind,
                target_id=item.target_id,
                revision=proposal.base_revision,
                delete_epoch=proposal.delete_epoch,
            ),
        )

    def _accept_sync_prepared(self, frozen, receipt):
        row = self._db.execute(
            "SELECT metadata FROM sync_prepared WHERE intent_id=?", (str(frozen.intent_id),)
        ).fetchone()
        if row is None or receipt.status != "applied":
            return
        metadata = _read_metadata(row[0])
        for local, remote in metadata["identities"]:
            existing = self._db.execute(
                "SELECT remote_id FROM sync_identities WHERE local_id=?", (str(local),)
            ).fetchone()
            if existing is not None and existing[0] != str(remote):
                raise ValueError("Подтверждение меняет прежнее соответствие идентификаторов")
            self._db.execute(
                "INSERT OR IGNORE INTO sync_identities VALUES (?,?,1)", (str(local), str(remote))
            )
            # OR IGNORE must not hide a conflicting remote-side mapping.
            found = self._db.execute(
                "SELECT remote_id FROM sync_identities WHERE local_id=?", (str(local),)
            ).fetchone()
            if found is None or found[0] != str(remote):
                raise ValueError("Облачный идентификатор уже связан с другой задачей")
        phases = [(x, "barrier") for x in metadata["barriers"]]
        if metadata["transition"] is not None:
            phases.append((metadata["transition"], frozen.operation.action))
        for change, phase in phases:
            self._db.execute(
                "INSERT OR IGNORE INTO sync_phase_acks VALUES (?,?,?)",
                (str(change), phase, str(frozen.intent_id)),
            )

    def read_sync_prepared_state(self):
        if self._db.in_transaction:
            raise ValueError("Чтение требует отдельную транзакцию")
        with self._db:
            self._db.execute("BEGIN")
            return self._read_sync_prepared_state()

    def _read_sync_prepared_state(self):
        identities = tuple(
            (_stored_uuid(r[0]), _stored_uuid(r[1]))
            for r in self._db.execute(
                "SELECT local_id,remote_id FROM sync_identities ORDER BY local_id"
            )
        )
        phases = {}
        for row in self._db.execute("SELECT * FROM sync_phase_acks"):
            frozen = self._frozen_sync_request(_stored_uuid(row["intent_id"]))
            if frozen is None or frozen.receipt is None or frozen.receipt.status != "applied":
                raise ValueError("Фаза не подтверждена квитанцией")
            phases.setdefault(row["phase"], set()).add(_stored_uuid(row["change_id"]))
        return PreparedState(
            identities=identities,
            barriers=frozenset(phases.get("barrier", ())),
            reschedules=frozenset(phases.get("reschedule_regular", ())),
            promotions=frozenset(phases.get("promote_regular", ())),
            detaches=frozenset(phases.get("detach_occurrence", ())),
            stops=frozenset(phases.get("stop_regular", ()))
            | frozenset(phases.get("stop_offline", ())),
        )
