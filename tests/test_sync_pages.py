"""Full-snapshot staging contract. No network, profile, or application of changes."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import ritm.integrations.sync_pages as pages
from ritm.integrations.sync_codec import SyncCodecError, decode_entity
from ritm.integrations.sync_pages import (
    SnapshotCollector,
    SnapshotEntry,
    SnapshotPage,
    decode_page,
    encode_page,
)

ENTITIES = Path(__file__).parent / "fixtures" / "sync" / "v1"
FIXTURES = Path(__file__).parent / "fixtures" / "sync_pages" / "v1"
SERVER = UUID(int=301)
READ = UUID(int=302)


def entry(name, revision=3, epoch=0):
    entity = decode_entity((ENTITIES / f"{name}.json").read_bytes())
    if name == "archived_block":
        entity = replace(entity, block=replace(entity.block, id=UUID(int=306)))
    return SnapshotEntry(
        revision=revision,
        delete_epoch=epoch,
        entity=entity,
    )


def page(**changes):
    values = dict(
        server_id=SERVER,
        snapshot_id=READ,
        set_revision=10,
        total=4,
        offset=0,
        cursor=None,
        next_cursor="cursor_2",
        entries=(entry("category", 2), entry("task", 3)),
    )
    values.update(changes)
    return SnapshotPage(**values)


def last(**changes):
    return page(
        offset=2,
        cursor="cursor_2",
        next_cursor=None,
        entries=(entry("event", 4), entry("archived_block", 5)),
        **changes,
    )


def test_complete_snapshot_only_after_entire_chain():
    collector = SnapshotCollector(SERVER)
    with pytest.raises(SyncCodecError):
        collector.finish()
    collector.add(page())
    assert not collector.complete
    with pytest.raises(SyncCodecError):
        collector.finish()
    collector.add(last())
    snapshot = collector.finish()
    assert collector.complete and len(snapshot.entries) == 4
    assert snapshot.server_id == SERVER and snapshot.snapshot_id == READ
    assert snapshot.set_revision == 10


@pytest.mark.parametrize(
    "changes",
    [
        {"server_id": uuid4()},
        {"snapshot_id": uuid4()},
        {"set_revision": 11},
        {"total": 5, "next_cursor": "more"},
        {"offset": 1, "cursor": "cursor_2", "next_cursor": "more"},
        {"cursor": "wrong_cursor"},
    ],
)
def test_changed_or_wrong_page_poisoned_without_result(changes):
    collector = SnapshotCollector(SERVER)
    collector.add(page())
    values = dict(
        offset=2,
        cursor="cursor_2",
        next_cursor=None,
        entries=(entry("event", 4), entry("archived_block", 5)),
    )
    values.update(changes)
    with pytest.raises(SyncCodecError):
        collector.add(page(**values))
    assert not collector.complete
    with pytest.raises(SyncCodecError):
        collector.finish()
    with pytest.raises(SyncCodecError):
        collector.add(last())


def test_out_of_order_duplicate_and_post_complete_page_rejected():
    for sequence in ((last(),), (page(), page()), (page(), last(), last())):
        collector = SnapshotCollector(SERVER)
        with pytest.raises(SyncCodecError):
            for item in sequence:
                collector.add(item)
        with pytest.raises(SyncCodecError):
            collector.finish()


def test_cursor_cycle_is_rejected():
    collector = SnapshotCollector(SERVER)
    collector.add(page(total=6))
    collector.add(
        page(
            total=6, offset=2, cursor="cursor_2", next_cursor="cursor_3", entries=(entry("event"),)
        )
    )
    with pytest.raises(SyncCodecError):
        collector.add(
            page(
                total=6,
                offset=3,
                cursor="cursor_3",
                next_cursor="cursor_2",
                entries=(entry("archived_block"),),
            )
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"total": -1},
        {"total": True},
        {"total": 10001},
        {"offset": 5},
        {"offset": 1},
        {"next_cursor": None},
        {"entries": ()},
        {"cursor": ""},
        {"next_cursor": ""},
        {"next_cursor": "bad cursor"},
        {"next_cursor": "x" * 513},
        {"set_revision": 0},
        {"set_revision": True},
        {"entries": tuple(entry("event") for _ in range(51))},
    ],
)
def test_invalid_page_shape_or_limits(changes):
    with pytest.raises(SyncCodecError):
        encode_page(page(**changes))


def test_empty_same_generation_snapshot_is_valid_data_not_delete_instruction():
    collector = SnapshotCollector(SERVER)
    collector.add(page(total=0, entries=(), set_revision=0, next_cursor=None))
    snapshot = collector.finish()
    assert snapshot.entries == () and snapshot.set_revision == 0
    # It has no profile or apply method: absence is never a local deletion command.
    assert not hasattr(collector, "apply")
    another = SnapshotCollector(uuid4())
    with pytest.raises(SyncCodecError):
        another.add(page(total=0, entries=(), set_revision=0, next_cursor=None))


@pytest.mark.parametrize(
    "entry_value",
    [
        replace(entry("task"), revision=0),
        replace(entry("task"), revision=11),
        replace(entry("task"), delete_epoch=True),
        entry("deleted_node", revision=1, epoch=3),
    ],
)
def test_entry_counters_and_tombstone_wrapper_must_agree(entry_value):
    with pytest.raises(SyncCodecError):
        encode_page(page(entries=(entry_value,), total=1, next_cursor=None))


def test_valid_removal_does_not_carry_live_text():
    entity = decode_entity((ENTITIES / "deleted_node.json").read_bytes())
    item = SnapshotEntry(entity=entity, revision=entity.revision, delete_epoch=entity.delete_epoch)
    collector = SnapshotCollector(SERVER)
    collector.add(page(entries=(item,), total=1, next_cursor=None, set_revision=entity.revision))
    assert collector.finish().entries == (item,)


def test_cross_page_duplicate_or_regular_current_duplicated_as_plain_task():
    removed = decode_entity((ENTITIES / "deleted_task.json").read_bytes())
    tombstone = SnapshotEntry(
        entity=removed, revision=removed.revision, delete_epoch=removed.delete_epoch
    )
    revision = removed.revision
    for extra in (entry("task"), entry("regular"), tombstone):
        collector = SnapshotCollector(SERVER)
        collector.add(page(total=3, set_revision=revision))
        with pytest.raises(SyncCodecError):
            collector.add(
                page(
                    offset=2,
                    cursor="cursor_2",
                    next_cursor=None,
                    total=3,
                    entries=(extra,),
                    set_revision=revision,
                )
            )
        with pytest.raises(SyncCodecError):
            collector.finish()


def test_missing_category_or_task_reference_rejected_but_archived_caption_allowed():
    for item in (entry("task"), entry("regular")):
        collector = SnapshotCollector(SERVER)
        with pytest.raises(SyncCodecError):
            collector.add(page(total=1, entries=(item,), next_cursor=None))
    linked = entry("archived_block")
    without = replace(linked, entity=replace(linked.entity, archived_task_title=None))
    collector = SnapshotCollector(SERVER)
    with pytest.raises(SyncCodecError):
        collector.add(page(total=1, entries=(without,), next_cursor=None))
    collector = SnapshotCollector(SERVER)
    collector.add(page(total=1, entries=(linked,), next_cursor=None))
    assert collector.complete


def test_regular_snapshot_with_category_is_valid():
    collector = SnapshotCollector(SERVER)
    collector.add(
        page(
            total=3,
            entries=(entry("category"), entry("calendar_settings"), entry("regular")),
            next_cursor=None,
        )
    )
    assert collector.complete


def test_collector_memory_cap_discards_staging(monkeypatch):
    collector = SnapshotCollector(SERVER)
    monkeypatch.setattr(pages, "MAX_STAGED_BYTES", len(encode_page(page())))
    collector.add(page())
    with pytest.raises(SyncCodecError):
        collector.add(last())
    with pytest.raises(SyncCodecError):
        collector.finish()


@pytest.mark.parametrize("where", ["envelope", "page", "entry", "entity"])
def test_unknown_sensitive_fields_never_echoed(where):
    raw = json.loads(encode_page(page()))
    target = {
        "envelope": raw,
        "page": raw["data"],
        "entry": raw["data"]["entries"][0],
        "entity": raw["data"]["entries"][0]["entity"]["data"],
    }[where]
    target["password"] = "synthetic-sensitive"
    with pytest.raises(SyncCodecError) as caught:
        decode_page(json.dumps(raw))
    assert "synthetic-sensitive" not in str(caught.value)


def test_required_keys_versions_duplicate_keys_and_floats():
    raw = json.loads(encode_page(page()))
    for key in raw["data"]:
        altered = json.loads(json.dumps(raw))
        del altered["data"][key]
        with pytest.raises(SyncCodecError):
            decode_page(json.dumps(altered))
    for value in (True, 2, 1.0):
        raw["version"] = value
        with pytest.raises(SyncCodecError):
            decode_page(json.dumps(raw))
    duplicate = encode_page(page()).decode().replace('"offset":"0"', '"offset":"0","offset":"0"')
    with pytest.raises(SyncCodecError):
        decode_page(duplicate)


@pytest.mark.parametrize("raw", [b"null", b"[]", b"\xff", b"{", b" " * (2 * 1024 * 1024 + 1)])
def test_bad_json_size(raw):
    with pytest.raises(SyncCodecError):
        decode_page(raw)


@pytest.mark.parametrize("name", sorted(p.stem for p in FIXTURES.glob("*.json")))
def test_frozen_canonical_examples(name):
    raw = (FIXTURES / f"{name}.json").read_bytes().rstrip(b"\n")
    assert encode_page(decode_page(raw)) == raw


def test_sample_chain_matches_empty_or_full_snapshot():
    for names in (["empty"], ["first", "last"], ["regular_history"]):
        collector = SnapshotCollector(SERVER)
        for name in names:
            collector.add(decode_page((FIXTURES / f"{name}.json").read_bytes()))
        assert collector.complete
