import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest

from ritm.application.drafts import parse_draft
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Subtask, Task
from ritm.storage import backup
from ritm.storage.sqlite import SCHEMA_VERSION, TaskStore


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{}",
        "[]",
        '{"title":"A","title":"B"}',
        '{"title":"A","id":"00000000-0000-0000-0000-000000000000"}',
        '{"title":"A","command":"delete_all"}',
        '{"title":"A","deadline":"2026-11-13T12:00"}',
        '{"title":"A","difficulty":true}',
        '{"title":"A","difficulty":11}',
        '{"title":"A","expected_minutes":-1}',
        '{"title":"A","expected_minutes":0.5}',
        '{"title":"A","subtasks":[{"title":"B","items":[{"title":"C"}]}]}',
        '{"title":"A","subtasks":[{"title":"B","deadline":"today"}]}',
        '{"title":"A","subtasks":[{"title":"B","id":"foreign"}]}',
        '{"title":"A","subtasks":[{"title":""}]}',
        '{"title":"A","subtasks":[{"title":"B"}],"weights":["99"]}',
        '{"title":"A","subtasks":[{"title":"B"}],"weights":[true]}',
        '{"title":"A","subtasks":[{"title":"B"}],"weights":["NaN"]}',
        '{"title":"A","subtasks":[{"title":"B"}],"weights":["1e-99999999"]}',
        json.dumps({"title": "A", "subtasks": [{"title": "B"}] * 101}),
        json.dumps({"title": "A", "description": "x" * 128001}),
    ],
)
def test_reject_invalid_model_responses(payload):
    with pytest.raises(ValueError):
        parse_draft(uuid4(), payload)


def test_stable_identity_weights_and_editable_draft():
    identifier = uuid4()
    draft = parse_draft(
        identifier,
        json.dumps(
            {
                "title": "Лабораторная",
                "difficulty": 7,
                "expected_minutes": 90,
                "deadline": "2026-11-28T15:00:00+03:00",
                "subtasks": [{"title": "A", "items": ["a", "b"]}, {"title": "B"}],
            }
        ),
    )
    assert draft.task.subtasks[0].weight == Decimal("50")
    data = json.loads(draft.payload)
    data["title"] = "После проверки"
    data["weights"] = ["30", "70"]
    edited = parse_draft(identifier, json.dumps(data))
    assert edited.task.id == draft.task.id
    assert edited.task.subtasks[0].items == draft.task.subtasks[0].items
    assert edited.task.subtasks[0].weight == 30
    assert edited.digest != draft.digest
    assert parse_draft(identifier, edited.payload) == edited


def test_split_preserves_existing_tree_fields_and_rejects_foreign_changes():
    baseline = Task(
        title="Работа", completed=True, subtasks=(Subtask(title="Готово", completed=True),)
    )
    with pytest.raises(ValueError):
        parse_draft(uuid4(), '{"subtasks":[],"deadline":null}', baseline)
    draft = parse_draft(uuid4(), '{"subtasks":[{"title":"Новый"}]}', baseline)
    assert draft.task.subtasks[0].id == baseline.subtasks[0].id
    assert draft.task.subtasks[0].completed
    assert not draft.task.completed
    assert replace(draft.task, subtasks=baseline.subtasks, completed=True) == baseline


def test_atomic_apply_rollback_stale_detection_and_receipt_restart(tmp_path, monkeypatch):
    path = tmp_path / "tasks.sqlite3"
    draft = parse_draft(uuid4(), '{"title":"Один раз","subtasks":[{"title":"Шаг"}]}')
    with TaskStore(path) as store:
        original = store._write_task

        def fail(task):
            original(task)
            raise sqlite3.OperationalError("write interrupted")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_write_task", fail)
            with pytest.raises(sqlite3.Error):
                TaskService(store).apply_proposal(draft)
        assert not store.list_tasks()
        assert store._db.execute("SELECT count(*) FROM applied_proposals").fetchone()[0] == 0
        assert TaskService(store).apply_proposal(draft) == draft.task.id
    with TaskStore(path) as store:
        service = TaskService(store)
        assert service.apply_proposal(draft) == draft.task.id
        assert len(store.list_tasks()) == 1
        changed = parse_draft(draft.id, '{"title":"Другой результат"}')
        with pytest.raises(ValueError, match="уже применён"):
            service.apply_proposal(changed)
        split = parse_draft(uuid4(), '{"subtasks":[{"title":"Ещё"}]}', draft.task)
        service.rename_node(draft.task.id, draft.task.id, "Изменено вручную")
        with pytest.raises(ValueError, match="изменилась"):
            service.apply_proposal(split)
        assert store.get_task(draft.task.id).title == "Изменено вручную"
        entry = service.delete_node(draft.task.id, draft.task.id)
        store.purge_trash({UUID(entry)})
        assert service.apply_proposal(draft) == draft.task.id
        assert not store.list_tasks()  # повтор старого подтверждения не воскрешает удалённое


def test_competing_confirmations_insert_one_task(tmp_path):
    draft = parse_draft(uuid4(), '{"title":"Однократно"}')
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path):
        pass

    def apply(_):
        with TaskStore(path) as store:
            return TaskService(store).apply_proposal(draft)

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(apply, range(2))) == [draft.task.id, draft.task.id]
    with TaskStore(path) as store:
        assert store.list_tasks() == (draft.task,)


def test_v9_profile_and_archive_migrate_and_receipts_survive_new_backup(tmp_path):
    path = tmp_path / backup.DATABASE
    with TaskStore(path) as store:
        store.create_task(Task(title="Из версии 9"))
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE applied_proposals")
        db.execute("PRAGMA user_version=9")
    database = path.read_bytes()
    archive = tmp_path / "old.zip"
    with ZipFile(archive, "w") as dest:
        dest.writestr(backup.DATABASE, database)
        dest.writestr(
            backup.MANIFEST,
            json.dumps(
                {
                    "format": backup.FORMAT,
                    "kind": "manual",
                    "created": datetime.now(UTC).isoformat(),
                    "files": {backup.DATABASE: hashlib.sha256(database).hexdigest()},
                }
            ),
        )
    preview = backup.inspect_backup(archive)
    assert path.read_bytes() == database
    backup.restore_backup(tmp_path, archive, preview["token"])
    draft = parse_draft(uuid4(), '{"title":"После обновления"}')
    with TaskStore(path) as store:
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        TaskService(store).apply_proposal(draft)
    current = backup.create_backup(tmp_path, tmp_path / "copies", datetime.now(UTC))
    token = backup.inspect_backup(current)["token"]
    backup.restore_backup(tmp_path, current, token)
    with TaskStore(path) as store:
        TaskService(store).apply_proposal(draft)
        assert len(store.list_tasks()) == 2


def test_assistant_gui_and_cancelled_late_response(tmp_path):
    environment = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ui_assistant_scenario.py")), str(tmp_path)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
        assert warning not in result.stderr, result.stderr


def test_migration_and_receipt_failure_leave_existing_data_intact(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    draft = parse_draft(uuid4(), '{"title":"Без частичной записи"}')
    with TaskStore(path) as store:
        store._db.execute(
            "CREATE TRIGGER reject_receipt BEFORE INSERT ON applied_proposals "
            "BEGIN SELECT RAISE(ABORT,'receipt failure'); END"
        )
        store._db.commit()
        with pytest.raises(sqlite3.Error):
            TaskService(store).apply_proposal(draft)
        assert not store.list_tasks()
        assert store._db.execute("SELECT count(*) FROM task_identities").fetchone()[0] == 0
    # Ошибка DDL миграции откатывает смену версии и сохраняет исходные байты.
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=9")
    before = path.read_bytes()
    with pytest.raises(sqlite3.Error):
        TaskStore(path)
    assert path.read_bytes() == before
