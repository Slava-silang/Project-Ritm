"""Игровые PNG сада из исходников или wheel, без Qt и изменения изображений."""

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ritm.domain.garden import CATALOG, TERRAINS

EDGE_FAMILIES = (
    ("soil_grass", "tilled_soil", "grass"),
    ("water_soil", "water", "tilled_soil"),
    ("water_grass", "water", "grass"),
)
EDGE_BITS = ((1, -1, 0), (2, 0, -1), (4, 1, 0), (8, 0, 1))
CORNERS = ((-1, -1, 0, 1), (1, -1, 1, 2), (1, 1, 2, 3), (-1, 1, 3, 0))


@dataclass(frozen=True)
class GardenSprite:
    id: str
    role: str
    path: Path
    canvas: tuple[int, int]
    anchor: tuple[int, int]


def garden_asset_root():
    package = Path(__file__).resolve().parents[1] / "assets/garden"
    return package if package.is_dir() else Path(__file__).resolve().parents[3] / "assets/garden"


def _pair(value, *, positive=False):
    if (
        type(value) is not list
        or len(value) != 2
        or any(type(n) is not int or n < int(positive) for n in value)
    ):
        raise ValueError("Некорректные размеры или привязка графики сада.")
    return tuple(value)


def load_garden_assets(root=None):
    """Читать в worker один раз при подключении графики; все 98 PNG проверяются."""
    root = Path(root) if root is not None else garden_asset_root()
    sprites = {}
    for pack in ("plants-v1", "environment-v1"):
        folder = root / pack
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if (
            manifest["version"] != 1
            or manifest["tile_surface"] != [64, 32]
            or manifest["grid_vectors"] != [[32, 16], [-32, 16]]
        ):
            raise ValueError("Неизвестная геометрия графики сада.")
        for entry in manifest["assets"]:
            if pack == "plants-v1" and entry["role"] != "plant":
                continue  # The shared grass tile comes from environment-v1 only.
            identifier = entry["catalog_kind"] if entry["role"] == "decor" else entry["id"]
            path = (folder / entry["file"]).resolve()
            if not path.is_relative_to(folder.resolve()) or path.suffix != ".png":
                raise ValueError("PNG сада должен находиться внутри игрового набора.")
            canvas, anchor = _pair(entry["canvas"], positive=True), _pair(entry["anchor"])
            content = path.read_bytes()
            if (
                content[:8] != b"\x89PNG\r\n\x1a\n"
                or len(content) < 24
                or struct.unpack(">II", content[16:24]) != canvas
                or hashlib.sha256(content).hexdigest() != entry["sha256"]
                or any(a > size for a, size in zip(anchor, canvas, strict=True))
            ):
                raise ValueError("PNG сада не совпадает с утверждённым манифестом.")
            if identifier in sprites:
                raise ValueError("Повторный ID графики сада.")
            sprites[identifier] = GardenSprite(identifier, entry["role"], path, canvas, anchor)
    expected = {
        f"{p.id}_{stage:02}" for p in CATALOG.values() if p.growing for stage in range(1, 5)
    }
    expected.update(p.id for p in CATALOG.values() if not p.growing)
    expected.update(TERRAINS | {"fertilizer", "fertilizer_32"})
    expected.update(f"{name}_{mask}" for name, _, _ in EDGE_FAMILIES for mask in range(1, 16))
    expected.update(
        f"{name}_corner_{corner}" for name, _, _ in EDGE_FAMILIES for corner in range(4)
    )
    if set(sprites) != expected:
        raise ValueError("Игровой набор сада неполон или содержит неизвестные изображения.")
    return MappingProxyType(sprites)
