"""Последовательная доставка событий задач в отдельную БД сада, без Qt."""

import json
from fractions import Fraction
from functools import partial
from uuid import UUID
from zoneinfo import ZoneInfo

from ritm.domain.garden_payments import EventReward, allocate_time, claim_completion
from ritm.domain.garden_rewards import reconcile_time
from ritm.storage.codec import decode_task
from ritm.storage.garden_source_state import _focus


def apply_event(state, identity, event, ledger, zone, *, completion_awards=True):
    UUID(identity)
    if state["identity"] not in (None, identity):
        raise ValueError("Источник сада не совпадает с базой задач.")
    if type(event) is not dict or set(event) != {"seq", "id", "happened_us", "kind", "payload"}:
        raise ValueError("Неизвестный формат события сада.")
    UUID(event["id"])
    if type(event["seq"]) is not int or event["seq"] != state["seq"] + 1:
        raise ValueError("Нарушена последовательность событий сада.")
    state = json.loads(json.dumps(state))
    if state["payments"] is None:
        raise ValueError("Сначала восстановите распределение прежней оплаты из журнала источника.")
    kind, payload = event["kind"], event["payload"]
    enabled = None
    if kind == "begin":
        if ledger.windows:
            raise ValueError(
                "Журнал времени уже использовался без источника; требуется явная миграция."
            )
        if state["seq"] or set(payload) != {"tasks", "focus"}:
            raise ValueError("Повторный или неверный baseline источника.")
        tasks = [decode_task(p) for p in payload["tasks"]]
        if len({t.id for t in tasks}) != len(tasks):
            raise ValueError("Повторные задачи в baseline.")
        state["tasks"] = {str(t.id): p for t, p in zip(tasks, payload["tasks"], strict=True)}
        for item in payload["focus"]:
            identifier, session = _focus(item)
            if identifier in state["focus"] or session is None:
                raise ValueError("Повторный или удалённый фокус в baseline.")
            state["focus"][identifier] = session
        enabled = True
    elif not state["seq"]:
        raise ValueError("Сначала требуется baseline источника.")
    elif kind == "task":
        if (
            set(payload) != {"id", "before", "after", "restore"}
            or type(payload["restore"]) is not bool
        ):
            raise ValueError("Неверное изменение задачи источника.")
        identifier = str(UUID(payload["id"]))
        if state["tasks"].get(identifier) != payload["before"]:
            raise ValueError("Исходная задача не совпадает с зеркалом сада.")
        if payload["after"] is None:
            state["tasks"].pop(identifier, None)
        else:
            task = decode_task(payload["after"])
            if str(task.id) != identifier:
                raise ValueError("Изменился ID задачи источника.")
            state["tasks"][identifier] = payload["after"]
    elif kind == "focus":
        identifier, session = _focus(payload)
        if session is None:
            state["focus"].pop(identifier, None)
        else:
            state["focus"][identifier] = session
    elif kind == "enabled":
        if set(payload) != {"enabled"} or type(payload["enabled"]) is not bool:
            raise ValueError("Неверное включение сада.")
        enabled = payload["enabled"]
    else:
        raise ValueError("Неизвестный вид события сада.")
    intervals = [pair for session in state["focus"].values() for pair in session["intervals"]]
    # Pre-epoch history cannot intersect a garden window opened after epoch.
    intervals = [(max(0, start), end) for start, end in intervals if end > 0]
    reward = reconcile_time(
        ledger, intervals, now_us=event["happened_us"], zone=ZoneInfo(zone), enabled=enabled
    )
    allocate_time(state["payments"], state["focus"], reward, ZoneInfo(zone))
    bonus = Fraction()
    if kind == "task" and completion_awards:
        bonus = claim_completion(
            state["payments"],
            decode_task(payload["before"]) if payload["before"] else None,
            decode_task(payload["after"]) if payload["after"] else None,
            enabled=reward.ledger.enabled,
            restored=payload["restore"],
        )
    state.update(identity=identity, seq=event["seq"], event_id=event["id"])
    return state, EventReward(reward.ledger, reward.paid_us, bonus)


class GardenEventService:
    """Оба соединения принадлежат вызывающему потоку; обычный UI пока не подключён."""

    def __init__(self, source, garden, *, zone="UTC"):
        ZoneInfo(zone)
        self.source, self.garden, self.zone = source, garden, zone

    def begin(self, now):
        self.source.begin_garden_source(now)
        return self.drain()

    def drain(self):
        cursor = self.garden.source_snapshot()
        if cursor["payments"] is None:
            self._rebuild_payments(cursor)
        count = 0
        while True:
            cursor = self.garden.source_snapshot()
            identity, events = self.source.garden_events_after(
                identity=cursor["identity"], sequence=cursor["seq"], event_id=cursor["event_id"]
            )
            if not events:
                return count
            for event in events:
                self.garden.apply_source_event(
                    identity,
                    event,
                    zone=self.zone,
                    expected_revision=self.garden.snapshot().revision,
                    transition=apply_event,
                )
                count += 1

    def _rebuild_payments(self, cursor):
        events, sequence, event_id = [], 0, None
        while sequence < cursor["seq"]:
            _, batch = self.source.garden_events_after(
                identity=cursor["identity"], sequence=sequence, event_id=event_id
            )
            accepted = [e for e in batch if e["seq"] <= cursor["seq"]]
            if not accepted:
                raise ValueError("Недостаточно истории для восстановления распределения оплаты.")
            events.extend(accepted)
            sequence, event_id = accepted[-1]["seq"], accepted[-1]["id"]
        self.source.garden_events_after(
            identity=cursor["identity"], sequence=cursor["seq"], event_id=cursor["event_id"]
        )
        self.garden.rebuild_source_payments(
            events,
            zone=self.zone,
            expected_revision=self.garden.snapshot().revision,
            transition=partial(apply_event, completion_awards=False),
        )
