"""Общий контракт без привязки к SDK, аккаунту или ключу провайдера."""

import json
from dataclasses import dataclass, field
from threading import Event
from typing import Literal, Protocol
from uuid import UUID

from ritm.domain.skills import Skill
from ritm.domain.tasks import Task


@dataclass(frozen=True)
class Connection:
    provider: str
    model: str
    authentication: Literal["none", "account", "api_key"]
    operations: frozenset[str] = frozenset({"create", "split"})
    input_kinds: frozenset[str] = frozenset({"text"})


@dataclass(frozen=True)
class SourceText:
    name: str
    text: str


@dataclass(frozen=True)
class SourceFile:
    name: str
    mime: str
    data: bytes = field(repr=False)
    kind: Literal["image", "file"] = "file"


@dataclass(frozen=True)
class ChatTurn:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class Request:
    id: UUID
    prompt: str
    operation: Literal["create", "split", "schedule"]
    selected: Task | None = None
    materials: tuple[SourceText, ...] = ()
    history: tuple[ChatTurn, ...] = ()
    planning: dict | None = None
    skill: Skill | None = None
    files: tuple[SourceFile, ...] = ()


class Adapter(Protocol):
    connection: Connection

    def generate(self, request: Request, cancelled: Event) -> str:
        """Вернуть JSON-предложение. Учётные данные остаются внутри адаптера."""
        ...


class MockAdapter:
    """Предсказуемый сценарий, явно обозначенный в интерфейсе как тестовый."""

    connection = Connection(
        "mock", "local-demo", "none", frozenset({"create", "split", "schedule"})
    )

    def generate(self, request: Request, cancelled: Event) -> str:
        if cancelled.wait(0.15):
            raise ValueError("Запрос отменён")
        if request.operation == "schedule":
            from datetime import datetime, timedelta

            remaining = timedelta(minutes=request.planning["remaining_minutes"])
            blocks = []
            for slot in request.planning["free"][:200]:
                start, end = (
                    datetime.fromisoformat(slot["start"]),
                    datetime.fromisoformat(slot["end"]),
                )
                duration = min(end - start, remaining)
                if duration > timedelta():
                    blocks.append(
                        {"start": start.isoformat(), "end": (start + duration).isoformat()}
                    )
                    remaining -= duration
            return json.dumps({"blocks": blocks})
        proposal = {
            "subtasks": [
                {
                    "title": "Разобраться с заданием",
                    "items": ["Прочитать условия", "Выписать вопросы"],
                },
                {
                    "title": "Выполнить работу",
                    "items": ["Подготовить решение", "Проверить результат"],
                },
            ],
        }
        if request.operation == "create":
            proposal.update(title=request.prompt.strip()[:120], description=request.prompt)
        return json.dumps(proposal, ensure_ascii=False)
