"""One durable queue step and settled retirement from cached knowledge; no transport."""

from dataclasses import dataclass, field

from ritm.integrations.sync_codec import (
    CalendarRecord,
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    TaskRecord,
    history_id,
)
from ritm.integrations.sync_history_plan import HistoryBase, HistoryContext, plan_history
from ritm.integrations.sync_lineage_plan import ordinary_changes, plan_lineage
from ritm.integrations.sync_operations import target_identity
from ritm.integrations.sync_promotion_plan import PromotionBase
from ritm.integrations.sync_regular_changes import task_node_ids
from ritm.integrations.sync_regular_plan import RegularBase, RegularContext, plan_regular
from ritm.storage.sync_intents import _stored_uuid
from ritm.storage.sync_outbox import FrozenRequest, SyncBase, SyncBaseConflict
from ritm.storage.sync_prepared import SyncIdentityConflict


@dataclass(frozen=True, kw_only=True)
class PreparationStep:
    status: str
    request: FrozenRequest | None = field(default=None, repr=False)
    reasons: tuple[str, ...] = ()
    remaining_facts: int = 0
    retired_facts: int = 0


def _key(entity):
    return (
        (entity.entity_kind, entity.entity_id)
        if type(entity) is RemovalRecord
        else target_identity(entity)
    )


def _contexts(changes, snapshot, state, moment):
    entries = {_key(e.entity): e for e in snapshot.entries}
    settings = [e.entity for e in snapshot.entries if type(e.entity) is CalendarRecord]
    if len(settings) != 1:
        raise ValueError("Для подготовки требуется общий часовой пояс аккаунта")
    identities = dict(state.identities)
    inverse = {b: a for a, b in state.identities}
    rules, histories, roots, nodes, ambiguous = set(), set(), set(), set(), set()
    promotions = set()
    for change in changes:
        rules.add(change.rule_id)
        roots.add(change.root_id)
        if change.day is not None:
            histories.add(history_id(change.rule_id, change.day))
        if change.kind == "save" and type(change.before) is TaskRecord:
            promotions.add(("task", identities.get(change.before.task.id, change.before.task.id)))
        elif change.kind == "save" and type(change.before) is OccurrenceRecord:
            promotions.add(("occurrence", change.before.id))
        for record in (change.before, change.after):
            if record is None:
                continue
            if type(record) is RegularRecord:
                rules.add(record.id)
                histories.add(history_id(record.id, record.current_day))
                remote = entries.get(("regular", record.id))
                same_slot = (
                    remote is not None
                    and type(remote.entity) is RegularRecord
                    and remote.entity.current_day == record.current_day
                )
                current = record.current
                if (
                    remote is not None
                    and type(remote.entity) is RegularRecord
                    and record.current_day > remote.entity.current_day
                ):
                    ambiguous.update(task_node_ids(current.task))
                history_entry = entries.get(
                    ("occurrence", history_id(record.id, record.current_day))
                )
                if (
                    history_entry is not None
                    and type(history_entry.entity) is OccurrenceRecord
                    and history_entry.entity.current.task.id != current.task.id
                    and current.task.id not in identities
                ):
                    ambiguous.update(task_node_ids(current.task))
            elif type(record) is OccurrenceRecord:
                histories.add(record.id)
                remote = entries.get(("occurrence", record.id))
                same_slot = remote is not None and type(remote.entity) is OccurrenceRecord
                current = record.current
            else:
                current, remote, same_slot = record, None, False
            ids = task_node_ids(current.task)
            nodes.update(ids)
            if (
                same_slot
                and remote.entity.current.task.id != current.task.id
                and current.task.id not in identities
            ):
                # Leave canonical birth inference to the regular planner. Never invent a remap.
                ambiguous.update(ids)
    nodes.update(roots)
    for node in sorted(nodes - ambiguous, key=str):
        if node not in identities and node not in inverse:
            identities[node] = node
    occupied, ordinary = set(), set()
    by_root = {}
    regulars = []
    for entry in snapshot.entries:
        record = entry.entity
        occupied.add(_key(record)[1])
        if type(record) is RegularRecord:
            regulars.append(record)
            occupied.update(task_node_ids(record.current.task))
        elif type(record) is OccurrenceRecord:
            occupied.update(task_node_ids(record.current.task))
            by_root[record.current.task.id] = entry
        elif type(record) is TaskRecord:
            occupied.update(task_node_ids(record.task))
            ordinary.add(record.task.id)
        elif type(record) is RemovalRecord and record.entity_kind == "occurrence":
            by_root.setdefault(record.root_id, entry)
    # Ordinary representation wins over the paired old occurrence type_changed mark.
    for (kind, identifier), entry in entries.items():
        if kind == "task" and (
            type(entry.entity) is TaskRecord
            or identifier not in by_root
            or entry.entity.reason != "type_changed"
        ):
            by_root[identifier] = entry
    mapped_roots = {identities.get(root, root) for root in roots}
    pairs = tuple(sorted(identities.items(), key=lambda pair: str(pair[0])))
    regular = RegularContext(
        server_id=snapshot.server_id,
        settings=settings[0],
        moment=moment,
        bases=tuple(
            RegularBase(id=key, entry=entries.get(("regular", key)))
            for key in sorted(rules, key=str)
        ),
        identities=pairs,
        occupied_ids=frozenset(occupied),
        acknowledged_barriers=state.barriers,
        acknowledged_reschedules=state.reschedules,
        acknowledged_promotions=state.promotions,
        acknowledged_stops=state.stops,
        acknowledged_closures=state.closures,
        terminal_bases=tuple(
            RegularBase(id=key, entry=by_root.get(key)) for key in sorted(mapped_roots, key=str)
        ),
        promotion_bases=tuple(
            PromotionBase(kind=kind, id=key, entry=entries.get((kind, key)))
            for kind, key in sorted(promotions, key=lambda pair: (pair[0], str(pair[1])))
        ),
    )
    history = HistoryContext(
        server_id=snapshot.server_id,
        bases=tuple(
            HistoryBase(id=key, entry=entries.get(("occurrence", key)))
            for key in sorted(histories, key=str)
        ),
        identities=pairs,
        regulars=tuple(regulars),
        ordinary_roots=frozenset(ordinary),
        task_bases=tuple(
            HistoryBase(id=key, entry=entries.get(("task", key)))
            for key in sorted(mapped_roots, key=str)
        ),
        acknowledged_barriers=state.barriers,
        acknowledged_detaches=state.detaches,
    )
    return regular, history


class SyncPrepareLoopMixin:
    def prepare_next_sync(self, moment):
        """Resume frozen head or prepare one command from a full cached snapshot.

        Returning a request does not authorize transport; coverage barrier remains.
        """
        if self._db.in_transaction:
            raise ValueError("Подготовка требует отдельную транзакцию")
        try:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                return self._prepare_next_sync(moment)
        except (SyncBaseConflict, SyncIdentityConflict) as error:
            return PreparationStep(
                status="conflict",
                reasons=(
                    "base_epoch_conflict"
                    if isinstance(error, SyncBaseConflict)
                    else "occupied_identity",
                ),
                remaining_facts=len(self.list_sync_regular_changes()),
            )

    def _prepare_next_sync(self, moment):
        binding = self.get_sync_binding()
        if binding is None or not self.is_sync_task_capture_enabled():
            raise ValueError("Сначала требуется подключение с первоначальным объединением")
        changes = self._read_sync_regular_changes(binding)
        snapshot = self._read_sync_snapshot()
        required_revision = 0
        for row in self._db.execute("SELECT id FROM sync_intents ORDER BY seq"):
            frozen = self._frozen_sync_request(_stored_uuid(row[0]))
            if frozen is None:
                if snapshot is None or snapshot.set_revision < required_revision:
                    return PreparationStep(status="needs_snapshot", remaining_facts=len(changes))
                _, intent = self._sync_intent_row(_stored_uuid(row[0]))
                known = next(
                    (
                        e
                        for e in snapshot.entries
                        if _key(e.entity) == (intent.target_kind, intent.target_id)
                    ),
                    None,
                )
                if (
                    (known is None and intent.action != "create")
                    or (known is not None and intent.action == "create")
                    or (
                        known is not None
                        and type(known.entity) is RemovalRecord
                        and intent.action not in ("restore", "delete")
                    )
                ):
                    return PreparationStep(
                        status="blocked",
                        reasons=("intent_base_conflict",),
                        remaining_facts=len(changes),
                    )
                frozen = self._freeze_sync_intent(
                    intent.id,
                    SyncBase(
                        server_id=binding.server_id,
                        target_kind=intent.target_kind,
                        target_id=intent.target_id,
                        revision=known.revision if known else 0,
                        delete_epoch=known.delete_epoch if known else 0,
                    ),
                )
                return PreparationStep(
                    status="prepared", request=frozen, remaining_facts=len(changes)
                )
            if frozen.receipt is None:
                return PreparationStep(
                    status="pending", request=frozen, remaining_facts=len(changes)
                )
            if frozen.receipt.status != "applied":
                return PreparationStep(
                    status="conflict", request=frozen, remaining_facts=len(changes)
                )
            required_revision = max(required_revision, frozen.receipt.revision)
        if snapshot is None or snapshot.set_revision < required_revision:
            return PreparationStep(status="needs_snapshot", remaining_facts=len(changes))
        if not changes:
            return PreparationStep(status="idle")
        state = self._read_sync_prepared_state()
        regular, history = _contexts(changes, snapshot, state, moment)
        entries = {_key(e.entity): e for e in snapshot.entries}
        identities = dict(regular.identities)
        ordinary_ids = ordinary_changes(changes, entries, identities)
        lineage = plan_lineage(
            changes, entries, identities, ordinary_ids, state.barriers, state.promotions
        )
        # A confirmed promotion owns the prior history frames through its transition.
        cutoffs = {
            c.before.id: c.sequence
            for c in changes
            if c.kind == "save" and type(c.before) is OccurrenceRecord and c.id in state.promotions
        }
        promoted_history = set()
        for c in changes:
            slot = history_id(c.rule_id, c.day) if c.day is not None else None
            if c.kind == "rollover" and c.before.current.task.completed:
                slot = history_id(c.before.id, c.before.current_day)
            elif c.kind == "save" and type(c.before) is OccurrenceRecord:
                slot = c.before.id
            if slot in cutoffs and c.sequence <= cutoffs[slot]:
                promoted_history.add(c.id)
        pending_history_promotions = {
            c.id
            for c in changes
            if c.kind == "save"
            and type(c.before) is OccurrenceRecord
            and c.id not in state.promotions
        }
        history_changes = tuple(
            c
            for c in changes
            if c.id not in ordinary_ids | promoted_history | pending_history_promotions
        )
        current_plan = plan_regular(changes, regular, server_id=binding.server_id)
        if current_plan.proposals:
            frozen = self._enqueue_sync_regular(regular, current_plan.proposals[0].id)
            return PreparationStep(status="prepared", request=frozen, remaining_facts=len(changes))
        history_plan = plan_history(history_changes, history, server_id=binding.server_id)
        if history_plan.proposals:
            frozen = self._enqueue_sync_history(
                history, history_plan.proposals[0].id, projection_changes=history_changes
            )
            return PreparationStep(status="prepared", request=frozen, remaining_facts=len(changes))
        if lineage.proposals:
            frozen = self._persist_sync_proposal(lineage.proposals[0], binding, state)
            return PreparationStep(status="prepared", request=frozen, remaining_facts=len(changes))
        reasons = tuple(
            sorted(
                {b.reason for b in (*current_plan.blocked, *history_plan.blocked)}
                | set(lineage.blocked)
            )
        )
        if reasons:
            return PreparationStep(status="blocked", reasons=reasons, remaining_facts=len(changes))
        covered = (
            set(current_plan.projected_changes)
            | set(history_plan.projected_changes)
            | set(lineage.projected_changes)
            | promoted_history
        )
        if covered != {c.id for c in changes}:
            return PreparationStep(
                status="blocked", reasons=("projection_missing",), remaining_facts=len(changes)
            )
        if self._db.execute(
            "SELECT 1 FROM sync_block_changes WHERE after_regular_id IN "
            "(SELECT id FROM sync_regular_changes) LIMIT 1"
        ).fetchone():
            return PreparationStep(
                status="blocked", reasons=("calendar_pending",), remaining_facts=len(changes)
            )
        # Both projections agree with a complete, covering remote snapshot.
        # No pending command, blocked fact or unrepresented ordinary edit is discarded.
        self._db.execute(
            "DELETE FROM sync_phase_acks WHERE change_id IN "
            "(SELECT id FROM sync_regular_changes WHERE context_id=1)"
        )
        self._db.execute("DELETE FROM sync_regular_changes WHERE context_id=1")
        return PreparationStep(status="settled", retired_facts=len(changes))
