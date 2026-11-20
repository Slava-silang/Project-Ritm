import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from PySide6.QtGui import QImage, QPainter

from ritm.domain.garden import CATALOG, Garden, GardenItem, GardenState
from ritm.platform.garden_assets import garden_asset_root, load_garden_assets
from ritm.storage.backup import create_backup, inspect_backup, restore_backup
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore
from ritm.ui.garden_scene import cell_at, garden_scene, project


@pytest.fixture(scope="module")
def sprites():
    return load_garden_assets()


def test_catalog_matches_environment_offsets_terrains_alias_and_original_bytes(sprites):
    folder = garden_asset_root() / "environment-v1"
    manifest = json.loads((folder / "manifest.json").read_text())
    for entry in manifest["assets"]:
        if entry["role"] != "decor":
            continue
        product = CATALOG[entry["catalog_kind"]]
        assert product.cell_offsets == tuple(map(tuple, entry["occupied_cell_offsets"]))
        assert product.allowed_terrain == set(entry["allowed_terrain"])
        assert hashlib.sha256((folder / entry["file"]).read_bytes()).hexdigest() == entry["sha256"]
    assert sprites["stone"].path.name == "rock.png"
    assert "rock" not in CATALOG and "rock" not in sprites


def test_all_runtime_images_load_at_exact_size_and_catalog_is_immutable(sprites):
    assert len(sprites) == 98
    for sprite in sprites.values():
        image = QImage(str(sprite.path))
        assert not image.isNull() and (image.width(), image.height()) == sprite.canvas
    with pytest.raises(TypeError):
        sprites["bogus"] = sprites["stone"]


def test_bench_uses_global_y_axis_bounds_collision_and_both_ground_cells():
    bench = GardenItem("bench", uuid4(), stage=0, x=9, y=8)
    assert bench.cells == {(9, 8), (9, 9)}
    field = Garden("2026-11", items=(bench,))
    assert field.planted_cells == field.mature_cells == 0
    with pytest.raises(ValueError, match="границы"):
        replace(field, items=(replace(bench, y=9),))
    with pytest.raises(ValueError, match="занята"):
        replace(field, items=(bench, GardenItem("stone", uuid4(), 0, 9, 9)))
    terrain = list(field.terrain)
    terrain[98] = "water"  # unused horizontal neighbor (8,9) does not block the bench
    assert replace(field, terrain=tuple(terrain))
    terrain[99] = "water"
    with pytest.raises(ValueError, match="грунтом"):
        replace(field, terrain=tuple(terrain))
    terrain[99] = "tilled_soil"  # mixed grass/soil under a bench is allowed
    assert replace(field, terrain=tuple(terrain))


@pytest.mark.parametrize("kind", ["stone", "lantern", "fence", "bench"])
def test_decor_stash_move_terrain_protection_and_rejection_leave_store_unchanged(tmp_path, kind):
    with GardenStore(tmp_path / "garden.sqlite3", month="2026-11") as store:
        store.credit("fixture", 1000)

        def run(action, **values):
            return store.execute(
                uuid4(), {"action": action, **values}, expected_revision=store.snapshot().revision
            )

        item = run("buy", kind=kind).item_id
        operation = uuid4()
        command = {"action": "place", "id": str(item), "x": 1, "y": 1}
        revision = store.snapshot().revision
        saved = store.execute(operation, command, expected_revision=revision).state
        assert store.execute(operation, command, expected_revision=revision).state == saved
        with pytest.raises(ValueError, match="свободной"):
            run("terrain", x=1, y=1, terrain="water")
        if kind == "bench":
            with pytest.raises(ValueError, match="свободной"):
                run("terrain", x=1, y=2, terrain="tilled_soil")
        assert store.snapshot() == saved
        for action in ("grow", "sell"):
            with pytest.raises(ValueError):
                run(action, id=str(item))
        assert store.snapshot() == saved
        run("place", id=str(item), x=4, y=4)
        run("stash", id=str(item))
        final = store.snapshot()
        assert not final.garden.item(item).placed and final.garden.item(item).stage == 0
        run("terrain", x=4, y=4, terrain="water")
    with GardenStore(tmp_path / "garden.sqlite3") as reopened:
        assert reopened.snapshot().balance == final.balance
        assert not reopened.snapshot().garden.item(item).placed


def test_decor_fills_remaining_twenty_cells_without_goal_or_plant_limit_change():
    plants = tuple(GardenItem("daisy", uuid4(), 4, x, y) for y in range(8) for x in range(10))
    decor = tuple(GardenItem("stone", uuid4(), 0, x, y) for y in (8, 9) for x in range(10))
    garden = Garden("2026-11", items=plants + decor)
    assert garden.planted_cells == garden.mature_cells == 80
    assert not Garden("2026-11", items=decor).milestone


def test_placed_decor_survives_paired_backup_restore_and_free_terrain_changes(tmp_path):
    from datetime import UTC, datetime

    with TaskStore(tmp_path / "tasks.sqlite3"):
        pass
    with GardenStore(tmp_path / "garden.sqlite3", month="2026-11") as store:
        store.credit("fixture", 1000)
        bench = store.execute(
            uuid4(), {"action": "buy", "kind": "bench"}, expected_revision=store.snapshot().revision
        ).item_id
        store.execute(
            uuid4(),
            {"action": "place", "id": str(bench), "x": 2, "y": 3},
            expected_revision=store.snapshot().revision,
        )
        expected = store.snapshot()
    archive = create_backup(tmp_path, tmp_path / "backups", datetime(2026, 11, 20, tzinfo=UTC))
    with GardenStore(tmp_path / "garden.sqlite3") as store:
        store.execute(
            uuid4(),
            {"action": "stash", "id": str(bench)},
            expected_revision=store.snapshot().revision,
        )
    restore_backup(tmp_path, archive, inspect_backup(archive)["token"])
    with GardenStore(tmp_path / "garden.sqlite3") as store:
        assert store.snapshot() == expected
        assert store.snapshot().garden.item(bench).cells == {(2, 3), (2, 4)}


def test_empty_scene_bounds_depth_and_every_cell_center_roundtrips(sprites):
    scene = garden_scene(Garden("2026-11"), sprites)
    assert (scene["width"], scene["height"]) == (640, 328)
    assert len(scene["layers"]) == 100
    for cell in scene["cells"]:
        assert cell_at(scene, cell["centerX"], cell["centerY"]) == (cell["x"], cell["y"])
    assert cell_at(scene, 0, 0) is None
    assert cell_at(scene, scene["originX"], scene["height"] - 1) is None
    assert cell_at(scene, float("nan"), 0) is None
    assert project(1, 0) == (32, 16) and project(0, 1) == (-32, 16)


def overlays_at(scene, x, y):
    center = next(c for c in scene["cells"] if (c["x"], c["y"]) == (x, y))
    return [
        f["sprite"]
        for f in scene["layers"]
        if f["role"].endswith("overlay")
        and f["x"] + 32 == center["centerX"]
        and f["y"] + 16 == center["centerY"]
    ]


def with_terrain(**tiles):
    values = ["grass"] * 100
    for cell, terrain in tiles.items():
        x, y = map(int, cell.split("_"))
        values[y * 10 + x] = terrain
    return Garden("2026-11", terrain=tuple(values))


def test_transition_masks_use_grid_neighbors_and_order_water_soil_before_grass(sprites):
    garden = with_terrain(**{"4_4": "water", "3_4": "tilled_soil", "4_3": "water"})
    scene = garden_scene(garden, sprites)
    assert overlays_at(scene, 4, 4) == ["water_soil_1", "water_grass_12"]
    soil = garden_scene(with_terrain(**{"4_4": "tilled_soil"}), sprites)
    assert overlays_at(soil, 4, 4) == ["soil_grass_15"]
    corner = garden_scene(with_terrain(**{"0_0": "water"}), sprites)
    assert overlays_at(corner, 0, 0) == ["water_grass_12"]  # no edges outside the field


def test_inner_corner_requires_two_equal_side_neighbors_and_correct_diagonal(sprites):
    garden = with_terrain(**{"4_4": "water", "3_4": "water", "4_3": "water"})
    scene = garden_scene(garden, sprites)
    assert overlays_at(scene, 4, 4) == ["water_grass_12", "water_grass_corner_0"]
    changed = garden_scene(with_terrain(**{"4_4": "water", "3_4": "water"}), sprites)
    assert "water_grass_corner_0" not in overlays_at(changed, 4, 4)


def test_object_ground_anchors_footprints_layer_order_and_determinism(sprites, tmp_path):
    bench = GardenItem("bench", uuid4(), 0, 8, 7)
    tree = GardenItem("oak", uuid4(), 4, 0, 0)
    flower = GardenItem("daisy", uuid4(), 2, 5, 4)
    garden = Garden("2026-11", items=(bench, tree, flower))
    scene = garden_scene(garden, sprites)
    assert scene == garden_scene(replace(garden, items=(flower, bench, tree)), sprites)
    assert [f["sprite"] for f in scene["layers"][-3:]] == ["oak_04", "daisy_02", "bench"]
    for item, delta in ((tree, (0, 16)), (bench, (-16, 8))):
        layer = next(f for f in scene["layers"] if f["itemId"] == str(item.id))
        sprite = sprites[layer["sprite"]]
        cx, cy = project(item.x, item.y)
        assert layer["x"] + sprite.anchor[0] == cx + delta[0] + scene["originX"]
        assert layer["y"] + sprite.anchor[1] == cy + delta[1] + scene["originY"]
        for x, y in item.cells:
            cell = next(c for c in scene["cells"] if (c["x"], c["y"]) == (x, y))
            assert cell["itemId"] == str(item.id)
    assert GardenState(garden).garden == garden
    # The exact scene data is also drawable without QML or a desktop session.
    image = QImage(scene["width"], scene["height"], QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    for layer in scene["layers"]:
        painter.drawImage(layer["x"], layer["y"], QImage(str(sprites[layer["sprite"]].path)))
    painter.end()
    assert image.save(str(tmp_path / "garden-scene.png"))


@pytest.fixture
def copied_pack(tmp_path):
    import shutil

    for pack in ("plants-v1", "environment-v1"):
        source = garden_asset_root() / pack
        destination = tmp_path / pack
        destination.mkdir()
        shutil.copyfile(source / "manifest.json", destination / "manifest.json")
        for entry in json.loads((source / "manifest.json").read_text())["assets"]:
            target = destination / entry["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / entry["file"], target)
    return tmp_path


@pytest.mark.parametrize("problem", ["missing", "corrupt", "anchor", "incomplete"])
def test_runtime_pack_damage_fails_instead_of_substituting_another_image(copied_pack, problem):
    manifest_path = copied_pack / "environment-v1/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entry = next(a for a in manifest["assets"] if a["id"] == "rock")
    path = copied_pack / "environment-v1" / entry["file"]
    if problem == "missing":
        path.unlink()
    elif problem == "corrupt":
        path.write_bytes(path.read_bytes() + b"corrupt")
    elif problem == "anchor":
        entry["anchor"] = [-1, 80]
    else:
        manifest["assets"].remove(entry)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, FileNotFoundError)):
        load_garden_assets(copied_pack)


def test_reference_layout_projects_all_plants_decor_and_mixed_terrain(sprites, tmp_path):
    # The visual reference stores map[x][y], while the domain stores terrain[y*10+x].
    reference = json.loads(
        (garden_asset_root() / "environment-v1/review/scene-layout.json").read_text()
    )
    items = []
    for entry in reference["items"]:
        sprite_id = entry["id"]
        if sprite_id in ("rock", "fence", "bench", "lantern"):
            kind, stage = "stone" if sprite_id == "rock" else sprite_id, 0
        else:
            kind, stage_text = sprite_id.rsplit("_", 1)
            stage = int(stage_text)
        x, y = min(c[0] for c in entry["cells"]), min(c[1] for c in entry["cells"])
        item = GardenItem(kind, uuid4(), stage, x, y)
        assert item.cells == set(map(tuple, entry["cells"]))
        items.append(item)
    garden = Garden(
        "2026-11",
        terrain=tuple(reference["map"][x][y] for y in range(10) for x in range(10)),
        items=tuple(items),
    )
    assert (garden.planted_cells, garden.mature_cells) == (21, 16)
    scene = garden_scene(garden, sprites)
    assert len([f for f in scene["layers"] if f["itemId"]]) == len(items)
    image = QImage(scene["width"], scene["height"], QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    for layer in scene["layers"]:
        painter.drawImage(layer["x"], layer["y"], QImage(str(sprites[layer["sprite"]].path)))
    painter.end()
    assert image.save(str(tmp_path / "garden-reference.png"))
    assert image.scaled(image.width() * 2, image.height() * 2).save(
        str(tmp_path / "garden-reference-2x.png")
    )
