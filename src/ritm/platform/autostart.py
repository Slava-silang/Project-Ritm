"""Пользовательский XDG-автозапуск; файл служит источником настройки."""

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def entry_path(config: Path, profile: Path) -> Path:
    identifier = hashlib.sha256(os.fsencode(profile.resolve())).hexdigest()[:16]
    return config / "autostart" / f"project-ritm-{identifier}.desktop"


def quote_argument(value: str) -> str:
    # Desktop Entry Exec: не shell; проценты имеют собственное экранирование.
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Путь запуска содержит неподдерживаемые символы")
    value = value.replace("%", "%%")
    for character in ("\\", '"', "`", "$"):
        value = value.replace(character, "\\" + character)
    return '"' + value.replace("\\", "\\\\") + '"'


def read_autostart(path: Path) -> dict:
    if not path.exists():
        return {"enabled": False, "hidden": False}
    text = path.read_text(encoding="utf-8")
    return {
        "enabled": "Hidden=true" not in text.splitlines(),
        "hidden": "X-Ritm-Hidden=true" in text.splitlines(),
    }


def write_autostart(path: Path, command: list[str], enabled: bool, hidden: bool) -> dict:
    if not enabled:
        path.unlink(missing_ok=True)
        return {"enabled": False, "hidden": hidden}
    # GLib проверяет существование первого executable до раскрытия %%.
    # env позволяет сохранять буквальный % даже в пути к самому приложению.
    arguments = ["/usr/bin/env", "--", *command, *(["--start-hidden"] if hidden else [])]
    text = (
        "[Desktop Entry]\nType=Application\nName=Ритм\nTerminal=false\n"
        f"Exec={' '.join(quote_argument(part) for part in arguments)}\n"
        f"X-Ritm-Hidden={'true' if hidden else 'false'}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text.encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {"enabled": True, "hidden": hidden}
