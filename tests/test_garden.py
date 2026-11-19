import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import pytest

from ritm.domain.garden import (
    CATALOG,
    Garden,
    GardenItem,
    GardenState,
    apply_command,
    coins,
    completion_bonus,
    focus_coins,
)
from ritm.storage.garden import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    GardenStore,
    decode_garden,
    encode_garden,
)
from ritm.storage.sqlite import TaskStore


def action(state, name, **fields):
    return apply_command(state, {"action": name, **fields}, uuid4())


def command(store, name, **fields):
    return store.execute(
        uuid4(), {"action": name, **fields}, expected_revision=store.snapshot().revision
    )


def test_catalog_matches_all_approved_plant_assets_without_modifying_files():
    folder = Path(__file__).resolve().parents[1] / "assets/garden/plants-v1"
    manifest = json.loads((folder / "manifest.json").read_text())
    expected = {p.id for p in CATALOG.values() if p.growing}
    assets = [a for a in manifest["assets"] if a.get("species") in expected]
    assert len(assets) == 32 and len(expected) == 8
    for kind in expected:
        stages = sorted((a for a in assets if a["species"] == kind), key=lambda a: a["stage"])
        assert [a["stage"] for a in stages] == [1, 2, 3, 4]
        for asset in stages:
            product = CATALOG[kind]
            assert asset["terrain"] == product.terrain
            assert asset["footprint"] == [product.size, product.size]
            assert asset["file"] == f"sprites/{kind}_{asset['stage']:02}.png"
            assert (
                hashlib.sha256((folder / asset["file"]).read_bytes()).hexdigest() == asset["sha256"]
            )


def test_exact_money_and_approved_economy():
    assert coins(Fraction(1, 3)) + coins(Fraction(1, 6)) == Fraction(1, 2)
    assert focus_coins(4) == 0 and focus_coins(5) == 10 and focus_coins(208 * 60) == 24960
    assert completion_bonus(240) == 24
    assert completion_bonus(240, 5, Decimal("60")) == Fraction(72, 5)
    assert completion_bonus(240, 5, Decimal("40")) == Fraction(48, 5)
    assert completion_bonus(0, None, Decimal("60")) == 3
    assert completion_bonus(0, None, Decimal("40")) == 2
    assert 80 + sum(CATALOG["daisy"].doses) * 10 == 360
    assert 320 + sum(CATALOG["oak"].doses) * 10 == 1440
    assert 72 * 360 + 2 * 1440 == 28800


@pytest.mark.parametrize("value", [True, 1.25, -1, Fraction(-1, 2), "10"])
def test_money_rejects_lossy_or_negative_inputs(value):
    with pytest.raises(ValueError):
        coins(value)


@pytest.mark.parametrize(
    "difficulty,weight",
    [
        (True, Decimal(100)),
        (0, Decimal(100)),
        (11, Decimal(100)),
        (5, Decimal("NaN")),
        (5, Decimal("100.01")),
        (5, Decimal("0.001")),
        (5, 50.0),
    ],
)
def test_bonus_input_types_and_weights(difficulty, weight):
    with pytest.raises(ValueError):
        completion_bonus(100, difficulty, weight)


def test_terrain_water_plants_bounds_occupancy_and_free_changes():
    initial = GardenState(Garden("2026-11"), 1000)
    state, identifier = action(initial, "buy", kind="water_lily")
    with pytest.raises(ValueError, match="грунтом"):
        action(state, "place", id=str(identifier), x=0, y=0)
    assert not initial.garden.items and initial.balance == 1000
    state, _ = action(state, "terrain", x=0, y=0, terrain="water")
    assert state.balance == 920
    state, _ = action(state, "place", id=str(identifier), x=0, y=0)
    assert state.garden.planted_cells == 1
    with pytest.raises(ValueError, match="свободной"):
        action(state, "terrain", x=0, y=0, terrain="grass")
    for x, y in ((10, 0), (-1, 0), (0, True)):
        with pytest.raises(ValueError):
            action(state, "place", id=str(identifier), x=x, y=y)
    assert state.garden.item(identifier).x == 0


def test_trees_four_cells_compatible_ground_two_on_field_unlimited_inventory():
    state = GardenState(Garden("2026-11"), 10000)
    ids = []
    for _ in range(3):
        state, identifier = action(state, "buy", kind="oak")
        ids.append(identifier)
    state, _ = action(state, "place", id=str(ids[0]), x=0, y=0)
    state, _ = action(state, "place", id=str(ids[1]), x=3, y=0)
    assert state.garden.planted_cells == 8
    with pytest.raises(ValueError, match="двух деревьев"):
        action(state, "place", id=str(ids[2]), x=6, y=0)
    with pytest.raises(ValueError, match="занята"):
        action(state, "place", id=str(ids[0]), x=3, y=0)
    with pytest.raises(ValueError, match="границы"):
        action(state, "place", id=str(ids[0]), x=9, y=9)
    # Дерево при сдвиге может пересечь собственное старое основание.
    state, _ = action(state, "place", id=str(ids[0]), x=1, y=0)
    assert state.garden.item(ids[0]).x == 1
    state, _ = action(state, "stash", id=str(ids[1]))
    state, _ = action(state, "place", id=str(ids[2]), x=6, y=0)
    assert state.garden.planted_cells == 8


def test_eighty_cell_limit_mature_goal_and_milestone_survive_sale():
    items = tuple(GardenItem("daisy", uuid4(), stage=4, x=n % 10, y=n // 10) for n in range(79))
    state = GardenState(Garden("2026-11", items=items), 1000)
    state, identifier = action(state, "buy", kind="daisy")
    state, _ = action(state, "place", id=str(identifier), x=9, y=7)
    assert state.garden.planted_cells == 80 and not state.garden.milestone
    state, another = action(state, "buy", kind="daisy")
    with pytest.raises(ValueError, match="80 клеток"):
        action(state, "place", id=str(another), x=0, y=8)
    state, _ = action(state, "buy_fertilizer", quantity=28)
    for expected_stage in (2, 3, 4):
        state, _ = action(state, "grow", id=str(identifier))
        assert state.garden.item(identifier).stage == expected_stage
    assert state.garden.mature_cells == 80 and state.garden.milestone
    state, _ = action(state, "sell", id=str(identifier))
    assert state.garden.mature_cells == 79 and state.garden.milestone


def test_growth_cost_storage_stage_and_sale_refunds_only_plant_price():
    state = GardenState(Garden("2026-11"), 1000)
    state, identifier = action(state, "buy", kind="daisy")
    with pytest.raises(ValueError, match="удобрений"):
        action(state, "grow", id=str(identifier))
    state, _ = action(state, "buy_fertilizer", quantity=28)
    state, _ = action(state, "grow", id=str(identifier))
    state, _ = action(state, "place", id=str(identifier), x=2, y=2)
    state, _ = action(state, "stash", id=str(identifier))
    assert state.garden.item(identifier).stage == 2
    state, _ = action(state, "grow", id=str(identifier))
    state, _ = action(state, "grow", id=str(identifier))
    assert state.balance == 640 and state.garden.fertilizer == 0
    assert state.garden.item(identifier).sprite_id == "daisy_04"
    with pytest.raises(ValueError, match="не растёт"):
        action(state, "grow", id=str(identifier))
    state, _ = action(state, "sell", id=str(identifier))
    assert state.balance == 680 and not state.garden.items
    with pytest.raises(ValueError, match="не принадлежит"):
        action(state, "sell", id=str(identifier))


def test_decor_prices_approved_placement_and_no_growth():
    state = GardenState(Garden("2026-11"), 1000)
    for kind, price in (("stone", 40), ("fence", 40), ("lantern", 100), ("bench", 160)):
        before = state.balance
        state, identifier = action(state, "buy", kind=kind)
        assert state.balance == before - price and state.garden.item(identifier).stage == 0
        placed, _ = action(state, "place", id=str(identifier), x=0, y=0)
        assert placed.garden.item(identifier).placed
        assert not state.garden.item(identifier).placed
        with pytest.raises(ValueError, match="не растёт"):
            action(state, "grow", id=str(identifier))


@pytest.mark.parametrize(
    "command",
    [
        {"action": "buy", "kind": "unknown"},
        {"action": "buy", "kind": "daisy", "price": 0},
        {"action": "buy_fertilizer", "quantity": True},
        {"action": "buy_fertilizer", "quantity": -1},
        {"action": "place", "id": "wrong", "x": 0, "y": 0},
        {"action": "terrain", "x": 0, "y": 0, "terrain": "unknown"},
        {"action": "free_money"},
    ],
)
def test_commands_reject_extra_fields_bad_types_and_do_not_mutate(command):
    original = GardenState(Garden("2026-11"), 1000)
    with pytest.raises(ValueError):
        apply_command(original, command, uuid4())
    assert original.balance == 1000 and original.garden == Garden("2026-11")


def test_round_trip_store_fraction_repeat_stale_and_sold_item_do_not_resurrect(tmp_path):
    path = tmp_path / "garden.sqlite3"
    identifier = uuid4()
    buy = {"action": "buy", "kind": "daisy"}
    with GardenStore(path, month="2026-11") as store:
        assert store.snapshot().balance == 0
        assert store._db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        store.credit("focus-1", 100)
        before = store.snapshot()
        result = store.execute(identifier, buy, expected_revision=before.revision)
        assert result.state.balance == 20
        store.credit("fraction", Fraction(1, 3))
        command(store, "sell", id=str(result.item_id))
        saved = store.snapshot()
    with GardenStore(path) as store:
        assert store.snapshot() == saved and saved.balance == Fraction(181, 3)
        repeat = store.execute(identifier, buy, expected_revision=before.revision)
        assert repeat.replayed and repeat.item_id == result.item_id and repeat.state == saved
        assert not repeat.state.garden.items
        with pytest.raises(ValueError, match="другим содержимым"):
            store.execute(identifier, {"action": "buy", "kind": "oak"}, expected_revision=0)
        with pytest.raises(ValueError, match="изменились"):
            store.execute(uuid4(), buy, expected_revision=0)
        again = store.credit("fraction", Fraction(1, 3))
        assert again.replayed and again.state == saved
        with pytest.raises(ValueError, match="другим содержимым"):
            store.credit("fraction", 1)


@pytest.mark.parametrize("kind", ["buy", "credit", "grow"])
def test_failed_receipt_rolls_back_money_inventory_fertilizer_and_revision(tmp_path, kind):
    with GardenStore(tmp_path / "garden.sqlite3", month="2026-11") as store:
        store.credit("seed", 1000)
        item = command(store, "buy", kind="daisy").item_id
        command(store, "buy_fertilizer", quantity=4)
        before = store.snapshot()
        store._db.execute(
            "CREATE TRIGGER fail_receipt BEFORE INSERT ON garden_receipts "
            "BEGIN SELECT RAISE(ABORT,'fake'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            if kind == "credit":
                store.credit("another-credit", 100)
            elif kind == "buy":
                command(store, "buy", kind="oak")
            else:
                command(store, "grow", id=str(item))
        assert store.snapshot() == before


def test_two_connections_same_purchase_and_competing_purchase(tmp_path):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11") as store:
        revision = store.credit("start", 80).state.revision
    operation = uuid4()

    def purchase(identifier):
        with GardenStore(path) as store:
            return store.execute(
                identifier, {"action": "buy", "kind": "daisy"}, expected_revision=revision
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(purchase, (operation, operation)))
    assert sorted(r.replayed for r in results) == [False, True]
    assert results[0].item_id == results[1].item_id
    with pytest.raises(ValueError, match="изменились"):
        purchase(uuid4())
    with GardenStore(path) as store:
        assert store.snapshot().balance == 0 and len(store.snapshot().garden.items) == 1


@pytest.mark.parametrize("corruption", ["payload", "wallet", "missing", "future"])
def test_corrupted_or_future_garden_never_resets_existing_data(tmp_path, corruption):
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11") as store:
        store.credit("seed", 100)
    with sqlite3.connect(path) as db:
        if corruption == "payload":
            db.execute("UPDATE gardens SET payload='bad json'")
        elif corruption == "wallet":
            db.execute("UPDATE garden_state SET denominator='0'")
        elif corruption == "missing":
            db.execute("DELETE FROM garden_state")
        else:
            db.execute("PRAGMA user_version=999")
    original = path.read_bytes()
    with pytest.raises(ValueError):
        GardenStore(path)
    assert path.read_bytes() == original


def test_task_database_and_other_month_are_not_modified(tmp_path):
    tasks = tmp_path / "tasks.sqlite3"
    with TaskStore(tasks):
        pass
    original = tasks.read_bytes()
    with pytest.raises(ValueError, match="Чужая"):
        GardenStore(tasks, month="2026-11")
    assert tasks.read_bytes() == original
    path = tmp_path / "garden.sqlite3"
    with GardenStore(path, month="2026-11") as store:
        store.credit("seed", 100)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="ротация"):
        GardenStore(path, month="2026-12")
    assert path.read_bytes() == original


def test_codec_rejects_duplicate_fields_and_invalid_positions():
    payload = encode_garden(Garden("2026-11"))
    assert decode_garden(payload) == Garden("2026-11")
    with pytest.raises(ValueError):
        decode_garden(payload.replace('"fertilizer": 0', '"fertilizer": 0, "fertilizer": 10'))
    with pytest.raises(ValueError):
        Garden("2026-13")
    item = GardenItem("oak", uuid4(), x=9, y=9)
    with pytest.raises(ValueError, match="границы"):
        Garden("2026-11", items=(item,))


def test_fresh_python_process_reads_same_wallet_items_and_receipts(tmp_path):
    path = tmp_path / "garden.sqlite3"
    identifier = uuid4()
    with GardenStore(path, month="2026-11") as store:
        store.credit("initial", Fraction(301, 3))
        result = store.execute(identifier, {"action": "buy", "kind": "daisy"}, expected_revision=1)
    code = """from pathlib import Path
from fractions import Fraction
from uuid import UUID
from ritm.storage.garden import GardenStore
import sys
with GardenStore(Path(sys.argv[1])) as store:
    state = store.snapshot()
    assert state.balance == Fraction(61,3)
    result = store.execute(UUID(sys.argv[2]), {'action':'buy','kind':'daisy'}, expected_revision=1)
    assert result.replayed and str(result.item_id) == sys.argv[3]
    assert len(result.state.garden.items) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(path), str(identifier), str(result.item_id)],
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
