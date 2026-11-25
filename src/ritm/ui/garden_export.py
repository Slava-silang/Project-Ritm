"""Полный снимок поля и автономный HTML; вызывается только из storage-worker."""

import base64
import hashlib
import html
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter

from ritm.platform.garden_assets import load_garden_assets
from ritm.storage.garden import encode_garden
from ritm.ui.garden_scene import garden_scene


def garden_png(garden):
    sprites = load_garden_assets()
    scene = garden_scene(garden, sprites)
    padding = 16
    width, height = scene["width"] + padding * 2, scene["height"] + padding * 2
    if width > 2048 or height > 2048:
        raise ValueError("Размер изображения сада превышает допустимый предел.")
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        raise ValueError("Не удалось подготовить изображение сада.")
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    cache = {}
    try:
        for layer in scene["layers"]:
            key = layer["sprite"]
            if key not in cache:
                sprite = QImage(str(sprites[key].path))
                if sprite.isNull():
                    raise ValueError("Не удалось прочитать графику сада для экспорта.")
                cache[key] = sprite
            painter.drawImage(layer["x"] + padding, layer["y"] + padding, cache[key])
    finally:
        painter.end()
    image = image.scaled(
        width * 2,
        height * 2,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("Не удалось сохранить изображение PNG.")
    return bytes(buffer.data())


def garden_html(garden, png):
    payload = encode_garden(garden)
    record = (
        json.dumps(
            {
                "format": "ritm-garden-1",
                "garden": json.loads(payload),
                "sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "image_sha256": hashlib.sha256(png).hexdigest(),
            },
            ensure_ascii=False,
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    rows = []
    for item in garden.items:
        place = f"На поле · {item.x + 1}:{item.y + 1}" if item.placed else "В инвентаре"
        stage = f"{item.stage}/4" if item.product.growing else "Украшение"
        rows.append(
            f"<tr><td>{html.escape(item.product.title)}</td><td>{stage}</td><td>{place}</td></tr>"
        )
    inventory = "".join(rows) or '<tr><td colspan="3">Предметов пока нет.</td></tr>'
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:;
style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Сад · {garden.month}</title>
<style>
* {{ box-sizing: border-box }}
body {{ margin: 0; background: #1b2031; color: #f0f1fb; font: 16px/1.6 system-ui, sans-serif }}
main {{ max-width: 980px; margin: auto; padding: 32px 20px }}
h1 {{ margin: 0; font-size: 32px }}
p, footer {{ color: #c5cde4 }}
figure {{ margin: 24px 0; border-radius: 20px; background: #242b40; padding: 16px }}
img {{ display: block; max-width: 100%; height: auto; margin: auto; image-rendering: pixelated }}
table {{ width: 100%; border-collapse: collapse; text-align: left }}
td, th {{ padding: 10px 8px; border-bottom: 1px solid #6e7c9f }}
footer {{ margin-top: 24px; font-size: 14px }}
</style></head><body><main>
<h1>Сад · {garden.month}</h1>
<p>Взрослых клеток: {garden.mature_cells}/80 · Занято растениями: {garden.planted_cells}/80
· Удобрение: {garden.fertilizer}</p>
<figure><img alt="Участок сада за {garden.month}"
src="data:image/png;base64,{base64.b64encode(png).decode()}"></figure>
<h2>Растения и украшения</h2>
<table><thead><tr><th>Предмет</th><th>Стадия</th><th>Расположение</th></tr></thead>
<tbody>{inventory}</tbody></table>
<footer>Ритм · Автономная копия участка · Только просмотр</footer>
<script id="ritm-garden" type="application/json">{record}</script>
</main></body></html>""".encode()


def export_garden(garden, destination, kind):
    """Замена только после полной записи; сбой оставляет прежний файл целым."""
    path = Path(destination)
    if kind not in ("html", "png") or path.suffix.lower() != "." + kind:
        raise ValueError("Выберите имя файла с расширением .html или .png для выбранного формата.")
    png = garden_png(garden)
    content = garden_html(garden, png) if kind == "html" else png
    stream = NamedTemporaryFile(dir=path.parent, prefix=".ritm-garden-", delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
