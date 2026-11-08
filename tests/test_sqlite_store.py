import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task
from ritm.storage.sqlite import APPLICATION_ID, SCHEMA_VERSION, TaskStore


def test_roundtrip_tree_and_reopen(tmp_path):
    path = tmp_path / "профиль" / "tasks.sqlite3"
    category = Category(title="Учёба ' SQL")
    task = Task(
        title="Лабораторная ' ; --",
        description="Материалы\nОписание",
        category_id=category.id,
        difficulty=7,
        deadline=datetime(2026, 11, 28, 18, 45, tzinfo=timezone(timedelta(hours=3))),
        expected_duration=timedelta(hours=2, microseconds=123),
        subtasks=(
            Subtask(
                title="Второе",
                weight=Decimal("33.333333333333333333"),
                items=(ChecklistItem(title="Б", completed=True), ChecklistItem(title="А")),
            ),
            Subtask(title="Первое", weight=None),
        ),
    )
    with TaskStore(path) as store:
        store.save_category(category)
        store.save_task(task)
        assert store.get_task(uuid4()) is None
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task
        assert store.list_tasks() == (task,)
        assert store.list_categories() == (category,)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from ritm.storage.sqlite import TaskStore; import sys; "
            "s=TaskStore(Path(sys.argv[1])); print(s.list_tasks()[0].id); s.close()",
            str(path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout.strip() == str(task.id)


def test_update_replaces_descendants_without_duplicates(tmp_path):
    task = Task(
        title="Работа", subtasks=(Subtask(title="Этап", items=(ChecklistItem(title="Пункт"),)),)
    )
    with TaskStore(tmp_path / "db.sqlite3") as store:
        store.save_task(task)
        updated = replace(task, title="Обновлено", subtasks=(Subtask(title="Новый"),))
        store.save_task(updated)
        store.save_task(updated)
        assert store.list_tasks() == (updated,)
        assert store.get_task(task.subtasks[0].id) is None


def test_failed_save_rolls_back_entire_tree(tmp_path):
    a = Task(title="А", subtasks=(Subtask(title="Этап А"),))
    b = Task(title="Б", subtasks=(Subtask(title="Этап Б"),))
    with TaskStore(tmp_path / "db.sqlite3") as store:
        store.save_task(a)
        store.save_task(b)
        collision = replace(a, title="Не должно сохраниться", subtasks=b.subtasks)
        with pytest.raises(sqlite3.IntegrityError):
            store.save_task(collision)
        assert store.list_tasks() == (a, b)
        with pytest.raises(ValueError):
            store.save_task(Task(id=b.subtasks[0].id, title="Коллизия корня"))
        assert store.list_tasks() == (a, b)


def test_category_reference_and_rename(tmp_path):
    category = Category(title="Старое")
    task = Task(title="Задача", category_id=category.id)
    with TaskStore(tmp_path / "db.sqlite3") as store:
        with pytest.raises(sqlite3.IntegrityError):
            store.save_task(task)
        assert store.list_tasks() == ()
        store.save_category(category)
        store.save_task(task)
        store.save_category(replace(category, title="Новое"))
        assert store.list_categories() == (replace(category, title="Новое"),)
        assert store.get_task(task.id) == task


@pytest.mark.parametrize("foreign", [False, True])
def test_unknown_database_untouched(tmp_path, foreign):
    path = tmp_path / "db.sqlite3"
    with sqlite3.connect(path) as db:
        if foreign:
            db.execute("CREATE TABLE other (value TEXT)")
            db.execute("INSERT INTO other VALUES ('важно')")
        else:
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            db.execute("PRAGMA user_version=999")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        TaskStore(path)
    assert path.read_bytes() == before


def test_minimal_task_and_schema_constraints(tmp_path):
    path = tmp_path / "db.sqlite3"
    task = Task(title="Минимальная")
    with TaskStore(path) as store:
        store.save_task(task)
        assert store.get_task(task.id) == task
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO nodes(id,parent_id,position,kind,title,completed) "
                "VALUES(?,?,0,2,'пункт',0)",
                (str(uuid4()), str(task.id)),
            )


def test_large_duration_does_not_replace_saved_task(tmp_path):
    task = Task(title="Задача")
    with TaskStore(tmp_path / "db.sqlite3") as store:
        store.save_task(task)
        with pytest.raises(ValueError):
            store.save_task(replace(task, expected_duration=timedelta.max))
        assert store.get_task(task.id) == task
