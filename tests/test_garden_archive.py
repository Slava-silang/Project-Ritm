from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from uuid import uuid4

import pytest

from ritm.domain.garden import Garden, GardenItem, GardenState
from ritm.domain.garden_archive import (
    GardenArchive,
    recent_months,
    rotate_month,
    set_favorite,
    view_month,
)


def archive(month="2026-11"):
    terrain = ("grass",) * 99 + ("water",)
    garden = Garden(
        month,
        terrain,
        (
            GardenItem("daisy", uuid4(), 2, 0, 0),
            GardenItem("bench", uuid4(), 0, 6, 6),
            GardenItem("oak", uuid4(), 3),
        ),
        fertilizer=12,
        milestone=True,
    )
    return GardenArchive(GardenState(garden, Fraction(199, 3), 7))


def test_new_month_keeps_property_in_snapshot_and_exact_wallet_only_in_current():
    old = archive()
    new = rotate_month(old, "2026-12")
    assert new.current == GardenState(Garden("2026-12"), Fraction(199, 3), 8)
    assert new.history == (old.current.garden,)
    assert view_month(new, "2026-11") is old.current.garden
    assert old.current.garden.fertilizer == 12
    assert old.current.garden.items[2].stage == 3
    with pytest.raises(FrozenInstanceError):
        view_month(new, "2026-11").fertilizer = 0


@pytest.mark.parametrize("month", ["2026-11", "2026-10", "0001-01"])
def test_repeated_month_or_clock_rewind_never_resets_state(month):
    old = set_favorite(archive(), "2026-11", True)
    assert rotate_month(old, month) is old


@pytest.mark.parametrize(
    "start,end",
    [("2026-12", "2027-01"), ("2028-02", "2028-03"), ("0001-01", "9999-12")],
)
def test_calendar_boundaries_and_long_gaps_do_not_generate_activity(start, end):
    old = archive(start)
    new = rotate_month(old, end)
    assert new.current.garden == Garden(end)
    assert new.current.balance == old.current.balance
    assert new.current.revision == old.current.revision + 1
    assert len(new.history) <= 1
    assert len(recent_months(new)) == 12
    assert rotate_month(new, start) is new


def test_recent_window_is_current_plus_eleven_and_missing_months_are_virtual():
    old = archive("2027-01")
    months = recent_months(old)
    assert len(months) == 12
    assert months[0] == "2027-01" and months[-1] == "2026-02"
    assert view_month(old, "2026-02") == Garden("2026-02")
    assert old.history == () and old.current.revision == 7
    saved = set_favorite(old, "2026-02", True)
    assert saved.history == (Garden("2026-02"),)
    assert saved.favorites == ("2026-02",)
    assert saved.current.balance == old.current.balance
    assert recent_months(archive("0001-01")) == ("0001-01",)


@pytest.mark.parametrize("month", ["2026-01", "2027-02"])
def test_unavailable_month_cannot_be_viewed_or_saved(month):
    old = archive("2027-01")
    with pytest.raises(ValueError):
        view_month(old, month)
    with pytest.raises(ValueError):
        set_favorite(old, month, True)


def test_current_favorite_is_one_snapshot_and_retries_are_noops():
    old = archive()
    marked = set_favorite(old, "2026-11", True)
    assert marked.history == ()
    assert marked.current.garden is old.current.garden
    assert marked.current.revision == old.current.revision + 1
    assert set_favorite(marked, "2026-11", True) is marked
    new = rotate_month(marked, "2026-12")
    assert new.history == (old.current.garden,)
    unmarked = set_favorite(new, "2026-11", False)
    assert unmarked.history == new.history
    assert unmarked.favorites == ()
    assert set_favorite(unmarked, "2026-11", False) is unmarked


def test_old_favorite_survives_expiry_and_unmark_prunes_only_its_snapshot():
    old = set_favorite(archive("2025-01"), "2025-01", True)
    feb = rotate_month(old, "2025-02")
    new = rotate_month(feb, "2026-01")
    assert [g.month for g in new.history] == ["2025-01", "2025-02"]
    next_month = rotate_month(new, "2026-02")
    assert [g.month for g in next_month.history] == ["2025-01", "2026-01"]
    removed = set_favorite(next_month, "2025-01", False)
    assert [g.month for g in removed.history] == ["2026-01"]
    assert removed.current.garden is next_month.current.garden
    assert removed.current.balance == next_month.current.balance
    with pytest.raises(ValueError):
        view_month(removed, "2025-01")


def test_twenty_favorites_include_current_and_never_evict_silently():
    state = archive("2025-01")
    for year, month in [(2025, m) for m in range(1, 13)] + [(2026, m) for m in range(1, 9)]:
        name = f"{year:04}-{month:02}"
        state = set_favorite(rotate_month(state, name), name, True)
    assert len(state.favorites) == 20
    assert state.current.garden.month in state.favorites
    assert len(state.history) == 19
    september = rotate_month(state, "2026-09")
    with pytest.raises(ValueError, match="20"):
        set_favorite(september, "2026-09", True)
    assert september.favorites == state.favorites
    freed = set_favorite(september, "2025-01", False)
    saved = set_favorite(freed, "2026-09", True)
    assert len(saved.favorites) == 20
    assert "2025-01" not in saved.favorites and "2026-09" in saved.favorites
    assert saved.current.balance == state.current.balance


def test_ten_years_retention_is_bounded_without_touching_money():
    state = archive("2020-01")
    for year in range(2020, 2030):
        for month in range(1, 13):
            name = f"{year:04}-{month:02}"
            state = rotate_month(state, name)
            if month == 1:
                state = set_favorite(state, name, True)
    assert len(state.history) == 20  # 11 recent + 9 older January favorites
    assert len(state.favorites) == 10
    assert state.current.balance == Fraction(199, 3)
    assert state.current.revision == 7 + 119 + 10
    assert view_month(state, "2020-01").fertilizer == 12
    assert state.current.garden == Garden("2029-12")


@pytest.mark.parametrize(
    "month",
    [
        None,
        202611,
        "2026-1",
        "2026-00",
        "2026-13",
        "0000-01",
        "10000-01",
        "２０２６-１１",
        "2026-11\n",
        " 2026-11",
        "2026-11-01",
    ],
)
def test_all_public_month_operations_reject_noncanonical_dates(month):
    old = archive()
    for operation in (rotate_month, view_month):
        with pytest.raises(ValueError):
            operation(old, month)
    with pytest.raises(ValueError):
        set_favorite(old, month, True)


@pytest.mark.parametrize("enabled", [0, 1, None, "yes"])
def test_favorite_flag_must_be_boolean(enabled):
    with pytest.raises(ValueError):
        set_favorite(archive(), "2026-11", enabled)


@pytest.mark.parametrize(
    "changes",
    [
        {"current": Garden("2026-11")},
        {"history": []},
        {"history": (None,)},
        {"history": (Garden("2026-10"), Garden("2026-10"))},
        {"history": (Garden("2026-10"), Garden("2026-09"))},
        {"history": (Garden("2026-11"),)},
        {"history": (Garden("2026-12"),)},
        {"history": (Garden("2025-11"),)},
        {"favorites": []},
        {"favorites": (None,)},
        {"favorites": ("2026-11", "2026-11")},
        {"favorites": ("2026-10",)},
        {"favorites": ("2026-12",)},
        {"favorites": ("2026-11",) * 21},
    ],
)
def test_invalid_loaded_archive_is_rejected(changes):
    with pytest.raises(ValueError):
        replace(archive(), **changes)
