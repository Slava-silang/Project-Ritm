"""Принадлежность основной оплаты корням и однократные надбавки завершения."""

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from uuid import UUID

from ritm.domain.focus import focus_by_day
from ritm.domain.garden import coins, completion_bonus, natural
from ritm.domain.garden_achievements import AchievementReward
from ritm.domain.garden_rewards import DAY_LIMIT, QUANTUM, RewardLedger
from ritm.domain.weights import initialize_weights


def empty_payments():
    return {"observed": {}, "pending": [], "paid": {}, "claims": {}}


def validate_payments(state):
    if type(state) is not dict or set(state) != set(empty_payments()):
        raise ValueError("Неизвестный формат распределения оплаты.")
    for key in ("observed", "paid", "claims"):
        if type(state[key]) is not dict:
            raise ValueError("Некорректный журнал оплаты корней.")
        for root in state[key]:
            UUID(root)
    for duration in state["observed"].values():
        natural(duration, positive=True)
    if type(state["pending"]) is not list:
        raise ValueError("Некорректная очередь неоплаченного времени.")
    for pair in state["pending"]:
        if type(pair) is not list or len(pair) != 2:
            raise ValueError("Некорректная часть неоплаченного времени.")
        UUID(pair[0])
        natural(pair[1], positive=True)
    for value in state["paid"].values():
        if not isinstance(value, str) or coins(Fraction(value)) <= 0:
            raise ValueError("Некорректная базовая оплата корня.")
    for root, claims in state["claims"].items():
        if type(claims) is not dict:
            raise ValueError("Некорректный журнал надбавок.")
        units = 0
        for node, record in claims.items():
            UUID(node)
            if type(record) is not dict or set(record) != {
                "weight",
                "difficulty",
                "base",
                "amount",
            }:
                raise ValueError("Некорректная запись надбавки.")
            if any(not isinstance(record[k], str) for k in ("weight", "base", "amount")):
                raise ValueError("Надбавка должна хранить точные суммы.")
            weight = Decimal(record["weight"])
            expected = completion_bonus(Fraction(record["base"]), record["difficulty"], weight)
            if Fraction(record["base"]) > Fraction(state["paid"].get(root, "0")):
                raise ValueError("Надбавка ссылается на неоплаченную базу.")
            if expected != coins(Fraction(record["amount"])):
                raise ValueError("Сумма надбавки не соответствует сохранённым условиям.")
            units += int(Fraction(weight) * 100)
        if units > 10000:
            raise ValueError("Оплачено более 100% корня.")


def validate_payment_totals(state, ledger):
    if (
        sum(state["observed"].values()) != sum(duration for _, duration in ledger.days)
        or sum(part[1] for part in state["pending"]) != ledger.remainder_us
        or sum(Fraction(v) for v in state["paid"].values())
        != Fraction(ledger.paid_us * 10, QUANTUM)
    ):
        raise ValueError("Распределение оплаты повреждено: суммы не совпадают с журналом времени.")


def eligible_roots(focus, ledger, zone):
    """Те же окна/дневные пределы T39c1, с сохранением принадлежности частей."""
    parts = []
    for session_id, session in focus.items():
        root = session["root"]
        for start, end in session["intervals"]:
            for left, right in ledger.windows:
                low = max(0, start, left)
                high = min(end, ledger.last_us if right is None else right, ledger.last_us)
                if low >= high:
                    continue
                cursor = low
                for day, duration in focus_by_day(((low, high),), zone).items():
                    parts.append((day, cursor, cursor + duration, root, session_id))
                    cursor += duration
    totals, days, order = {}, {}, []
    for day, start, end, root, _session in sorted(parts):
        accepted = min(end - start, DAY_LIMIT - days.get(day, 0))
        if accepted:
            days[day] = days.get(day, 0) + accepted
            totals[root] = totals.get(root, 0) + accepted
            if root not in order:
                order.append(root)
    if sum(totals.values()) != sum(duration for _, duration in ledger.days):
        raise ValueError("Распределение по корням не совпадает с журналом времени.")
    return totals, order


def _consume(queue, duration, paid=None):
    while duration:
        if not queue:
            raise ValueError("Утеряна принадлежность оплачиваемого времени.")
        root, available = queue[0]
        take = min(available, duration)
        if paid is not None:
            paid[root] = str(Fraction(paid.get(root, "0")) + Fraction(take * 10, QUANTUM))
        duration -= take
        if take == available:
            queue.pop(0)
        else:
            queue[0][1] -= take


def allocate_time(state, focus, reward, zone):
    totals, order = eligible_roots(focus, reward.ledger, zone)
    previous, queue = state["observed"], state["pending"]
    # A correction first cancels that root's unpaid remainder, never paid money.
    for root, duration in previous.items():
        removed = max(0, duration - totals.get(root, 0))
        for pair in queue:
            if pair[0] == root:
                take = min(removed, pair[1])
                pair[1] -= take
                removed -= take
        queue[:] = [pair for pair in queue if pair[1]]
    for root in order:
        added = totals[root] - previous.get(root, 0)
        if added > 0:
            queue.append([root, added])
    remaining = reward.ledger.remainder_us
    debt = sum(part[1] for part in queue) - reward.paid_us - remaining
    if debt < 0:
        raise ValueError("Очередь не содержит все неоплаченные части.")
    _consume(queue, debt)
    _consume(queue, reward.paid_us, state["paid"])
    state["observed"] = totals
    if sum(Fraction(v) for v in state["paid"].values()) != Fraction(
        reward.ledger.paid_us * 10, QUANTUM
    ):
        raise ValueError("Базовая оплата корней не совпадает с уже выданной суммой.")


def claim_completion(state, before, after, *, enabled, restored):
    if after is None or not enabled or restored:
        return Fraction()
    task = initialize_weights(after)
    root = str(task.id)
    claims = state["claims"].setdefault(root, {})
    old = {str(c.id): c.completed for c in before.subtasks} if before else {}
    if task.subtasks:
        candidates = [(str(c.id), c.completed, c.weight) for c in task.subtasks]
    else:
        old[root] = before.completed if before else False
        candidates = [(root, task.completed, Decimal(100))]
    remaining = 10000 - sum(int(Fraction(c["weight"]) * 100) for c in claims.values())
    base = Fraction(state["paid"].get(root, "0"))
    difficulty = 5 if task.difficulty is None else task.difficulty
    amount = Fraction()
    for node, completed, weight in candidates:
        if not completed or old.get(node, False) or node in claims:
            continue
        units = min(int(Fraction(weight) * 100), remaining)
        saved_weight = f"{units // 100}.{units % 100:02d}"
        bonus = completion_bonus(base, difficulty, Decimal(saved_weight))
        claims[node] = {
            "weight": saved_weight,
            "difficulty": difficulty,
            "base": str(base),
            "amount": str(bonus),
        }
        remaining -= units
        amount += bonus
    return amount


@dataclass(frozen=True)
class EventReward:
    ledger: RewardLedger
    paid_us: int
    bonus: Fraction
    achievements: AchievementReward | None = None

    @property
    def coins(self):
        extra = self.achievements.coins if self.achievements is not None else 0
        return Fraction(self.paid_us * 10, QUANTUM) + self.bonus + extra
