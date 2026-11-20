"""Проверяемые предложения из одного письма: сохранение только явным apply."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from ritm.application.drafts import MAX_BYTES, Draft, _object, _text, _unique, parse_draft
from ritm.application.task_updates import parse_update, update_preview
from ritm.domain.calendar import WorkBlock
from ritm.domain.sources import SourceLink, SourceRef
from ritm.domain.tasks import Task
from ritm.storage.codec import encode_task


def entity_json(entity):
    if entity is None:
        return None
    if isinstance(entity, Task):
        return encode_task(entity)
    return {
        "id": str(entity.id),
        "title": entity.title,
        "starts_at": entity.starts_at.isoformat(),
        "ends_at": entity.ends_at.isoformat(),
        "task_id": str(entity.task_id) if entity.task_id else None,
    }


@dataclass(frozen=True)
class MailProposal:
    id: UUID
    source: SourceRef
    link: SourceLink | None
    before: Task | WorkBlock | None
    after: Task | WorkBlock
    payload: str

    @property
    def kind(self):
        return "task" if isinstance(self.after, Task) else "event"

    @property
    def digest(self):
        value = {
            "key": self.source.key,
            "fingerprint": self.source.fingerprint,
            "url": self.source.url,
            "link": {
                "fingerprint": self.link.source.fingerprint,
                "url": self.link.source.url,
                "kind": self.link.kind,
                "target_id": str(self.link.target_id),
                "revision": self.link.revision,
            }
            if self.link
            else None,
            "before": entity_json(self.before),
            "after": entity_json(self.after),
        }
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()


def parse_mail_proposal(
    identifier: UUID,
    payload: str,
    source: SourceRef,
    *,
    link: SourceLink | None = None,
    before: Task | WorkBlock | None = None,
) -> MailProposal:
    if not isinstance(identifier, UUID) or not isinstance(source, SourceRef):
        raise ValueError("Некорректный запрос обработки письма.")
    if link is not None:
        if link.source.key != source.key or before is None or before.id != link.target_id:
            raise ValueError("Связанная запись удалена или не соответствует письму.")
        if link.source.fingerprint == source.fingerprint:
            raise ValueError("Это содержание письма уже обработано.")
    if before is not None and not isinstance(before, (Task, WorkBlock)):
        raise ValueError("Некорректный снимок связанной записи.")
    if not isinstance(payload, str) or len(payload.encode()) > MAX_BYTES:
        raise ValueError("Предложение по письму слишком большое.")
    try:
        data = json.loads(payload, object_pairs_hook=_unique)
        _object(data, {"kind", "task", "event"}, ("kind",))
        kind = data["kind"]
        if kind not in ("task", "event"):
            raise ValueError("Из письма можно предложить задачу или событие.")
        _object(data, {"kind", kind}, ("kind", kind))
        if link is not None and link.kind != kind:
            raise ValueError("Обновление письма не должно менять вид связанной записи.")
        namespace = uuid5(NAMESPACE_URL, source.key)
        if kind == "task":
            if before is not None and not isinstance(before, Task):
                raise ValueError("Письмо связано с событием, а не задачей.")
            if before is None:
                task_draft = parse_draft(namespace, json.dumps(data["task"]))
            else:
                task_draft = parse_update(
                    uuid5(namespace, source.fingerprint),
                    json.dumps(data["task"]),
                    (before,),
                    preserve_completed=True,
                )
                if not isinstance(task_draft, Draft):
                    raise ValueError("Ответ без изменений не является предложением записи.")
            after = task_draft.task
            data["task"] = json.loads(task_draft.payload)
        else:
            if before is not None and (
                not isinstance(before, WorkBlock) or before.task_id is not None
            ):
                raise ValueError("Письмо должно относиться к самостоятельному событию.")
            event = data["event"]
            _object(event, {"title", "start", "end"}, ("title", "start", "end"))
            after = WorkBlock(
                id=before.id if before else uuid5(namespace, "event"),
                title=_text(event["title"]),
                starts_at=datetime.fromisoformat(event["start"]),
                ends_at=datetime.fromisoformat(event["end"]),
            )
            if after == before:
                raise ValueError("В предложении события нет изменений.")
            data["event"] = {
                "title": after.title,
                "start": after.starts_at.isoformat(),
                "end": after.ends_at.isoformat(),
            }
        return MailProposal(
            identifier, source, link, before, after, json.dumps(data, ensure_ascii=False)
        )
    except (TypeError, KeyError, OverflowError, RecursionError) as error:
        raise ValueError("Некорректное предложение по письму.") from error


def mail_preview(draft):
    lines = ["Письмо Gmail → " + ("задача" if draft.kind == "task" else "событие")]
    if isinstance(draft.after, Task):
        if draft.before is not None:
            lines.append(update_preview(Draft(draft.id, draft.before, draft.after, "", "update")))
        else:
            task = draft.after
            lines.extend(
                (
                    "Название: " + task.title,
                    "Описание: " + task.description,
                    "Сложность: "
                    + (str(task.difficulty) if task.difficulty is not None else "не указана"),
                    "Ожидаемое время: "
                    + (
                        f"{task.expected_duration.total_seconds() / 60:g} мин"
                        if task.expected_duration
                        else "не указано"
                    ),
                    "Срок: "
                    + (
                        task.deadline.astimezone().strftime("%d.%m.%Y %H:%M")
                        if task.deadline
                        else "не указан"
                    ),
                )
            )
            for child in task.subtasks:
                lines.append(f"• {child.title} ({child.weight}%)")
                lines.extend("  · " + item.title for item in child.items)
    else:

        def show(event):
            return (
                event.title
                + ": "
                + event.starts_at.astimezone().strftime("%d.%m.%Y %H:%M")
                + " — "
                + event.ends_at.astimezone().strftime("%d.%m.%Y %H:%M")
            )

        if draft.before:
            lines.append(show(draft.before) + " → " + show(draft.after))
        else:
            lines.append(show(draft.after))
    lines.extend(("Источник: " + draft.source.url, "Запись только после подтверждения."))
    return "\n".join(lines)


class MailProposalService:
    def __init__(self, store):
        self.store = store

    def source_state(self, source):
        link = self.store.get_source_link(source.key)
        if link is None:
            return "new", None, None
        target = (
            self.store.get_task(link.target_id)
            if link.kind == "task"
            else self.store.get_work_block(link.target_id)
        )
        if target is None:
            return "unavailable", link, None
        return (
            ("unchanged" if link.source.fingerprint == source.fingerprint else "changed"),
            link,
            target,
        )

    def apply(self, draft, *, expected_overlaps=None):
        checked = parse_mail_proposal(
            draft.id, draft.payload, draft.source, link=draft.link, before=draft.before
        )
        if checked != draft:
            raise ValueError("Содержимое предложения по письму не соответствует проверенному.")
        return self.store.apply_mail_proposal(checked, expected_overlaps=expected_overlaps)
