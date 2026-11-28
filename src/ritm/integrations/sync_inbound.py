"""Pure three-way inbound preview. No database writes, acknowledgements or rewards."""

from dataclasses import dataclass, field, replace
from uuid import UUID

from ritm.integrations._sync_wire import _id_text
from ritm.integrations.sync_codec import (
    BlockRecord,
    CalendarRecord,
    CategoryRecord,
    Entity,
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    TaskRecord,
    encode_entity,
)
from ritm.integrations.sync_operations import target_identity
from ritm.integrations.sync_pages import (
    MAX_PAGE_ITEMS,
    MAX_SNAPSHOT_ITEMS,
    MAX_STAGED_BYTES,
    Snapshot,
    SnapshotCollector,
    SnapshotEntry,
    SnapshotPage,
    _check_graph,
    decode_page,
    encode_page,
)

Key = tuple[str, UUID]
LIVE_TYPES = (
    TaskRecord,
    RegularRecord,
    OccurrenceRecord,
    CategoryRecord,
    BlockRecord,
    CalendarRecord,
)


@dataclass(frozen=True, kw_only=True)
class InboundDecision:
    key: Key
    action: str
    reason: str
    local: Entity | None = field(default=None, repr=False)
    incoming: SnapshotEntry | None = field(default=None, repr=False)


@dataclass(frozen=True, kw_only=True)
class InboundPlan:
    server_id: UUID
    snapshot_id: UUID
    set_revision: int
    bootstrap: bool
    decisions: tuple[InboundDecision, ...] = field(repr=False)

    @property
    def needs_resolution(self):
        return any(d.action in ("conflict", "defer") for d in self.decisions)


def _key(entity):
    return (
        (entity.entity_kind, entity.entity_id)
        if type(entity) is RemovalRecord
        else target_identity(entity)
    )


def _root(entity):
    if type(entity) is TaskRecord:
        return entity.task.id
    if type(entity) in (RegularRecord, OccurrenceRecord):
        return entity.current.task.id
    if type(entity) is RemovalRecord:
        if entity.entity_kind == "task":
            return entity.entity_id
        return entity.root_id
    return None


def _related_root(entity):
    return entity.block.task_id if type(entity) is BlockRecord else _root(entity)


def _ids(entity):
    result = {_key(entity)[1]}
    task = (
        entity.task
        if type(entity) is TaskRecord
        else entity.current.task
        if type(entity) in (RegularRecord, OccurrenceRecord)
        else None
    )
    if task is not None:
        result.add(task.id)
        for child in task.subtasks:
            result.add(child.id)
            result.update(item.id for item in child.items)
    return result


def _same_owner(owner, entity, identifier):
    if _key(owner) == _key(entity) and identifier == _key(entity)[1]:
        return True  # A rule keeps its own UUID while advancing its current root.
    root = _root(entity)
    return root is not None and _root(owner) == root


def _snapshot(snapshot, server_id):
    if type(snapshot) is not Snapshot or snapshot.server_id != server_id:
        raise ValueError("Снимок относится к другой привязке сервера")
    if type(snapshot.entries) is not tuple or len(snapshot.entries) > MAX_SNAPSHOT_ITEMS:
        raise ValueError("Превышены границы входящего снимка")
    collector = SnapshotCollector(server_id)
    for start in range(0, max(1, len(snapshot.entries)), MAX_PAGE_ITEMS):
        end = min(start + MAX_PAGE_ITEMS, len(snapshot.entries))
        collector.add(
            decode_page(
                encode_page(
                    SnapshotPage(
                        server_id=server_id,
                        snapshot_id=snapshot.snapshot_id,
                        set_revision=snapshot.set_revision,
                        total=len(snapshot.entries),
                        offset=start,
                        cursor=str(start) if start else None,
                        next_cursor=str(end) if end < len(snapshot.entries) else None,
                        entries=snapshot.entries[start:end],
                    )
                )
            )
        )
    return collector.finish()


def _protected(keys):
    if type(keys) is not frozenset or len(keys) > MAX_SNAPSHOT_ITEMS:
        raise ValueError("Нужен ограниченный набор защищённых сущностей")
    for key in keys:
        if (
            type(key) is not tuple
            or len(key) != 2
            or key[0]
            not in (
                "task",
                "regular",
                "occurrence",
                "category",
                "block",
                "calendar_settings",
                "node",
            )
        ):
            raise ValueError("Неизвестная защищённая сущность")
        _id_text(key[1])
    return keys


def plan_inbound(*, local, incoming, accepted=None, server_id, protected=frozenset()):
    """Preview against the last APPLIED snapshot, never merely the outgoing cache.

    local must contain canonical wire identities and one representation per root
    (ordinary/current/history), including local history outside cloud limits.
    protected is supplied by the owning transaction from all pending facts and
    intents; equality of bodies is not permission to discard an unsent command.
    """
    _id_text(server_id)
    incoming = _snapshot(incoming, server_id)
    accepted = _snapshot(accepted, server_id) if accepted is not None else None
    protected = _protected(protected)
    if type(local) is not tuple or len(local) > MAX_SNAPSHOT_ITEMS:
        raise ValueError("Нужен ограниченный локальный снимок без усечения")
    if any(type(entity) not in LIVE_TYPES for entity in local):
        raise ValueError("Локальный снимок должен содержать только живые DTO")
    if sum(len(encode_entity(entity)) for entity in local) > MAX_STAGED_BYTES:
        raise ValueError("Локальный снимок слишком велик")
    _check_graph(tuple(SnapshotEntry(revision=1, delete_epoch=0, entity=e) for e in local))
    current = {_key(entity): entity for entity in local}
    remote = {_key(entry.entity): entry for entry in incoming.entries}
    before = {_key(entry.entity): entry for entry in accepted.entries} if accepted else {}
    if accepted is not None:
        if incoming.set_revision < accepted.set_revision:
            raise ValueError("Нельзя применять более старый снимок")
        if incoming.set_revision == accepted.set_revision and remote != before:
            raise ValueError("Противоречащие снимки одной ревизии")
        for key in before.keys() & remote.keys():
            old, new = before[key], remote[key]
            if new.revision < old.revision or new.delete_epoch < old.delete_epoch:
                raise ValueError("Сущность вернулась к прежней версии")
            if new.revision == old.revision and new != old:
                raise ValueError("Противоречащие данные одной версии")
            if (
                type(old.entity) is RemovalRecord
                and type(new.entity) is not RemovalRecord
                and new.delete_epoch <= old.delete_epoch
            ):
                raise ValueError("Восстановление требует нового поколения")
    owners = {identifier: entity for entity in local for identifier in _ids(entity)}
    local_roots = {_root(e): e for e in local if _root(e) is not None}
    remote_roots = {
        _root(e.entity): e.entity
        for e in incoming.entries
        if type(e.entity) in (TaskRecord, RegularRecord, OccurrenceRecord)
    }
    protected_roots = set()
    for key in protected:
        owner = owners.get(key[1])
        if _root(owner) is not None:
            protected_roots.add(_root(owner))
        for entity in (
            current.get(key),
            before[key].entity if key in before else None,
            remote[key].entity if key in remote else None,
        ):
            root = _root(entity)
            if root is not None:
                protected_roots.add(root)
    decisions = []
    for key in sorted(current.keys() | remote.keys(), key=lambda k: (k[0], str(k[1]))):
        entry = remote.get(key)
        entity = entry.entity if entry else None
        local_entity = current.get(key)
        root = _root(entity) or _root(local_entity)
        if local_entity is None and root is not None:
            local_entity = local_roots.get(root)
        base = before.get(key)
        base_entity = base.entity if base is not None else None
        if base_entity is None and root is not None:
            base_entity = next(
                (
                    e.entity
                    for e in before.values()
                    if _root(e.entity) == root and type(e.entity) in LIVE_TYPES
                ),
                None,
            )
        action, reason = "keep", "unchanged"
        if (
            key in protected
            or root in protected_roots
            or (_related_root(entity) or _related_root(local_entity)) in protected_roots
        ):
            action, reason = "defer", "local_pending"
        elif entry is None:
            action, reason = "retain", "remote_absence_is_not_deletion"
        elif type(entity) is RemovalRecord:
            if entity.reason in ("retention", "type_changed"):
                action, reason = "retain", entity.reason
            elif entity.entity_kind == "node":
                action, reason = "marker", "node_owned_by_root"
            elif local_entity is None:
                action, reason = "marker", "already_absent"
            elif accepted is None or local_entity != base_entity:
                action, reason = (
                    "conflict",
                    "bootstrap_delete" if accepted is None else "local_changed",
                )
            elif entity.entity_kind == "regular":
                action, reason = "retire_rule", "remote_rule_closed"
            else:
                action, reason = "archive", "remote_user_deleted"
        elif (
            type(entity) is BlockRecord
            and type(local_entity) is BlockRecord
            and entity.block.task_id != local_entity.block.task_id
        ):
            action, reason = "conflict", "calendar_parent_changed"
        elif local_entity == entity:
            pass
        elif any(
            identifier in owners and not _same_owner(owners[identifier], entity, identifier)
            for identifier in _ids(entity)
        ):
            action, reason = "conflict", "local_identity_occupied"
        elif local_entity is None:
            if base_entity is not None and type(base_entity) in LIVE_TYPES:
                action, reason = "conflict", "local_missing"
            else:
                action, reason = "insert", "remote_new"
        elif accepted is None:
            action, reason = "conflict", "bootstrap_difference"
        elif local_entity != base_entity:
            action, reason = "conflict", "local_changed"
        elif _key(local_entity) != key or _root(local_entity) != _root(entity):
            action, reason = "transition", "remote_role_changed"
        else:
            action, reason = "update", "remote_changed"
        decisions.append(
            InboundDecision(
                key=key, action=action, reason=reason, local=local_entity, incoming=entry
            )
        )
    # Never archive a physical tree that is represented in another incoming role.
    decisions = [
        _replace_decision(d, "retain", "represented_in_another_role")
        if d.action == "archive" and _root(d.local) in remote_roots
        else d
        for d in decisions
    ]
    unresolved_categories = set()
    retained_categories = set()
    for decision in decisions:
        if decision.action in ("retain", "retire_rule") and type(decision.local) in (
            TaskRecord,
            RegularRecord,
            OccurrenceRecord,
        ):
            record = decision.local
            task = record.task if type(record) is TaskRecord else record.current.task
            retained_categories.add(task.category_id)
        if decision.action in ("conflict", "defer") and type(decision.local) in (
            TaskRecord,
            RegularRecord,
            OccurrenceRecord,
        ):
            record = decision.local
            task = record.task if type(record) is TaskRecord else record.current.task
            unresolved_categories.add(task.category_id)
    decisions = [
        _replace_decision(d, "defer", "referenced_by_pending_root")
        if d.key[0] == "category" and d.action == "archive" and d.key[1] in unresolved_categories
        else _replace_decision(d, "retain", "referenced_by_local_history")
        if d.key[0] == "category" and d.action == "archive" and d.key[1] in retained_categories
        else d
        for d in decisions
    ]
    blocked_categories = {
        d.key[1] for d in decisions if d.key[0] == "category" and d.action in ("defer", "conflict")
    }
    for i, decision in enumerate(decisions):
        entity = decision.incoming.entity if decision.incoming else None
        if decision.action in ("insert", "update", "transition") and type(entity) in (
            TaskRecord,
            RegularRecord,
            OccurrenceRecord,
        ):
            task = entity.task if type(entity) is TaskRecord else entity.current.task
            if task.category_id in blocked_categories:
                decisions[i] = _replace_decision(decision, "defer", "category_unresolved")
    blocked_roots = {
        _root(d.local) or (_root(d.incoming.entity) if d.incoming else None)
        for d in decisions
        if d.action in ("defer", "conflict")
    } - {None}
    for i, decision in enumerate(decisions):
        entity = decision.incoming.entity if decision.incoming else None
        if decision.action in ("insert", "update", "transition", "archive"):
            root = _related_root(entity) or _related_root(decision.local)
            if decision.key[0] == "block" and root in blocked_roots:
                decisions[i] = _replace_decision(decision, "defer", "parent_unresolved")
    return InboundPlan(
        server_id=server_id,
        snapshot_id=incoming.snapshot_id,
        set_revision=incoming.set_revision,
        bootstrap=accepted is None,
        decisions=tuple(decisions),
    )


def _replace_decision(decision, action, reason):
    return replace(decision, action=action, reason=reason)
