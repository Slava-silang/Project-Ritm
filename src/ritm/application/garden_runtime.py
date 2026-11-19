"""Подключение сада в storage-worker; без Qt, фоновых потоков и своих таймеров."""

from decimal import Decimal
from zoneinfo import ZoneInfo

from ritm.application.garden_events import GardenEventService
from ritm.domain.garden_achievements import HOUR, AchievementState
from ritm.storage.garden import GardenStore


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


def sync_garden(source, profile, now, *, zone, enabled=None, checkpoint=False):
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
        return {
            **source.garden_source_status(),
            "error": "",
            "balance": format(
                Decimal(state.balance.numerator) / Decimal(state.balance.denominator), ".2f"
            ),
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
