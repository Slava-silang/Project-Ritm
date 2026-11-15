"""Локальные настройки поведения таймеров, отдельно от базы задач."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def load_focus_settings(profile: Path) -> bool:
    path = profile / "focus-settings.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {"pause_on_system"}:
            raise ValueError("Неверные поля")
        if type(data["pause_on_system"]) is not bool:
            raise ValueError("Неверный тип")
        return data["pause_on_system"]
    except (ValueError, TypeError) as error:
        raise ValueError(
            "Не удалось прочитать настройку паузы. Автопауза выключена; выберите настройку заново."
        ) from error


def save_focus_settings(profile: Path, pause_on_system: bool) -> bool:
    if type(pause_on_system) is not bool:
        raise ValueError("Выберите настройку паузы")
    with NamedTemporaryFile(dir=profile, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps({"pause_on_system": pause_on_system}).encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, profile / "focus-settings.json")
        finally:
            temporary.unlink(missing_ok=True)
    return pause_on_system
