"""Чистый снимок общего цикла Pomodoro; работа берётся из фокус-сессий."""

from dataclasses import asdict, dataclass, replace
from uuid import UUID

MINUTE = 60_000_000


@dataclass(frozen=True)
class Pomodoro:
    phase: str = "idle"
    cycle: int = 0
    work: int = 25
    short: int = 5
    long: int = 15
    elapsed_us: int = 0
    base_us: int = 0
    session_id: str = ""
    running_since_us: int | None = None
    revision: int = 0

    def __post_init__(self):
        if self.phase not in ("idle", "work", "short", "long"):
            raise ValueError("Неизвестный этап Pomodoro")
        for value in (self.work, self.short, self.long):
            if type(value) is not int or not 1 <= value <= 240:
                raise ValueError("Длительности Pomodoro должны быть от 1 до 240 минут")
        if type(self.cycle) is not int or not 0 <= self.cycle < 4:
            raise ValueError("Некорректный цикл Pomodoro")
        for value in (self.elapsed_us, self.base_us, self.revision):
            if type(value) is not int or value < 0:
                raise ValueError("Некорректное время Pomodoro")
        if not isinstance(self.session_id, str):
            raise ValueError("Некорректная ссылка на фокус-сессию")
        if self.session_id:
            UUID(self.session_id)
        if self.running_since_us is not None and type(self.running_since_us) is not int:
            raise ValueError("Некорректное начало перерыва")
        if self.running_since_us is not None and self.phase not in ("short", "long"):
            raise ValueError("Только перерыв использует отдельный таймер")

    @property
    def target_us(self):
        return {"idle": 0, "work": self.work, "short": self.short, "long": self.long}[
            self.phase
        ] * MINUTE

    def elapsed(self, now_us, focus=None, *, recovering=False):
        if self.phase == "work" and focus is not None and str(focus.id) == self.session_id:
            worked = focus.worked_us if recovering else focus.elapsed(now_us)
            return self.elapsed_us + max(0, worked - self.base_us)
        if self.running_since_us is not None:
            return min(self.target_us, self.elapsed_us + max(0, now_us - self.running_since_us))
        return self.elapsed_us

    def checkpoint_break(self, now_us, *, pause=False, startup=False):
        if self.phase not in ("short", "long"):
            return self
        elapsed = self.elapsed_us if startup else self.elapsed(now_us)
        return replace(
            self,
            elapsed_us=elapsed,
            running_since_us=None if pause or startup or elapsed >= self.target_us else now_us,
        )

    def to_dict(self):
        return asdict(self)
