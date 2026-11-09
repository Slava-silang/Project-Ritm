"""Снимок фокус-сессии и расчёт фактического времени."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class FocusSession:
    id: UUID
    task_id: UUID
    started_us: int
    target_us: int | None
    finished_us: int | None
    running_since_us: int | None
    worked_us: int
    overtime: bool
    revision: int

    def elapsed(self, now_us: int) -> int:
        if self.running_since_us is None:
            return self.worked_us
        return self.worked_us + max(0, now_us - self.running_since_us)

    def target_reached(self, now_us: int) -> bool:
        return (
            self.target_us is not None
            and not self.overtime
            and self.elapsed(now_us) >= self.target_us
        )
