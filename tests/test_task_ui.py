import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import parse_details, task_view


def test_optional_fields_and_local_datetime():
    fields = parse_details(
        {"title": "Работа", "minutes": "1,5", "difficulty": "7", "deadline": "28.11.2026 18:00"}
    )
    task = Task(**fields)
    assert task.expected_duration == timedelta(seconds=90)
    assert task.deadline.utcoffset() is not None
    assert task_view(task)["minutes"] == "1.5"
    assert task_view(task)["deadline"] == "28.11.2026 18:00"
    assert Task(**parse_details({"title": "Минимальная"})).deadline is None


@pytest.mark.parametrize(
    "fields",
    [
        {"difficulty": "1.5"},
        {"difficulty": "11"},
        {"minutes": "0"},
        {"minutes": "-1"},
        {"minutes": "NaN"},
        {"minutes": "1e-999999999"},
        {"minutes": "abc"},
        {"deadline": "31.02.2026 12:00"},
        {"deadline": "28.11.2026"},
    ],
)
def test_invalid_form(fields):
    with pytest.raises(ValueError):
        Task(**parse_details({"title": "Название", **fields}))


def test_editing_other_fields_preserves_exact_time():
    task = Task(
        title="Старая",
        expected_duration=timedelta(microseconds=1),
        deadline=datetime(2026, 11, 28, 18, 0, 59, tzinfo=timezone(timedelta(hours=5))),
    )
    view = task_view(task)
    view["title"] = "Новая"
    updated = parse_details(view, task)
    assert updated["deadline"] == task.deadline
    assert updated["expected_duration"] == task.expected_duration


def test_qml_task_scenario_and_restart(tmp_path):
    env = os.environ.copy()
    env.update(
        QT_QPA_PLATFORM=os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        QT_QUICK_BACKEND="software",
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ui_scenario.py")), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"ok": true' in result.stdout
    task_id = json.loads(result.stdout)["id"]
    reopened = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("ui_scenario.py")),
            str(tmp_path),
            "--reopen",
            task_id,
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    assert '"reopened": true' in reopened.stdout
    for warning in ("ReferenceError", "TypeError", "Binding loop", "failed to load"):
        assert warning not in result.stderr + reopened.stderr, result.stderr + reopened.stderr


def test_creating_existing_id_cannot_overwrite_task(tmp_path):
    original = Task(title="Оригинал")
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        service = TaskService(store)
        service.create_task(original)
        with pytest.raises(ValueError):
            service.create_task(replace(original, title="Не должно перезаписать"))
        assert store.get_task(original.id) == original


@pytest.mark.skipif(os.name == "nt", reason="Unix signals")
@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_main_process_stops_cleanly(tmp_path, stop_signal):
    env = os.environ.copy()
    env.update(
        QT_QPA_PLATFORM="offscreen",
        QT_QUICK_BACKEND="software",
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "ritm", "--profile", str(tmp_path / "profile")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "profile" / "tasks.sqlite3").exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.02)
        time.sleep(0.2)
        process.send_signal(stop_signal)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stdout + stderr
        assert "TypeError" not in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
