import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from ritm.storage import chat_history
from ritm.storage.chat_history import ChatHistory, HistoryError


def message(text="Запрос", role="user"):
    return {
        "id": str(uuid4()),
        "chat": str(uuid4()),
        "role": role,
        "text": text,
        "time": "2026-11-13T18:00:00+03:00",
        "label": "Тестовый помощник",
    }


def test_local_history_restart_disable_resume_and_clear(tmp_path):
    history = ChatHistory(tmp_path)
    history.load()
    row = message()
    history.append(row)
    history.append(row)
    assert history.messages == [row]
    assert stat.S_IMODE(history.path.stat().st_mode) == 0o600
    history.set_enabled(False)
    saved = history.path.read_bytes()
    history.append(message("Не сохранять"))
    assert history.path.read_bytes() == saved
    reopened = ChatHistory(tmp_path)
    reopened.load()
    assert not reopened.enabled and reopened.messages == [row]
    reopened.set_enabled(True)
    reopened.append(message("Снова сохранять"))
    assert "Не сохранять" not in reopened.path.read_text()
    reopened.clear()
    assert json.loads(reopened.path.read_text())["messages"] == []
    assert not (tmp_path / "tasks.sqlite3").exists()


def test_history_write_failure_preserves_disk_and_in_memory_state(tmp_path, monkeypatch):
    history = ChatHistory(tmp_path)
    history.append(message("Прежнее"))
    previous = history.path.read_bytes()

    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr(chat_history.os, "replace", fail)
    with pytest.raises(OSError):
        history.append(message("Не записалось"))
    assert history.path.read_bytes() == previous
    assert len(history.messages) == 1
    assert list(tmp_path.iterdir()) == [history.path]


def test_corrupt_history_not_overwritten_implicitly(tmp_path):
    path = tmp_path / "chat-history.json"
    path.write_text("broken")
    history = ChatHistory(tmp_path)
    with pytest.raises(HistoryError):
        history.load()
    history.append(message("Не перезаписывать повреждённый файл"))
    assert path.read_text() == "broken"
    with pytest.raises(HistoryError):
        history.set_enabled(True)
    history.clear()
    assert json.loads(path.read_text())["messages"] == []


def test_full_history_keeps_previous_messages_without_silent_rotation(tmp_path, monkeypatch):
    history = ChatHistory(tmp_path)
    history.append(message())
    previous = history.path.read_bytes()
    monkeypatch.setattr(chat_history, "MAX_BYTES", len(previous) + 10)
    with pytest.raises(HistoryError, match="заполнена"):
        history.append(message())
    assert history.path.read_bytes() == previous and len(history.messages) == 1


def test_chat_context_history_delete_and_restart_gui(tmp_path):
    environment = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    for mode in ("run", "reopen", "cleared"):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("ui_chat_scenario.py")),
                str(tmp_path),
                mode,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for warning in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
            assert warning not in result.stderr, result.stderr


def test_history_can_be_disabled_when_saved_file_is_at_limit(tmp_path, monkeypatch):
    history = ChatHistory(tmp_path)
    history.append(message())
    monkeypatch.setattr(chat_history, "MAX_BYTES", len(history.path.read_bytes()) + 1)
    history.set_enabled(False)
    assert not history.enabled
    reopened = ChatHistory(tmp_path)
    reopened.load()
    assert not reopened.enabled and len(reopened.messages) == 1
