"""Prepare durable calendar facts after task projections have settled. No transport."""

from dataclasses import dataclass, field, replace
from datetime import UTC, timedelta

from ritm.domain.calendar import instant_us
from ritm.integrations.sync_codec import (
    BlockRecord,
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    TaskRecord,
)
from ritm.integrations.sync_operations import target_identity
from ritm.integrations.sync_projection import CALENDAR_DAYS
from ritm.integrations.sync_regular_changes import task_node_ids
from ritm.storage.sync_intents import SyncIntent, _stored_uuid
from ritm.storage.sync_outbox import FrozenRequest, SyncBase


@dataclass(frozen=True, kw_only=True)
class BlockPreparation:
    status: str
    request: FrozenRequest | None = field(default=None, repr=False)
    reasons: tuple[str, ...] = ()
    retired_facts: int = 0
    remaining_facts: int = 0


def _entity_key(entity):
    return (
        (entity.entity_kind, entity.entity_id)
        if type(entity) is RemovalRecord
        else target_identity(entity)
    )


class SyncBlockPrepareMixin:
    def _retire_block_prefix(self, identifier, sequence):
        cursor = self._db.execute(
            "DELETE FROM sync_block_changes WHERE block_id=? AND seq<=?",
            (str(identifier), sequence),
        )
        return cursor.rowcount

    def _calendar_payload(self, record, snapshot, identities, base):
        local_root = record.block.task_id
        if local_root is None:
            return record, None
        remote_root = identities.get(local_root, local_root)
        live = set()
        retained = set()
        deleted = set()
        for entry in snapshot.entries:
            entity = entry.entity
            if type(entity) is TaskRecord:
                live.add(entity.task.id)
            elif type(entity) in (RegularRecord, OccurrenceRecord):
                live.add(entity.current.task.id)
            elif type(entity) is RemovalRecord:
                root = entity.entity_id if entity.entity_kind == "task" else entity.root_id
                if root is not None and entity.entity_kind in ("task", "occurrence", "regular"):
                    if entity.reason == "retention":
                        retained.add(root)
                    elif entity.reason == "user_deleted":
                        deleted.add(root)
        block = replace(record.block, task_id=remote_root)
        if remote_root in live:
            return BlockRecord(block=block), None
        if remote_root in deleted:
            return None, "calendar_parent_deleted"
        if remote_root in retained:
            task = self.get_task(local_root)
            title = task.title if task is not None else None
            if title is None and base is not None and type(base.entity) is BlockRecord:
                title = base.entity.archived_task_title
            if title is not None:
                return BlockRecord(block=block, archived_task_title=title), None
            return None, "calendar_parent_caption_missing"
        # Snapshot absence is not permission to invent an archived parent caption.
        return None, "calendar_parent_pending"

    def _prepare_sync_blocks(self, snapshot, moment):
        instant_us(moment)  # Reject naive/out-of-range input before touching the journal.
        facts = self.list_sync_block_changes()
        if not facts:
            return None
        retired = 0
        entries = {_entity_key(entry.entity): entry for entry in snapshot.entries}
        # A fact ID is also its immutable intent ID. New edits while pending have
        # new IDs and cannot replace the frozen request or disappear with its ack.
        for fact in facts:
            row = self._db.execute(
                "SELECT target_kind,target_id FROM sync_intents WHERE id=?", (str(fact.id),)
            ).fetchone()
            if row is None:
                continue
            if row[0] != "block" or row[1] != str(fact.block_id):
                raise ValueError("Идентификатор изменения календаря уже занят")
            frozen = self._frozen_sync_request(fact.id)
            if frozen is None or frozen.receipt is None:
                return BlockPreparation(status="blocked", reasons=("calendar_ack_pending",))
            if frozen.receipt.status != "applied":
                return BlockPreparation(status="conflict", reasons=("calendar_rejected",))
            if snapshot.set_revision < frozen.receipt.revision:
                return BlockPreparation(status="needs_snapshot")
            effect = entries.get(("block", fact.block_id))
            if effect is None or effect.revision < frozen.receipt.revision:
                return BlockPreparation(
                    status="needs_snapshot",
                    reasons=("calendar_effect_missing",),
                    retired_facts=retired,
                    remaining_facts=len(facts),
                )
            if effect.revision == frozen.receipt.revision and (
                effect.delete_epoch != frozen.receipt.delete_epoch
                or (
                    type(effect.entity) is BlockRecord and effect.entity != frozen.operation.payload
                )
            ):
                return BlockPreparation(
                    status="conflict",
                    reasons=("calendar_effect_mismatch",),
                    retired_facts=retired,
                    remaining_facts=len(facts),
                )
            retired += self._retire_block_prefix(fact.block_id, fact.sequence)
        facts = self.list_sync_block_changes()
        groups = {}
        for fact in facts:
            groups.setdefault(fact.block_id, []).append(fact)
        identities = dict(self._read_sync_prepared_state().identities)
        cutoff = moment.astimezone(UTC) - timedelta(days=CALENDAR_DAYS)
        for identifier, group in groups.items():
            first, last = group[0], group[-1]
            base = entries.get(("block", identifier))
            for entry in snapshot.entries:
                entity = entry.entity
                if _entity_key(entity) == ("block", identifier):
                    continue
                occupied = {_entity_key(entity)[1]}
                if type(entity) is TaskRecord:
                    occupied |= task_node_ids(entity.task)
                elif type(entity) in (RegularRecord, OccurrenceRecord):
                    occupied |= task_node_ids(entity.current.task)
                elif type(entity) is RemovalRecord and entity.root_id is not None:
                    occupied.add(entity.root_id)
                if identifier in occupied:
                    return BlockPreparation(
                        status="conflict",
                        reasons=("calendar_identity_occupied",),
                        retired_facts=retired,
                        remaining_facts=len(facts),
                    )
            payload = last.after
            if (
                payload is not None
                and payload.block.task_id is not None
                and self.is_skipped_occurrence(payload.block.task_id)
            ):
                if base is None or type(base.entity) is RemovalRecord:
                    retired += self._retire_block_prefix(identifier, last.sequence)
                    continue
                return BlockPreparation(
                    status="needs_snapshot",
                    reasons=("calendar_policy_effect_missing",),
                    retired_facts=retired,
                    remaining_facts=len(facts),
                )
            reason = None
            if payload is None:
                if base is None or type(base.entity) is RemovalRecord:
                    retired += self._retire_block_prefix(identifier, last.sequence)
                    continue
                action = "delete"
            elif payload.block.ends_at < cutoff and (
                base is None or type(base.entity) is RemovalRecord
            ):
                retired += self._retire_block_prefix(identifier, last.sequence)
                continue
            else:
                payload, reason = self._calendar_payload(payload, snapshot, identities, base)
                if reason is None:
                    if base is None:
                        if any(key[1] == identifier for key in entries):
                            reason = "calendar_identity_occupied"
                        elif first.before is not None and first.kind != "restore":
                            reason = "calendar_base_missing"
                        else:
                            action = "create"
                    elif type(base.entity) is RemovalRecord:
                        if last.kind == "restore" or (
                            base.entity.reason == "retention" and first.before is not None
                        ):
                            action = "restore"
                        else:
                            reason = "calendar_deleted"
                    elif base.entity.block.task_id != payload.block.task_id:
                        reason = "calendar_parent_conflict"
                    else:
                        action = "put"
            if reason is not None:
                return BlockPreparation(
                    status="blocked",
                    reasons=(reason,),
                    retired_facts=retired,
                    remaining_facts=len(self.list_sync_block_changes()),
                )
            parent = self._db.execute(
                "SELECT id FROM sync_intents ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            self._enqueue_sync_intent(
                SyncIntent(
                    id=last.id,
                    target_kind="block",
                    target_id=identifier,
                    action=action,
                    payload=payload,
                    depends_on=_stored_uuid(parent[0]) if parent else None,
                )
            )
            frozen = self._freeze_sync_intent(
                last.id,
                SyncBase(
                    server_id=snapshot.server_id,
                    target_kind="block",
                    target_id=identifier,
                    revision=base.revision if base else 0,
                    delete_epoch=base.delete_epoch if base else 0,
                ),
            )
            return BlockPreparation(
                status="prepared",
                request=frozen,
                retired_facts=retired,
                remaining_facts=len(self.list_sync_block_changes()),
            )
        return BlockPreparation(status="settled", retired_facts=retired)
