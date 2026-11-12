"""Независимые от данных задач настройки оформления."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

THEMES = ("light", "dark", "space", "jungle", "system")
SCALES = (100, 125, 150)
DEFAULT = {"theme": "light", "scale": 100}


def validate_appearance(theme: str, scale: int) -> dict:
    if theme not in THEMES or type(scale) is not int or scale not in SCALES:
        raise ValueError("Выберите тему и доступный масштаб интерфейса")
    return {"theme": theme, "scale": scale}


def load_appearance(profile: Path) -> dict:
    path = profile / "appearance.json"
    if not path.exists():
        return dict(DEFAULT)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_appearance(data["theme"], data["scale"])
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(
            "Не удалось прочитать настройки оформления. Выберите их заново."
        ) from error


def save_appearance(profile: Path, theme: str, scale: int) -> dict:
    data = validate_appearance(theme, scale)
    with NamedTemporaryFile(dir=profile, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(data).encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, profile / "appearance.json")
        finally:
            temporary.unlink(missing_ok=True)
    return data
