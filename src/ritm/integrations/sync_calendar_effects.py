"""Calendar side effects of a verified task/series transition. No server or I/O."""

from ritm.integrations.sync_codec import (
    BlockRecord,
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    TaskRecord,
    encode_entity,
)
from ritm.integrations.sync_operations import target_identity, verify_receipt
from ritm.integrations.sync_pages import SnapshotEntry


def _key(entity):
    return (
        (entity.entity_kind, entity.entity_id)
        if type(entity) is RemovalRecord
        else target_identity(entity)
    )


def calendar_transition_effects(snapshot, operation, receipt, source_effects):
    """Attach block markers at the SAME revision as the owning source change.

    The caller validates/plans source_effects with the existing typed transition
    validators first; this helper does not authorize or execute a source command.
    """
    verify_receipt(operation, receipt)
    if receipt.status != "applied" or snapshot.server_id != operation.server_id:
        raise ValueError("Нужно подтверждение перехода того же сервера")
    if type(source_effects) is not tuple:
        raise ValueError("Нужен полный набор эффектов перехода")
    entries = {_key(e.entity): e for e in snapshot.entries}
    effects = {}
    deleted = set()
    for entry in source_effects:
        encode_entity(entry.entity)
        if entry.revision != receipt.revision:
            raise ValueError("Эффекты должны принадлежать подтверждённой ревизии")
        key = _key(entry.entity)
        if key in effects:
            raise ValueError("Эффекты перехода повторяются")
        effects[key] = entry
        entity = entry.entity
        if type(entity) is RemovalRecord and entity.reason == "user_deleted":
            if entity.entity_kind == "task":
                deleted.add(entity.entity_id)
            elif entity.entity_kind == "occurrence":
                deleted.add(entity.root_id)
    merged = entries | effects
    live = set()
    for entry in merged.values():
        entity = entry.entity
        if type(entity) is TaskRecord:
            live.add(entity.task.id)
        elif type(entity) in (RegularRecord, OccurrenceRecord):
            live.add(entity.current.task.id)
    skipped = set()
    source = entries.get((operation.target_kind, operation.target_id))
    if source is not None and type(source.entity) is RegularRecord:
        old = source.entity.current.task
        mark = effects.get(("task", old.id))
        if (
            not old.completed
            and mark is not None
            and type(mark.entity) is RemovalRecord
            and mark.entity.reason == "retention"
        ):
            skipped.add(old.id)
    deleted -= live
    skipped -= live
    result = []
    for entry in snapshot.entries:
        entity = entry.entity
        if type(entity) is not BlockRecord:
            continue
        root = entity.block.task_id
        if root not in deleted | skipped:
            continue
        epoch = entry.delete_epoch + (root in deleted)
        marker = RemovalRecord(
            entity_kind="block",
            entity_id=entity.block.id,
            revision=receipt.revision,
            delete_epoch=epoch,
            reason="user_deleted" if root in deleted else "retention",
        )
        result.append(SnapshotEntry(revision=receipt.revision, delete_epoch=epoch, entity=marker))
    return tuple(result)
