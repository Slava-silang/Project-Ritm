"""Настройки персонажа отдельно от задач; оригиналы картинок не изменяются."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

PETS = {"dragon": "Китайский дракончик", "cat": "Котёнок", "dog": "Щенок"}
SIZES = (160, 224, 288, 352)
DEFAULT = {"enabled": False, "pet": "dragon", "size": 224, "x": None, "y": None, "muted": False}
LEGACY_KEYS = set(DEFAULT)
DEFAULT.update(frequency="normal", break_minutes=60, reduce_motion=False, hide_fullscreen=True)


def validate_companion(data):
    if isinstance(data, dict) and set(data) == LEGACY_KEYS:
        data = {**DEFAULT, **data}
    if (
        not isinstance(data, dict)
        or set(data) != set(DEFAULT)
        or type(data["enabled"]) is not bool
        or type(data["muted"]) is not bool
        or not isinstance(data["pet"], str)
        or data["pet"] not in PETS
        or type(data["size"]) is not int
        or data["size"] not in SIZES
        or type(data["reduce_motion"]) is not bool
        or type(data["hide_fullscreen"]) is not bool
        or data["frequency"] not in ("rare", "normal", "often")
        or type(data["break_minutes"]) is not int
        or not 5 <= data["break_minutes"] <= 240
    ):
        raise ValueError("Выберите доступного персонажа и размер")
    if not (
        (data["x"] is None and data["y"] is None)
        or all(type(data[key]) is int and abs(data[key]) <= 100000 for key in ("x", "y"))
    ):
        raise ValueError("Неверное положение компаньона")
    return dict(data)


def load_companion(profile):
    path = profile / "companion.json"
    if not path.exists():
        return dict(DEFAULT)
    try:
        if path.stat().st_size > 4096:
            raise ValueError()
        return validate_companion(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError, KeyError):
        raise ValueError(
            "Настройки компаньона повреждены. Выберите их заново и примените явно."
        ) from None


def save_companion(profile, data):
    data = validate_companion(data)
    with NamedTemporaryFile(dir=profile, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(data).encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, profile / "companion.json")
        finally:
            temporary.unlink(missing_ok=True)
    return data


def pet_asset(pet):
    if pet not in PETS:
        raise ValueError("Неизвестный персонаж")
    package = Path(__file__).resolve().parents[1] / "assets" / "pets" / f"{pet}.jpg"
    return (
        package
        if package.is_file()
        else Path(__file__).resolve().parents[3] / "assets" / "pets" / f"{pet}.jpg"
    )
