"""Снимок фокус-сессии и расчёт фактического времени."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from uuid import UUID

from ritm.domain.calendar import from_instant_us, instant_us


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


def focus_by_day(intervals: Iterable[tuple[int, int]], zone: tzinfo | None) -> dict[date, int]:
    """Разбить закрытые полуоткрытые интервалы по местным суткам."""
    totals: dict[date, int] = {}
    for start, end in intervals:
        while start < end:
            day = from_instant_us(start).astimezone(zone).date()
            if day == date.max:
                stop = end
            else:
                midnight = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
                stop = min(end, instant_us(midnight.astimezone(UTC)))
            totals[day] = totals.get(day, 0) + stop - start
            start = stop
    return totals
