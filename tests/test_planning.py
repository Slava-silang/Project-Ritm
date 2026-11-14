import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from ritm.application.planning import (
    apply_schedule,
    parse_schedule,
    planning_context,
    validate_hours,
)
from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Task
from ritm.integrations.assistant import MockAdapter, Request
from ritm.integrations.chatgpt import SCHEDULE_INSTRUCTIONS, ChatGPTAdapter
from ritm.storage import working_hours
from ritm.storage.sqlite import TaskStore
from ritm.storage.working_hours import load_hours, save_hours

START = datetime(2030, 1, 7, 9, tzinfo=UTC)  # Monday
HOURS = ((540, 1080),) * 7


@pytest.fixture
def setup(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3", clock=lambda: START) as store:
        task = Task(
            title="Лабораторная",
            expected_duration=timedelta(hours=4),
            deadline=START + timedelta(hours=6),
        )
        store.create_task(task)
        yield store, task


def context(store, task, **kwargs):
    return planning_context(
        store, task.id, START, START + timedelta(days=1), HOURS, zone=UTC, now=START, **kwargs
    )


def proposal(ctx):
    identifier = uuid4()
    payload = MockAdapter().generate(
        Request(identifier, "Составь расписание", "schedule", ctx.task, planning=ctx.data()),
        Event(),
    )
    return parse_schedule(identifier, payload, ctx)


def test_free_time_union_deadline_and_remaining(setup):
    store, task = setup
    store.create_work_block(
        WorkBlock(title="Пара", starts_at=START, ends_at=START + timedelta(hours=1))
    )
    store.create_work_block(
        WorkBlock(
            title="Другая пара",
            starts_at=START + timedelta(minutes=30),
            ends_at=START + timedelta(hours=2),
        )
    )
    own = WorkBlock(
        task_id=task.id, starts_at=START + timedelta(hours=3), ends_at=START + timedelta(hours=4)
    )
    store.create_work_block(own)
    ctx = context(store, task)
    assert ctx.remaining == timedelta(hours=3)
    assert ctx.free == (
        (START + timedelta(hours=2), START + timedelta(hours=3)),
        (START + timedelta(hours=4), START + timedelta(hours=6)),
    )
    draft = proposal(ctx)
    assert not draft.missing and len(draft.blocks) == 2
    assert not store.get_task(task.id).completed


def test_insufficient_time_empty_days_and_already_planned(setup):
    store, task = setup
    store.create_work_block(
        WorkBlock(title="Занято", starts_at=START, ends_at=START + timedelta(hours=5))
    )
    draft = proposal(context(store, task))
    assert draft.missing == timedelta(hours=3)
    assert sum((b.duration for b in draft.blocks), timedelta()) == timedelta(hours=1)
    ctx = planning_context(
        store, task.id, START, START + timedelta(days=1), (None,) * 7, zone=UTC, now=START
    )
    assert proposal(ctx).blocks == () and proposal(ctx).missing == timedelta(hours=4)
    store.create_work_block(
        WorkBlock(
            task_id=task.id,
            starts_at=START + timedelta(days=2),
            ends_at=START + timedelta(days=2, hours=4),
        )
    )
    assert context(store, task).remaining == timedelta()


@pytest.mark.parametrize("kind", ["missing_time", "completed", "missing_task"])
def test_require_live_incomplete_task_with_estimate(setup, kind):
    store, task = setup
    if kind == "missing_time":
        store.save_task(replace(task, expected_duration=None))
    elif kind == "completed":
        store.save_task(replace(task, completed=True))
    else:
        task = replace(task, id=uuid4())
    with pytest.raises(ValueError):
        context(store, task)


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        "[]",
        '{"blocks":{},"deadline":null}',
        '{"blocks":[null]}',
        '{"blocks":[{"start":"2030-01-07T09:00:00","end":"2030-01-07T10:00:00"}]}',
        '{"blocks":[{"start":"2030-01-07T08:00:00Z","end":"2030-01-07T10:00:00Z"}]}',
        '{"blocks":[{"start":"2030-01-07T14:00:00Z","end":"2030-01-07T16:00:00Z"}]}',
        '{"blocks":[{"start":"2030-01-07T09:00:00Z","end":"2030-01-07T14:00:00Z"}]}',
    ],
)
def test_reject_invalid_or_excessive_model_blocks(setup, payload):
    store, task = setup
    with pytest.raises(ValueError):
        parse_schedule(uuid4(), payload, context(store, task))
    assert not store.list_work_blocks()


def test_overlap_and_unknown_fields_rejected(setup):
    store, task = setup
    block = {"start": START.isoformat(), "end": (START + timedelta(hours=1)).isoformat()}
    with pytest.raises(ValueError, match="пересекаются"):
        parse_schedule(uuid4(), json.dumps({"blocks": [block, block]}), context(store, task))
    with pytest.raises(ValueError):
        parse_schedule(
            uuid4(),
            json.dumps({"blocks": [{**block, "task_id": str(uuid4())}]}),
            context(store, task),
        )


def test_round_current_time_up_and_reject_expired_draft(setup):
    store, task = setup
    ctx = planning_context(
        store,
        task.id,
        START,
        START + timedelta(days=1),
        HOURS,
        zone=UTC,
        now=START + timedelta(seconds=1),
    )
    assert ctx.free[0][0] == START + timedelta(minutes=15)
    draft = proposal(ctx)
    store._clock = lambda: START + timedelta(minutes=16)
    with pytest.raises(ValueError, match="прошло"):
        apply_schedule(store, draft)
    assert not store.list_work_blocks()


def test_atomic_apply_idempotent_restart_and_digest(setup, tmp_path):
    store, task = setup
    draft = proposal(context(store, task))
    assert apply_schedule(store, draft) == task.id
    assert store.list_work_blocks() == draft.blocks
    with TaskStore(tmp_path / "tasks.sqlite3") as reopened:
        assert apply_schedule(reopened, draft) == task.id
        assert reopened.list_work_blocks() == draft.blocks
        edited = parse_schedule(draft.id, '{"blocks":[]}', draft.context)
        with pytest.raises(ValueError):
            reopened.apply_schedule(edited)
    assert store.get_task(task.id) == task


@pytest.mark.parametrize("change", ["task", "busy", "outside"])
def test_stale_snapshot_rejected(setup, change):
    store, task = setup
    draft = proposal(context(store, task))
    if change == "task":
        store.save_task(replace(task, title="Изменённая"))
    elif change == "busy":
        store.create_work_block(
            WorkBlock(title="Прогулка", starts_at=START, ends_at=START + timedelta(minutes=30))
        )
    else:
        store.create_work_block(
            WorkBlock(
                task_id=task.id,
                starts_at=START + timedelta(days=2),
                ends_at=START + timedelta(days=2, hours=1),
            )
        )
    before = store.list_work_blocks()
    with pytest.raises(ValueError, match="изменились"):
        apply_schedule(store, draft)
    assert store.list_work_blocks() == before


def test_all_blocks_and_receipt_rollback_on_write_failure(setup):
    store, task = setup
    store.create_work_block(
        WorkBlock(
            title="Перерыв",
            starts_at=START + timedelta(hours=2),
            ends_at=START + timedelta(hours=3),
        )
    )
    draft = proposal(context(store, task))
    assert len(draft.blocks) == 2
    store._db.execute(
        "CREATE TRIGGER fail_block BEFORE INSERT ON work_blocks "
        f"WHEN NEW.id='{draft.blocks[1].id}' "
        "BEGIN SELECT RAISE(ABORT,'test failure'); END"
    )
    before = store.list_work_blocks()
    with pytest.raises(sqlite3.IntegrityError):
        apply_schedule(store, draft)
    assert store.list_work_blocks() == before
    assert not store._db.execute("SELECT * FROM applied_proposals").fetchall()
    store._db.execute("DROP TRIGGER fail_block")
    apply_schedule(store, draft)
    assert len(store.list_work_blocks()) == 3


def test_hours_atomic_restart_validation_and_failure(tmp_path, monkeypatch):
    assert load_hours(tmp_path) == working_hours.DEFAULT
    save_hours(tmp_path, HOURS)
    assert load_hours(tmp_path) == HOURS
    original = (tmp_path / "working-hours.json").read_bytes()
    with pytest.raises(ValueError):
        save_hours(tmp_path, ((1080, 540),) * 7)

    def fail(*_):
        raise OSError("full")

    monkeypatch.setattr(working_hours.os, "replace", fail)
    with pytest.raises(OSError):
        save_hours(tmp_path, (None,) * 7)
    assert (tmp_path / "working-hours.json").read_bytes() == original
    assert len(list(tmp_path.iterdir())) == 1
    for invalid in ([], [True] * 7, [[False, 900]] * 7, [[0, 1441]] * 7):
        with pytest.raises(ValueError):
            validate_hours(invalid)


def test_dst_actual_duration_and_past_clipping(tmp_path):
    zone = ZoneInfo("Europe/Berlin")
    start = datetime(2030, 3, 31, tzinfo=zone)
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        task = Task(title="Без срока", expected_duration=timedelta(hours=5))
        store.create_task(task)
        ctx = planning_context(
            store, task.id, start, start + timedelta(days=1), ((0, 240),) * 7, zone=zone, now=start
        )
        assert ctx.free[0][1] - ctx.free[0][0] == timedelta(hours=3)
        later = planning_context(
            store,
            task.id,
            start,
            start + timedelta(days=1),
            ((0, 240),) * 7,
            zone=zone,
            now=start + timedelta(hours=1),
        )
        assert later.free[0][0] == (start + timedelta(hours=1)).astimezone(UTC)


def test_chatgpt_schedule_uses_same_transport_and_restricted_context(setup):
    store, task = setup

    class Session:
        model = "test"

        def infer(self, prompt, instructions, cancelled):
            data = json.loads(prompt)
            assert data["operation"] == "schedule"
            assert data["selected_task"]["id"] == str(task.id)
            assert data["planning"]["remaining_minutes"] == 240
            assert instructions == SCHEDULE_INSTRUCTIONS
            return '{"blocks":[]}'

    adapter = ChatGPTAdapter(Session())
    ctx = context(store, task)
    response = adapter.generate(
        Request(uuid4(), "План", "schedule", task, planning=ctx.data()), Event()
    )
    assert json.loads(response) == {"blocks": []}


def test_planning_gui_and_restart(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    for mode in ("run", "restart"):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("ui_planning_scenario.py")),
                str(tmp_path),
                mode,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
            assert warning not in result.stderr, result.stderr
