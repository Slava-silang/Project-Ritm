"""Cloud limits and upload projection do not mutate the desktop history."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Task
from ritm.integrations.sync_codec import (
    BlockRecord,
    CalendarRecord,
    OccurrenceRecord,
    RegularRecord,
    RemovalRecord,
    SyncCodecError,
    TaskRecord,
    encode_entity,
    history_id,
)
from ritm.integrations.sync_pages import Snapshot, SnapshotEntry
from ritm.integrations.sync_projection import (
    CompletionSelectionRequired,
    LocalData,
    project_local,
    retain_cloud,
)

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)
CALENDAR = CalendarRecord(id=UUID(int=501), timezone="Europe/Moscow")


def task(i, completed=True, known=True):
    return TaskRecord(
        task=Task(
            id=UUID(int=1000 + i),
            title=f"Task {i}",
            description=f"private description {i}",
            completed=completed,
        ),
        completed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=i)
        if completed and known
        else None,
    )


def occurrence(i):
    record = task(200 + i)
    rule = UUID(int=5000 + i)
    day = date(2026, 1, 1) + timedelta(days=i)
    return OccurrenceRecord(id=history_id(rule, day), rule_id=rule, day=day, current=record)


def regular(i, completed=True):
    return RegularRecord(
        id=UUID(int=7000 + i),
        first_day=date(2026, 11, 26),
        current_day=date(2026, 11, 26),
        interval_days=3,
        current=task(400 + i, completed),
    )


def block(i, root=None, end=NOW, caption=None):
    return BlockRecord(
        block=WorkBlock(
            id=UUID(int=9000 + i),
            task_id=root,
            title="" if root else f"Event {i}",
            starts_at=end - timedelta(hours=1),
            ends_at=end,
        ),
        archived_task_title=caption,
    )


def snapshot(records):
    return Snapshot(
        server_id=UUID(int=1),
        snapshot_id=UUID(int=2),
        set_revision=10,
        entries=tuple(SnapshotEntry(entity=e, revision=5, delete_epoch=2) for e in records),
    )


def root(record):
    return (
        record.current.task.id
        if isinstance(record, (RegularRecord, OccurrenceRecord))
        else record.task.id
    )


def test_initial_selection_all_active_plus_fifty_shared_completed_roots():
    ordinary = tuple(task(i) for i in range(60))
    histories = tuple(occurrence(i) for i in range(10))
    active = task(90, False)
    data = LocalData(tasks=(*ordinary, active, *(h.current for h in histories)), history=histories)
    result = project_local(data, NOW)
    kept = {root(e) for e in result.entities}
    assert len(kept) == 51 and active.task.id in kept
    assert all(h.current.task.id in kept for h in histories)
    assert task(20).task.id in kept and task(19).task.id not in kept
    assert len(result.exclusions) == 20
    assert len(data.tasks) == 71 and all(t.task.completed for t in ordinary)


def test_current_rules_preserved_even_more_than_fifty_completed_and_future():
    rules = tuple(regular(i) for i in range(60))
    future = replace(regular(61, False), first_day=date(2026, 12, 1), current_day=date(2026, 12, 1))
    result = project_local(
        LocalData(
            tasks=tuple(r.current for r in rules), regulars=(*rules, future), calendar=CALENDAR
        ),
        NOW,
    )
    assert len(result.entities) == 62
    assert sum(isinstance(e, RegularRecord) for e in result.entities) == 61
    assert not any(isinstance(e, TaskRecord) for e in result.entities)


def test_unknown_completion_dates_require_selection_when_over_limit():
    records = tuple(task(i, known=i >= 5) for i in range(55))
    with pytest.raises(CompletionSelectionRequired) as caught:
        project_local(LocalData(tasks=records), NOW)
    assert caught.value.unknown_ids == frozenset(t.task.id for t in records[:5])
    assert len(caught.value.suggested_ids) == 50
    assert "private description" not in str(caught.value)
    chosen = frozenset(t.task.id for t in records[:50])
    result = project_local(LocalData(tasks=records), NOW, chosen)
    assert len(result.entities) == 50
    assert all(t.completed_at is None for t in result.entities[:5])
    small = project_local(LocalData(tasks=records[:5]), NOW)
    assert len(small.entities) == 5 and len(small.unknown_completed_ids) == 5


@pytest.mark.parametrize(
    "selection",
    [frozenset({UUID(int=123)}), frozenset(task(i).task.id for i in range(51)), {UUID(int=1000)}],
)
def test_invalid_explicit_selection_rejected(selection):
    with pytest.raises(SyncCodecError):
        project_local(LocalData(tasks=tuple(task(i) for i in range(60))), NOW, selection)


def test_skipped_deleted_not_exported_and_inconsistent_duplicate_rejected():
    deleted, skipped, live = task(1), task(2), task(3, False)
    result = project_local(
        LocalData(
            tasks=(deleted, skipped, live),
            deleted_ids=frozenset({deleted.task.id}),
            skipped_ids=frozenset({skipped.task.id}),
            blocks=(block(1, deleted.task.id), block(2, skipped.task.id)),
        ),
        NOW,
    )
    assert result.entities == (live,)
    assert "private description 1" not in repr(result)
    rule = regular(1)
    with pytest.raises(SyncCodecError):
        project_local(
            LocalData(
                tasks=(replace(rule.current, task=replace(rule.current.task, title="Stale copy")),),
                regulars=(rule,),
                calendar=CALENDAR,
            ),
            NOW,
        )


def test_calendar_boundary_future_events_and_old_history_minimal_caption():
    cutoff = NOW - timedelta(days=30)
    records = tuple(task(i) for i in range(51))
    old = records[0]
    keep = block(1, old.task.id)
    result = project_local(
        LocalData(
            tasks=records,
            blocks=(
                keep,
                block(2, end=cutoff),
                block(3, end=cutoff - timedelta(microseconds=1)),
                block(4, end=NOW + timedelta(days=800)),
            ),
        ),
        NOW,
    )
    blocks = [e for e in result.entities if isinstance(e, BlockRecord)]
    assert len(blocks) == 3 and blocks[0].archived_task_title == old.task.title
    encoded = b"".join(encode_entity(e) for e in result.entities)
    assert old.task.description.encode() not in encoded
    assert old.task.title.encode() in encoded
    assert any(x.reason == "calendar_age" for x in result.exclusions)


def test_missing_source_is_not_recovered_from_trash_or_invented():
    unknown = UUID(int=88)
    result = project_local(
        LocalData(
            blocks=(block(1, unknown), block(2, unknown, caption="Previously retained caption"))
        ),
        NOW,
    )
    assert (
        len(result.entities) == 1
        and result.entities[0].archived_task_title == "Previously retained caption"
    )
    deleted = project_local(
        LocalData(
            blocks=(block(2, unknown, caption="Deleted title"),), deleted_ids=frozenset({unknown})
        ),
        NOW,
    )
    assert not deleted.entities


def test_server_order_not_modified_revision_or_local_date_selects_fifty():
    records = tuple(task(i, known=False) for i in range(60))
    original = snapshot(records)
    # A newer edit revision must not promote the oldest completion into the limit.
    original = replace(
        original, entries=(*original.entries[:-1], replace(original.entries[-1], revision=10))
    )
    order = {t.task.id: 60 - i for i, t in enumerate(records)}
    plan = retain_cloud(original, order, NOW, 11)
    live = [e.entity for e in plan.entries if isinstance(e.entity, TaskRecord)]
    marks = [e.entity for e in plan.effects]
    assert len(live) == 50 and len(marks) == 10
    assert {t.task.id for t in live} == {t.task.id for t in records[:50]}
    assert all(
        isinstance(m, RemovalRecord) and m.reason == "retention" and m.delete_epoch == 2
        for m in marks
    )
    assert all(e.revision == 11 for e in plan.effects)
    assert original.entries[59].entity == records[59]
    again = retain_cloud(replace(original, set_revision=11, entries=plan.entries), order, NOW, 12)
    assert not again.effects and again.revision == 11


def test_server_history_cap_shared_and_templates_never_evicted():
    normal = tuple(task(i) for i in range(50))
    history = tuple(occurrence(i) for i in range(10))
    rules = tuple(regular(i) for i in range(3))
    original = snapshot((*normal, *history, *rules, CALENDAR))
    order = {root(record): i + 1 for i, record in enumerate((*normal, *history))}
    plan = retain_cloud(original, order, NOW, 11)
    assert sum(isinstance(e.entity, RegularRecord) for e in plan.entries) == 3
    assert sum(isinstance(e.entity, (TaskRecord, OccurrenceRecord)) for e in plan.entries) == 50
    assert sum(isinstance(e.entity, RemovalRecord) for e in plan.effects) == 10


def test_server_occurrence_retention_and_calendar_caption():
    history = tuple(occurrence(i) for i in range(51))
    old = history[0]
    original = snapshot((*history, block(1, old.current.task.id)))
    order = {root(record): i + 1 for i, record in enumerate(history)}
    plan = retain_cloud(original, order, NOW, 11)
    mark = next(e.entity for e in plan.effects if isinstance(e.entity, RemovalRecord))
    assert (
        mark.entity_kind == "occurrence"
        and mark.entity_id == old.id
        and mark.root_id == old.current.task.id
    )
    saved = next(e.entity for e in plan.entries if isinstance(e.entity, BlockRecord))
    assert saved.archived_task_title == old.current.task.title


def test_explicit_root_delete_removes_calendar_title_and_increments_its_barrier():
    root_id = UUID(int=1000)
    mark = RemovalRecord(
        entity_kind="task", entity_id=root_id, revision=5, delete_epoch=2, reason="user_deleted"
    )
    original = snapshot((mark, block(1, root_id, caption="Deleted title")))
    plan = retain_cloud(original, {}, NOW, 11)
    assert all(isinstance(e.entity, RemovalRecord) for e in plan.entries)
    block_mark = plan.effects[0].entity
    assert (
        block_mark.entity_kind == "block"
        and block_mark.reason == "user_deleted"
        and block_mark.delete_epoch == 3
    )
    assert "Deleted title" not in repr(plan)


def test_server_calendar_cutoff_inclusive_and_does_not_change_overlaps():
    cutoff = NOW - timedelta(days=30)
    records = (
        block(1, end=cutoff),
        block(2, end=cutoff - timedelta(microseconds=1)),
        block(3, end=cutoff),
        block(4, end=NOW + timedelta(days=2)),
    )
    plan = retain_cloud(snapshot(records), {}, NOW, 11)
    assert sum(isinstance(e.entity, BlockRecord) for e in plan.entries) == 3
    assert plan.effects[0].entity.reason == "retention"


@pytest.mark.parametrize(
    "order", [{}, {UUID(int=1000): 0}, {UUID(int=1000): True}, {UUID(int=1000): "1"}]
)
def test_server_completion_order_must_be_explicit_and_valid(order):
    with pytest.raises(SyncCodecError):
        retain_cloud(snapshot((task(0),)), order, NOW, 11)


def test_clock_revision_and_input_validation_no_implicit_fallback():
    with pytest.raises(SyncCodecError):
        project_local(LocalData(), NOW.replace(tzinfo=None))
    for revision in (10, 9, True, 2**63):
        with pytest.raises(SyncCodecError):
            retain_cloud(snapshot(()), {}, NOW, revision)
    with pytest.raises(SyncCodecError):
        project_local(LocalData(tasks=(task(0), task(0))), NOW)
    with pytest.raises(SyncCodecError):
        project_local(LocalData(blocks=(block(1), block(1))), NOW)


def test_reopened_root_is_active_and_calendar_caption_cleared():
    reopened = task(99, False)
    linked = block(1, reopened.task.id, caption="Old caption")
    local = project_local(LocalData(tasks=(reopened,), blocks=(linked,)), NOW)
    assert local.entities[-1].archived_task_title is None
    plan = retain_cloud(snapshot((reopened, linked)), {}, NOW, 11)
    assert plan.entries[-1].entity.archived_task_title is None
    assert plan.effects[0].revision == 11 and plan.effects[0].delete_epoch == 2


def test_history_duplicates_detected_before_selection_hides_them():
    original = occurrence(1)
    other = replace(original, current=task(900))
    with pytest.raises(SyncCodecError):
        project_local(
            LocalData(tasks=tuple(task(i) for i in range(60)), history=(original, other)), NOW
        )


def test_utc_cutoff_equivalent_with_another_input_offset():
    from datetime import timezone

    instant = NOW.astimezone(timezone(timedelta(hours=7)))
    records = (
        block(1, end=NOW - timedelta(days=30)),
        block(2, end=NOW - timedelta(days=30, microseconds=1)),
    )
    assert project_local(LocalData(blocks=records), instant) == project_local(
        LocalData(blocks=records), NOW
    )


def test_ties_deterministic_and_tombstones_have_no_old_description():
    records = tuple(replace(task(i), completed_at=NOW) for i in range(51))
    a = project_local(LocalData(tasks=records), NOW)
    b = project_local(LocalData(tasks=tuple(reversed(records))), NOW)
    assert {root(r) for r in a.entities} == {root(r) for r in b.entities}
    order = {r.task.id: 1 for r in records}
    cloud = retain_cloud(snapshot(records), order, NOW, 11)
    assert len(cloud.effects) == 1
    raw = encode_entity(cloud.effects[0].entity)
    assert b"private description" not in raw and b"Task 0" not in raw
