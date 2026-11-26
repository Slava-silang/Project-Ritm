"""Реальные QML-контролы планирования на временном профиле."""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event

from garden_support import unlock_themes
from gui_support import GuiHarness, load_offscreen_fonts
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ritm.app import load_window
from ritm.domain.calendar import WorkBlock
from ritm.domain.tasks import Task
from ritm.integrations.assistant import MockAdapter
from ritm.storage.sqlite import TaskStore
from ritm.storage.working_hours import DEFAULT, save_hours
from ritm.ui.assistant import AssistantController
from ritm.ui.planning import PlanningController
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
load_offscreen_fonts(app)
if mode in ("run", "scroll-layout"):
    with TaskStore(profile / "tasks.sqlite3") as store:
        start = datetime(2030, 1, 7, 9).astimezone()
        task = Task(
            title="Лабораторная по физике",
            expected_duration=timedelta(hours=10),
            deadline=start + timedelta(hours=4),
        )
        store.create_task(task)
        store.create_work_block(
            WorkBlock(title="Пара", starts_at=start, ends_at=start + timedelta(hours=1))
        )
if mode == "scroll-layout":
    unlock_themes(profile)
agent = AssistantController(profile)
controller = TaskController(profile)
planner = PlanningController(profile, agent)
engine = load_window(controller, assistant=agent, planner=planner)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def ready():
    gui.wait_for(lambda: not planner.state["busy"])


def check_scrollbar(name, scale, *controls):
    bar = gui.item(name)
    assert 0 < bar.property("size") < 1, name
    bar.setProperty("active", True)
    left = bar.mapToScene(QPointF()).x()
    for control in controls:
        item = gui.item(control)
        right = item.mapToScene(QPointF(item.width(), 0)).x()
        assert left - right >= 5 * scale, (name, control, left, right)
    return bar


def check_scroll_layout():
    for width, height, percent in ((1480, 930, 100), (1300, 820, 125), (640, 480, 150)):
        window.resize(width, height)
        scale = percent / 100
        themes = (
            ("dark", "light", "space", "jungle") if percent == 100 else ("dark", "light", "space")
        )
        for theme in themes:
            controller.saveAppearance(theme, percent)
            gui.idle()
            assert controller.appearance == {"theme": theme, "scale": percent}
            for period, cell in (("Week", 6), ("Month", 20)):
                gui.click(f"calendar{period}Button")
                gui.click(f"calendarDay_{cell}")
                bar = check_scrollbar(
                    "calendarScrollBar", scale, f"calendarDrop_{cell}", "calendarDayTimeline"
                )
                if period == "Month":
                    view = gui.item("calendarScroll").property("contentItem")
                    view.setProperty("contentY", 148 * scale)
                    gui.wait_for(lambda bar=bar: bar.property("position") > 0)
                    gui.screenshot(profile / f"{theme}-calendar-{percent}.png")

            gui.click("planningButton")
            ready()
            gui.reveal(gui.item("workingEnd_3"))
            check_scrollbar(
                "planningScrollBar", scale, "workingEnd_3", "planningTask", "planningLast"
            )
            if theme in ("space", "jungle") and percent == 100:
                visitor_name = "jungleVisitor" if theme == "jungle" else "observatoryVisitor"
                visitor = gui.item(visitor_name)
                gui.wait_for(
                    lambda visitor=visitor: not visitor.property("tucked"), "visitor peeks out"
                )
                gui.click(visitor_name)
                assert visitor.property("tucked")
                gui.wait_for(
                    lambda visitor=visitor: not visitor.property("tucked"), "visitor returns"
                )
                QTest.qWait(320)
            gui.screenshot(profile / f"{theme}-planning-{percent}.png")
            gui.click("planningClose")

            if percent == 150:
                gui.click("newBlockButton")
                check_scrollbar("blockScrollBar", scale, "blockStartInput", "blockEndInput")
                gui.click("cancelBlockButton")


try:
    gui.idle()
    gui.click("calendarPageButton")
    if mode == "scroll-layout":
        check_scroll_layout()
        raise SystemExit(0)
    gui.click("planningButton")
    ready()
    if mode == "run":
        assert len(planner.state["tasks"]) == 1
        gui.click("saveWorkingHours")
        ready()
        assert (profile / "working-hours.json").exists()
        gui.fill("planningFirst", "2030-01-07")
        gui.fill("planningLast", "2030-01-07")
        gui.fill("planningPrompt", "Оставь время на отдых")
        gui.click("planningGenerate")
        ready()
        assert planner.state["hasDraft"], planner.state["error"]
        assert "Не распределено: 420 мин" in planner.state["summary"]
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert len(store.list_work_blocks()) == 1
        # Реальный ввод меняет локальный черновик, подтверждение блокируется до проверки.
        field = gui.item("planningBlockEnd_0")
        field.forceActiveFocus()
        field.selectAll()
        for char in "07.01.2030 12:00":
            QTest.keyClick(window, Qt.Key(ord(char)))
        app.processEvents()
        assert not gui.item("planningApply").isEnabled()
        gui.click("planningCheckBlock_0")
        assert planner.state["canApply"], planner.state["error"]
        assert "Не распределено: 480 мин" in planner.state["summary"]
        gui.screenshot(profile / "schedule-draft.png")
        window.resize(640, 480)
        controller.saveAppearance("dark", 150)
        gui.idle()
        gui.reveal(gui.item("planningBlockEnd_0"))
        gui.screenshot(profile / "schedule-narrow.png")
        save_hours(profile, (None,) * 7)
        planner.apply()
        ready()
        assert "Рабочие часы изменились" in planner.state["error"]
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert len(store.list_work_blocks()) == 1
        save_hours(profile, DEFAULT)
        gui.click("planningApply")
        ready()
        gui.idle()
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert len(store.list_work_blocks()) == 2
            saved_task = store.list_tasks()[0]
            assert saved_task.deadline == task.deadline and not saved_task.completed

        # Отменённый поздний ответ не должен воскресить черновик или записать блоки.
        class LateAdapter:
            connection = MockAdapter.connection

            def __init__(self):
                self.entered, self.release = Event(), Event()

            def generate(self, request, cancelled):
                self.entered.set()
                self.release.wait(3)
                return MockAdapter().generate(request, Event())

        late = LateAdapter()
        agent.adapter = late
        planner.generate(str(task.id), "2030-01-07", "2030-01-07", "Поздний ответ")
        gui.wait_for(late.entered.is_set)
        planner.cancel()
        late.release.set()
        QTest.qWait(400)
        app.processEvents()
        assert not planner.state["hasDraft"]
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert len(store.list_work_blocks()) == 2
    else:
        with TaskStore(profile / "tasks.sqlite3") as store:
            assert len(store.list_work_blocks()) == 2
        assert planner.state["hours"][0]["start"] == "09:00"
        assert not planner.state["hasDraft"]
        (profile / "working-hours.json").write_text("corrupt", encoding="utf-8")
        planner.load()
        ready()
        assert planner.state["error"] and planner.state["tasks"]
        assert (profile / "working-hours.json").read_text() == "corrupt"
        gui.click("saveWorkingHours")
        ready()
        assert not planner.state["error"]
        gui.click("planningClose")
finally:
    planner.close()
    agent.close()
    controller.close()
    window.close()
    engine = None
    app.processEvents()
