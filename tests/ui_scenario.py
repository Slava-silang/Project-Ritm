"""Сценарий настоящего QML-окна; запускается отдельным процессом с временным профилем."""

import json
import sqlite3
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ritm.app import load_window
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

QQuickStyle.setStyle("Basic")
app = QGuiApplication([])
profile = Path(sys.argv[1])
controller = TaskController(profile)
engine = load_window(controller)
window = engine.rootObjects()[0]


def wait_for(predicate, message="condition"):
    deadline = time.monotonic() + 10
    while not predicate():
        app.processEvents()
        QTest.qWait(10)
        assert time.monotonic() < deadline, f"Timeout: {message}; error={controller.error}"
    app.processEvents()
    QTest.qWait(50)


def idle():
    wait_for(lambda: not controller.busy, "storage idle")


def item(name):
    candidates = window.findChildren(QObject, name)
    if candidates:
        return candidates[0]
    pending = [window.contentItem()]
    while pending:
        candidate = pending.pop()
        if candidate.objectName() == name:
            return candidate
        if isinstance(candidate, QQuickItem):
            pending.extend(candidate.childItems())
    raise AssertionError(f"Missing QML item {name}; detail={controller.detail}")


def fill(name, value):
    item(name).setProperty("text", value)
    app.processEvents()


def click(name):
    target = item(name)
    assert target.isVisible() and target.isEnabled(), name
    point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
    assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), (name, point)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    app.processEvents()
    QTest.qWait(80)
    idle()


def screenshot(name):
    QTest.qWait(100)
    assert window.grabWindow().save(str(profile / name))


try:
    idle()
    if "--reopen" in sys.argv:
        assert not controller.error
        assert controller.detail["id"] == sys.argv[-1]
        assert controller.detail["title"] == "Лабораторная № 1 · Механика"
        assert controller.detail["progress"] == 60
        assert controller.detail["deadline"] == ""
        assert controller.detail["subtasks"][1]["completed"]
        print(json.dumps({"reopened": True}))
        raise SystemExit(0)
    assert not controller.error
    click("newTaskButton")
    fill("titleInput", "Лабораторная по физике")
    fill("descriptionInput", "Изучить движение тела и подготовить отчёт.")
    fill("difficultyInput", "11")
    fill("minutesInput", "90")
    fill("deadlineInput", "28.11.2026 18:00")
    click("saveTaskButton")
    assert controller.error
    assert item("titleInput").property("text") == "Лабораторная по физике"
    assert not controller.tasks
    screenshot("form.png")
    fill("difficultyInput", "7")
    click("saveTaskButton")
    assert not controller.error
    assert len(controller.tasks) == 1
    identifier = controller.detail["id"]
    assert controller.detail["minutes"] == "90"
    assert controller.detail["deadline"] == "28.11.2026 18:00"
    for title in ("Расчёты", "Отчёт"):
        fill("newSubtask", title)
        click("addSubtaskButton")
    fill("newItem_0", "Записать формулы")
    click("addItem_0")
    click("renameBranch_0")
    fill("renameInput", "Расчёты и формулы")
    click("saveRenameButton")
    assert controller.detail["subtasks"][0]["title"] == "Расчёты и формулы"
    click("renameItem_0_0")
    fill("renameInput", "")
    click("saveRenameButton")
    assert controller.error
    fill("renameInput", "Выписать формулы")
    click("saveRenameButton")
    assert controller.detail["subtasks"][0]["items"][0]["title"] == "Выписать формулы"
    click("weightsButton")
    fill("weightInput_0", "20")
    fill("weightInput_1", "60")
    click("saveWeightsButton")
    assert controller.error
    assert item("weightInput_0").property("text") == "20"
    screenshot("weights.png")
    fill("weightInput_0", "40")
    click("saveWeightsButton")
    assert not controller.error
    click("itemCheck_0_0")
    assert controller.detail["progress"] == 40
    assert not controller.detail["completed"]
    screenshot("wide.png")
    click("taskComplete")
    assert controller.detail["completed"]
    assert controller.detail["progress"] == 100
    click("itemCheck_0_0")
    assert not controller.detail["completed"]
    assert controller.detail["subtasks"][1]["completed"]
    assert controller.detail["progress"] == 60
    click("editTaskButton")
    fill("titleInput", "Лабораторная № 1 · Механика")
    fill("deadlineInput", "")
    click("saveTaskButton")
    assert controller.detail["deadline"] == ""
    assert len(controller.detail["subtasks"]) == 2
    assert controller.detail["progress"] == 60
    window.resize(640, 480)
    app.processEvents()
    screenshot("narrow.png")
    click("editTaskButton")
    fill("titleInput", "Несохранённый черновик")
    screenshot("narrow-form.png")
    QTest.keyClick(window, Qt.Key.Key_Escape)
    app.processEvents()
    assert controller.detail["title"] == "Лабораторная № 1 · Механика"
    # A blocked SQLite writer must leave the Qt loop responsive.
    with sqlite3.connect(profile / "tasks.sqlite3") as lock:
        lock.execute("BEGIN EXCLUSIVE")
        timer_fired = []

        def release():
            timer_fired.append(True)
            lock.rollback()

        controller.reload()
        QTimer.singleShot(100, release)
        idle()
        assert timer_fired and not controller.error
    controller.close()
    window.close()
    engine = None
    app.processEvents()
    controller = TaskController(profile)
    engine = load_window(controller)
    window = engine.rootObjects()[0]
    idle()
    assert controller.detail["id"] == identifier
    assert controller.detail["title"] == "Лабораторная № 1 · Механика"
    assert controller.detail["progress"] == 60
    assert controller.detail["subtasks"][0]["weight"] == "40"
    with TaskStore(profile / "tasks.sqlite3") as store:
        saved = store.list_tasks()[0]
        assert saved.difficulty == 7
        assert saved.deadline is None
        assert saved.subtasks[1].completed
    blocked_profile = profile / "not-a-directory"
    blocked_profile.write_text("test", encoding="utf-8")
    broken = TaskController(blocked_profile)
    wait_for(lambda: not broken.busy)
    assert broken.error
    broken.close()
    print(
        json.dumps({"ok": True, "platform": app.platformName(), "restart": True, "id": identifier})
    )
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
