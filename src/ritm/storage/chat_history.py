"""Локальная история без токенов и файлового текста, отдельная от архива задач."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

MAX_BYTES = 1024 * 1024


class HistoryError(ValueError):
    pass


class ChatHistory:
    def __init__(self, profile: Path):
        self.path = profile / "chat-history.json"
        self.enabled = True
        self.messages = []
        self.valid = True

    def load(self):
        if not self.path.exists():
            return
        try:
            with self.path.open("rb") as source:
                raw = source.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError
            data = json.loads(raw)
            if data["version"] != 1 or type(data["enabled"]) is not bool:
                raise ValueError
            messages = data["messages"]
            if not isinstance(messages, list) or len(messages) > 1000:
                raise ValueError
            ids = set()
            for row in messages:
                if set(row) not in (
                    {"id", "chat", "role", "text", "time", "label"},
                    {"id", "chat", "role", "text", "time", "label", "scope"},
                ):
                    raise ValueError
                if any(not isinstance(value, str) for value in row.values()):
                    raise ValueError
                UUID(row["id"])
                UUID(row["chat"])
                if row["id"] in ids or row["role"] not in ("user", "assistant", "notice"):
                    raise ValueError
                ids.add(row["id"])
            self.enabled, self.messages = data["enabled"], messages
        except (ValueError, KeyError, TypeError, OSError, RecursionError):
            self.enabled, self.valid = False, False
            raise HistoryError(
                "Не удалось прочитать историю. Она не перезаписана. Можно очистить её явно."
            ) from None

    def _write(self, enabled, messages):
        raw = json.dumps(
            {"version": 1, "enabled": enabled, "messages": messages}, ensure_ascii=False
        ).encode()
        # Оставить байт на true → false, чтобы отключение работало даже у лимита.
        if len(raw) > MAX_BYTES - int(enabled) or len(messages) > 1000:
            raise HistoryError(
                "История заполнена. Прежние сообщения сохранены; новые остаются в памяти. "
                "Очистите историю или отключите сохранение."
            )
        with NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        self.enabled, self.messages, self.valid = enabled, messages, True

    def append(self, row):
        if not self.valid or not self.enabled:
            return
        if any(previous["id"] == row["id"] for previous in self.messages):
            return
        self._write(self.enabled, [*self.messages, row])

    def set_enabled(self, enabled):
        if type(enabled) is not bool or not self.valid:
            raise HistoryError("Сначала очистите повреждённую историю.")
        self._write(enabled, self.messages)

    def clear(self):
        self._write(self.enabled, [])
