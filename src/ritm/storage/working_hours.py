"""Настройки рабочих часов отдельно от задач и истории чата."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from ritm.application.planning import validate_hours

DEFAULT = ((540, 1080),) * 5 + (None, None)


def load_hours(profile):
    path = profile / "working-hours.json"
    if not path.exists():
        return DEFAULT
    try:
        if path.stat().st_size > 4096:
            raise ValueError()
        return validate_hours(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as error:
        raise ValueError(
            "Не удалось прочитать рабочие часы. Укажите и сохраните их заново."
        ) from error


def save_hours(profile, value):
    hours = validate_hours(value)
    with NamedTemporaryFile(dir=profile, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(hours).encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, profile / "working-hours.json")
        finally:
            temporary.unlink(missing_ok=True)
    return hours
