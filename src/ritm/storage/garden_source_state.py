"""Кодек локального зеркала/курсора сада; данные задач остаются в профиле."""

import json
from uuid import UUID

from ritm.domain.garden import natural
from ritm.storage.codec import decode_task


def empty_source():
    return {"identity": None, "seq": 0, "event_id": None, "tasks": {}, "focus": {}}


def _focus(payload):
    if type(payload) is not dict or set(payload) != {"id", "session"}:
        raise ValueError("Неверный снимок фокуса источника.")
    UUID(payload["id"])
    session = payload["session"]
    if session is not None:
        if type(session) is not dict or set(session) != {"root", "intervals"}:
            raise ValueError("Неверные поля фокуса источника.")
        UUID(session["root"])
        if type(session["intervals"]) is not list:
            raise ValueError("Неверные интервалы фокуса источника.")
        for pair in session["intervals"]:
            if type(pair) is not list or len(pair) != 2:
                raise ValueError("Неверный рабочий интервал источника.")
            start, end = pair
            if type(start) is not int or type(end) is not int or end <= start:
                raise ValueError("Неверные границы фокуса источника.")
    return payload["id"], session


def decode_source(payload):
    try:
        state = json.loads(payload)
        if type(state) is not dict or set(state) != set(empty_source()):
            raise ValueError
        natural(state["seq"])
        if state["identity"] is None:
            if state != empty_source():
                raise ValueError
        else:
            UUID(state["identity"])
            UUID(state["event_id"])
            if not state["seq"]:
                raise ValueError
        if type(state["tasks"]) is not dict or type(state["focus"]) is not dict:
            raise ValueError
        for identifier, payload in state["tasks"].items():
            if str(decode_task(payload).id) != identifier:
                raise ValueError
        for identifier, session in state["focus"].items():
            if _focus({"id": identifier, "session": session})[1] is None:
                raise ValueError
        return state
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ValueError("Курсор/зеркало сада повреждены; автоматический сброс запрещён.") from None
