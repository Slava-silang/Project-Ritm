"""Общий контракт без привязки к SDK, аккаунту или ключу провайдера."""

import json
from dataclasses import dataclass
from threading import Event
from typing import Literal, Protocol
from uuid import UUID

from ritm.domain.tasks import Task


@dataclass(frozen=True)
class Connection:
    provider: str
    model: str
    authentication: Literal["none", "account", "api_key"]
    operations: frozenset[str] = frozenset({"create", "split"})


@dataclass(frozen=True)
class SourceText:
    name: str
    text: str


@dataclass(frozen=True)
class Request:
    id: UUID
    prompt: str
    operation: Literal["create", "split"]
    selected: Task | None = None
    materials: tuple[SourceText, ...] = ()


class Adapter(Protocol):
    connection: Connection

    def generate(self, request: Request, cancelled: Event) -> str:
        """Вернуть JSON-предложение. Учётные данные остаются внутри адаптера."""
        ...


class MockAdapter:
    """Предсказуемый сценарий, явно обозначенный в интерфейсе как тестовый."""

    connection = Connection("mock", "local-demo", "none")

    def generate(self, request: Request, cancelled: Event) -> str:
        if cancelled.wait(0.15):
            raise ValueError("Запрос отменён")
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
