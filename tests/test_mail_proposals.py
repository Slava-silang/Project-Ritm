import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4
from zipfile import ZipFile

import pytest
from migration_support import drop_later_task_tables
from test_gmail import message

from ritm.application.mail_proposals import MailProposalService, mail_preview, parse_mail_proposal
from ritm.application.task_updates import parse_update
from ritm.application.tasks import TaskService
from ritm.domain.sources import SourceRef
from ritm.domain.tasks import Task
from ritm.integrations.gmail import parse_mail
from ritm.storage import backup
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore

NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)


def source(body="Первая версия", account="student@example.org"):
    return SourceRef.from_mail(parse_mail(account, message(value=body), "a1"))


def proposal(ref=None, *, identifier=None, before=None, link=None, task=None, event=None):
    data = (
        {"kind": "event", "event": event}
        if event is not None
        else {
            "kind": "task",
            "task": task
            if task is not None
            else {
                "title": "Работа из письма",
                "description": "Проверить условие",
                "difficulty": 6,
                "expected_minutes": 90,
                "subtasks": [{"title": "Решить", "items": ["Ответ"]}],
            },
        }
    )
    return parse_mail_proposal(
        identifier or uuid4(), json.dumps(data), ref or source(), before=before, link=link
    )


def event(title="Пара"):
    return {
        "title": title,
        "start": "2026-11-18T10:00:00+03:00",
        "end": "2026-11-18T11:30:00+03:00",
    }


def test_create_is_preview_only_then_atomic_and_repeat_after_reopen(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    draft = proposal()
    with TaskStore(path) as store:
        service = MailProposalService(store)
        assert not store.list_tasks() and not store.list_source_links()
        assert "Письмо Gmail" in mail_preview(draft) and draft.source.url in mail_preview(draft)
        assert service.source_state(draft.source) == ("new", None, None)
        assert service.apply(draft) == draft.after.id
        assert store.list_tasks() == (draft.after,)
        link = store.get_source_link(draft.source.key)
        assert link.target_id == draft.after.id and link.revision == 1
        assert service.source_state(draft.source)[0] == "unchanged"
        changed = source("Новый срок")
        assert service.source_state(changed)[0] == "changed"
    with TaskStore(path) as store:
        assert MailProposalService(store).apply(draft) == draft.after.id
        assert len(store.list_tasks()) == 1
        assert store._db.execute("SELECT count(*) FROM mail_proposals").fetchone()[0] == 1


def test_task_ids_stable_for_same_source_and_separate_accounts():
    assert proposal().after.id == proposal().after.id
    assert proposal(source("Исправленный текст")).after.id == proposal().after.id
    assert proposal(source(account="other@example.org")).after.id != proposal().after.id


def test_reprocessing_or_changed_kind_cannot_create_second_entity(tmp_path):
    first, other = proposal(), proposal()
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        service = MailProposalService(store)
        service.apply(first)
        with pytest.raises(ValueError, match="обработано"):
            service.apply(other)
        link = store.get_source_link(first.source.key)
        with pytest.raises(ValueError, match="уже обработано"):
            proposal(link=link, before=first.after)
        with pytest.raises(ValueError, match="вид"):
            proposal(source("Изменено"), link=link, before=first.after, event=event())
        assert len(store.list_tasks()) == 1 and not store.list_work_blocks()


def test_update_completed_task_preserves_root_children_ids_and_completion_date(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: NOW) as store:
        service = MailProposalService(store)
        first = proposal()
        service.apply(first)
        done = TaskService(store).set_completed(first.after.id, first.after.id, True)
        dates = store.list_completion_dates()
        link = store.get_source_link(first.source.key)
        patch = {
            "task_id": str(done.id),
            "changes": {"title": "Уточнённая работа", "expected_minutes": 120},
            "subtasks": [{"title": "Добавленное условие", "items": ["Проверить"]}],
        }
        draft = proposal(source("Новое условие"), before=done, link=link, task=patch)
        assert draft.after.completed
        assert all(c.completed and all(i.completed for i in c.items) for c in draft.after.subtasks)
        assert draft.after.subtasks[0].id == done.subtasks[0].id
        assert "Уточнённая работа" in mail_preview(draft)
        service.apply(draft)
        assert store.get_task(done.id) == draft.after
        assert store.list_completion_dates() == dates
        assert store.get_source_link(first.source.key).revision == 2
        # Обычный режим T36 по-прежнему не включает выполненные задачи.
        with pytest.raises(ValueError, match="активного"):
            parse_update(uuid4(), json.dumps(patch), (done,))
        patch["changes"]["completed"] = False
        with pytest.raises(ValueError, match="сохранить выполненную"):
            proposal(source("Другое изменение"), before=done, link=link, task=patch)


def test_existing_task_can_be_linked_without_creating_root(tmp_path):
    existing = Task(title="Уже существующая работа")
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.create_task(existing)
        draft = proposal(
            before=existing,
            task={"task_id": str(existing.id), "changes": {"description": "Условие из письма"}},
        )
        MailProposalService(store).apply(draft)
        assert len(store.list_tasks()) == 1 and store.get_task(existing.id) == draft.after
        assert store.get_source_link(draft.source.key).target_id == existing.id


def test_stale_task_and_source_revision_preserve_parallel_changes(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        service = MailProposalService(store)
        first = proposal()
        service.apply(first)
        link = store.get_source_link(first.source.key)
        data = {"task_id": str(first.after.id), "changes": {"title": "Предложенный новый срок"}}
        stale = proposal(source("Изменено"), before=first.after, link=link, task=data)
        edited = store.update_task(
            first.after.id, lambda t: replace(t, description="Ручная правка")
        )
        with pytest.raises(ValueError, match="изменилась"):
            service.apply(stale)
        assert (
            store.get_task(edited.id) == edited and store.get_source_link(first.source.key) == link
        )
        fresh = proposal(source("Изменено"), before=edited, link=link, task=data)
        service.apply(fresh)
        assert store.get_task(edited.id).description == "Ручная правка"
        with pytest.raises(ValueError, match="связь изменилась"):
            service.apply(stale)


def test_trash_restore_purge_and_receipt_do_not_recreate_root(tmp_path):
    first = proposal()
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        service = MailProposalService(store)
        service.apply(first)
        trash_id = TaskService(store).delete_node(first.after.id, first.after.id, NOW)
        assert service.source_state(first.source)[0] == "unavailable"
        assert service.apply(first) == first.after.id
        assert not store.list_tasks()
        with pytest.raises(ValueError, match="удалена"):
            proposal(source("Изменено"), link=store.get_source_link(first.source.key))
        TaskService(store).restore(trash_id)
        assert service.source_state(first.source)[0] == "unchanged"
        trash_id = TaskService(store).delete_node(first.after.id, first.after.id, NOW)
        store.purge_trash({trash_id})
        assert service.source_state(source("Изменено"))[0] == "unavailable"
        assert service.apply(first) == first.after.id and not store.list_tasks()


def test_event_create_update_stale_delete_and_no_task_conversion(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        service = MailProposalService(store)
        first = proposal(event=event())
        service.apply(first)
        link = store.get_source_link(first.source.key)
        update = proposal(
            source("Новая пара"), event=event("Уточнённая пара"), before=first.after, link=link
        )
        assert "→" in mail_preview(update)
        manual = replace(first.after, title="Ручное изменение")
        store.update_work_block(manual, expected=first.after)
        with pytest.raises(ValueError, match="изменилась"):
            service.apply(update)
        assert store.get_work_block(first.after.id) == manual
        assert store.get_source_link(first.source.key) == link
        store.update_work_block(first.after, expected=manual)
        service.apply(update)
        assert store.get_work_block(first.after.id) == update.after
        assert store.list_tasks() == ()
        assert service.apply(update) == update.after.id
        with pytest.raises(ValueError):
            proposal(
                source("Другое"),
                event=event(),
                before=replace(first.after, task_id=uuid4(), title=""),
            )
        with store._db:
            store._db.execute("DELETE FROM work_blocks WHERE id=?", (str(first.after.id),))
        assert service.source_state(source("Изменено"))[0] == "unavailable"
        assert service.apply(update) == update.after.id
        assert not store.list_work_blocks()


@pytest.mark.parametrize("failure", ["source_links", "mail_proposals"])
@pytest.mark.parametrize("kind", ["task", "event"])
def test_database_failure_rolls_back_entity_link_and_receipt(tmp_path, failure, kind):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store._db.execute(
            f"CREATE TRIGGER fail_mail BEFORE INSERT ON {failure} "
            "BEGIN SELECT RAISE(ABORT,'fake failure'); END"
        )
        draft = proposal(event=event()) if kind == "event" else proposal()
        with pytest.raises(sqlite3.IntegrityError):
            MailProposalService(store).apply(draft)
        assert (
            not store.list_tasks()
            and not store.list_work_blocks()
            and not store.list_source_links()
        )
        assert store._db.execute("SELECT count(*) FROM mail_proposals").fetchone()[0] == 0
        assert store._db.execute("SELECT count(*) FROM task_identities").fetchone()[0] == 0


def test_idempotent_concurrent_confirmations_and_different_payload(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path):
        pass
    draft = proposal()

    def apply(_):
        with TaskStore(path) as store:
            return MailProposalService(store).apply(draft)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(apply, range(2))) == [draft.after.id] * 2
    altered = proposal(identifier=draft.id, task={"title": "Другое содержание"})
    with TaskStore(path) as store:
        with pytest.raises(ValueError, match="другим содержимым"):
            MailProposalService(store).apply(altered)
        assert len(store.list_tasks()) == 1


@pytest.mark.parametrize(
    "payload",
    [
        '{"kind":"task","kind":"event"}',
        '{"kind":"task","task":{"title":"X","command":"send_email"}}',
        '{"kind":"event","event":{"title":"X","start":"2026-11-18T12:00:00","end":"2026-11-18T13:00:00"}}',
        '{"kind":"event","event":{"title":"X","start":"2026-11-18T12:00:00Z","end":"2026-11-18T11:00:00Z"}}',
        '{"kind":"event","event":{"title":"X","start":"2026-11-18T12:00:00Z","end":"2026-11-18T13:00:00Z","task_id":"other"}}',
        '{"kind":"task","task":{"title":"X"},"source_url":"https://evil.invalid"}',
        '{"kind":"delete","task":{}}',
    ],
)
def test_untrusted_model_payload_rejected(payload):
    with pytest.raises(ValueError):
        parse_mail_proposal(uuid4(), payload, source())


def test_forged_after_or_source_link_is_not_applied(tmp_path):
    draft = proposal()
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        with pytest.raises(ValueError, match="проверенному"):
            MailProposalService(store).apply(
                replace(draft, after=replace(draft.after, title="Unvalidated"))
            )
        assert not store.list_tasks()
    with pytest.raises(ValueError, match="ссылка"):
        replace(draft.source, url="https://evil.invalid")


def test_v11_migration_and_backups_preserve_ledger_and_old_archives(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    task = Task(title="Старая задача")
    with TaskStore(path) as store:
        store.create_task(task)
    with sqlite3.connect(path) as db:
        drop_later_task_tables(db, 11)
        db.execute("DROP TABLE mail_proposals")
        db.execute("DROP TABLE source_links")
        db.execute("DROP TABLE garden_events")
        db.execute("DROP TABLE garden_source")
        db.execute("PRAGMA user_version=11")
    # Настоящий архив v11: create_backup текущей версии мигрирует свой snapshot.
    database = path.read_bytes()
    old = tmp_path / "old-v11.zip"
    with ZipFile(old, "w") as archive:
        archive.writestr(backup.DATABASE, database)
        archive.writestr(
            backup.MANIFEST,
            json.dumps(
                {
                    "format": backup.FORMAT,
                    "kind": "manual",
                    "created": NOW.isoformat(),
                    "files": {backup.DATABASE: hashlib.sha256(database).hexdigest()},
                }
            ),
        )
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        first = proposal()
        MailProposalService(store).apply(first)
        link = store.get_source_link(first.source.key)
    current = backup.create_backup(tmp_path, tmp_path / "copies", NOW)
    preview = backup.inspect_backup(old)
    backup.restore_backup(tmp_path, old, preview["token"])
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task and not store.list_source_links()
    preview = backup.inspect_backup(current)
    backup.restore_backup(tmp_path, current, preview["token"])
    with TaskStore(path) as store:
        assert store.get_source_link(first.source.key) == link
        assert MailProposalService(store).apply(first) == first.after.id
        assert len(store.list_tasks()) == 2
