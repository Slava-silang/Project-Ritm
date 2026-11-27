"""Ordinary task projection after leaving a recurrence; no SQL or transport."""

from dataclasses import dataclass, field
from uuid import UUID

from ritm.integrations.sync_codec import OccurrenceRecord, RegularRecord, RemovalRecord, TaskRecord
from ritm.integrations.sync_regular_changes import mapped_task_record, task_node_ids


@dataclass(frozen=True, kw_only=True)
class LineageProposal:
    id: UUID
    action: str
    base_revision: int
    delete_epoch: int
    payload: TaskRecord | None = field(repr=False)
    identities: tuple = field(repr=False)
    source_changes: tuple[UUID, ...]
    barrier_changes: tuple[UUID, ...] = ()
    transition_change: UUID | None = None
    target_kind: str = "task"


@dataclass(frozen=True, kw_only=True)
class LineagePlan:
    proposals: tuple = field(repr=False)
    blocked: tuple[str, ...]
    projected_changes: tuple[UUID, ...]


def ordinary_changes(changes, entries, identities):
    roles = {}
    # Live representations take precedence over their older type_changed markers.
    for entry in entries.values():
        record = entry.entity
        if type(record) is RemovalRecord:
            root = record.entity_id if record.entity_kind == "task" else record.root_id
            if root is not None and record.reason != "type_changed":
                roles[root] = record.entity_kind
    for entry in entries.values():
        record = entry.entity
        if type(record) is TaskRecord:
            roles[record.task.id] = "task"
        elif type(record) in (RegularRecord, OccurrenceRecord):
            roles[record.current.task.id] = "history"
    result = set()
    for change in changes:
        root = identities.get(change.root_id, change.root_id)
        if change.kind == "root_delete" and roles.get(root) == "task":
            result.add(change.id)
        for record in (change.before, change.after):
            if type(record) is TaskRecord:
                roles[identities.get(record.task.id, record.task.id)] = "task"
            elif type(record) in (RegularRecord, OccurrenceRecord):
                roles[identities.get(record.current.task.id, record.current.task.id)] = "history"
        if change.kind in ("root_save", "root_restore") and type(change.after) is TaskRecord:
            result.add(change.id)
    return frozenset(result)


def plan_lineage(changes, entries, identities, ordinary_ids, barriers, promotions):
    groups = {}
    for change in changes:
        if change.id in ordinary_ids:
            groups.setdefault(change.root_id, []).append(change)
    promoted = {}
    for change in changes:
        if change.kind == "save" and type(change.before) is TaskRecord and change.id in promotions:
            promoted[change.before.task.id] = change.sequence
    proposals, blocked = [], []
    for local_root, frames in groups.items():
        active = [c for c in frames if c.sequence > promoted.get(local_root, -1)]
        if not active:
            continue
        root = identities.get(local_root)
        if root is None:
            blocked.append("lineage_identity_required")
            continue
        entry = entries.get(("task", root))
        desired = active[-1].after
        restore_ids = {c.id for c in active if c.kind == "root_restore"}
        prune_ids = {
            c.id
            for c in active
            if type(c.before) is TaskRecord
            and type(c.after) is TaskRecord
            and task_node_ids(c.before.task) - task_node_ids(c.after.task)
        }
        restore = bool(restore_ids - barriers)
        prune = bool(prune_ids - barriers)
        if desired is not None:
            desired = mapped_task_record(desired, identities)
            if desired is None:
                blocked.append("lineage_identity_required")
                continue
        remote = entry.entity if entry is not None else None
        fallback = None
        if entry is None:
            fallback = next(
                (
                    e
                    for e in entries.values()
                    if (type(e.entity) is OccurrenceRecord and e.entity.current.task.id == root)
                    or (
                        type(e.entity) is RemovalRecord
                        and e.entity.entity_kind == "occurrence"
                        and e.entity.root_id == root
                    )
                ),
                None,
            )
        if fallback is not None:
            marker = fallback.entity
            if desired is None:
                if type(marker) is RemovalRecord and marker.reason == "user_deleted":
                    continue
                action = "delete"
            elif restore:
                action = "detach_occurrence"
            elif type(marker) is RemovalRecord and marker.reason == "retention":
                continue
            else:
                blocked.append("ordinary_transition_required")
                continue
            proposals.append(
                LineageProposal(
                    id=(marker.id if type(marker) is OccurrenceRecord else marker.entity_id),
                    target_kind="occurrence",
                    action=action,
                    payload=desired,
                    base_revision=fallback.revision,
                    delete_epoch=fallback.delete_epoch,
                    source_changes=tuple(c.id for c in active),
                    identities=tuple(
                        (a, b)
                        for a, b in identities.items()
                        if b in (task_node_ids(desired.task) if desired else {root})
                    ),
                    barrier_changes=tuple(c.id for c in active if c.id in restore_ids | prune_ids)
                    if restore
                    else (),
                )
            )
            continue
        if desired is None:
            if type(remote) is RemovalRecord and remote.reason == "user_deleted":
                continue
            action = "forget_task" if entry is None else "delete"
        elif entry is None:
            if not restore:
                blocked.append("ordinary_transition_required")
                continue
            action = "create"
        elif type(remote) is RemovalRecord:
            if not restore:
                if remote.reason == "retention":
                    continue
                blocked.append("lineage_deleted")
                continue
            action = "restore"
        else:
            initial = next((c.before for c in active if type(c.before) is TaskRecord), None)
            if not restore and initial is not None:
                original = mapped_task_record(initial, identities)
                if original is not None and (
                    (task_node_ids(original.task) - task_node_ids(remote.task))
                    & task_node_ids(desired.task)
                ):
                    blocked.append("node_restore_required")
                    continue
            if restore:
                action = "restore"
            elif desired == remote and not prune:
                continue
            else:
                action = (
                    "prune"
                    if prune or task_node_ids(remote.task) - task_node_ids(desired.task)
                    else "put"
                )
        used = task_node_ids(desired.task) if desired else {root}
        proposals.append(
            LineageProposal(
                id=root,
                action=action,
                payload=desired,
                base_revision=entry.revision if entry else 0,
                delete_epoch=entry.delete_epoch if entry else 0,
                source_changes=tuple(c.id for c in active),
                identities=tuple(
                    (local, remote) for local, remote in identities.items() if remote in used
                ),
                barrier_changes=tuple(c.id for c in active if c.id in restore_ids | prune_ids)
                if action in ("restore", "prune", "create")
                else (),
            )
        )
    return LineagePlan(
        proposals=tuple(proposals),
        blocked=tuple(blocked),
        projected_changes=tuple(c.id for c in changes if c.id in ordinary_ids),
    )
