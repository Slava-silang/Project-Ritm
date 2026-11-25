import base64
import hashlib
import json
import os
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtGui import QImage

from ritm.domain.garden import Garden, GardenItem
from ritm.platform.garden_assets import load_garden_assets
from ritm.storage.garden import decode_garden, encode_garden
from ritm.ui import garden_export
from ritm.ui.garden_scene import garden_scene


def test_garden_export_gui(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{k}_HOME": str(tmp_path / k.lower()) for k in ("CACHE", "DATA", "CONFIG")},
    }
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("ui_garden_export_scenario.py")),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("TypeError", "ReferenceError", "Binding loop", "Cannot assign"):
        assert warning not in result.stderr, result.stderr


class ExportPage(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.images, self.record, self.scripts = [], "", []
        self.in_record = False
        self.feed(content.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            self.images.append(attrs["src"])
        if tag == "script":
            self.scripts.append(attrs)
            self.in_record = attrs.get("id") == "ritm-garden"

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_record = False

    def handle_data(self, data):
        if self.in_record:
            self.record += data


def planted():
    return Garden(
        "2026-11",
        items=(
            GardenItem("sakura", uuid4(), 4, 0, 0),
            GardenItem("daisy", uuid4(), 2, 9, 9),
            GardenItem("bench", uuid4(), 0, 6, 6),
            GardenItem("fern", uuid4(), 1),
        ),
        fertilizer=7,
    )


@pytest.mark.parametrize("garden", [Garden("2026-12"), planted()])
def test_png_contains_entire_scene_with_transparent_margin_at_integer_scale(garden):
    png = garden_export.garden_png(garden)
    image = QImage.fromData(png)
    scene = garden_scene(garden, load_garden_assets())
    assert image.width() == (scene["width"] + 32) * 2
    assert image.height() == (scene["height"] + 32) * 2
    assert image.hasAlphaChannel() and image.pixelColor(0, 0).alpha() == 0
    assert not image.isNull()
    assert png == garden_export.garden_png(garden)


def test_html_is_portable_and_contains_exact_property_and_embedded_png(tmp_path):
    garden = planted()
    original = garden_export.export_garden(garden, tmp_path / "Сад #1.html", "html")
    moved = tmp_path / "other-folder"
    moved.mkdir()
    original.rename(moved / original.name)
    page = ExportPage((moved / original.name).read_bytes())
    record = json.loads(page.record)
    assert record["format"] == "ritm-garden-1"
    assert decode_garden(json.dumps(record["garden"])) == garden
    assert record["sha256"] == hashlib.sha256(encode_garden(garden).encode()).hexdigest()
    assert len(page.images) == 1 and page.images[0].startswith("data:image/png;base64,")
    png = base64.b64decode(page.images[0].split(",", 1)[1], validate=True)
    assert record["image_sha256"] == hashlib.sha256(png).hexdigest()
    assert not QImage.fromData(png).isNull()
    assert page.scripts == [{"id": "ritm-garden", "type": "application/json"}]
    content = (moved / original.name).read_text()
    assert "file://" not in content and "http://" not in content and "https://" not in content
    assert "В инвентаре" in content and "Удобрение: 7" in content
    assert "balance" not in record and "source" not in record


@pytest.mark.parametrize("failure", ["replace", "fsync", "render"])
@pytest.mark.parametrize("kind", ["html", "png"])
def test_failed_export_keeps_existing_file_and_removes_temporary(
    tmp_path, monkeypatch, failure, kind
):
    path = tmp_path / f"existing.{kind}"
    path.write_bytes(b"previous export")

    def fail(*args):
        raise OSError("injected export failure")

    if failure == "render":
        monkeypatch.setattr(garden_export, "garden_png", fail)
    else:
        monkeypatch.setattr(garden_export.os, failure, fail)
    with pytest.raises(OSError, match="injected"):
        garden_export.export_garden(Garden("2026-11"), path, kind)
    assert path.read_bytes() == b"previous export"
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


@pytest.mark.parametrize(
    "name,kind",
    [("tasks.sqlite3", "png"), ("wrong.html", "png"), ("a.jpg", "jpeg"), ("missing", "html")],
)
def test_wrong_extension_is_rejected_before_any_write(tmp_path, name, kind):
    path = tmp_path / name
    path.write_bytes(b"keep")
    with pytest.raises(ValueError, match="расширением"):
        garden_export.export_garden(Garden("2026-11"), path, kind)
    assert path.read_bytes() == b"keep"


def test_successful_replace_and_missing_parent(tmp_path):
    path = tmp_path / "сад.PNG"
    path.write_bytes(b"old")
    garden_export.export_garden(Garden("2026-11"), path, "png")
    assert not QImage(str(path)).isNull()
    with pytest.raises(FileNotFoundError):
        garden_export.export_garden(Garden("2026-11"), tmp_path / "absent" / "garden.png", "png")
    assert not (tmp_path / "absent").exists()
