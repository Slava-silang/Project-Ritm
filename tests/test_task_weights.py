from dataclasses import replace
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest

from ritm.application.tasks import TaskService
from ritm.domain.completion import add_subtask, remove_subtask, set_completed
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.domain.weights import equal_weights, initialize_weights, progress, set_weights
from ritm.storage.sqlite import TaskStore


def make_task(count):
    return Task(title="Работа", subtasks=tuple(Subtask(title=f"Этап {i}") for i in range(count)))


def values(task):
    return tuple(child.weight for child in task.subtasks)


@pytest.mark.parametrize("count", [0, 1, 2, 3, 7, 10001])
def test_equal_weights_are_exact_and_stable(count):
    task = make_task(count)
    updated = equal_weights(task)
    assert sum(values(updated)) == (100 if count else 0)
    assert all(value >= 0 and value.as_tuple().exponent == -2 for value in values(updated))
    if count:
        assert max(values(updated)) - min(values(updated)) <= Decimal("0.01")
    assert equal_weights(updated) == updated
    assert tuple(child.id for child in updated.subtasks) == tuple(c.id for c in task.subtasks)
    assert all(child.weight is None for child in task.subtasks)


def test_rounding_uses_order_and_is_independent_of_decimal_context():
    task = make_task(3)
    with localcontext() as ctx:
        ctx.prec = 2
        updated = equal_weights(task)
        assert values(updated) == (Decimal("33.34"), Decimal("33.33"), Decimal("33.33"))
        done = set_completed(updated, updated.id, True)
        assert progress(done) == Decimal("100.00")
        assert initialize_weights(done) == done


def test_manual_weights_match_ids_and_allow_reset():
    task = make_task(3)
    a, b, c = task.subtasks
    updated = set_weights(task, {c.id: Decimal(50), a.id: Decimal(20), b.id: Decimal(30)})
    assert values(updated) == (Decimal("20.00"), Decimal("30.00"), Decimal("50.00"))
    assert values(equal_weights(updated)) == (Decimal("33.34"), Decimal("33.33"), Decimal("33.33"))


@pytest.mark.parametrize(
    "bad",
    [
        Decimal(0),
        Decimal("99.99"),
        Decimal("100.01"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("99.999"),
        Decimal("1e-999999999"),
        100,
        100.0,
        True,
        None,
    ],
)
def test_invalid_manual_weight_is_rejected(bad):
    task = make_task(1)
    with pytest.raises(ValueError):
        set_weights(task, {task.subtasks[0].id: bad})


def test_manual_weights_require_exact_sibling_set():
    task = make_task(2)
    a, b = task.subtasks
    for mapping in ({a.id: Decimal(100)}, {a.id: Decimal(50), uuid4(): Decimal(50)}):
        with pytest.raises(ValueError):
            set_weights(task, mapping)
    with pytest.raises(ValueError):
        initialize_weights(replace(task, subtasks=(replace(a, weight=Decimal(100)), b)))
    empty = make_task(0)
    assert set_weights(empty, {}) == empty


def test_adding_preserves_proportions_and_assigns_new_share():
    task = make_task(2)
    a, b = task.subtasks
    task = set_weights(task, {a.id: Decimal(20), b.id: Decimal(80)})
    child = Subtask(title="Новое", weight=Decimal(99))
    updated = add_subtask(task, child)
    assert values(updated) == (Decimal("13.34"), Decimal("53.33"), Decimal("33.33"))
    assert values(task) == (Decimal(20), Decimal(80))
    assert updated.subtasks[-1].id == child.id
    assert values(remove_subtask(updated, child.id)) == (Decimal("20.01"), Decimal("79.99"))


def test_removing_preserves_proportions_and_completes_remaining_branches():
    task = make_task(3)
    a, b, c = task.subtasks
    task = set_weights(task, {a.id: Decimal(20), b.id: Decimal(30), c.id: Decimal(50)})
    task = set_completed(set_completed(task, a.id, True), b.id, True)
    updated = remove_subtask(task, c.id)
    assert values(updated) == (Decimal(40), Decimal(60))
    assert updated.completed
    assert progress(updated) == 100


def test_zero_weights_and_all_zero_survivors():
    task = make_task(3)
    a, b, c = task.subtasks
    task = set_weights(task, {a.id: Decimal(100), b.id: Decimal(0), c.id: Decimal(0)})
    done_weight = set_completed(task, a.id, True)
    assert progress(done_weight) == 100
    assert not done_weight.completed
    assert values(remove_subtask(task, a.id)) == (Decimal(50), Decimal(50))
    updated = add_subtask(task, Subtask(title="Новый"))
    assert values(updated) == (Decimal(75), Decimal(0), Decimal(0), Decimal(25))


@pytest.mark.parametrize("completed", [False, True])
def test_removing_last_branch_preserves_manual_completion(completed):
    task = make_task(1)
    task = set_completed(task, task.id, completed)
    updated = remove_subtask(task, task.subtasks[0].id)
    assert updated.subtasks == ()
    assert updated.completed is completed
    assert progress(updated) == (100 if completed else 0)
    with pytest.raises(KeyError):
        remove_subtask(updated, uuid4())


def test_progress_only_counts_fully_completed_branch():
    items = tuple(ChecklistItem(title=str(i)) for i in range(3))
    task = Task(
        title="Лабораторная",
        subtasks=(
            Subtask(title="Расчёты", weight=Decimal(40), items=items),
            Subtask(title="Отчёт", weight=Decimal(60)),
        ),
    )
    assert progress(task) == 0
    for item in items[:2]:
        task = set_completed(task, item.id, True)
        assert progress(task) == 0
    task = set_completed(task, items[-1].id, True)
    assert progress(task) == 40
    task = set_completed(task, task.subtasks[1].id, True)
    assert progress(task) == 100
    assert progress(set_completed(task, items[0].id, False)) == 60


def test_legacy_unassigned_weights_are_initialized_without_mutating_input():
    task = make_task(2)
    task = set_completed(task, task.subtasks[0].id, True)
    assert progress(task) == 50
    assert values(task) == (None, None)
    assert values(add_subtask(task, Subtask(title="Новый"))) == (
        Decimal("33.34"),
        Decimal("33.33"),
        Decimal("33.33"),
    )


def test_service_persists_weights_progress_and_rejects_invalid_change(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    task = make_task(3)
    a, b, c = task.subtasks
    with TaskStore(path) as store:
        store.save_task(task)
        service = TaskService(store)
        task = service.set_weights(
            task.id, {a.id: Decimal(20), b.id: Decimal(30), c.id: Decimal(50)}
        )
        task = service.set_completed(task.id, a.id, True)
        assert progress(task) == 20
        with pytest.raises(ValueError):
            service.set_weights(task.id, {a.id: Decimal(10), b.id: Decimal(30), c.id: Decimal(50)})
        assert store.get_task(task.id) == task
        child = Subtask(title="Четвёртый")
        task = service.add_subtask(task.id, child)
        assert values(task) == (Decimal(15), Decimal("22.50"), Decimal("37.50"), Decimal(25))
        task = service.remove_subtask(task.id, child.id)
        assert values(task) == (Decimal(20), Decimal(30), Decimal(50))
        task = service.equal_weights(task.id)
        assert progress(task) == Decimal("33.34")
    with TaskStore(path) as store:
        assert store.get_task(task.id) == task
        assert progress(store.get_task(task.id)) == Decimal("33.34")
