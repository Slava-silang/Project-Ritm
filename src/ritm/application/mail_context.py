"""Только выбранное письмо и раскрытый снимок задач, без истории/вложений/секретов."""

import json
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from ritm.application.drafts import _object, _text, _unique
from ritm.application.mail_proposals import parse_mail_proposal
from ritm.application.task_updates import active_context
from ritm.domain.calendar import WorkBlock


def mail_prompt(request):
    _object(
        request.mail,
        {"subject", "sender", "date", "body", "linked_event"},
        ("subject", "sender", "date", "body"),
    )
    for key in ("subject", "sender", "date", "body"):
        value = request.mail[key]
        if not isinstance(value, str) or len(value) > (60000 if key == "body" else 16000):
            raise ValueError("Недопустимый размер выбранного письма.")
    if request.files or request.materials or request.history or request.skill or request.planning:
        raise ValueError("Письмо нельзя смешивать с невыбранными материалами или историей.")
    if request.mail.get("linked_event") is not None:
        event = request.mail["linked_event"]
        _object(event, {"id", "title", "start", "end"}, ("id", "title", "start", "end"))
        WorkBlock(
            id=UUID(event["id"]),
            title=_text(event["title"]),
            starts_at=datetime.fromisoformat(event["start"]),
            ends_at=datetime.fromisoformat(event["end"]),
        )
    selected = None
    if request.selected:
        selected = active_context((replace(request.selected, completed=False),))[0]
        selected["completed"] = request.selected.completed
    prompt = {
        "operation": "mail",
        "prompt": request.prompt,
        "mail": request.mail,
        "active_tasks": active_context(request.tasks),
        "selected_task": selected,
        "current_time": request.current_time.isoformat() if request.current_time else None,
    }
    result = json.dumps(prompt, ensure_ascii=False)
    if len(result.encode()) > 512000:
        raise ValueError("Письмо и список задач слишком велики для одного запроса.")
    return result


def mail_result(identifier, payload, source, link, before, tasks):
    if not isinstance(payload, str) or len(payload.encode()) > 128000:
        raise ValueError("Предложение по письму слишком большое.")
    data = json.loads(payload, object_pairs_hook=_unique)
    if isinstance(data, dict) and "answer" in data:
        _object(data, {"answer"}, ("answer",))
        return _text(data["answer"])
    if before is None and isinstance(data, dict) and data.get("kind") == "task":
        task = data.get("task")
        if isinstance(task, dict) and "task_id" in task:
            identifier_value = UUID(task["task_id"])
            before = next((t for t in tasks if t.id == identifier_value and not t.completed), None)
            if before is None:
                raise ValueError("Задача для изменения отсутствует в активном снимке.")
    return parse_mail_proposal(identifier, payload, source, link=link, before=before)
