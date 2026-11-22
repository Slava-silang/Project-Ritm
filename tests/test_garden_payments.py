import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from functools import partial
from uuid import UUID, uuid4

import pytest
from test_garden_events import MINUTE, NOW
from test_garden_events import pair as pair

from ritm.application.garden_events import GardenEventService, apply_event
from ritm.application.tasks import TaskService
from ritm.domain.tasks import ChecklistItem, Subtask, Task
from ritm.storage.garden import GardenStore


def work(source, task, now, minutes):
    start = now[0]
    now[0] += timedelta(minutes=minutes)
    source.add_focus_record(task.id, start, now[0], now[0])


def complete(source, task, node=None, value=True):
    TaskService(source).set_completed(task.id, node or task.id, value)


def payments(garden):
    return garden.source_snapshot()["payments"]


def test_two_hours_and_default_complexity_give_exactly_240_plus_24(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    work(source, task, now, 120)
    complete(source, task)
    service.drain()
    assert garden.snapshot().balance == 264
    record = payments(garden)["claims"][str(task.id)][str(task.id)]
    assert record == {"weight": "100.00", "difficulty": 5, "base": "240", "amount": "24"}
    complete(source, task, value=False)
    complete(source, task)
    service.drain()
    assert garden.snapshot().balance == 264


def test_shared_five_minutes_keep_two_root_shares(pair):
    source, garden, service, task, now = pair
    other = Task(title="Вторая")
    TaskService(source).create_task(other)
    service.begin(NOW)
    work(source, task, now, 2)
    service.drain()
    assert payments(garden)["pending"] == [[str(task.id), 2 * MINUTE]]
    work(source, other, now, 3)
    service.drain()
    assert payments(garden)["paid"] == {str(task.id): "4", str(other.id): "6"}
    assert garden.snapshot().balance == 10 and payments(garden)["pending"] == []


def test_branch_60_40_bonus_and_second_level_only_after_all_items(pair):
    source, garden, service, task, now = pair
    first = Subtask(
        title="Первая",
        weight=Decimal(60),
        items=(ChecklistItem(title="a"), ChecklistItem(title="b")),
    )
    second = Subtask(title="Вторая", weight=Decimal(40))
    task = replace(task, subtasks=(first, second))
    source.save_task(task)
    service.begin(NOW)
    work(source, task, now, 120)
    complete(source, task, first.items[0].id)
    service.drain()
    assert garden.snapshot().balance == 240
    complete(source, task, first.items[1].id)
    service.drain()
    assert garden.snapshot().balance == Fraction(1272, 5)
    assert payments(garden)["claims"][str(task.id)][str(first.id)]["amount"] == "72/5"
    complete(source, task, second.id)
    service.drain()
    assert garden.snapshot().balance == 264
    assert str(task.id) not in payments(garden)["claims"][str(task.id)]


def test_minimum_fractions_sum_to_five_without_rounding_up(pair):
    source, garden, service, task, now = pair
    children = tuple(Subtask(title=str(i)) for i in range(3))
    task = TaskService(source).add_subtask(task.id, children[0])
    TaskService(source).add_subtask(task.id, children[1])
    TaskService(source).add_subtask(task.id, children[2])
    service.begin(NOW)
    for child in children:
        complete(source, task, child.id)
    service.drain()
    claims = payments(garden)["claims"][str(task.id)]
    assert garden.snapshot().balance == 5
    assert sum(Fraction(c["amount"]) for c in claims.values()) == 5
    assert len(claims) == 3


def test_new_time_does_not_recalculate_already_claimed_minimum(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    complete(source, task)
    service.drain()
    work(source, task, now, 120)
    complete(source, task, value=False)
    complete(source, task)
    service.drain()
    assert garden.snapshot().balance == 245
    assert payments(garden)["claims"][str(task.id)][str(task.id)]["amount"] == "5"


def test_changed_weights_and_complexity_affect_only_new_capped_claims(pair):
    source, garden, service, task, now = pair
    a, b = Subtask(title="a", weight=Decimal(60)), Subtask(title="b", weight=Decimal(40))
    source.save_task(replace(task, difficulty=5, subtasks=(a, b)))
    service.begin(NOW)
    complete(source, task, a.id)
    service.drain()
    before = payments(garden)["claims"][str(task.id)][str(a.id)]
    source.update_task(task.id, lambda t: replace(t, difficulty=10))
    TaskService(source).set_weights(task.id, {a.id: Decimal(20), b.id: Decimal(80)})
    complete(source, task, b.id)
    complete(source, task, a.id, False)
    complete(source, task, a.id)
    service.drain()
    claims = payments(garden)["claims"][str(task.id)]
    assert claims[str(a.id)] == before
    assert claims[str(b.id)] == {"weight": "40.00", "difficulty": 10, "base": "0", "amount": "4"}
    assert garden.snapshot().balance == 7


def test_tree_changes_cannot_spend_same_root_share_twice(pair):
    source, garden, service, task, now = pair
    a, b = Subtask(title="a"), Subtask(title="b")
    TaskService(source).add_subtask(task.id, a)
    TaskService(source).add_subtask(task.id, b)
    service.begin(NOW)
    complete(source, task, a.id)
    TaskService(source).delete_node(task.id, a.id, now[0])
    complete(source, task, b.id)
    c = Subtask(title="c")
    TaskService(source).add_subtask(task.id, c)
    complete(source, task, c.id)
    service.drain()
    assert garden.snapshot().balance == 5
    claims = payments(garden)["claims"][str(task.id)]
    assert sum(Fraction(c["weight"]) for c in claims.values()) == 100
    assert claims[str(c.id)]["weight"] == "0.00"


def test_paid_root_then_new_children_cannot_get_another_bonus(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    complete(source, task)
    child = Subtask(title="Добавлен позже")
    TaskService(source).add_subtask(task.id, child)
    complete(source, task, child.id)
    service.drain()
    assert garden.snapshot().balance == 5


def test_zero_weight_is_recorded_and_not_repaid_after_weight_change(pair):
    source, garden, service, task, now = pair
    a, b = Subtask(title="a", weight=Decimal(0)), Subtask(title="b", weight=Decimal(100))
    source.save_task(replace(task, subtasks=(a, b)))
    service.begin(NOW)
    complete(source, task, a.id)
    TaskService(source).set_weights(task.id, {a.id: Decimal(100), b.id: Decimal(0)})
    complete(source, task, a.id, False)
    complete(source, task, a.id)
    service.drain()
    assert garden.snapshot().balance == 0
    assert payments(garden)["claims"][str(task.id)][str(a.id)]["weight"] == "0.00"


def test_baseline_disabled_completion_and_restore_do_not_award_retroactively(pair):
    source, garden, service, task, now = pair
    complete(source, task)
    service.begin(NOW)
    source.update_task(task.id, lambda t: replace(t, title="Переименовано"))
    identifier = TaskService(source).delete_node(task.id, task.id, now[0])
    TaskService(source).restore(UUID(identifier))
    service.drain()
    assert garden.snapshot().balance == 0
    source.set_garden_source_enabled(False, now[0])
    complete(source, task, value=False)
    complete(source, task)
    source.set_garden_source_enabled(True, now[0])
    service.drain()
    assert garden.snapshot().balance == 0
    complete(source, task, value=False)
    complete(source, task)
    service.drain()
    assert garden.snapshot().balance == 5


def test_day_cap_and_correction_debt_do_not_fund_another_root_twice(pair):
    source, garden, service, task, now = pair
    other = Task(title="Другая")
    TaskService(source).create_task(other)
    service.begin(NOW)
    work(source, task, now, 7 * 60)
    work(source, other, now, 7 * 60)
    service.drain()
    assert payments(garden)["paid"] == {str(task.id): "840", str(other.id): "600"}
    assert garden.snapshot().balance == 1440
    complete(source, task)
    complete(source, other)
    service.drain()
    assert garden.snapshot().balance == 1584


def test_debt_repayment_is_not_counted_as_paid_base_of_new_root(pair):
    source, garden, service, task, now = pair
    other = Task(title="Другая")
    TaskService(source).create_task(other)
    service.begin(NOW)
    work(source, task, now, 120)
    service.drain()
    session = source.list_focus_sessions()[0]
    source.edit_focus_duration(session.id, session.revision, 60 * MINUTE, now[0])
    work(source, other, now, 65)
    service.drain()
    assert payments(garden)["paid"] == {str(task.id): "240", str(other.id): "10"}
    assert garden.snapshot().balance == 250


def test_correcting_unpaid_remainder_preserves_other_root_share(pair):
    source, garden, service, task, now = pair
    other = Task(title="Другая")
    TaskService(source).create_task(other)
    service.begin(NOW)
    work(source, task, now, 4)
    service.drain()
    session = source.list_focus_sessions()[0]
    source.edit_focus_duration(session.id, session.revision, MINUTE, now[0])
    work(source, other, now, 4)
    service.drain()
    assert payments(garden)["paid"] == {str(task.id): "2", str(other.id): "8"}


def test_failed_claim_receipt_rolls_back_bonus_claim_and_cursor_then_retry_once(pair):
    source, garden, service, task, now = pair
    service.begin(NOW)
    work(source, task, now, 120)
    service.drain()
    complete(source, task)
    before, cursor = garden.snapshot(), garden.source_snapshot()
    garden._db.execute(
        "CREATE TRIGGER fail_bonus BEFORE INSERT ON garden_receipts "
        "BEGIN SELECT RAISE(ABORT,'fake'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        service.drain()
    assert garden.snapshot() == before and garden.source_snapshot() == cursor
    garden._db.execute("DROP TRIGGER fail_bonus")
    service.drain()
    assert garden.snapshot().balance == 264 and service.drain() == 0


def test_new_process_preserves_paid_claims_and_no_bonus_on_recompletion(pair, tmp_path):
    source, garden, service, task, now = pair
    service.begin(NOW)
    work(source, task, now, 120)
    complete(source, task)
    service.drain()
    code = """from pathlib import Path
from uuid import UUID
import sys
from ritm.storage.sqlite import TaskStore
from ritm.storage.garden import GardenStore
from ritm.application.tasks import TaskService
from ritm.application.garden_events import GardenEventService
p=Path(sys.argv[1]); root=UUID(sys.argv[2])
with TaskStore(p/'tasks.sqlite3') as s, GardenStore(p/'garden.sqlite3') as g:
    TaskService(s).set_completed(root, root, False)
    TaskService(s).set_completed(root, root, True)
    GardenEventService(s,g).drain()
    assert g.snapshot().balance == 264
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), str(task.id)],
        cwd=tmp_path,
        env={**os.environ},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def legacy_v3(pair, tmp_path, *, spend=False):
    source, garden, service, task, now = pair
    source.begin_garden_source(NOW)
    work(source, task, now, 120)
    complete(source, task)
    identity, events = source.garden_events_after()
    for event in events:
        garden.apply_source_event(
            identity,
            event,
            zone="UTC",
            expected_revision=garden.snapshot().revision,
            transition=partial(apply_event, completion_awards=False),
        )
    if spend:
        garden.execute(
            uuid4(),
            {"action": "buy", "kind": "daisy"},
            expected_revision=garden.snapshot().revision,
        )
    garden.close()
    path = tmp_path / "garden.sqlite3"
    with sqlite3.connect(path) as db:
        payload = json.loads(db.execute("SELECT payload FROM garden_source_state").fetchone()[0])
        del payload["payments"]
        db.execute("UPDATE garden_source_state SET payload=?", (json.dumps(payload),))
        db.execute("DROP TABLE garden_favorites")
        db.execute("DROP TABLE garden_source_binding")
        db.execute("DROP TABLE garden_achievement_state")
        db.execute("PRAGMA user_version=3")
    return source, path, task


def test_v3_backfill_verifies_history_preserves_money_and_does_not_pay_old_completions(
    pair, tmp_path
):
    source, path, task = legacy_v3(pair, tmp_path)
    with GardenStore(path) as garden:
        assert payments(garden) is None and garden.snapshot().balance == 240
        service = GardenEventService(source, garden)
        assert service.drain() == 0
        assert payments(garden)["paid"] == {str(task.id): "240"}
        assert payments(garden)["claims"] == {} and garden.snapshot().balance == 240
        complete(source, task, value=False)
        complete(source, task)
        service.drain()
        assert garden.snapshot().balance == 264


@pytest.mark.parametrize("failure", ["receipt", "tampered", "zone", "write"])
def test_v3_backfill_refuses_missing_proof_and_rolls_back_on_write_failure(pair, tmp_path, failure):
    source, path, task = legacy_v3(pair, tmp_path)
    with GardenStore(path) as garden:
        if failure == "receipt":
            with garden._db:
                garden._db.execute("DELETE FROM garden_receipts WHERE key LIKE 'source:%:2'")
        elif failure == "tampered":
            with source._db:
                source._db.execute("UPDATE garden_events SET happened_us=happened_us+1 WHERE seq=2")
        elif failure == "write":
            garden._db.execute(
                "CREATE TRIGGER fail_backfill BEFORE INSERT ON garden_receipts "
                "BEGIN SELECT RAISE(ABORT,'fake'); END"
            )
        before, cursor = garden.snapshot(), garden.source_snapshot()
        with pytest.raises((ValueError, sqlite3.IntegrityError)):
            GardenEventService(
                source, garden, zone="Europe/Moscow" if failure == "zone" else "UTC"
            ).drain()
        assert garden.snapshot() == before and garden.source_snapshot() == cursor
        if failure == "write":
            garden._db.execute("DROP TRIGGER fail_backfill")
            GardenEventService(source, garden).drain()
            assert garden.snapshot().balance == 240 and payments(garden) is not None


@pytest.mark.parametrize("field", ["observed", "pending", "paid", "amount", "weight", "base"])
def test_corrupt_payment_provenance_is_rejected_without_reset(pair, tmp_path, field):
    source, garden, service, task, now = pair
    service.begin(NOW)
    work(source, task, now, 120)
    complete(source, task)
    service.drain()
    garden.close()
    path = tmp_path / "garden.sqlite3"
    with sqlite3.connect(path) as db:
        state = json.loads(db.execute("SELECT payload FROM garden_source_state").fetchone()[0])
        p = state["payments"]
        if field == "observed":
            p[field][str(task.id)] += 1
        elif field == "pending":
            p[field].append([str(task.id), 1])
        elif field == "paid":
            p[field][str(task.id)] = "241"
        else:
            record = p["claims"][str(task.id)][str(task.id)]
            record[field] = {"amount": "25", "weight": "101.00", "base": "999"}[field]
        db.execute("UPDATE garden_source_state SET payload=?", (json.dumps(state),))
    original = path.read_bytes()
    with pytest.raises(ValueError):
        GardenStore(path)
    assert path.read_bytes() == original


def test_month_boundary_and_new_process_keep_pending_root_ownership(pair, tmp_path):
    source, garden, service, task, now = pair
    other = Task(title="Следующий месяц")
    TaskService(source).create_task(other)
    service.begin(NOW)
    now[0] = datetime(2026, 11, 30, 23, 58, tzinfo=UTC)
    work(source, task, now, 2)
    service.drain()
    work(source, other, now, 3)
    code = """from pathlib import Path
import sys
from ritm.storage.sqlite import TaskStore
from ritm.storage.garden import GardenStore
from ritm.application.garden_events import GardenEventService
p=Path(sys.argv[1])
with TaskStore(p/'tasks.sqlite3') as s, GardenStore(p/'garden.sqlite3') as g:
    GardenEventService(s,g).drain()
    assert g.snapshot().balance==10
    paid=g.source_snapshot()['payments']['paid']
    assert paid=={sys.argv[2]:'4',sys.argv[3]:'6'}
    assert len(g.reward_snapshot().days)==2
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), str(task.id), str(other.id)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_v3_backfill_preserves_spent_coins_and_bought_property(pair, tmp_path):
    source, path, task = legacy_v3(pair, tmp_path, spend=True)
    with GardenStore(path) as garden:
        before = garden.snapshot()
        GardenEventService(source, garden).drain()
        assert garden.snapshot().balance == before.balance == 160
        assert garden.snapshot().garden.items == before.garden.items
        assert payments(garden)["paid"][str(task.id)] == "240"


def test_v3_backfill_rejects_callback_which_retroactively_claims_bonus(pair, tmp_path):
    source, path, task = legacy_v3(pair, tmp_path)
    with GardenStore(path) as garden:
        before, cursor = garden.snapshot(), garden.source_snapshot()
        with pytest.raises(ValueError, match="прежние надбавки"):
            garden.rebuild_source_payments(
                source.garden_events_after()[1],
                zone="UTC",
                expected_revision=before.revision,
                transition=apply_event,
            )
        assert garden.snapshot() == before and garden.source_snapshot() == cursor
