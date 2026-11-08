import json
import sqlite3
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from ritm.application.calendar import CalendarService
from ritm.application.tasks import TaskService
from ritm.domain.calendar import WorkBlock, block_warnings, overlaps, planned_duration
from ritm.domain.tasks import Subtask, Task
from ritm.storage import sqlite as storage
from ritm.storage.codec import encode_task
from ritm.storage.sqlite import APPLICATION_ID, SCHEMA, SCHEMA_VERSION, TRASH_SCHEMA, TaskStore

START = datetime(2026, 11, 8, 12, tzinfo=UTC)


def block(task, start=START, minutes=60, **changes):
    return WorkBlock(
        task_id=task.id, starts_at=start, ends_at=start + timedelta(minutes=minutes), **changes
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"starts_at": START.replace(tzinfo=None)},
        {"ends_at": START.replace(tzinfo=None)},
        {"ends_at": START},
        {"ends_at": START - timedelta(seconds=1)},
        {"task_id": "not-uuid"},
        {"id": "not-uuid"},
        {"starts_at": "2026-11-08"},
    ],
)
def test_model_rejects_invalid_block(changes):
    fields = {"task_id": uuid4(), "starts_at": START, "ends_at": START + timedelta(hours=1)}
    with pytest.raises(ValueError):
        WorkBlock(**(fields | changes))


def test_model_normalizes_utc_and_keeps_id_on_move():
    task = Task(title="Работа")
    start = START.astimezone(timezone(timedelta(hours=5, minutes=30)))
    original = block(task, start=start)
    assert original.starts_at == START
    assert original.starts_at.tzinfo is UTC
    assert original.duration == timedelta(hours=1)
    moved = replace(
        original, starts_at=START + timedelta(days=1), ends_at=START + timedelta(days=1, hours=2)
    )
    assert moved.id == original.id
    assert moved.duration == timedelta(hours=2)
    with pytest.raises(FrozenInstanceError):
        original.starts_at = START


def test_dst_repeated_hour_uses_elapsed_time():
    berlin = ZoneInfo("Europe/Berlin")
    task = Task(title="Перевод часов")
    original = WorkBlock(
        task_id=task.id,
        starts_at=datetime(2026, 10, 25, 2, 30, fold=0, tzinfo=berlin),
        ends_at=datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=berlin),
    )
    assert original.duration == timedelta(hours=1)
    spring = WorkBlock(
        task_id=task.id,
        starts_at=datetime(2026, 3, 29, 1, 30, tzinfo=berlin),
        ends_at=datetime(2026, 3, 29, 3, 30, tzinfo=berlin),
    )
    assert spring.duration == timedelta(hours=1)


def test_nonexistent_local_time_is_rejected():
    berlin = ZoneInfo("Europe/Berlin")
    with pytest.raises(ValueError, match="не существует"):
        WorkBlock(
            task_id=uuid4(),
            starts_at=datetime(2026, 3, 29, 2, 30, tzinfo=berlin),
            ends_at=datetime(2026, 3, 29, 4, tzinfo=berlin),
        )


def test_half_open_intervals_and_sum_include_overlap():
    task = Task(title="План", deadline=START + timedelta(hours=2))
    first = block(task, minutes=120)
    inside = block(task, start=START + timedelta(minutes=30), minutes=60)
    touching = block(task, start=first.ends_at)
    assert overlaps(first, inside) and overlaps(inside, first)
    assert not overlaps(first, touching)
    assert planned_duration((first, inside)) == timedelta(hours=3)
    assert planned_duration(()) == timedelta()
    warnings = block_warnings(first, task, (first, inside, touching))
    assert warnings.overlapping_ids == (inside.id,)
    assert not warnings.after_deadline
    assert block_warnings(touching, task, ()).after_deadline
    assert not block_warnings(touching, replace(task, deadline=None), ()).after_deadline
    with pytest.raises(ValueError):
        block_warnings(first, Task(title="Другая"), ())


def test_roundtrip_microseconds_and_new_process(tmp_path):
    path = tmp_path / "профиль" / "tasks.sqlite3"
    task = Task(title="Лабораторная", expected_duration=timedelta(hours=5))
    first = WorkBlock(
        task_id=task.id,
        starts_at=START + timedelta(microseconds=1),
        ends_at=START + timedelta(minutes=45, microseconds=9),
    )
    second = block(task, start=START + timedelta(days=1), minutes=90)
    with TaskStore(path) as store:
        TaskService(store).create_task(task)
        service = CalendarService(store)
        service.create_block(first)
        service.create_block(second)
        assert service.planned_time(task.id) == timedelta(minutes=135, microseconds=8)
        assert store.get_task(task.id) == task
    with TaskStore(path) as store:
        assert store.list_work_blocks(task_id=task.id) == (first, second)
        assert store.get_work_block(first.id) == first
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; from ritm.storage.sqlite import TaskStore; import json,sys; "
            "s=TaskStore(Path(sys.argv[1])); "
            "print(json.dumps([str(x.id) for x in s.list_work_blocks()])); s.close()",
            str(path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == [str(first.id), str(second.id)]


def test_creation_requires_main_task_and_does_not_overwrite(tmp_path):
    task = Task(title="Главная", subtasks=(Subtask(title="Шаг"),))
    with TaskStore(tmp_path / "db.sqlite3") as store:
        TaskService(store).create_task(task)
        service = CalendarService(store)
        with pytest.raises(KeyError):
            service.create_block(block(Task(title="Не существует")))
        with pytest.raises(KeyError):
            service.create_block(
                WorkBlock(
                    task_id=task.subtasks[0].id, starts_at=START, ends_at=START + timedelta(hours=1)
                )
            )
        original = block(task)
        service.create_block(original)
        with pytest.raises(sqlite3.IntegrityError):
            service.create_block(replace(original, ends_at=START + timedelta(hours=2)))
        assert store.get_work_block(original.id) == original
        assert len(store.list_work_blocks()) == 1


def test_cross_task_conflicts_are_allowed_and_warned(tmp_path):
    first = Task(title="Первая", deadline=START + timedelta(minutes=30))
    second = Task(title="Вторая", completed=True)
    with TaskStore(tmp_path / "db.sqlite3") as store:
        tasks = TaskService(store)
        tasks.create_task(first)
        tasks.create_task(second)
        service = CalendarService(store)
        a = block(first)
        assert service.create_block(a).warnings.after_deadline
        b = block(second, start=START + timedelta(minutes=30), minutes=90)
        change = service.create_block(b)
        assert change.warnings.overlapping_ids == (a.id,)
        assert not change.warnings.after_deadline
        assert service.warnings(a.id).overlapping_ids == (b.id,)
        assert service.planned_time(first.id) == timedelta(hours=1)
        assert service.planned_time(second.id) == timedelta(minutes=90)
        assert store.get_task(first.id) == first
        assert store.get_task(second.id) == second


def test_move_keeps_identity_and_clears_outdated_conflicts(tmp_path):
    task = Task(title="План")
    other = Task(title="Другая")
    with TaskStore(tmp_path / "db.sqlite3") as store:
        tasks = TaskService(store)
        tasks.create_task(task)
        tasks.create_task(other)
        service = CalendarService(store)
        a, b = block(task), block(other)
        service.create_block(a)
        service.create_block(b)
        moved = replace(
            a, starts_at=START + timedelta(days=1), ends_at=START + timedelta(days=1, hours=2)
        )
        assert not service.update_block(moved).warnings.overlapping_ids
        assert service.planned_time(task.id) == timedelta(hours=2)
        assert store.get_work_block(a.id) == moved
        assert not service.warnings(b.id).overlapping_ids
        with pytest.raises(ValueError):
            service.update_block(replace(moved, task_id=other.id))
        assert store.get_work_block(a.id) == moved
        with pytest.raises(KeyError):
            service.update_block(replace(moved, id=uuid4()))


def test_deadline_change_retains_blocks_and_recomputes_warning(tmp_path):
    task = Task(title="Дедлайн", deadline=START + timedelta(hours=2))
    with TaskStore(tmp_path / "db.sqlite3") as store:
        tasks = TaskService(store)
        tasks.create_task(task)
        service = CalendarService(store)
        original = block(task)
        service.create_block(original)
        assert not service.warnings(original.id).after_deadline
        tasks.edit_details(
            task.id,
            title=task.title,
            description=task.description,
            difficulty=None,
            expected_duration=None,
            deadline=START + timedelta(minutes=15),
        )
        assert store.get_work_block(original.id) == original
        assert service.warnings(original.id).after_deadline
        assert not store.get_task(task.id).completed


def test_range_query_across_year_and_exact_edges(tmp_path):
    task = Task(title="Новый год")
    midnight = datetime(2027, 1, 1, tzinfo=UTC)
    first = block(task, start=midnight - timedelta(minutes=30))
    second = block(task, start=midnight + timedelta(minutes=30))
    before = block(task, start=midnight - timedelta(minutes=90))
    with TaskStore(tmp_path / "db.sqlite3") as store:
        TaskService(store).create_task(task)
        service = CalendarService(store)
        for item in (second, before, first):
            service.create_block(item)
        assert store.list_work_blocks(
            window_start=midnight, window_end=midnight + timedelta(minutes=30)
        ) == (first,)
        assert store.list_work_blocks(window_end=first.starts_at) == (before,)
        assert store.list_work_blocks(window_start=second.starts_at) == (second,)
        local = midnight.astimezone(timezone(timedelta(hours=-5)))
        assert store.list_work_blocks(
            window_start=local, window_end=local + timedelta(minutes=30)
        ) == (first,)
        with pytest.raises(ValueError):
            store.list_work_blocks(window_start=midnight, window_end=midnight)
        with pytest.raises(ValueError):
            store.list_work_blocks(window_start=midnight.replace(tzinfo=None))


def test_trash_hides_blocks_and_restoration_returns_them(tmp_path):
    task = Task(title="Не терять", subtasks=(Subtask(title="Шаг"),))
    path = tmp_path / "db.sqlite3"
    original = block(task)
    with TaskStore(path) as store:
        tasks = TaskService(store)
        tasks.create_task(task)
        calendar = CalendarService(store)
        calendar.create_block(original)
        branch_entry = tasks.delete_node(task.id, task.subtasks[0].id)
        assert store.get_work_block(original.id) == original
        tasks.restore(UUID(branch_entry))
        entry = tasks.delete_node(task.id, task.id)
        assert not store.list_work_blocks()
        assert store.get_work_block(original.id) is None
        assert calendar.planned_time(task.id) == timedelta()
        with pytest.raises(KeyError):
            calendar.create_block(block(task, start=START + timedelta(days=1)))
        with pytest.raises(KeyError):
            calendar.update_block(replace(original, ends_at=START + timedelta(hours=2)))
        with pytest.raises(ValueError):
            tasks.create_task(Task(id=task.id, title="Не подменить владельца"))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM work_blocks").fetchone()[0] == 1
    with TaskStore(path) as store:
        TaskService(store).restore(UUID(entry))
        assert store.list_work_blocks() == (original,)
        assert CalendarService(store).planned_time(task.id) == timedelta(hours=1)


def create_v2(path):
    live, archived = Task(title="Живая"), Task(title="В корзине")
    entry = uuid4()
    with sqlite3.connect(path) as db:
        for statement in SCHEMA:
            db.execute(statement)
        db.execute(TRASH_SCHEMA)
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA user_version=2")
        db.execute(
            "INSERT INTO nodes(id,position,kind,title,completed,description) VALUES(?,0,0,?,0,'')",
            (str(live.id), live.title),
        )
        db.execute(
            "INSERT INTO trash VALUES (?,?,?,?,?,?,?,NULL)",
            (
                str(entry),
                str(archived.id),
                str(archived.id),
                archived.title,
                0,
                START.isoformat(),
                encode_task(archived),
            ),
        )
    return live, archived, entry


def test_migration_v2_includes_archived_task_identity(tmp_path):
    path = tmp_path / "db.sqlite3"
    live, archived, entry = create_v2(path)
    with TaskStore(path) as store:
        assert store.get_task(live.id) == live
        assert len(store.list_trash()) == 1
        assert not store.list_work_blocks()
        CalendarService(store).create_block(block(live))
        with pytest.raises(ValueError):
            TaskService(store).create_task(archived)
        assert TaskService(store).restore(entry) == archived
        CalendarService(store).create_block(block(archived))
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert {row[0] for row in db.execute("SELECT id FROM task_identities")} == {
            str(live.id),
            str(archived.id),
        }


def test_failed_v2_migration_leaves_database_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    live, archived, _ = create_v2(path)
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(storage, "CALENDAR_SCHEMA", (storage.CALENDAR_SCHEMA[0], "invalid SQL"))
        with pytest.raises(sqlite3.Error):
            TaskStore(path)
    assert path.read_bytes() == before
    with TaskStore(path) as store:
        assert store.get_task(live.id) == live
        assert store.list_trash()[0]["task_id"] == str(archived.id)


def test_storage_failure_does_not_change_existing_block(tmp_path):
    path = tmp_path / "db.sqlite3"
    task = Task(title="Атомарная запись")
    original = block(task)
    with TaskStore(path) as store:
        TaskService(store).create_task(task)
        service = CalendarService(store)
        service.create_block(original)
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TRIGGER fail_block BEFORE UPDATE ON work_blocks
                          BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            service.update_block(replace(original, ends_at=START + timedelta(hours=2)))
        assert store.get_work_block(original.id) == original
        assert service.planned_time(task.id) == timedelta(hours=1)


def test_sql_constraints_and_registry_foreign_key(tmp_path):
    path = tmp_path / "db.sqlite3"
    task = Task(title="Связь")
    with TaskStore(path) as store:
        TaskService(store).create_task(task)
        CalendarService(store).create_block(block(task))
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO work_blocks(id,task_id,starts_us,ends_us) VALUES (?,?,0,0)",
                (str(uuid4()), str(task.id)),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO work_blocks(id,task_id,starts_us,ends_us) VALUES (?,?,0,1)",
                (str(uuid4()), str(uuid4())),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM task_identities WHERE id=?", (str(task.id),))
