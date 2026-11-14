"""Пользовательская текстовая инструкция без исполняемых действий."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Skill:
    id: UUID
    name: str
    description: str
    instruction: str

    def __post_init__(self):
        if not isinstance(self.id, UUID):
            raise ValueError("Неверный идентификатор навыка")
        for field, limit, required in (
            ("name", 120, True),
            ("description", 1000, False),
            ("instruction", 12000, True),
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or len(value.strip()) > limit:
                label = {
                    "name": "Название",
                    "description": "Описание",
                    "instruction": "Инструкция",
                }[field]
                raise ValueError(f"Поле «{label}» должно быть текстом длиной до {limit} символов")
            value = value.strip()
            if required and not value:
                raise ValueError("Название и инструкция навыка обязательны")
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("Текст навыка содержит недопустимые символы") from None
            object.__setattr__(self, field, value)

    def data(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "instruction": self.instruction,
        }

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.data(), sort_keys=True).encode()).hexdigest()
