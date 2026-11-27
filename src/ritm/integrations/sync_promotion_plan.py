"""Compound ordinary/history to regular proposals, without writes or authority."""

from dataclasses import dataclass, field, replace
from uuid import UUID

from ritm.domain.recurrence import scheduled_day
from ritm.integrations.sync_codec import OccurrenceRecord, RegularRecord, RemovalRecord, TaskRecord
from ritm.integrations.sync_pages import MAX_SNAPSHOT_ITEMS, SnapshotEntry
from ritm.integrations.sync_regular_changes import (
    checked_counter,
    checked_entity,
    checked_uuid,
    mapped_task_record,
    task_node_ids,
)


@dataclass(frozen=True, kw_only=True)
class PromotionBase:
    kind: str
    id: UUID
    entry: SnapshotEntry | None = field(repr=False)


def promotion_sources(context):
    values = context.promotion_bases
    if type(values) is not tuple or len(values) > MAX_SNAPSHOT_ITEMS:
        raise ValueError("Некорректные основания превращения задач")
    result = {}
    size = 0
    for base in values:
        if type(base) is not PromotionBase or base.kind not in ("task", "occurrence"):
            raise ValueError("Некорректное основание превращения задачи")
        checked_uuid(base.id)
        key = (base.kind, base.id)
        if key in result:
            raise ValueError("Повтор основания превращения задачи")
        entry = base.entry
        if entry is not None:
            if type(entry) is not SnapshotEntry:
                raise ValueError("Некорректный снимок превращения задачи")
            checked_counter(entry.revision, positive=True)
            checked_counter(entry.delete_epoch)
            entity = entry.entity
            checked_entity(entity)
            from ritm.integrations.sync_codec import encode_entity

            size += len(encode_entity(entity))
            if size > 32 * 1024 * 1024:
                raise ValueError("Основания превращения слишком велики")
            if type(entity) is RemovalRecord:
                valid = (
                    entity.entity_kind == base.kind
                    and entity.entity_id == base.id
                    and entity.revision == entry.revision
                    and entity.delete_epoch == entry.delete_epoch
                )
            elif base.kind == "task":
                valid = type(entity) is TaskRecord and entity.task.id == base.id
            else:
                valid = type(entity) is OccurrenceRecord and entity.id == base.id
            if not valid:
                raise ValueError("Снимок не соответствует источнику превращения")
        result[key] = entry
    return result


def prepare_promotions(changes, context, bases, identities, today):
    from ritm.integrations.sync_regular_plan import (
        RegularBlock,
        RegularProposal,
        _map_record,
        _shape,
    )

    sources = promotion_sources(context)
    handled, proposals, blocked, unchanged, released = set(), [], [], [], set()
    for change in changes:
        if change.kind != "save" or type(change.before) not in (TaskRecord, OccurrenceRecord):
            continue
        target = change.after.id
        if target in handled:
            raise ValueError("Повтор превращения в то же правило")
        if not context.promotion_bases:
            continue
        handled.add(target)
        kind = "task" if type(change.before) is TaskRecord else "occurrence"
        local = change.before if kind == "task" else change.before.current
        root = identities.get(local.task.id)
        key = (kind, root if kind == "task" else change.before.id)
        reason = None
        if change.id in context.frozen_changes:
            reason = "frozen_change"
        elif root is None:
            reason = "identity_mapping_required"
        elif key not in sources or target not in bases:
            reason = "unknown_base"
        elif change.id in context.acknowledged_promotions:
            old, new = sources[key], bases[target]
            if (
                old is None
                or type(old.entity) is not RemovalRecord
                or old.entity.reason != "type_changed"
                or new is None
                or type(new.entity) not in (RegularRecord, RemovalRecord)
                or new.revision < old.revision
                or new.delete_epoch < old.delete_epoch
            ):
                reason = "promotion_ack_conflict"
            else:
                released.add(target)
                continue
        elif sources[key] is None:
            record = change.before
            mapped_source = mapped_task_record(local, identities)
            parent = bases.get(record.rule_id) if kind == "occurrence" else None
            if mapped_source is None:
                reason = "identity_mapping_required"
            elif root in context.occupied_ids or (
                parent is not None
                and type(parent.entity) is RegularRecord
                and record.day >= parent.entity.current_day
            ):
                reason = "regular_transition_required"
            else:
                payload = (
                    mapped_source if kind == "task" else replace(record, current=mapped_source)
                )
                proposals.append(
                    RegularProposal(
                        id=key[1],
                        target_kind=kind,
                        action="create",
                        base_revision=0,
                        delete_epoch=0,
                        payload=payload,
                        identities=tuple(
                            (i, identities[i]) for i in sorted(task_node_ids(local.task))
                        ),
                        source_changes=(change.id,),
                    )
                )
                continue
        elif type(sources[key].entity) is RemovalRecord:
            reason = "live_source_required"
        elif bases[target] is not None:
            reason = "new_rule_exists"
        if not reason:
            before = sources[key]
            remote = before.entity if kind == "task" else before.entity.current
            mapped = _map_record(change.after, identities)
            if mapped is None:
                reason = "identity_mapping_required"
            elif mapped.current_day > scheduled_day(mapped.first_day, mapped.interval_days, today):
                reason = "promotion_day_conflict"
            elif (
                set(identities[i] for i in task_node_ids(local.task) if i in identities)
                - task_node_ids(remote.task)
            ) & task_node_ids(mapped.current.task):
                reason = "node_restore_required"
            else:
                proposals.append(
                    RegularProposal(
                        id=key[1],
                        action="promote_regular"
                        if (
                            mapped.current_day
                            == scheduled_day(mapped.first_day, mapped.interval_days, today)
                            and _shape(mapped.current.task) == _shape(remote.task)
                        )
                        else "promote_offline",
                        base_revision=before.revision,
                        delete_epoch=before.delete_epoch,
                        payload=mapped,
                        identities=tuple(
                            (i, identities[i])
                            for i in sorted(task_node_ids(change.after.current.task))
                        ),
                        source_changes=(change.id,),
                        transition_change=change.id,
                        target_kind=kind,
                    )
                )
        if reason:
            blocked.append(RegularBlock(id=target, reason=reason, source_changes=(change.id,)))
    return handled, proposals, blocked, unchanged, released
