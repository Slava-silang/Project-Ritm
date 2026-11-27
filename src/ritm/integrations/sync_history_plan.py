"""Pure history preparation against explicit server knowledge; no send, SQL or authority."""

from dataclasses import dataclass, field, replace
from datetime import date
from uuid import UUID

from ritm.integrations.sync_codec import (
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    TaskRecord,
    encode_entity,
    history_id,
)
from ritm.integrations.sync_pages import MAX_SNAPSHOT_ITEMS, MAX_STAGED_BYTES, SnapshotEntry
from ritm.integrations.sync_regular_changes import (
    checked_counter as _counter,
)
from ritm.integrations.sync_regular_changes import (
    checked_entity as _checked,
)
from ritm.integrations.sync_regular_changes import (
    checked_uuid as _uuid,
)
from ritm.integrations.sync_regular_changes import (
    task_node_ids as _nodes,
)
from ritm.integrations.sync_regular_changes import (
    validate_regular_journal,
)
from ritm.integrations.sync_transitions import history_record


@dataclass(frozen=True, kw_only=True)
class HistoryBase:
    id: UUID
    entry: SnapshotEntry | None = field(repr=False)  # None explicitly confirms absence.


@dataclass(frozen=True, kw_only=True)
class HistoryContext:
    server_id: UUID
    bases: tuple[HistoryBase, ...] = field(repr=False)
    identities: tuple[tuple[UUID, UUID], ...] = field(repr=False)
    regulars: tuple[RegularRecord, ...] = field(repr=False)
    ordinary_roots: frozenset[UUID] = frozenset()
    frozen_changes: frozenset[UUID] = frozenset()
    task_bases: tuple[HistoryBase, ...] = field(default=(), repr=False)
    acknowledged_detaches: frozenset[UUID] = frozenset()
    acknowledged_barriers: frozenset[UUID] = frozenset()


@dataclass(frozen=True, kw_only=True)
class HistoryProposal:
    id: UUID
    action: str
    base_revision: int
    delete_epoch: int
    payload: OccurrenceRecord | TaskRecord | None = field(repr=False)
    source_changes: tuple[UUID, ...]
    transition_change: UUID | None = None
    barrier_changes: tuple[UUID, ...] = ()


@dataclass(frozen=True, kw_only=True)
class HistoryBlock:
    id: UUID
    reason: str
    source_changes: tuple[UUID, ...]


@dataclass(frozen=True, kw_only=True)
class HistoryPlan:
    proposals: tuple[HistoryProposal, ...] = field(repr=False)
    blocked: tuple[HistoryBlock, ...]
    unchanged: tuple[UUID, ...]
    remaining_changes: tuple[UUID, ...]  # No fact may be retired by this read-only planner.
    projected_changes: tuple[UUID, ...]


@dataclass
class _Candidate:
    rule: UUID
    day: date
    root: UUID
    desired: OccurrenceRecord | None = field(repr=False)
    sources: list[UUID] = field(default_factory=list)
    prune_changes: set[UUID] = field(default_factory=set)
    restore_changes: set[UUID] = field(default_factory=set)
    removed: set[UUID] = field(default_factory=set)
    original_nodes: set[UUID] = field(default_factory=set)
    blocked: str | None = None


def _context(context, server_id):
    if type(context) is not HistoryContext:
        raise ValueError("Нужен явный контекст подготовки истории")
    _uuid(server_id)
    _uuid(context.server_id)
    if context.server_id != server_id:
        raise ValueError("Контекст относится к другому поколению сервера")
    for collection in (context.bases, context.identities, context.regulars):
        if type(collection) is not tuple or len(collection) > MAX_SNAPSHOT_ITEMS:
            raise ValueError("Превышены границы контекста истории")
    for collection in (
        context.ordinary_roots,
        context.frozen_changes,
        context.acknowledged_barriers,
    ):
        if type(collection) is not frozenset or len(collection) > MAX_SNAPSHOT_ITEMS:
            raise ValueError("Некорректные границы контекста истории")
        for key in collection:
            _uuid(key)
    identities, remote = {}, set()
    for pair in context.identities:
        if type(pair) is not tuple or len(pair) != 2:
            raise ValueError("Некорректное соответствие идентификаторов")
        a, b = pair
        _uuid(a)
        _uuid(b)
        if a in identities or b in remote:
            raise ValueError("Неоднозначное соответствие идентификаторов")
        identities[a] = b
        remote.add(b)
    bases = {}
    size = 0
    for base in context.bases:
        if type(base) is not HistoryBase:
            raise ValueError("Некорректное основание истории")
        _uuid(base.id)
        if base.id in bases:
            raise ValueError("Повтор основания истории")
        entry = base.entry
        if entry is not None:
            if type(entry) is not SnapshotEntry:
                raise ValueError("Некорректное основание истории")
            _counter(entry.revision, positive=True)
            _counter(entry.delete_epoch)
            record = entry.entity
            _checked(record)
            size += len(encode_entity(record))
            if type(record) is OccurrenceRecord:
                valid = record.id == base.id
            elif type(record) is RemovalRecord:
                valid = (
                    record.entity_kind == "occurrence"
                    and record.entity_id == base.id
                    and record.revision == entry.revision
                    and record.delete_epoch == entry.delete_epoch
                )
            else:
                valid = False
            if not valid:
                raise ValueError("Основание не соответствует истории")
        bases[base.id] = entry
    regulars, roots = {}, set(context.ordinary_roots)
    for regular in context.regulars:
        if type(regular) is not RegularRecord:
            raise ValueError("Некорректное серверное расписание")
        _checked(regular)
        size += len(encode_entity(regular))
        if regular.id in regulars or regular.current.task.id in roots:
            raise ValueError("Повтор серверного расписания или корня")
        regulars[regular.id] = regular
        roots.add(regular.current.task.id)
    if size > MAX_STAGED_BYTES:
        raise ValueError("Контекст истории слишком велик")
    return bases, identities, regulars, roots


def _candidates(changes):
    validate_regular_journal(changes, max_items=MAX_SNAPSHOT_ITEMS, max_bytes=MAX_STAGED_BYTES)
    candidates = {}
    for change in changes:
        record, day, rule, root, compound = None, change.day, change.rule_id, change.root_id, False
        if change.kind == "rollover":
            if not change.before.current.task.completed:
                continue
            record = history_record(change.before)
            day, root = record.day, record.current.task.id
        elif change.kind == "history" or (
            change.kind == "root_restore" and type(change.after) is OccurrenceRecord
        ):
            record = change.after
        elif change.kind == "detach" or (
            change.kind == "save" and type(change.before) is OccurrenceRecord
        ):
            record = change.before
            day, rule, root, compound = record.day, record.rule_id, record.current.task.id, True
        elif change.kind not in ("root_delete", "root_save", "root_restore"):
            continue
        identifier = history_id(rule, day)
        candidate = candidates.get(identifier)
        if candidate is None:
            initial = change.before if type(change.before) is OccurrenceRecord else record
            candidate = candidates[identifier] = _Candidate(
                rule=rule,
                day=day,
                root=root,
                desired=None,
                original_nodes=_nodes(initial.current.task) if initial is not None else set(),
            )
        if candidate.root != root:
            raise ValueError("Один слот истории связан с разными корнями")
        candidate.sources.append(change.id)
        if (
            compound
            or change.kind in ("root_save", "root_restore")
            and type(change.after) is TaskRecord
        ):
            candidate.blocked = "atomic_type_transition"
            candidate.desired = None
            continue
        if change.kind == "root_delete":
            candidate.desired = None
            continue
        if record is not None:
            before = change.before
            if type(before) is OccurrenceRecord and change.kind == "history":
                removed = _nodes(before.current.task) - _nodes(record.current.task)
                returned = candidate.removed & _nodes(record.current.task)
                if returned:
                    candidate.blocked = "node_restore_required"
                candidate.removed.update(removed)
                if removed:
                    candidate.prune_changes.add(change.id)
            if change.kind == "root_restore":
                candidate.restore_changes.add(change.id)
                if candidate.blocked == "node_restore_required":
                    candidate.blocked = None
                candidate.removed.clear()
            candidate.desired = record
    return candidates


def _mapped(record, identities):
    current = record if type(record) is TaskRecord else record.current
    task = current.task
    if not _nodes(task) <= identities.keys():
        return None
    children = tuple(
        replace(
            child,
            id=identities[child.id],
            items=tuple(replace(item, id=identities[item.id]) for item in child.items),
        )
        for child in task.subtasks
    )
    task = replace(task, id=identities[task.id], subtasks=children)
    mapped = replace(current, task=task)
    result = mapped if type(record) is TaskRecord else replace(record, current=mapped)
    _checked(result)
    return result


def _detach_proposals(changes, context, bases, identities, occupied):
    if type(context.task_bases) is not tuple or len(context.task_bases) > MAX_SNAPSHOT_ITEMS:
        raise ValueError("Некорректные основания ordinary задач")
    if type(context.acknowledged_detaches) is not frozenset:
        raise ValueError("Некорректные подтверждения отделения")
    for key in context.acknowledged_detaches:
        _uuid(key)
    if context.acknowledged_detaches & context.frozen_changes:
        raise ValueError("Противоречивые подтверждения отделения")
    tasks = {}
    for base in context.task_bases:
        if type(base) is not HistoryBase or base.id in tasks:
            raise ValueError("Некорректное основание ordinary задачи")
        _uuid(base.id)
        if base.entry is not None:
            entry = base.entry
            if type(entry) is not SnapshotEntry or type(entry.entity) is not TaskRecord:
                raise ValueError("Нужно известное живое ordinary представление")
            _counter(entry.revision, positive=True)
            _counter(entry.delete_epoch)
            _checked(entry.entity)
            if entry.entity.task.id != base.id:
                raise ValueError("Не совпадает ordinary root")
        tasks[base.id] = base.entry
    proposals, blocks, unchanged, handled = [], [], [], set()
    for change in changes:
        if change.kind != "detach":
            continue
        identifier = change.before.id
        if not context.task_bases:
            continue  # Existing caller has not supplied compound target knowledge yet.
        reason = None
        root = identities.get(change.root_id)
        if identifier in handled:
            raise ValueError("Повторное отделение одного слота")
        handled.add(identifier)
        if change.id in context.frozen_changes:
            reason = "frozen_change"
        elif root is None:
            reason = "identity_mapping_required"
        elif identifier not in bases or root not in tasks:
            reason = "unknown_base"
        elif change.id in context.acknowledged_detaches:
            before = bases[identifier]
            if (
                before is None
                or type(before.entity) is not RemovalRecord
                or before.entity.reason != "type_changed"
                or before.entity.root_id != root
                or tasks[root] is None
            ):
                reason = "detach_ack_conflict"
            else:
                unchanged.append(identifier)
                continue
        elif root in occupied:
            reason = "current_root"
        elif tasks[root] is not None:
            reason = "ordinary_target_exists"
        elif bases[identifier] is None or type(bases[identifier].entity) is not OccurrenceRecord:
            reason = "live_history_required"
        if not reason:
            before = bases[identifier]
            mapped = _mapped(change.after, identities)
            if mapped is None:
                reason = "identity_mapping_required"
            else:

                def shape(task):
                    return (
                        task.id,
                        tuple((x.id, tuple(y.id for y in x.items)) for x in task.subtasks),
                    )

                if shape(mapped.task) != shape(before.entity.current.task):
                    reason = "detach_tree_conflict"
                else:
                    proposals.append(
                        HistoryProposal(
                            id=identifier,
                            action="detach_occurrence",
                            base_revision=before.revision,
                            delete_epoch=before.delete_epoch,
                            payload=mapped,
                            source_changes=(change.id,),
                            transition_change=change.id,
                        )
                    )
        if reason:
            blocks.append(HistoryBlock(id=identifier, reason=reason, source_changes=(change.id,)))
    return handled, proposals, blocks, unchanged


def plan_history(changes, context: HistoryContext, *, server_id: UUID) -> HistoryPlan:
    """Proposals only. Caller must later verify auth, dependencies and persist atomically."""
    bases, identities, regulars, occupied = _context(context, server_id)
    remote_owners = {}
    for key, entry in bases.items():
        if entry is not None and type(entry.entity) is OccurrenceRecord:
            root = entry.entity.current.task.id
            if root in remote_owners:
                raise ValueError("Один серверный корень занят разными слотами истории")
            remote_owners[root] = key
    candidates = _candidates(changes)
    handled, proposals, blocked, unchanged = _detach_proposals(
        changes, context, bases, identities, occupied
    )
    roots = {}
    for key, candidate in candidates.items():
        if candidate.desired is not None:
            roots.setdefault(candidate.root, []).append(key)
    for keys in roots.values():
        if len(keys) > 1:
            for key in keys:
                candidates[key].blocked = "duplicate_root"
    for identifier, candidate in candidates.items():
        if identifier in handled:
            continue
        sources = tuple(candidate.sources)
        reason = candidate.blocked
        desired = _mapped(candidate.desired, identities) if candidate.desired is not None else None
        root = identities.get(candidate.root)
        if not reason and (root is None or candidate.desired is not None and desired is None):
            reason = "identity_mapping_required"
        if not reason and context.frozen_changes.intersection(sources):
            reason = "frozen_change"
        if not reason and identifier not in bases:
            reason = "unknown_base"
        regular = regulars.get(candidate.rule)
        if not reason and root in occupied:
            reason = "current_root"
        if not reason and root in remote_owners and remote_owners[root] != identifier:
            reason = "root_conflict"
        if not reason and desired is not None and regular is not None:
            if candidate.day >= regular.current_day:
                reason = "regular_transition_required"
            elif (candidate.day - regular.first_day).days < 0 or (
                candidate.day - regular.first_day
            ).days % regular.interval_days:
                reason = "schedule_conflict"
        pending_restore = bool(candidate.restore_changes - context.acknowledged_barriers)
        pending_prune = bool(candidate.prune_changes - context.acknowledged_barriers)
        entry = bases.get(identifier)
        if not reason and entry is not None:
            entity = entry.entity
            remote_root = (
                entity.current.task.id if type(entity) is OccurrenceRecord else entity.root_id
            )
            if remote_root != root:
                reason = "root_conflict"
            elif type(entity) is OccurrenceRecord and desired is not None and not pending_restore:
                original = {
                    identities[key] for key in candidate.original_nodes if key in identities
                }
                if (original - _nodes(entity.current.task)) & _nodes(desired.current.task):
                    reason = "node_restore_required"
        if reason:
            blocked.append(HistoryBlock(id=identifier, reason=reason, source_changes=sources))
            continue
        revision, epoch = (entry.revision, entry.delete_epoch) if entry is not None else (0, 0)
        remote = entry.entity if entry is not None else None
        if desired is None:
            if entry is None:
                blocked.append(
                    HistoryBlock(
                        id=identifier, reason="delete_requires_base", source_changes=sources
                    )
                )
                continue
            if type(remote) is RemovalRecord and remote.reason == "user_deleted":
                unchanged.append(identifier)
                continue
            action = "delete"
        elif type(remote) is RemovalRecord:
            if not pending_restore:
                if remote.reason == "retention":
                    unchanged.append(identifier)
                else:
                    blocked.append(
                        HistoryBlock(id=identifier, reason="deleted", source_changes=sources)
                    )
                continue
            action = "restore"
        elif remote is None:
            action = "create"
        elif pending_restore:
            action = "restore"
        elif desired == remote and not pending_prune:
            unchanged.append(identifier)
            continue
        else:
            action = (
                "prune"
                if pending_prune or _nodes(remote.current.task) - _nodes(desired.current.task)
                else "put"
            )
        proposals.append(
            HistoryProposal(
                id=identifier,
                action=action,
                base_revision=revision,
                delete_epoch=epoch,
                payload=desired,
                source_changes=sources,
                barrier_changes=tuple(
                    x for x in sources if x in candidate.prune_changes | candidate.restore_changes
                )
                if action in ("restore", "prune")
                else (),
            )
        )
    sequence = {c.id: c.sequence for c in changes}
    detached_at = {c.before.id: c.sequence for c in changes if c.kind == "detach"}
    covered = {
        source
        for identifier, candidate in candidates.items()
        for source in candidate.sources
        if identifier not in handled or sequence[source] <= detached_at[identifier]
    }
    return HistoryPlan(
        proposals=tuple(proposals),
        blocked=tuple(blocked),
        unchanged=tuple(unchanged),
        remaining_changes=tuple(change.id for change in changes),
        projected_changes=tuple(c.id for c in changes if c.id in covered),
    )
