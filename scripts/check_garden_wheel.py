"""Проверить runtime-графику реально установленного wheel вне исходного репозитория."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

CHECK = """
import sys
from pathlib import Path
from uuid import uuid4
import ritm
from PySide6.QtGui import QImage
from ritm.domain.garden import Garden, GardenItem
from ritm.platform.garden_assets import garden_asset_root, load_garden_assets
from ritm.ui.garden_scene import garden_scene, cell_at
installed = Path(sys.argv[1]).resolve()
assert Path(ritm.__file__).resolve().is_relative_to(installed)
assert garden_asset_root().resolve().is_relative_to(installed)
sprites = load_garden_assets()
assert len(sprites) == 98
for sprite in sprites.values():
    image = QImage(str(sprite.path))
    assert not image.isNull()
    assert (image.width(), image.height()) == sprite.canvas
scene = garden_scene(Garden("2026-11", items=(
    GardenItem("bench", uuid4(), 0, 8, 8),
    GardenItem("oak", uuid4(), 4, 0, 0),
)), sprites)
assert len(scene["cells"]) == 100
for cell in scene["cells"]:
    assert cell_at(scene, cell["centerX"], cell["centerY"]) == (cell["x"], cell["y"])
assert scene["layers"][-1]["sprite"] == "bench"
print("Wheel: 98 игровых PNG загружены Qt, проекция/100 клеток проверены вне репозитория.")
"""


def main():
    wheel = Path(sys.argv[1]).resolve()
    with ZipFile(wheel) as archive:
        garden_files = [n for n in archive.namelist() if n.startswith("ritm/assets/garden/")]
        assert sum(n.endswith(".png") for n in garden_files) == 99  # includes shared support grass
        assert sum(n.endswith("manifest.json") for n in garden_files) == 2
        assert not any(
            set(Path(n).parts)
            & {"sources", "review", "references", "mockups", "design-v1", "probe-v1"}
            for n in garden_files
        )
    with TemporaryDirectory(prefix="ritm-wheel-garden-") as directory:
        root = Path(directory)
        installed, cwd = root / "installed", root / "Проверка вне проекта"
        cwd.mkdir()
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                sys.executable,
                "--target",
                str(installed),
                "--no-deps",
                "--offline",
                str(wheel),
            ],
            check=True,
        )
        subprocess.run(
            [sys.executable, "-c", CHECK, str(installed)],
            cwd=cwd,
            env={**os.environ, "PYTHONPATH": str(installed)},
            check=True,
        )


if __name__ == "__main__":
    main()
