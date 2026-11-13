"""Просмотр/правки черновика, отмена, устаревшая задача и поздний ответ."""

import sys
from pathlib import Path
from threading import Event

from gui_support import GuiHarness
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ritm.app import load_window
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.integrations.assistant import MockAdapter
from ritm.storage.sqlite import TaskStore
from ritm.ui.assistant import AssistantController
from ritm.ui.tasks import TaskController

profile = Path(sys.argv[1])
QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
controller = TaskController(profile)
agent = AssistantController(profile)
engine = load_window(controller, assistant=agent)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)
race = None
release = Event()
try:
    gui.idle()
    gui.click("assistantButton")
    gui.fill("assistantPrompt", "Лабораторная по физике")
    gui.click("assistantGenerate")
    gui.wait_for(lambda: agent.state["hasDraft"])
    with TaskStore(profile / "tasks.sqlite3") as store:
        assert not store.list_tasks()
    gui.screenshot(profile / "assistant-preview.png")
    gui.click("assistantEdit")
    gui.fill("draftTitle", "Проверенное название")
    gui.fill("draftDifficulty", "abc")
    gui.click("assistantCheck")
    assert agent.state["error"] and not agent.state["canApply"]
    assert gui.item("draftDifficulty").property("text") == "abc"
    gui.fill("draftDifficulty", "7")
    gui.fill("draftMinutes", "90")
    gui.fill("draftDeadline", "28.11.2026 18:00")
    gui.fill("draftWeights", "20; 20")
    gui.click("assistantCheck")
    assert agent.state["error"] and not agent.state["canApply"]
    gui.click("draftAddSubtask")
    field = gui.item("draftSubtask_2")
    gui.reveal(field)
    field.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window, Qt.Key.Key_Z)
    gui.fill("draftWeights", "20; 30; 50")
    gui.click("assistantCheck")
    assert not agent.state["error"], agent.state
    assert agent.state["draft"]["title"] == "Проверенное название"
    assert agent.state["draft"]["subtasks"][2]["title"].lower() == "z", (
        field.property("text"),
        agent.state,
    )
    gui.screenshot(profile / "assistant-edited.png")
    gui.click("assistantApply")
    gui.wait_for(lambda: not agent.state["busy"])
    gui.idle()
    assert len(controller.tasks) == 1
    identifier = controller.tasks[0]["id"]
    with TaskStore(profile / "tasks.sqlite3") as store:
        created = store.list_tasks()[0]
        assert created.title == "Проверенное название"
        assert created.difficulty == 7 and created.expected_duration.total_seconds() == 5400
        assert created.deadline.astimezone().hour == 18
        assert [child.weight for child in created.subtasks] == [20, 30, 50]
        assert len(created.subtasks[0].items) == 2
    # Отмена просмотра ничего не записывает.
    gui.click("assistantButton")
    gui.click("assistantGenerate")
    gui.wait_for(lambda: agent.state["hasDraft"])
    gui.click("assistantCancel")
    with TaskStore(profile / "tasks.sqlite3") as store:
        assert len(store.list_tasks()) == 1

    # Новый контракт получает только выбранную задачу, не весь профиль.
    class DelayedAdapter:
        connection = MockAdapter.connection

        def __init__(self):
            self.requests = []
            self.started = Event()

        def generate(self, request, cancelled):
            self.requests.append(request)
            self.started.set()
            if len(self.requests) == 1:
                release.wait(5)  # имитация провайдера, игнорирующего отмену
                return '{"title":"Поздний ответ"}'
            return '{"subtasks":[{"title":"Новое предложение"}]}'

    adapter = DelayedAdapter()
    race = AssistantController(profile, adapter)
    race.generate("старый запрос")
    gui.wait_for(adapter.started.is_set)
    ticks = []
    from PySide6.QtCore import QTimer

    QTimer.singleShot(0, lambda: ticks.append(True))
    gui.wait_for(lambda: bool(ticks))
    race.cancel()
    with TaskStore(profile / "tasks.sqlite3") as store:
        private = Task(title="Не передавать")
        store.create_task(private)
    race.generate("разбить выбранную", identifier)
    gui.wait_for(lambda: race.state["hasDraft"])
    assert adapter.requests[1].selected == created
    assert adapter.requests[1].prompt == "разбить выбранную"
    release.set()
    QTest.qWait(150)
    app.processEvents()
    assert race.state["draft"]["subtasks"][0]["title"] == "Новое предложение"
    # Ручное изменение между генерацией и подтверждением не затирается.
    with TaskStore(profile / "tasks.sqlite3") as store:
        TaskService(store).rename_node(created.id, created.id, "Ручное изменение")
    race.apply()
    gui.wait_for(lambda: not race.state["busy"])
    assert "изменилась" in race.state["error"]
    with TaskStore(profile / "tasks.sqlite3") as store:
        assert store.get_task(created.id).title == "Ручное изменение"
        assert len(store.get_task(created.id).subtasks) == 3
    race.cancel()
    race.generate("ещё раз", identifier)
    gui.wait_for(lambda: race.state["hasDraft"])
    race.apply()
    race.apply()
    gui.wait_for(lambda: not race.state["busy"])
    with TaskStore(profile / "tasks.sqlite3") as store:
        assert len(store.get_task(created.id).subtasks) == 4
        assert len(store.list_tasks()) == 2

    # Ошибка адаптера не оставляет UI в busy и не раскрывает его текст.
    def fail(*args):
        raise RuntimeError("secret token must not appear")

    adapter.generate = fail
    race.generate("ошибка")
    gui.wait_for(lambda: not race.state["busy"])
    assert race.state["error"] and "secret" not in race.state["error"]
    # Узкое окно, масштаб 150%, редактируемые названия и постоянные кнопки.
    controller.saveAppearance("dark", 150)
    gui.idle()
    window.setWidth(640)
    window.setHeight(480)
    gui.wait_for(lambda: window.width() == 640)
    gui.click("assistantButton")
    gui.choose("assistantOperation", 1)
    gui.click("assistantGenerate")
    gui.wait_for(lambda: agent.state["hasDraft"])
    gui.screenshot(profile / "assistant-narrow.png")
    gui.click("assistantEdit")
    gui.screenshot(profile / "assistant-editor-narrow.png")
    gui.click("assistantCancel")
finally:
    release.set()
    if race is not None:
        race.close()
    agent.close()
    controller.close()
    window.close()
    engine = None
    app.processEvents()
