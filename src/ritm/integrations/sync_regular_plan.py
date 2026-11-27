"""One next regular command against explicit server state. No SQL, send or guessed receipts."""

from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID

from ritm.domain.recurrence import scheduled_day
from ritm.integrations.sync_codec import (
    CalendarRecord,
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    TaskRecord,
    encode_entity,
)
from ritm.integrations.sync_operations import occurrence_id, rollover_record
from ritm.integrations.sync_pages import MAX_SNAPSHOT_ITEMS, MAX_STAGED_BYTES, SnapshotEntry
from ritm.integrations.sync_regular_changes import (
    checked_counter,
    checked_entity,
    checked_uuid,
    task_node_ids,
    validate_regular_journal,
)
from ritm.integrations.sync_transitions import calendar_day


@dataclass(frozen=True, kw_only=True)
class RegularBase:
    id: UUID
    entry: SnapshotEntry | None = field(repr=False)


@dataclass(frozen=True, kw_only=True)
class RegularContext:
    server_id: UUID
    settings: CalendarRecord
    moment: datetime
    bases: tuple[RegularBase, ...] = field(repr=False)
    identities: tuple[tuple[UUID, UUID], ...] = field(repr=False)
    occupied_ids: frozenset[UUID] = frozenset()
    frozen_changes: frozenset[UUID] = frozenset()
    acknowledged_barriers: frozenset[UUID] = frozenset()
    acknowledged_reschedules: frozenset[UUID] = frozenset()


@dataclass(frozen=True, kw_only=True)
class RegularProposal:
    id: UUID  # Target rule ID, never a network op_id.
    action: str
    base_revision: int
    delete_epoch: int
    payload: RegularRecord | None = field(repr=False)
    identities: tuple[tuple[UUID, UUID], ...] = field(repr=False)
    source_changes: tuple[UUID, ...]
    barrier_changes: tuple[UUID, ...] = ()
    transition_change: UUID | None = None


@dataclass(frozen=True, kw_only=True)
class RegularBlock:
    id: UUID
    reason: str
    source_changes: tuple[UUID, ...]


@dataclass(frozen=True, kw_only=True)
class RegularPlan:
    proposals: tuple[RegularProposal, ...] = field(repr=False)
    blocked: tuple[RegularBlock, ...]
    unchanged: tuple[UUID, ...]
    remaining_changes: tuple[UUID, ...]


@dataclass
class _Rule:
    snapshots: list = field(default_factory=list, repr=False)
    changes: list = field(default_factory=list, repr=False)
    terminal: str | None = None
    terminal_root: UUID | None = None
    new: bool = False
    blocked: str | None = None


def _context(context, server_id):
    if type(context) is not RegularContext:
        raise ValueError("Нужен явный контекст текущих повторов")
    checked_uuid(server_id)
    checked_uuid(context.server_id)
    if server_id != context.server_id:
        raise ValueError("Контекст относится к другому поколению сервера")
    today = calendar_day(context.settings, context.moment)
    for values in (context.bases, context.identities):
        if type(values) is not tuple or len(values) > MAX_SNAPSHOT_ITEMS:
            raise ValueError("Превышены границы контекста повторов")
    for values in (
        context.occupied_ids,
        context.frozen_changes,
        context.acknowledged_barriers,
        context.acknowledged_reschedules,
    ):
        if type(values) is not frozenset or len(values) > MAX_SNAPSHOT_ITEMS:
            raise ValueError("Превышены границы контекста повторов")
        for value in values:
            checked_uuid(value)
    if context.frozen_changes & (context.acknowledged_barriers | context.acknowledged_reschedules):
        raise ValueError("Противоречивые подтверждения барьеров")
    identities, inverse = {}, set()
    for pair in context.identities:
        if type(pair) is not tuple or len(pair) != 2:
            raise ValueError("Некорректное соответствие идентификаторов")
        local, remote = pair
        checked_uuid(local)
        checked_uuid(remote)
        if local in identities or remote in inverse:
            raise ValueError("Неоднозначное соответствие идентификаторов")
        identities[local] = remote
        inverse.add(remote)
    bases, size, used = {}, 0, set()
    for base in context.bases:
        if type(base) is not RegularBase:
            raise ValueError("Некорректное основание повторов")
        checked_uuid(base.id)
        if base.id in bases:
            raise ValueError("Повтор основания расписания")
        entry = base.entry
        if entry is not None:
            if type(entry) is not SnapshotEntry:
                raise ValueError("Некорректное основание повторов")
            checked_counter(entry.revision, positive=True)
            checked_counter(entry.delete_epoch)
            record = entry.entity
            checked_entity(record)
            size += len(encode_entity(record))
            if type(record) is RegularRecord:
                ids = task_node_ids(record.current.task)
                if record.id != base.id or ids & used:
                    raise ValueError("Конфликт идентификаторов серверных расписаний")
                used.update(ids)
            elif type(record) is RemovalRecord:
                if (
                    record.entity_kind != "regular"
                    or record.entity_id != base.id
                    or record.revision != entry.revision
                    or record.delete_epoch != entry.delete_epoch
                ):
                    raise ValueError("Основание не соответствует расписанию")
            else:
                raise ValueError("Основание не соответствует расписанию")
        bases[base.id] = entry
    if size > MAX_STAGED_BYTES:
        raise ValueError("Контекст повторов слишком велик")
    return bases, identities, today


def _rules(changes):
    validate_regular_journal(changes, max_items=MAX_SNAPSHOT_ITEMS, max_bytes=MAX_STAGED_BYTES)
    rules = {}
    for change in changes:
        records = [
            record for record in (change.before, change.after) if type(record) is RegularRecord
        ]
        keys = set(record.id for record in records)
        if change.kind == "delete":
            keys.add(change.rule_id)
        for key in sorted(keys, key=str):
            rule = rules.setdefault(key, _Rule())
            rule.changes.append(change)
            rule.snapshots.extend(record for record in records if record.id == key)
        if change.kind == "save" and change.before is None:
            rules[change.after.id].new = True
        if change.kind == "save" and type(change.before) in (TaskRecord, OccurrenceRecord):
            rules[change.after.id].blocked = "atomic_type_transition"
        if (
            type(change.before) is RegularRecord
            and type(change.after) is RegularRecord
            and change.before.id != change.after.id
        ):
            for key in keys:
                rules[key].blocked = "atomic_schedule_change"
        if change.kind in ("stop", "delete"):
            rule = rules[change.rule_id]
            rule.terminal, rule.terminal_root = change.kind, change.root_id
    for rule in rules.values():
        slots = {}
        for snapshot in rule.snapshots:
            root = snapshot.current.task.id
            if snapshot.current_day in slots and slots[snapshot.current_day] != root:
                raise ValueError("Один день расписания связан с разными корнями")
            slots[snapshot.current_day] = root
    return rules


def _merge_pairs(identities, pairs):
    result = dict(identities)
    inverse = {remote: local for local, remote in result.items()}
    for local, remote in pairs:
        if (
            local in result
            and result[local] != remote
            or remote in inverse
            and inverse[remote] != local
        ):
            return None
        result[local], inverse[remote] = remote, local
    return result


def _map_record(record, identities):
    task = record.current.task
    if not task_node_ids(task) <= identities.keys():
        return None
    children = tuple(
        replace(
            child,
            id=identities[child.id],
            items=tuple(replace(item, id=identities[item.id]) for item in child.items),
        )
        for child in task.subtasks
    )
    return replace(
        record,
        current=replace(
            record.current, task=replace(task, id=identities[task.id], subtasks=children)
        ),
    )


def _birth_pairs(local, canonical):
    a, b = local.current.task, canonical.current.task
    if len(a.subtasks) != len(b.subtasks):
        return None
    pairs = [(a.id, b.id)]
    for x, y in zip(a.subtasks, b.subtasks, strict=True):
        if len(x.items) != len(y.items):
            return None
        pairs.append((x.id, y.id))
        pairs.extend((i.id, j.id) for i, j in zip(x.items, y.items, strict=True))
    return tuple(pairs) if _map_record(local, dict(pairs)) == canonical else None


def _barriers(rule, day, acknowledged):
    removed, initial, restore, prune, invalid = set(), None, False, False, False
    markers = []
    for change in rule.changes:
        before, after = change.before, change.after
        for record in (before, after):
            if type(record) is RegularRecord and record.current_day == day and initial is None:
                initial = task_node_ids(record.current.task)
        if (
            type(before) is RegularRecord
            and type(after) is RegularRecord
            and before.current_day == after.current_day == day
        ):
            if change.id in acknowledged:
                continue
            old, new = task_node_ids(before.current.task), task_node_ids(after.current.task)
            if change.kind == "restore":
                markers.append(change.id)
                restore, invalid = True, False
                removed.clear()
            elif change.kind == "save":
                invalid |= bool(removed & new)
                removed.update(old - new)
                prune |= bool(old - new)
                if old - new:
                    markers.append(change.id)
    return initial or set(), restore, prune, invalid, tuple(markers)


def _next(rule, base, identities, context, today):
    if rule.blocked:
        return rule.blocked
    if set(c.id for c in rule.changes) & context.frozen_changes:
        return "frozen_change"
    if not rule.snapshots:
        if rule.terminal != "delete":
            return "local_snapshot_required"
        if base is None:
            return "terminal_requires_base"
        if type(base.entity) is RemovalRecord:
            return None
        root = base.entity.current.task.id
        if identities.get(rule.terminal_root) != root:
            return "identity_mapping_required"
        return "delete", None, ((rule.terminal_root, root),)
    latest = rule.snapshots[-1]
    due = scheduled_day(latest.first_day, latest.interval_days, today)
    if rule.terminal is None and latest.current_day != due:
        return "local_refresh_required" if latest.current_day < due else "future_local_day"
    if base is None:
        if rule.terminal is not None:
            return "terminal_requires_base"
        if not rule.new:
            return "creation_provenance_required"
        ids = task_node_ids(latest.current.task)
        if ids & context.occupied_ids:
            return "occupied_identity"
        pairs = tuple((key, key) for key in sorted(ids, key=str))
        mapping = _merge_pairs(identities, pairs)
        if mapping is None:
            return "identity_conflict"
        return "create", latest, pairs
    if type(base.entity) is RemovalRecord:
        return None if rule.terminal == "delete" else "deleted"
    remote = base.entity
    if remote.current_day > latest.current_day:
        return "server_ahead"
    if (
        rule.terminal is not None
        and remote.current_day < latest.current_day
        and latest.current_day != due
    ):
        return "terminal_day_conflict"
    same_day = [r for r in rule.snapshots if r.current_day == remote.current_day]
    if not same_day:
        return "local_snapshot_required"
    desired, birth = same_day[-1], same_day[0]
    if desired.first_day != remote.first_day or desired.interval_days != remote.interval_days:
        return "schedule_conflict"
    mapping = dict(identities)
    if (
        remote.current.task.id == occurrence_id(remote.id, remote.current_day)
        and not task_node_ids(desired.current.task) <= mapping.keys()
    ):
        pairs = _birth_pairs(birth, remote)
        if pairs is not None:
            mapping = _merge_pairs(mapping, pairs)
            if mapping is None:
                return "identity_conflict"
    # Deletion only needs proof of the exact current root, never a guessed child map.
    if rule.terminal == "delete" and remote.current_day == latest.current_day:
        if rule.terminal_root != latest.current.task.id:
            return "terminal_root_conflict"
        if latest.current.task.id not in mapping and remote.current.task.id == occurrence_id(
            remote.id, remote.current_day
        ):
            mapping = _merge_pairs(mapping, ((latest.current.task.id, remote.current.task.id),))
        if mapping is None or mapping.get(latest.current.task.id) != remote.current.task.id:
            return "identity_mapping_required"
        return "delete", None, ((latest.current.task.id, remote.current.task.id),)
    mapped = _map_record(desired, mapping)
    if mapped is None:
        return "identity_mapping_required"
    if mapped.current.task.id != remote.current.task.id:
        return "root_conflict"
    # Other rules / ordinary records may own IDs outside this known aggregate.
    if task_node_ids(mapped.current.task) & (
        context.occupied_ids - task_node_ids(remote.current.task)
    ):
        return "occupied_identity"
    original, restore, prune, invalid, _ = _barriers(
        rule, remote.current_day, context.acknowledged_barriers
    )
    if invalid:
        return "node_restore_required"
    if not restore:
        old = {mapping[key] for key in original if key in mapping}
        if (old - task_node_ids(remote.current.task)) & task_node_ids(mapped.current.task):
            return "node_restore_required"
    pairs = tuple(
        (key, mapping[key]) for key in sorted(task_node_ids(desired.current.task), key=str)
    )
    if mapped != remote or restore or prune:
        action = (
            "restore"
            if restore
            else (
                "prune"
                if prune or task_node_ids(remote.current.task) - task_node_ids(mapped.current.task)
                else "put"
            )
        )
        return action, mapped, pairs
    if latest.current_day > remote.current_day:
        canonical = rollover_record(remote, due)
        births = [r for r in rule.snapshots if r.current_day == due]
        pairs = _birth_pairs(births[0], canonical)
        if pairs is None:
            return "template_transition_required"
        if _merge_pairs(identities, pairs) is None:
            return "identity_conflict"
        if task_node_ids(canonical.current.task) & context.occupied_ids:
            return "occupied_identity"
        return "rollover", canonical, pairs
    if rule.terminal == "stop":
        if mapping.get(rule.terminal_root) != remote.current.task.id:
            return "terminal_root_conflict"
        return "stop_regular", None, pairs
    return None


def _shape(task):
    return (
        task.id,
        tuple((child.id, tuple(item.id for item in child.items)) for child in task.subtasks),
    )


def _reschedules(changes, rules, bases, identities, context, today):
    edges = [
        c
        for c in changes
        if type(c.before) is RegularRecord
        and type(c.after) is RegularRecord
        and c.before.id != c.after.id
    ]
    counts = {}
    for edge in edges:
        for key in (edge.before.id, edge.after.id):
            counts[key] = counts.get(key, 0) + 1
    handled, proposed, blocked, unchanged = set(), [], [], []
    for edge in edges:
        old, new = edge.before.id, edge.after.id
        sources = tuple(dict.fromkeys(c.id for key in (old, new) for c in rules[key].changes))
        reason = None
        if counts[old] > 1 or counts[new] > 1:
            reason = "reschedule_chain_required"
        elif context.frozen_changes.intersection(sources):
            reason = "frozen_change"
        elif old not in bases or new not in bases:
            reason = "unknown_base"
        elif edge.id in context.acknowledged_reschedules:
            if (
                bases[old] is None
                or type(bases[old].entity) is not RemovalRecord
                or bases[new] is None
                or type(bases[new].entity) is not RegularRecord
            ):
                reason = "reschedule_ack_conflict"
            else:
                # The verified receipt and both effects are supplied by the future ledger.
                handled.add(old)
                unchanged.append(old)
                rules[new].blocked = None
                continue
        elif bases[old] is None:
            reason = "old_rule_absent"
        elif type(bases[old].entity) is RemovalRecord:
            reason = "reschedule_confirmation_required"
        elif bases[new] is not None:
            reason = "new_rule_exists"
        if not reason:
            before = bases[old]
            pending = _next(replace(rules[old], blocked=None), before, identities, context, today)
            if type(pending) is str:
                reason = pending
            elif pending is not None:
                action, payload, pairs = pending
                proposed.append(
                    _proposal(old, action, payload, pairs, rules[old], before, context, sources)
                )
            else:
                target = _map_record(edge.after, identities)
                if target is None:
                    reason = "identity_mapping_required"
                elif target.current_day != scheduled_day(
                    target.first_day, target.interval_days, today
                ):
                    reason = "reschedule_day_conflict"
                elif _shape(target.current.task) != _shape(before.entity.current.task):
                    reason = "reschedule_tree_change"
                elif (target.first_day, target.interval_days) == (
                    before.entity.first_day,
                    before.entity.interval_days,
                ):
                    reason = "reschedule_schedule_conflict"
                elif task_node_ids(target.current.task) & (
                    context.occupied_ids - task_node_ids(before.entity.current.task)
                ):
                    reason = "occupied_identity"
                else:
                    pairs = tuple(
                        (key, identities[key])
                        for key in sorted(task_node_ids(edge.after.current.task), key=str)
                    )
                    proposed.append(
                        _proposal(
                            old,
                            "reschedule_regular",
                            target,
                            pairs,
                            rules[old],
                            before,
                            context,
                            sources,
                            transition_change=edge.id,
                        )
                    )
        handled.update((old, new))
        if reason:
            for key in (old, new):
                if not any(b.id == key for b in blocked):
                    blocked.append(RegularBlock(id=key, reason=reason, source_changes=sources))
    return handled, proposed, blocked, unchanged


def _proposal(key, action, payload, pairs, rule, base, context, sources, *, transition_change=None):
    if payload is not None:
        checked_entity(payload)
    return RegularProposal(
        id=key,
        action=action,
        base_revision=base.revision if base is not None else 0,
        delete_epoch=base.delete_epoch if base is not None else 0,
        payload=payload,
        identities=pairs,
        source_changes=sources,
        transition_change=transition_change,
        barrier_changes=_barriers(rule, payload.current_day, context.acknowledged_barriers)[4]
        if action in ("prune", "restore")
        else (),
    )


def plan_regular(changes, context: RegularContext, *, server_id: UUID) -> RegularPlan:
    """One proposal per rule; never infer dependent revisions or retire source facts."""
    bases, identities, today = _context(context, server_id)
    rules = _rules(changes)
    handled, proposals, blocked, unchanged = _reschedules(
        changes, rules, bases, identities, context, today
    )
    for key, rule in rules.items():
        if key in handled:
            continue
        sources = tuple(c.id for c in rule.changes)
        decision = (
            "unknown_base"
            if key not in bases
            else _next(rule, bases[key], identities, context, today)
        )
        if type(decision) is str:
            blocked.append(RegularBlock(id=key, reason=decision, source_changes=sources))
        elif decision is None:
            unchanged.append(key)
        else:
            action, payload, pairs = decision
            proposals.append(
                _proposal(key, action, payload, pairs, rule, bases[key], context, sources)
            )
    return RegularPlan(
        proposals=tuple(proposals),
        blocked=tuple(blocked),
        unchanged=tuple(unchanged),
        remaining_changes=tuple(c.id for c in changes),
    )
