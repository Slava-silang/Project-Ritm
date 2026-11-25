import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from test_mail_proposals import event, proposal, source

from ritm.application.mail_context import mail_prompt, mail_result
from ritm.application.mail_proposals import MailProposalService
from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Task
from ritm.integrations.assistant import ChatTurn, Request
from ritm.integrations.assistant_prompt import MAIL_INSTRUCTIONS
from ritm.integrations.chatgpt import ChatGPTAdapter
from ritm.storage.sqlite import TaskStore


def request(**extra):
    return Request(
        uuid4(),
        "Составь задачу",
        "mail",
        mail={
            "subject": "Условие",
            "sender": "teacher@example.org",
            "date": "Monday",
            "body": "SELECTED_MAIL_BODY",
        },
        current_time=datetime(2026, 11, 18, 12, tzinfo=UTC),
        **extra,
    )


def test_mail_context_only_selected_data_and_public_tasks():
    task = Task(title="Работа", description="Описание", difficulty=7)
    done = Task(title="PRIVATE_DONE", completed=True)
    selected = replace(done, title="Связанная выполненная")
    value = mail_prompt(request(tasks=(task, done), selected=selected))
    data = json.loads(value)
    assert len(data["active_tasks"]) == 1
    assert data["selected_task"]["completed"]
    assert "PRIVATE_DONE" not in value and "difficulty" not in value and "category_id" not in value
    assert "SELECTED_MAIL_BODY" in value and data["current_time"].endswith("+00:00")
    assert "history" not in data and "files" not in data


def test_mail_context_bounds_and_mixing_rejected():
    original = request()
    with pytest.raises(ValueError, match="историей"):
        mail_prompt(replace(original, history=(ChatTurn("user", "PRIVATE_CHAT"),)))
    with pytest.raises(ValueError):
        mail_prompt(replace(original, mail={**original.mail, "body": "x" * 60001}))
    with pytest.raises(ValueError):
        mail_prompt(replace(original, mail={**original.mail, "access_token": "PRIVATE_TOKEN"}))
    with pytest.raises(ValueError):
        mail_prompt(
            replace(
                original,
                mail={**original.mail, "linked_event": {"id": "unknown", "command": "send"}},
            )
        )


def test_mail_response_answer_existing_task_and_foreign_id():
    task = Task(title="Уже существующая")
    payload = json.dumps(
        {"kind": "task", "task": {"task_id": str(task.id), "changes": {"title": "Уточнение"}}}
    )
    draft = mail_result(uuid4(), payload, source(), None, None, (task,))
    assert draft.before == task and draft.after.id == task.id
    with pytest.raises(ValueError):
        mail_result(uuid4(), payload, source(), None, None, ())
    assert (
        mail_result(uuid4(), '{"answer":"Уточните время"}', source(), None, None, ())
        == "Уточните время"
    )
    with pytest.raises(ValueError):
        mail_result(uuid4(), '{"answer":"Уточните","kind":"task"}', source(), None, None, ())
    assert mail_result(uuid4(), proposal().payload, source(), None, None, ()).before is None


def test_chatgpt_mail_uses_isolated_user_data_and_fixed_instructions():
    class Session:
        model = "gpt-6.1-sol"

        def infer(self, prompt, instructions, cancelled):
            self.prompt, self.instructions = prompt, instructions
            return '{"answer":"Уточните"}'

    session = Session()
    adapter = ChatGPTAdapter(session)
    assert "mail" in adapter.connection.operations
    adapter.generate(request(), Event())
    assert json.loads(session.prompt)["mail"]["body"] == "SELECTED_MAIL_BODY"
    assert (
        session.instructions == MAIL_INSTRUCTIONS
        and "SELECTED_MAIL_BODY" not in session.instructions
    )


def test_event_calendar_snapshot_rechecked_in_apply_transaction(tmp_path):
    draft = proposal(event=event())
    busy = WorkBlock(
        title="Поздняя запись", starts_at=draft.after.starts_at, ends_at=draft.after.ends_at
    )
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.create_work_block(busy)
        with pytest.raises(ValueError, match="Календарь изменился"):
            MailProposalService(store).apply(draft, expected_overlaps=())
        assert not store.list_source_links() and len(store.list_work_blocks()) == 1
        assert MailProposalService(store).apply(draft, expected_overlaps=(busy,)) == draft.after.id


def test_mail_assistant_gui_and_restart(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{key}_HOME": str(tmp_path / key.lower()) for key in ("DATA", "CACHE", "CONFIG")},
    }
    for mode in ("run", "reopen"):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("ui_mail_assistant_scenario.py")),
                str(tmp_path),
                mode,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
            assert warning not in result.stderr, result.stderr
