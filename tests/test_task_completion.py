import sqlite3
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.completion import add_item, add_subtask, set_completed
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.storage.sqlite import TaskStore


@pytest.fixture
def task():
    return Task(
        title="Лабораторная",
        description="Описание должно сохраниться",
        difficulty=7,
        subtasks=(
            Subtask(
                title="Расчёты",
                weight=Decimal("40"),
                items=(ChecklistItem(title="Первый"), ChecklistItem(title="Второй")),
            ),
            Subtask(title="Отчёт", weight=Decimal("60")),
        ),
    )


def test_whole_task_cascades_both_ways_without_mutating_original(task):
    done = set_completed(task, task.id, True)
    assert done.completed
    assert all(child.completed for child in done.subtasks)
    assert all(item.completed for child in done.subtasks for item in child.items)
    assert set_completed(done, done.id, True) == done
    assert set_completed(done, done.id, False) == task
    assert not task.completed
    assert not task.subtasks[0].items[0].completed
    assert done.id == task.id
    assert done.description == task.description
    assert done.difficulty == task.difficulty
    assert done.subtasks[0].weight == Decimal("40")


def test_branch_cascades_but_leaves_neighbour_untouched(task):
    done = set_completed(task, task.subtasks[0].id, True)
    assert done.subtasks[0].completed
    assert all(item.completed for item in done.subtasks[0].items)
    assert done.subtasks[1] == task.subtasks[1]
    assert not done.completed
    assert set_completed(done, done.subtasks[0].id, False) == task


def test_last_leaf_completes_branch_and_last_branch_completes_task(task):
    first, second = task.subtasks[0].items
    partial = set_completed(task, first.id, True)
    assert not partial.subtasks[0].completed
    assert not partial.completed
    branch_done = set_completed(partial, second.id, True)
    assert branch_done.subtasks[0].completed
    assert not branch_done.completed
    done = set_completed(branch_done, task.subtasks[1].id, True)
    assert done.completed
    reopened = set_completed(done, first.id, False)
    assert not reopened.completed
    assert not reopened.subtasks[0].completed
    assert reopened.subtasks[0].items[1].completed
    assert reopened.subtasks[1] == done.subtasks[1]
    branch_reopened = set_completed(done, task.subtasks[0].id, False)
    assert not branch_reopened.completed
    assert branch_reopened.subtasks[1].completed
    assert not any(item.completed for item in branch_reopened.subtasks[0].items)


@pytest.mark.parametrize("initial", [False, True])
def test_task_without_children_is_manual(initial):
    task = Task(title="Без подзадач", completed=initial)
    assert set_completed(task, task.id, not initial).completed is not initial
    assert set_completed(task, task.id, initial) == task


@pytest.mark.parametrize("completed", [False, True])
def test_adding_second_level_recalculates_ancestors(task, completed):
    done = set_completed(task, task.id, True)
    item = ChecklistItem(title="Новый пункт", completed=completed)
    updated = add_item(done, done.subtasks[0].id, item)
    assert updated.completed is completed
    assert updated.subtasks[0].completed is completed
    assert updated.subtasks[0].items == (*done.subtasks[0].items, item)
    assert updated.subtasks[1] == done.subtasks[1]


def test_adding_first_item_to_manual_completed_branch_opens_ancestors(task):
    done = set_completed(task, task.id, True)
    updated = add_item(done, done.subtasks[1].id, ChecklistItem(title="Черновик"))
    assert not updated.completed
    assert not updated.subtasks[1].completed
    assert updated.subtasks[0] == done.subtasks[0]


@pytest.mark.parametrize("has_children", [False, True])
@pytest.mark.parametrize("completed", [False, True])
def test_add_first_level_to_completed_task(task, has_children, completed):
    done = (
        set_completed(task, task.id, True) if has_children else Task(title="Работа", completed=True)
    )
    child = Subtask(title="Новый этап", completed=completed)
    updated = add_subtask(done, child)
    assert updated.completed is completed
    assert tuple(branch.id for branch in updated.subtasks) == (
        *(branch.id for branch in done.subtasks),
        child.id,
    )
    assert updated.subtasks[-1].completed is completed
    assert sum(branch.weight for branch in updated.subtasks) == 100


def test_new_branch_uses_children_completion(task):
    child = Subtask(title="Этап", completed=True, items=(ChecklistItem(title="Не готово"),))
    updated = add_subtask(set_completed(task, task.id, True), child)
    assert not updated.completed
    assert not updated.subtasks[-1].completed


def test_invalid_commands_leave_original_unchanged(task):
    with pytest.raises(KeyError):
        set_completed(task, uuid4(), True)
    with pytest.raises(ValueError):
        set_completed(task, task.id, 1)
    with pytest.raises(KeyError):
        add_item(task, task.id, ChecklistItem(title="Не тот уровень"))
    with pytest.raises(ValueError):
        add_item(task, task.subtasks[0].id, task.subtasks[0].items[0])
    with pytest.raises(ValueError):
        add_subtask(task, task.subtasks[0])
    assert not task.completed


def test_service_saves_cascade_and_additions_across_reopen(tmp_path, task):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        store.save_task(task)
        service = TaskService(store)
        done = service.set_completed(task.id, task.id, True)
        assert store.get_task(task.id) == done
    with TaskStore(path) as store:
        service = TaskService(store)
        reopened = service.set_completed(task.id, task.subtasks[0].items[0].id, False)
        assert not reopened.completed
        assert reopened.subtasks[1].completed
        item = ChecklistItem(title="Дополнение")
        updated = service.add_item(task.id, task.subtasks[1].id, item)
        assert not updated.subtasks[1].completed
        child = Subtask(title="Проверка")
        updated = service.add_subtask(task.id, child)
    with TaskStore(path) as store:
        assert store.get_task(task.id) == updated


def test_service_failure_rolls_back_even_after_root_was_written(tmp_path, task):
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        store.save_task(task)
        # Fail while writing descendants, after the root was changed and children deleted.
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TRIGGER fail_save BEFORE INSERT ON nodes WHEN NEW.kind=2
                          BEGIN SELECT RAISE(ABORT,'injected disk write failure'); END""")
        service = TaskService(store)
        with pytest.raises(sqlite3.IntegrityError):
            service.set_completed(task.id, task.id, True)
        assert store.get_task(task.id) == task
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task


def test_invalid_service_commands_and_transform_keep_data(tmp_path, task):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        store.save_task(task)
        service = TaskService(store)
        with pytest.raises(KeyError):
            service.set_completed(uuid4(), task.id, True)
        with pytest.raises(KeyError):
            service.set_completed(task.id, uuid4(), True)
        with pytest.raises(ValueError):
            service.add_subtask(task.id, task.subtasks[0])
        with pytest.raises(ValueError):
            store.update_task(task.id, lambda current: replace(current, id=uuid4()))
        assert store.get_task(task.id) == task
        assert service.set_completed(task.id, task.id, True).completed
