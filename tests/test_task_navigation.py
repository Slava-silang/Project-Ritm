import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.task_list import query_tasks
from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task
from ritm.storage import sqlite as storage
from ritm.storage.sqlite import APPLICATION_ID, SCHEMA, SCHEMA_VERSION, TaskStore
from ritm.ui.tasks import select_category

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)


@pytest.fixture
def task():
    return Task(
        title="Лабораторная",
        description="Важное описание",
        difficulty=7,
        deadline=NOW.astimezone(timezone(timedelta(hours=3))),
        expected_duration=timedelta(minutes=90, microseconds=7),
        subtasks=(
            Subtask(
                title="Расчёты",
                weight=Decimal(40),
                items=(
                    ChecklistItem(title="Формулы"),
                    ChecklistItem(title="Таблица", completed=True),
                ),
            ),
            Subtask(title="Отчёт", weight=Decimal(60), completed=True),
        ),
    )


def test_query_groups_sorts_and_handles_exact_boundary():
    later = Task(title="Позже", deadline=NOW + timedelta(hours=1))
    overdue = Task(title="Вчера", deadline=NOW - timedelta(days=1))
    boundary = Task(title="Сейчас", deadline=NOW.astimezone(timezone(timedelta(hours=5))))
    no_date = Task(title="Без срока")
    done = Task(title="Готово", completed=True, deadline=NOW - timedelta(days=2))
    rows = query_tasks((no_date, later, done, boundary, overdue), now=NOW)
    assert [x.task for x in rows] == [overdue, boundary, later, no_date]
    assert [x.group for x in rows] == ["Просроченные", "Предстоящие", "Предстоящие", "Без дедлайна"]
    assert rows[0].overdue and not rows[1].overdue
    assert query_tasks((boundary,), now=NOW + timedelta(microseconds=1))[0].overdue
    completed = query_tasks((done,), now=NOW, state="completed")
    assert completed[0].group == "Выполненные" and not completed[0].overdue
    assert len(query_tasks((no_date, done), now=NOW, state="all")) == 2
    with pytest.raises(ValueError):
        query_tasks((), now=NOW.replace(tzinfo=None))


def test_query_search_and_category_combine(task):
    category = Category(title="Учёба")
    task = replace(task, category_id=category.id)
    other = Task(title="Другое")
    assert (
        query_tasks((task, other), now=NOW, search=" ФОРМУЛЫ ", category_id=category.id)[0].task
        == task
    )
    assert query_tasks((task,), now=NOW, search="важное")[0].task == task
    assert not query_tasks((task,), now=NOW, search="Нет совпадений")
    assert not query_tasks((task,), now=NOW, uncategorized=True)
    assert query_tasks((task, other), now=NOW, uncategorized=True)[0].task == other
    assert not query_tasks((task,), now=NOW, category_id=uuid4())


def create_v1(path):
    identifier = uuid4()
    with sqlite3.connect(path) as db:
        for statement in SCHEMA:
            db.execute(statement)
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA user_version=1")
        db.execute(
            "INSERT INTO nodes(id,position,kind,title,completed,description) VALUES(?,0,0,?,0,?)",
            (str(identifier), "До обновления", "Данные сохраняются"),
        )
    return identifier


def test_migration_v1_and_reopen(tmp_path):
    path = tmp_path / "db.sqlite3"
    identifier = create_v1(path)
    with TaskStore(path) as store:
        assert store.get_task(identifier).description == "Данные сохраняются"
        assert store.list_trash() == ()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    with TaskStore(path) as store:
        assert store.get_task(identifier).title == "До обновления"


def test_failed_migration_leaves_original_bytes(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    identifier = create_v1(path)
    original = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(storage, "TRASH_SCHEMA", "not valid SQL")
        with pytest.raises(sqlite3.Error):
            TaskStore(path)
    assert path.read_bytes() == original
    with TaskStore(path) as store:
        assert store.get_task(identifier).title == "До обновления"


@pytest.mark.parametrize("level", ["root", "branch", "leaf", "completed_leaf"])
def test_delete_restore_exact_tree_after_restart(tmp_path, task, level):
    node_id = {
        "root": task.id,
        "branch": task.subtasks[0].id,
        "leaf": task.subtasks[0].items[0].id,
        "completed_leaf": task.subtasks[0].items[1].id,
    }[level]
    path = tmp_path / "db.sqlite3"
    with TaskStore(path) as store:
        store.save_task(task)
        entry = TaskService(store).delete_node(task.id, node_id, now=NOW)
        assert len(store.list_trash()) == 1
        if level == "root":
            assert store.get_task(task.id) is None
        elif level == "branch":
            assert store.get_task(task.id).subtasks[0].weight == 100
            assert store.get_task(task.id).completed
    with TaskStore(path) as store:
        restored = TaskService(store).restore(UUID(entry))
        assert restored == task
        assert store.list_trash() == ()
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task


def test_nested_trash_requires_parent_before_child(tmp_path, task):
    with TaskStore(tmp_path / "db.sqlite3") as store:
        store.save_task(task)
        service = TaskService(store)
        leaf = service.delete_node(task.id, task.subtasks[0].items[0].id)
        branch = service.delete_node(task.id, task.subtasks[0].id)
        root = service.delete_node(task.id, task.id)
        with pytest.raises(ValueError, match="главную"):
            service.restore(UUID(leaf))
        assert len(store.list_trash()) == 3
        service.restore(UUID(root))
        with pytest.raises(ValueError, match="родительскую"):
            service.restore(UUID(leaf))
        service.restore(UUID(branch))
        assert service.restore(UUID(leaf)) == task
        with pytest.raises(KeyError):
            service.restore(UUID(leaf))


def test_restore_preserves_changes_since_deletion(tmp_path, task):
    with TaskStore(tmp_path / "db.sqlite3") as store:
        store.save_task(task)
        service = TaskService(store)
        entry = service.delete_node(task.id, task.subtasks[0].id)
        extra = Subtask(title="Дополнительный шаг")
        service.add_subtask(task.id, extra)
        service.rename_node(task.id, task.id, "Новое название")
        restored = service.restore(UUID(entry))
        assert restored.title == "Новое название"
        assert restored.subtasks[0].id == task.subtasks[0].id
        assert restored.subtasks[0].items == task.subtasks[0].items
        assert restored.subtasks[-1].id == extra.id
        assert sum(x.weight for x in restored.subtasks) == 100
        assert restored.subtasks[1].completed


def test_restore_collision_keeps_archive_and_other_task(tmp_path, task):
    with TaskStore(tmp_path / "db.sqlite3") as store:
        store.save_task(task)
        service = TaskService(store)
        entry = service.delete_node(task.id, task.id)
        conflict = Task(id=task.subtasks[0].id, title="Другие данные")
        service.create_task(conflict)
        with pytest.raises(sqlite3.IntegrityError):
            service.restore(UUID(entry))
        assert store.get_task(task.id) is None
        assert store.get_task(conflict.id) == conflict
        assert len(store.list_trash()) == 1


def test_failed_deletion_rolls_back_archive_and_tree(tmp_path, task):
    path = tmp_path / "db.sqlite3"
    with TaskStore(path) as store:
        store.save_task(task)
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TRIGGER fail_archive BEFORE INSERT ON nodes WHEN NEW.kind=1
                          BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            TaskService(store).delete_node(task.id, task.subtasks[0].items[0].id)
        assert store.get_task(task.id) == task
        assert not store.list_trash()


def test_category_and_metadata_are_atomic(tmp_path):
    category = Category(title="Новая категория")
    with TaskStore(tmp_path / "db.sqlite3") as store:
        service = TaskService(store)
        with pytest.raises(sqlite3.IntegrityError):
            service.create_task(Task(title="Не сохранять", category_id=uuid4()), category=category)
        assert not store.list_categories()
        task = service.create_task(
            Task(title="Сохранить", category_id=category.id), category=category
        )
        assert store.list_categories() == (category,)
        assert store.get_task(task.id) == task


def test_navigation_qml_scenario_and_trash_restart(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = os.environ.copy()
    env.update(
        QT_QPA_PLATFORM=os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        QT_QUICK_BACKEND="software",
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    script = str(Path(__file__).with_name("ui_navigation_scenario.py"))
    result = subprocess.run(
        [sys.executable, script, str(tmp_path)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    identifier = json.loads(result.stdout)["id"]
    restored = subprocess.run(
        [sys.executable, script, str(tmp_path), "--restore", identifier],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert '"restored": true' in restored.stdout
    for warning in ("ReferenceError", "TypeError", "Binding loop", "failed to load"):
        assert warning not in result.stderr + restored.stderr, result.stderr + restored.stderr


def test_restore_legacy_unassigned_weights(tmp_path):
    task = Task(title="Старая задача", subtasks=(Subtask(title="А"), Subtask(title="Б")))
    with TaskStore(tmp_path / "db.sqlite3") as store:
        store.save_task(task)
        service = TaskService(store)
        entry = service.delete_node(task.id, task.subtasks[0].id)
        restored = service.restore(UUID(entry))
        assert [x.id for x in restored.subtasks] == [x.id for x in task.subtasks]
        assert [x.weight for x in restored.subtasks] == [Decimal(50), Decimal(50)]


def test_category_selection_preserves_id_even_with_equal_titles():
    first, second = Category(title="Учёба"), Category(title="Учёба")
    assert select_category((first, second), "Учёба", str(second.id)) == second
    assert select_category((first, second), "УЧЁБА") == first
    assert select_category((first,), "", str(first.id)) is None
    assert select_category((first,), "Работа", str(first.id)).title == "Работа"
