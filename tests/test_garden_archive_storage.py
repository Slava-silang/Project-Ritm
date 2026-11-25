import sqlite3
from fractions import Fraction
from uuid import uuid4

import pytest

from ritm.domain.garden import Garden
from ritm.storage.garden import SCHEMA_VERSION, GardenStore, encode_garden
from ritm.storage.profile_backup import validate_garden


def favorite(store, month, enabled=True, *, identifier=None):
    return store.execute(
        identifier or uuid4(),
        {"action": "favorite", "month": month, "enabled": enabled},
        expected_revision=store.snapshot().revision,
    )


def test_month_guard_is_transactional_and_does_not_block_old_receipts(tmp_path):
    with GardenStore(tmp_path / "garden.sqlite3", month="2026-11") as store:
        store.credit("seed", 100)
        identifier = uuid4()
        command = {"action": "buy", "kind": "daisy"}
        store.execute(identifier, command, expected_revision=store.snapshot().revision)
        rotated = store.rotate_month("2026-12")
        replay = store.execute(identifier, command, expected_revision=0, expected_month="2026-11")
        assert replay.replayed and replay.state == rotated.current
        with pytest.raises(ValueError, match="просмотра"):
            store.execute(
                uuid4(),
                {"action": "terrain", "x": 0, "y": 0, "terrain": "water"},
                expected_revision=rotated.current.revision,
                expected_month="2026-11",
            )
        assert store.archive_snapshot() == rotated


def financial_rows(store):
    names = (
        "garden_receipts",
        "garden_reward_state",
        "garden_source_state",
        "garden_achievement_state",
        "garden_source_binding",
    )
    return {n: [tuple(r) for r in store._db.execute(f"SELECT * FROM {n}")] for n in names}


def test_rotation_reopen_retention_keeps_receipts_and_rejects_stale_purchase(tmp_path):
    path = tmp_path / "nested" / "garden.sqlite3"
    operation = uuid4()
    purchase = {"action": "buy", "kind": "daisy"}
    with GardenStore(path, month="2026-11") as store:
        store.credit("initial", Fraction(1000, 3))
        bought = store.execute(operation, purchase, expected_revision=store.snapshot().revision)
        original = store.archive_snapshot()
        money = financial_rows(store)
        rotated = store.rotate_month("2026-12")
        assert rotated.history == (original.current.garden,)
        assert rotated.current.garden == Garden("2026-12")
        assert rotated.current.balance == original.current.balance
        assert financial_rows(store) == money
        assert store.rotate_month("2026-12") == rotated
        assert store.rotate_month("2026-10") == rotated
        replay = store.execute(operation, purchase, expected_revision=0)
        assert replay.replayed and replay.item_id == bought.item_id
        assert replay.state == rotated.current
        with pytest.raises(ValueError, match="изменились"):
            store.execute(uuid4(), purchase, expected_revision=original.current.revision)
        with pytest.raises(ValueError):
            store.execute(
                uuid4(),
                {"action": "sell", "id": str(bought.item_id)},
                expected_revision=store.snapshot().revision,
            )
        store.rotate_month("2028-01")
        assert store.archive_snapshot().history == ()
        assert financial_rows(store) == money
        assert store.credit("initial", Fraction(1000, 3)).replayed
        saved = store.archive_snapshot()
    with GardenStore(path) as reopened:
        assert reopened.archive_snapshot() == saved
        assert financial_rows(reopened) == money
        assert reopened.execute(operation, purchase, expected_revision=0).replayed


def test_favorites_persist_share_snapshot_and_prune_after_unmark(tmp_path):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11") as store:
        identifier = uuid4()
        marked = favorite(store, "2026-11", identifier=identifier)
        assert store.archive_snapshot().history == ()
        favorite(store, "2026-11")  # Same value, different request: no revision change.
        assert store.snapshot() == marked.state
        store.rotate_month("2028-01")
        assert [g.month for g in store.archive_snapshot().history] == ["2026-11"]
        assert favorite(store, "2026-11", identifier=identifier).replayed
        saved = store.archive_snapshot()
    with GardenStore(path) as store:
        assert store.archive_snapshot() == saved
        favorite(store, "2026-11", False)
        assert store.archive_snapshot().history == ()
        assert store.archive_snapshot().favorites == ()
    validate_garden(path)


def test_twenty_favorites_no_eviction_and_empty_skipped_month_is_saved(tmp_path):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2025-01") as store:
        for index in range(20):
            month = f"{2025 + index // 12:04}-{index % 12 + 1:02}"
            store.rotate_month(month)
            favorite(store, month)
        store.rotate_month("2026-09")
        saved = store.archive_snapshot()
        rows = financial_rows(store)
        with pytest.raises(ValueError, match="20"):
            favorite(store, "2026-09")
        assert store.archive_snapshot() == saved and financial_rows(store) == rows
        favorite(store, "2025-01", False)
        favorite(store, "2026-09")
        assert len(store.archive_snapshot().favorites) == 20
    with GardenStore(path) as store:
        assert len(store.archive_snapshot().favorites) == 20
    validate_garden(path)
    other = tmp_path / "skipped.sqlite3"
    with GardenStore(other, month="2026-11") as store:
        favorite(store, "2026-02")
        assert store.archive_snapshot().history == (Garden("2026-02"),)


@pytest.mark.parametrize("action", ["rotate", "favorite", "unmark"])
def test_failure_rolls_back_month_wallet_properties_favorites_and_receipts(tmp_path, action):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2025-01") as store:
        store.credit("seed", 100)
        favorite(store, "2025-01")
        if action == "unmark":
            store.rotate_month("2026-11")
        saved = store.archive_snapshot()
        rows = financial_rows(store)
        with store._db:
            if action == "favorite":
                store._db.execute(
                    "CREATE TRIGGER fail BEFORE INSERT ON garden_favorites "
                    "BEGIN SELECT RAISE(ABORT,'injected failure'); END"
                )
            elif action == "rotate":
                store._db.execute(
                    "CREATE TRIGGER fail BEFORE UPDATE ON garden_state "
                    "BEGIN SELECT RAISE(ABORT,'injected failure'); END"
                )
            else:
                store._db.execute(
                    "CREATE TRIGGER fail BEFORE DELETE ON gardens "
                    "BEGIN SELECT RAISE(ABORT,'injected failure'); END"
                )
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            if action == "rotate":
                store.rotate_month("2028-01")
            elif action == "favorite":
                favorite(store, "2024-12")
            else:
                favorite(store, "2025-01", False)
        assert store.archive_snapshot() == saved
        assert financial_rows(store) == rows
    with GardenStore(path) as store:
        assert store.archive_snapshot() == saved


def test_v6_migration_preserves_all_existing_data_and_open_never_rotates(tmp_path):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11") as store:
        store.credit("seed", Fraction(1000, 3))
        saved = store.archive_snapshot()
        rows = financial_rows(store)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE garden_favorites")
        db.execute("PRAGMA user_version=6")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="ротация"):
        GardenStore(path, month="2026-12")
    assert path.read_bytes() == before
    with GardenStore(path) as store:
        assert store.archive_snapshot() == saved
        assert financial_rows(store) == rows
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


@pytest.mark.parametrize("legacy", [False, True])
def test_corrupted_history_is_rejected_without_reset_or_partial_migration(tmp_path, legacy):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11"):
        pass
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO gardens VALUES (?,?)", ("2026-10", encode_garden(Garden("2026-09")))
        )
        if legacy:
            db.execute("DROP TABLE garden_favorites")
            db.execute("PRAGMA user_version=6")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Месяц"):
        GardenStore(path)
    assert path.read_bytes() == before
    with pytest.raises(ValueError):
        validate_garden(path)
    assert path.read_bytes() == before


def test_second_connection_stale_command_and_wrong_generation_are_rejected(tmp_path):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11") as first, GardenStore(path) as second:
        stale = second.snapshot()
        first.rotate_month("2026-12")
        with pytest.raises(ValueError, match="изменились"):
            second.execute(
                uuid4(),
                {"action": "favorite", "month": "2026-11", "enabled": True},
                expected_revision=stale.revision,
            )
        saved = first.archive_snapshot()
        with pytest.raises(ValueError, match="восстановлен"):
            second.rotate_month("2027-01", expected_identity=str(uuid4()))
        assert first.archive_snapshot() == saved


@pytest.mark.parametrize("corruption", ["missing", "too_many", "expired"])
def test_backup_validation_rejects_invalid_favorites_and_retention(tmp_path, corruption):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2028-01"):
        pass
    with sqlite3.connect(path) as db:
        if corruption == "missing":
            db.execute("INSERT INTO garden_favorites VALUES ('2026-01')")
        else:
            for index in range(21 if corruption == "too_many" else 1):
                month = f"{2026 + index // 12:04}-{index % 12 + 1:02}"
                db.execute(
                    "INSERT INTO gardens VALUES (?,?)", (month, encode_garden(Garden(month)))
                )
                if corruption == "too_many":
                    db.execute("INSERT INTO garden_favorites VALUES (?)", (month,))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        validate_garden(path)
    assert path.read_bytes() == before
