"""Открытия через реальные события для проверок оформления, без правки кошелька."""

from datetime import UTC, datetime, timedelta

from ritm.application.garden_runtime import sync_garden
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage.sqlite import TaskStore


def unlock_themes(profile):
    now = datetime.now(UTC) - timedelta(days=5)
    with TaskStore(profile / "tasks.sqlite3", clock=lambda: now) as store:
        service = TaskService(store)
        sync_garden(store, profile, now, zone="UTC", enabled=True)
        for i in range(15):
            task = service.create_task(Task(title=f"Завершённая проверочная задача {i}"))
            service.set_completed(task.id, task.id, True)
        for _ in range(3):
            start = now
            now += timedelta(hours=10)
            store.add_focus_record(task.id, start, now, now)
            now += timedelta(hours=14)
        view = sync_garden(store, profile, now, zone="UTC")
        assert set(view["themes"]) == {"light", "dark", "space", "jungle"}
