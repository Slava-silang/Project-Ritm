"""Чистая проекция поля в спрайты и клетки выбора; без QML, Qt, файлов и часов."""

import math

from ritm.domain.garden import HEIGHT, WIDTH
from ritm.platform.garden_assets import CORNERS, EDGE_BITS, EDGE_FAMILIES


def project(x, y):
    return (x - y) * 32, (x + y) * 16


def garden_scene(garden, sprites):
    ground, overlays, objects = [], [], []
    centers, owners = {}, {}

    def terrain(x, y):
        return garden.terrain[y * WIDTH + x] if 0 <= x < WIDTH and 0 <= y < HEIGHT else None

    def frame(identifier, center, item_id=""):
        sprite = sprites[identifier]
        return {
            "sprite": identifier,
            "source": sprite.path.as_uri(),
            "role": sprite.role,
            "x": center[0] - sprite.anchor[0],
            "y": center[1] - sprite.anchor[1],
            "width": sprite.canvas[0],
            "height": sprite.canvas[1],
            "itemId": item_id,
        }

    order = sorted(
        ((x, y) for y in range(HEIGHT) for x in range(WIDTH)), key=lambda p: (sum(p), p[0])
    )
    for x, y in order:
        center, base = project(x, y), terrain(x, y)
        centers[x, y] = center
        ground.append(frame(base, center))
        for family, base_terrain, neighbor in EDGE_FAMILIES:
            if base != base_terrain:
                continue
            mask = sum(bit for bit, dx, dy in EDGE_BITS if terrain(x + dx, y + dy) == neighbor)
            if mask:
                overlays.append(frame(f"{family}_{mask}", center))
            for index, (dx, dy, side_a, side_b) in enumerate(CORNERS):
                _, ax, ay = EDGE_BITS[side_a]
                _, bx, by = EDGE_BITS[side_b]
                if (
                    terrain(x + ax, y + ay) == base
                    and terrain(x + bx, y + by) == base
                    and terrain(x + dx, y + dy) == neighbor
                ):
                    overlays.append(frame(f"{family}_corner_{index}", center))
    placed = [item for item in garden.items if item.placed]
    placed.sort(key=lambda item: (max(x + y for x, y in item.cells), item.x - item.y, str(item.id)))
    for item in placed:
        points = [centers[cell] for cell in item.cells]
        # All approved footprints have integer centers in this 32/16 pixel basis.
        center = tuple(sum(p[axis] for p in points) // len(points) for axis in (0, 1))
        objects.append(frame(item.sprite_id, center, str(item.id)))
        owners.update((cell, str(item.id)) for cell in item.cells)
    layers = ground + overlays + objects
    left, top = min(f["x"] for f in layers), min(f["y"] for f in layers)
    right = max(f["x"] + f["width"] for f in layers)
    bottom = max(f["y"] + f["height"] for f in layers)
    for layer in layers:
        layer["x"] -= left
        layer["y"] -= top
    return {
        "width": right - left,
        "height": bottom - top,
        "originX": -left,
        "originY": -top,
        "layers": layers,
        "cells": [
            {
                "x": x,
                "y": y,
                "centerX": centers[x, y][0] - left,
                "centerY": centers[x, y][1] - top,
                "terrain": terrain(x, y),
                "itemId": owners.get((x, y), ""),
            }
            for y in range(HEIGHT)
            for x in range(WIDTH)
        ],
    }


def cell_at(scene, screen_x, screen_y):
    """Выбрать ромб поверхности, не прозрачный прямоугольник PNG/боковину земли."""
    if any(
        type(value) not in (int, float) or not math.isfinite(value)
        for value in (screen_x, screen_y)
    ):
        return None
    sx, sy = screen_x - scene["originX"], screen_y - scene["originY"]
    x = math.floor((sx / 32 + sy / 16) / 2 + 0.5)
    y = math.floor((sy / 16 - sx / 32) / 2 + 0.5)
    return (x, y) if 0 <= x < WIDTH and 0 <= y < HEIGHT else None
