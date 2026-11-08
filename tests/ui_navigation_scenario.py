"""T09: фильтры, категории, узкое окно, корзина и восстановление в новом процессе."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle

from ritm.app import load_window
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
profile = Path(sys.argv[1])
now = [datetime(2026, 11, 8, 12, tzinfo=UTC)]
restoring = "--restore" in sys.argv
if not restoring:
    study = Category(title="Учёба")
    personal = Category(title="Личное")
    physics = Task(
        title="Физика",
        description="Эксперимент",
        category_id=study.id,
        difficulty=7,
        expected_duration=timedelta(minutes=90),
        deadline=now[0] - timedelta(days=1),
        subtasks=(
            Subtask(title="Расчёты", items=(ChecklistItem(title="Формулы"),)),
            Subtask(title="Отчёт", completed=True),
        ),
    )
    with TaskStore(profile / "tasks.sqlite3") as store:
        service = TaskService(store)
        service.create_task(physics, category=study)
        service.create_task(Task(title="Сдать форму", deadline=now[0], category_id=study.id))
        service.create_task(
            Task(title="Курсовая", deadline=now[0] + timedelta(days=1), category_id=study.id)
        )
        service.create_task(Task(title="Купить тетрадь"))
        service.create_task(
            Task(title="Решено", completed=True, category_id=personal.id), category=personal
        )
    physics_id = str(physics.id)
else:
    physics_id = sys.argv[-1]
controller = TaskController(profile, clock=lambda: now[0])
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)

try:
    gui.idle()
    assert not controller.error
    if restoring:
        assert len(controller.trash) == 1
        assert not any(row["id"] == physics_id for row in controller.tasks)
        gui.click("trashButton")
        gui.click("restore_0")
        assert not controller.error
        gui.click("closeTrashButton")
        assert controller.detail["id"] == physics_id
        assert controller.detail["category"] == "Учёба"
        assert len(controller.detail["subtasks"]) == 2
        assert len(controller.detail["subtasks"][0]["items"]) == 1
        assert controller.detail["difficulty"] == "7"
        assert not controller.trash
        print(json.dumps({"restored": True}))
        raise SystemExit(0)
    assert [x["group"] for x in controller.tasks] == [
        "Просроченные",
        "Предстоящие",
        "Предстоящие",
        "Без дедлайна",
    ]
    now[0] += timedelta(microseconds=1)
    gui.wait_for(lambda: controller.tasks[1]["overdue"], "deadline timer")
    gui.fill("searchInput", " ФОРМУЛЫ ")
    assert len(controller.tasks) == 1
    gui.fill("searchInput", "")
    gui.choose("statusFilter", 1)
    assert [x["title"] for x in controller.tasks] == ["Решено"]
    gui.click("taskComplete")
    assert not controller.detail["completed"]
    assert not controller.tasks
    gui.choose("statusFilter", 2)
    assert len(controller.tasks) == 5
    gui.choose("statusFilter", 0)
    gui.choose("categoryFilter", 1)
    assert [x["title"] for x in controller.tasks] == ["Купить тетрадь"]
    gui.choose("categoryFilter", 2)
    assert len(controller.tasks) == 3
    gui.choose("categoryFilter", 0)
    gui.click("newTaskButton")
    gui.fill("titleInput", "Эссе")
    gui.item("formCategory").setProperty("editText", "Гуманитарные")
    gui.click("saveTaskButton")
    assert not controller.error
    assert controller.detail["category"] == "Гуманитарные"
    assert len(controller.categories) == 3
    gui.click("taskRow_0")
    assert controller.detail["id"] == physics_id
    gui.click("expandTask_0")
    assert controller.tasks[0]["expanded"]
    gui.screenshot(profile / "navigation-wide.png")
    window.resize(640, 480)
    gui.wait_for(lambda: not gui.item("listPanel").isVisible(), "narrow window layout")
    assert gui.item("detailPanel").isVisible()
    gui.screenshot(profile / "navigation-narrow-card.png")
    gui.click("backToListButton")
    assert gui.item("listPanel").isVisible()
    assert not gui.item("detailPanel").isVisible()
    gui.screenshot(profile / "navigation-narrow-list.png")
    gui.click("taskRow_0")
    window.resize(1120, 780)
    app.processEvents()
    gui.click("deleteTaskButton")
    gui.click("cancelDeleteButton")
    assert not controller.trash
    gui.click("deleteItem_0_0")
    gui.click("confirmDeleteButton")
    assert len(controller.trash) == 1
    assert not controller.detail["subtasks"][0]["items"]
    gui.click("trashButton")
    gui.click("restore_0")
    gui.click("closeTrashButton")
    assert len(controller.detail["subtasks"][0]["items"]) == 1
    gui.click("deleteBranch_0")
    gui.click("confirmDeleteButton")
    assert len(controller.detail["subtasks"]) == 1
    assert controller.detail["subtasks"][0]["weight"] == "100,00"
    gui.click("trashButton")
    gui.click("restore_0")
    gui.click("closeTrashButton")
    assert [x["weight"] for x in controller.detail["subtasks"]] == ["50,00", "50,00"]
    gui.click("deleteTaskButton")
    gui.click("confirmDeleteButton")
    assert len(controller.trash) == 1
    assert not any(row["id"] == physics_id for row in controller.tasks)
    gui.click("trashButton")
    gui.screenshot(profile / "navigation-trash.png")
    gui.click("closeTrashButton")
    print(json.dumps({"ok": True, "id": physics_id, "platform": app.platformName()}))
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
