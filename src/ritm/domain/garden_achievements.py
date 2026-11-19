"""Постоянные и месячные достижения: точные счётчики, без Qt и внешних служб."""

import json
from dataclasses import asdict, dataclass
from datetime import date
from uuid import UUID

from ritm.domain.calendar import from_instant_us
from ritm.domain.garden import natural
from ritm.domain.garden_rewards import MINUTE, QUANTUM

HOUR = 60 * MINUTE
THRESHOLDS = ((5, 50), (15, 150), (30, 300))


def _claims(roots, paid_us):
    return tuple(
        sorted(
            [f"tasks:{n}" for n, _ in THRESHOLDS if roots >= n]
            + [f"focus:{n}" for n, _ in THRESHOLDS if paid_us >= n * HOUR]
        )
    )


@dataclass(frozen=True)
class MonthProgress:
    month: str
    roots: int = 0
    paid_us: int = 0
    claimed: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.month, str) or len(self.month) != 7:
            raise ValueError("Месяц достижения должен иметь формат ГГГГ-ММ.")
        if date.fromisoformat(self.month + "-01").strftime("%Y-%m") != self.month:
            raise ValueError("Некорректный месяц достижения.")
        natural(self.roots)
        natural(self.paid_us)
        if self.paid_us % QUANTUM or type(self.claimed) is not tuple:
            raise ValueError("Некорректное оплаченное время/пороги.")
        if self.claimed != _claims(self.roots, self.paid_us):
            raise ValueError("Полученные пороги не соответствуют счётчикам.")


@dataclass(frozen=True)
class AchievementState:
    welcomed: bool = False
    first_task: bool = False
    roots: tuple[str, ...] = ()
    paid_us: int = 0
    months: tuple[MonthProgress, ...] = ()
    themes: tuple[str, ...] = ("dark", "light")

    def __post_init__(self):
        if type(self.welcomed) is not bool or type(self.first_task) is not bool:
            raise ValueError("Признаки достижений должны быть логическими.")
        natural(self.paid_us)
        if type(self.roots) is not tuple or self.roots != tuple(sorted(set(self.roots))):
            raise ValueError("Корни достижений должны быть уникальными.")
        for root in self.roots:
            if not isinstance(root, str) or str(UUID(root)) != root:
                raise ValueError("Некорректный UUID достижения.")
        if type(self.months) is not tuple or any(type(m) is not MonthProgress for m in self.months):
            raise ValueError("Некорректный месячный журнал.")
        keys = [m.month for m in self.months]
        if keys != sorted(set(keys)):
            raise ValueError("Месяцы достижений повторяются или не упорядочены.")
        if sum(m.roots for m in self.months) != len(self.roots):
            raise ValueError("Количество уникальных корней не совпадает с месяцами.")
        if sum(m.paid_us for m in self.months) != self.paid_us or self.paid_us % QUANTUM:
            raise ValueError("Сумма оплаченных часов не совпадает с месяцами.")
        expected = {"light", "dark"}
        if self.paid_us >= 30 * HOUR:
            expected.add("space")
        if len(self.roots) >= 15:
            expected.add("jungle")
        if type(self.themes) is not tuple or self.themes != tuple(sorted(expected)):
            raise ValueError("Открытые темы не соответствуют постоянным достижениям.")
        if not self.welcomed and (self.first_task or self.roots or self.paid_us or self.months):
            raise ValueError("Прогресс без знакомства с садом недопустим.")


@dataclass(frozen=True)
class AchievementReward:
    state: AchievementState
    coins: int
    unlocked: tuple[str, ...]


def advance_achievements(
    state, *, enabled, paid_us, now_us, zone, before=None, after=None, restored=False
):
    if type(state) is not AchievementState or type(enabled) is not bool:
        raise ValueError("Нужно состояние достижений и логическое включение.")
    natural(paid_us)
    if paid_us % QUANTUM:
        raise ValueError("Учитываются только оплаченные пятиминутки.")
    if not enabled:
        if paid_us:
            raise ValueError("Выключенное достижение не может получить оплаченные часы.")
        return AchievementReward(state, 0, ())
    month = from_instant_us(now_us).astimezone(zone).strftime("%Y-%m")
    coins, unlocked = 0, []
    welcomed, first_task = state.welcomed, state.first_task
    if not welcomed:
        welcomed = True
        coins += 100
        unlocked.append("welcome")
    if after is not None and before is None and not restored and not first_task:
        first_task = True
        coins += 300
        unlocked.append("first_task")
    roots = set(state.roots)
    added = bool(
        after is not None
        and after.completed
        and not restored
        and (before is None or not before.completed)
        and str(after.id) not in roots
    )
    if added:
        roots.add(str(after.id))
    months = {m.month: m for m in state.months}
    if paid_us or added:
        previous = months.get(month, MonthProgress(month))
        root_count = previous.roots + int(added)
        focus = previous.paid_us + paid_us
        claimed = _claims(root_count, focus)
        for category in ("tasks", "focus"):
            for threshold, reward in THRESHOLDS:
                key = f"{category}:{threshold}"
                if key in claimed and key not in previous.claimed:
                    coins += reward
                    unlocked.append(f"{month}:{key}")
        months[month] = MonthProgress(month, root_count, focus, claimed)
    total = state.paid_us + paid_us
    themes = {"dark", "light"}
    if total >= 30 * HOUR:
        themes.add("space")
    if len(roots) >= 15:
        themes.add("jungle")
    unlocked.extend("theme:" + theme for theme in sorted(themes - set(state.themes)))
    updated = AchievementState(
        welcomed,
        first_task,
        tuple(sorted(roots)),
        total,
        tuple(months[k] for k in sorted(months)),
        tuple(sorted(themes)),
    )
    return AchievementReward(updated, coins, tuple(unlocked))


def encode_achievements(state):
    if type(state) is not AchievementState:
        raise ValueError("Нужно проверенное состояние достижений.")
    return json.dumps(asdict(state), sort_keys=True)


def decode_achievements(payload):
    try:

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        if not isinstance(payload, str) or len(payload.encode()) > 4 * 1024 * 1024:
            raise ValueError
        data = json.loads(payload, object_pairs_hook=unique)
        if type(data) is not dict or set(data) != {
            "welcomed",
            "first_task",
            "roots",
            "paid_us",
            "months",
            "themes",
        }:
            raise ValueError
        if any(type(data[k]) is not list for k in ("roots", "months", "themes")):
            raise ValueError
        months = []
        for m in data["months"]:
            if type(m) is not dict or set(m) != {"month", "roots", "paid_us", "claimed"}:
                raise ValueError
            if type(m["claimed"]) is not list:
                raise ValueError
            months.append(MonthProgress(**{**m, "claimed": tuple(m["claimed"])}))
        return AchievementState(
            **{
                **data,
                "roots": tuple(data["roots"]),
                "months": tuple(months),
                "themes": tuple(data["themes"]),
            }
        )
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise ValueError("Журнал достижений повреждён; автоматический сброс запрещён.") from None
