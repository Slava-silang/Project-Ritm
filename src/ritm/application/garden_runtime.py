"""Подключение сада в storage-worker; без Qt, фоновых потоков и своих таймеров."""

from decimal import Decimal
from zoneinfo import ZoneInfo

from ritm.application.garden_events import GardenEventService
from ritm.domain.garden_achievements import HOUR, AchievementState
from ritm.storage.garden import GardenStore


def coin_text(amount):
    # Display does not round an unaffordable exact balance up to a shop price.
    cents = amount.numerator * 100 // amount.denominator
    return f"{cents // 100}.{cents % 100:02}"


def empty_view():
    return {
        "subscribed": False,
        "enabled": False,
        "error": "",
        "balance": "0",
        "roots": 0,
        "paidHours": "0.00",
        "themes": list(AchievementState().themes),
        "months": [],
        "welcomed": False,
        "firstTask": False,
    }


def sync_garden(source, profile, now, *, zone, enabled=None, checkpoint=False, with_state=False):
    """Не подменяет ошибки пустым садом. Соединения принадлежат вызывающему worker."""
    status = source.garden_source_status()
    path = profile / "garden.sqlite3"
    if enabled is not None and type(enabled) is not bool:
        raise ValueError("Выберите настройку сада.")
    if not status["subscribed"] and enabled is not True:
        return empty_view()
    if status["subscribed"] and not path.is_file():
        raise ValueError("База сада отсутствует. Восстановите парную копию; новый сад не создан.")
    with GardenStore(
        path,
        **({} if path.exists() else {"month": now.astimezone(ZoneInfo(zone)).strftime("%Y-%m")}),
    ) as garden:
        service = GardenEventService(source, garden, zone=zone, award_achievements=True)
        if not status["subscribed"]:
            service.begin(now)
        else:
            # Validate the pair and deliver pending work before changing its setting.
            service.drain()
            if checkpoint and status["enabled"]:
                source.checkpoint_garden_focus(now)
            if enabled is not None and enabled != status["enabled"]:
                source.set_garden_source_enabled(enabled, now)
        service.drain()
        state, achievements = garden.snapshot(), garden.achievement_snapshot()
        view = {
            **source.garden_source_status(),
            "error": "",
            "balance": coin_text(state.balance),
            "roots": len(achievements.roots),
            "paidHours": format(Decimal(achievements.paid_us) / HOUR, ".2f"),
            "themes": list(achievements.themes),
            "months": [
                {
                    "month": m.month,
                    "roots": m.roots,
                    "paidHours": format(Decimal(m.paid_us) / HOUR, ".2f"),
                    "claimed": list(m.claimed),
                }
                for m in achievements.months
            ],
            "welcomed": achievements.welcomed,
            "firstTask": achievements.first_task,
        }
        if with_state:
            view["_state"] = state
            view["_identity"] = garden.source_snapshot()["identity"]
        return view
