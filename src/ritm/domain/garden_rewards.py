"""Сохраняемый учёт основной оплаты времени, без Qt и источников событий."""

import json
from dataclasses import asdict, dataclass
from datetime import date, tzinfo

from ritm.domain.focus import focus_by_day
from ritm.domain.garden import natural

MINUTE = 60_000_000
QUANTUM = 5 * MINUTE
DAY_LIMIT = 12 * 60 * MINUTE


def _instant(value):
    natural(value)
    if value > 253402300799999999:
        raise ValueError("Момент времени находится вне допустимого диапазона.")
    return value


@dataclass(frozen=True)
class RewardLedger:
    last_us: int = 0
    windows: tuple[tuple[int, int | None], ...] = ()
    days: tuple[tuple[str, int], ...] = ()
    paid_us: int = 0

    def __post_init__(self):
        _instant(self.last_us)
        natural(self.paid_us)
        if self.paid_us % QUANTUM:
            raise ValueError("Оплаченное время должно состоять из целых пятиминуток.")
        if type(self.windows) is not tuple or type(self.days) is not tuple:
            raise ValueError("Журнал должен быть неизменяемым.")
        previous = -1
        for index, window in enumerate(self.windows):
            if type(window) is not tuple or len(window) != 2:
                raise ValueError("Некорректное окно включения.")
            start, end = window
            _instant(start)
            if start < previous or start > self.last_us:
                raise ValueError("Окна включения пересекаются или находятся в будущем.")
            if end is None:
                if index != len(self.windows) - 1:
                    raise ValueError("Открытым может быть только последнее окно.")
                previous = self.last_us
            else:
                _instant(end)
                if not start <= end <= self.last_us:
                    raise ValueError("Некорректное окончание окна.")
                previous = end
        previous_day = ""
        for row in self.days:
            if type(row) is not tuple or len(row) != 2:
                raise ValueError("Некорректные сутки журнала.")
            day, duration = row
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                raise ValueError("Некорректная дата журнала.")
            natural(duration, positive=True)
            if day <= previous_day or duration > DAY_LIMIT:
                raise ValueError("Повторные сутки или превышен дневной предел.")
            previous_day = day
        if not self.windows and (self.days or self.paid_us):
            raise ValueError("Начисления без включения сада недопустимы.")

    @property
    def enabled(self):
        return bool(self.windows and self.windows[-1][1] is None)

    @property
    def debt_us(self):
        return max(0, self.paid_us - sum(duration for _, duration in self.days))

    @property
    def remainder_us(self):
        return max(0, sum(duration for _, duration in self.days) - self.paid_us)


@dataclass(frozen=True)
class TimeReward:
    ledger: RewardLedger
    paid_us: int

    @property
    def coins(self):
        return self.paid_us // QUANTUM * 10


def reconcile_time(ledger, intervals, *, now_us, zone, enabled=None):
    """Сверить рабочие интервалы; удаление истории не отнимает выданные монеты.

    intervals — полный согласованный снимок, а не очередная порция. Источник
    исключает перерывы и неподтверждённое аварийное время. Награды за задачи,
    распределение базовой оплаты по корням и достижения реализуются отдельно.
    """
    if type(ledger) is not RewardLedger or not isinstance(zone, tzinfo):
        raise ValueError("Нужен журнал и явный часовой пояс.")
    _instant(now_us)
    if now_us < ledger.last_us:
        raise ValueError("Часы переведены назад; журнал и выплаты не изменены.")
    if enabled is not None and type(enabled) is not bool:
        raise ValueError("Включение сада задаётся логическим значением.")
    windows = ledger.windows
    if enabled is True and not ledger.enabled:
        windows = (*windows, (now_us, None))
    elif enabled is False and ledger.enabled:
        windows = (*windows[:-1], (windows[-1][0], now_us))
    totals = {}
    for interval in intervals:
        if not isinstance(interval, (tuple, list)) or len(interval) != 2:
            raise ValueError("Нужна пара начала и окончания рабочего интервала.")
        start, end = interval
        _instant(start)
        _instant(end)
        if end < start:
            raise ValueError("Конец рабочего интервала раньше начала.")
        for left, right in windows:
            low, high = max(start, left), min(end, now_us if right is None else right, now_us)
            if low < high:
                for day, duration in focus_by_day(((low, high),), zone).items():
                    key = day.isoformat()
                    totals[key] = min(DAY_LIMIT, totals.get(key, 0) + duration)
    days = tuple(sorted(totals.items()))
    eligible = sum(totals.values())
    settling = ledger.enabled or (enabled is True)
    paid = max(0, (eligible - ledger.paid_us) // QUANTUM) * QUANTUM if settling else 0
    state = RewardLedger(now_us, windows, days, ledger.paid_us + paid)
    return TimeReward(state, paid)


def encode_ledger(ledger):
    return json.dumps(asdict(ledger), sort_keys=True)


def decode_ledger(payload):
    try:

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        if not isinstance(payload, str) or len(payload) > 4 * 1024 * 1024:
            raise ValueError
        data = json.loads(payload, object_pairs_hook=unique)
        if type(data) is not dict or set(data) != {"last_us", "windows", "days", "paid_us"}:
            raise ValueError
        if type(data["windows"]) is not list or type(data["days"]) is not list:
            raise ValueError
        return RewardLedger(
            data["last_us"],
            tuple(map(tuple, data["windows"])),
            tuple(map(tuple, data["days"])),
            data["paid_us"],
        )
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("Журнал оплаты повреждён. Автоматический сброс не выполняется.") from None
