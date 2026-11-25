"""Достижения из настоящих событий: месяцы, выключение, ошибка, новый процесс."""

import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import Qt, QUrl
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.application.garden_runtime import sync_garden
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.storage.backup import create_backup
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore
from ritm.ui import tasks as ui_tasks
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
now = datetime(2026, 11, 25, 9, tzinfo=UTC)
if mode == "view":
    with TaskStore(profile / "tasks.sqlite3", clock=lambda: now) as source:
        sync_garden(source, profile, now, zone="UTC", enabled=True)
        archive = create_backup(profile, profile / "backups", now)
        (profile / "early-backup-path.txt").write_text(str(archive))
        service = TaskService(source)
        for i in range(15):
            task = service.create_task(Task(title=f"Лабораторная {i}"))
            service.set_completed(task.id, task.id, True)
        for _ in range(3):
            source.add_focus_record(
                task.id, now, now + timedelta(hours=10), now + timedelta(hours=10)
            )
            now += timedelta(days=1)
        sync_garden(source, profile, now, zone="UTC")
now = datetime(2026, 12, 1, 9, tzinfo=UTC)
QQuickStyle.setStyle("Basic")
app = QApplication([])
controller = TaskController(profile, clock=lambda: now, zone=UTC)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def saved():
    with GardenStore(profile / "garden.sqlite3") as store:
        return store.snapshot(), store.achievement_snapshot()


try:
    gui.idle()
    before = saved()
    assert controller.garden["roots"] == 15
    assert set(controller.garden["themes"]) == {"dark", "light", "jungle", "space"}
    gui.click("gardenButton")
    gui.idle()
    if mode == "view":
        gui.click(
            "gardenProgressWhenOff"
            if gui.item("gardenProgressWhenOff").isVisible()
            else "gardenProgressButton"
        )
    else:
        assert not controller.garden["enabled"]
        gui.click("gardenProgressWhenOff")
    dialog = gui.item("gardenProgressDialog")
    assert dialog.property("selectedMonth") == "2026-12"
    assert "0 / 1000" in gui.item("gardenMonthEarned").property("text")
    assert "получено" in gui.item("gardenPermanent_space").property("text")
    gui.choose("gardenProgressMonth", 1)
    assert dialog.property("selectedMonth") == "2026-11"
    assert "700 / 1000" in gui.item("gardenMonthEarned").property("text")
    assert "30.00 / 30" in gui.item("gardenGoal_focus:30").property("text")
    if mode == "view":
        gui.screenshot(profile / "garden-progress-wide.png")
        # A modal progress window blocks the application's section shortcuts.
        QTest.keyClick(window, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
        assert gui.item("gardenPane").isVisible()
        controller.saveAppearance("dark", 150)
        gui.idle()
        window.setWidth(640)
        window.setHeight(480)
        app.processEvents()
        gui.reveal(gui.item("gardenPermanent_jungle"))
        gui.screenshot(profile / "garden-progress-narrow.png")
        gui.reveal(gui.item("gardenProgressMonth"))
        gui.choose("gardenProgressMonth", 0)
        assert "0 / 1000" in gui.item("gardenMonthEarned").property("text")
        QTest.keyClick(window, Qt.Key.Key_Escape)
        app.processEvents()
        assert not dialog.property("visible")
        assert saved() == before  # opening/choosing months never changes rewards or wallet
        controller.setGardenEnabled(False)
        gui.idle()
        gui.click("gardenProgressWhenOff")
        assert "получено" in gui.item("gardenPermanent_jungle").property("text")
        gui.click("closeGardenProgress")
        original = ui_tasks.sync_garden

        def unavailable(*args, **kwargs):
            raise OSError("test storage unavailable")

        ui_tasks.sync_garden = unavailable
        controller.reload()
        gui.idle()
        gui.click("gardenProgressWhenOff")
        assert gui.item("gardenProgressUnavailable").isVisible()
        assert not gui.item("gardenWorkProgress").isVisible()
        gui.click("closeGardenProgress")
        ui_tasks.sync_garden = original
        controller.reload()
        gui.idle()
        gui.click("gardenProgressWhenOff")
        gui.choose("gardenProgressMonth", 1)
        assert "700 / 1000" in gui.item("gardenMonthEarned").property("text")
    gui.click("closeGardenProgress")
    if mode == "restore":
        archive = Path((profile / "early-backup-path.txt").read_text())
        controller.inspectBackup(QUrl.fromLocalFile(str(archive)))
        gui.idle()
        controller.restoreBackup()
        started = time.monotonic()
        while controller.busy and time.monotonic() - started < 40:
            app.processEvents()
            QTest.qWait(10)
        assert not controller.busy, controller.error
        assert not controller.error, controller.error
        assert controller.garden["roots"] == 0
        assert controller.garden["balance"] == "100.00"
        assert controller.garden["progress"]["periods"][0]["month"] == "2026-12"
        gui.click("gardenButton")  # successful restore returns to tasks by design
        gui.click("gardenProgressWhenOff")
        assert "ещё не получено" in gui.item("gardenPermanent_space").property("text")
        assert "0 / 1000" in gui.item("gardenMonthEarned").property("text")
        gui.click("closeGardenProgress")
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
