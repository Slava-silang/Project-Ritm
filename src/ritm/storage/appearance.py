"""Независимые от данных задач настройки оформления."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

THEMES = ("light", "dark", "dusk", "space", "jungle", "system")
SCALES = (100, 125, 150)
PANELS = ("left", "top")
DEFAULT = {"theme": "light", "scale": 100, "panel": "left"}


def validate_appearance(theme: str, scale: int, panel: str = "left") -> dict:
    if theme not in THEMES or type(scale) is not int or scale not in SCALES or panel not in PANELS:
        raise ValueError("Выберите тему и доступный масштаб интерфейса")
    return {"theme": theme, "scale": scale, "panel": panel}


def load_appearance(profile: Path) -> dict:
    path = profile / "appearance.json"
    if not path.exists():
        return dict(DEFAULT)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_appearance(
            data["theme"],
            data["scale"],
            data.get("panel", "top" if data["theme"] in ("dusk", "jungle") else "left"),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(
            "Не удалось прочитать настройки оформления. Выберите их заново."
        ) from error


def save_appearance(profile: Path, theme: str, scale: int, panel: str | None = None) -> dict:
    if panel is None:
        try:
            panel = load_appearance(profile)["panel"]
        except ValueError:
            panel = DEFAULT["panel"]
    data = validate_appearance(theme, scale, panel)
    stream = NamedTemporaryFile(dir=profile, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(json.dumps(data).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, profile / "appearance.json")
    finally:
        temporary.unlink(missing_ok=True)
    return data
