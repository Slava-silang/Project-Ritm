"""История имущества, только чтение, избранное, лимит и новый процесс."""

import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from gui_support import GuiHarness
from PySide6.QtCore import Qt, QUrl
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.application.garden_runtime import sync_garden
from ritm.storage.backup import create_backup
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
now = datetime(2026, 12, 1, 12, tzinfo=UTC)


def command(garden, action, **fields):
    return garden.execute(
        uuid4(), {"action": action, **fields}, expected_revision=garden.snapshot().revision
    )


if mode != "reopen":
    start = datetime(2025 if mode == "limit" else 2026, 1 if mode == "limit" else 11, 1, tzinfo=UTC)
    with TaskStore(profile / "tasks.sqlite3", clock=lambda: start) as source:
        sync_garden(source, profile, start, zone="UTC", enabled=True)
    with GardenStore(profile / "garden.sqlite3") as garden:
        if mode != "limit":
            garden.credit("fixture", 1000)
            item = garden.execute(
                UUID(int=1),
                {"action": "buy", "kind": "daisy"},
                expected_revision=garden.snapshot().revision,
            ).item_id
            command(garden, "buy_fertilizer", quantity=4)
            command(garden, "grow", id=str(item))
            command(garden, "place", id=str(item), x=2, y=3)
        else:
            for index in range(20):
                month = f"{2025 + index // 12:04}-{index % 12 + 1:02}"
                garden.rotate_month(month)
                command(garden, "favorite", month=month, enabled=True)

QQuickStyle.setStyle("Basic")
app = QApplication([])
controller = TaskController(profile, clock=lambda: now, zone=UTC)
engine = load_window(controller)
window = engine.rootObjects()[0]
gui = GuiHarness(app, window, controller)


def saved():
    with GardenStore(profile / "garden.sqlite3") as garden:
        return garden.archive_snapshot()


try:
    gui.idle()
    controller.saveAppearance("dusk", 100)
    gui.idle()
    gui.click("gardenButton")
    before = saved()
    if mode == "limit":
        assert controller.gardenArchive["count"] == 20
        gui.click("gardenFavorite")
        assert "20" in controller.error
        assert saved() == before
        gui.click("gardenFavoritesTab")
        gui.click("gardenArchiveMonth")
        gui.wait_for(lambda: gui.item("gardenArchiveMonthPopup").property("visible"), "month menu")
        QTest.keyClick(window, Qt.Key.Key_End)
        QTest.keyClick(window, Qt.Key.Key_Return)
        gui.wait_for(
            lambda: controller.gardenBoard["month"] == "2025-01", "oldest favorite selected"
        )
        gui.idle()
        assert controller.gardenBoard["month"] == "2025-01"
        gui.click("gardenFavorite")
        assert controller.gardenArchive["count"] == 19
        assert "2025-01" not in [g.month for g in saved().history]
        gui.click("gardenCurrentTab")
        gui.click("gardenFavorite")
        assert controller.gardenArchive["count"] == 20
    else:
        if mode == "reopen":
            assert controller.gardenArchive["count"] == 1
            gui.click("gardenFavoritesTab")
        else:
            assert controller.gardenBoard["month"] == "2026-12"
            gui.click("gardenFavoritesTab")
            assert gui.item("gardenFavoritesEmpty").isVisible()
            assert gui.item("gardenPane").property("hideArchiveField")
            gui.click("gardenCurrentTab")
            gui.click("gardenArchiveTab")
        assert controller.gardenBoard["month"] == "2026-11"
        assert controller.gardenBoard["readOnly"]
        assert controller.gardenBoard["items"][0]["stage"] == 2
        assert controller.gardenBoard["catalog"] == []
        identifier = controller.gardenBoard["items"][0]["id"]
        gui.click("gardenChoose_" + identifier)
        assert not gui.item("gardenMove").isVisible()
        assert not gui.item("gardenSell").isVisible()
        assert not gui.item("gardenShopTab").isVisible()
        controller.gardenCommand({"action": "terrain", "x": 0, "y": 0, "terrain": "water"})
        gui.idle()
        assert saved() == before
        # Bypass the UI guard: the worker must also reject archived mutations.
        controller._submit(
            "garden_command",
            {
                "operation": str(uuid4()),
                "command": {"action": "sell", "id": identifier},
                "revision": controller.gardenBoard["revision"],
                "identity": controller.gardenBoard["identity"],
                "month": "2026-11",
            },
        )
        gui.idle()
        assert "просмотра" in controller.error
        assert saved() == before
        controller._submit(
            "garden_command",
            {
                "operation": str(UUID(int=1)),
                "command": {"action": "buy", "kind": "daisy"},
                "revision": 0,
                "identity": controller.gardenBoard["identity"],
                "month": "2026-11",
            },
        )
        gui.idle()
        assert not controller.error, controller.error
        assert saved() == before  # Old receipt remains replayable after month rotation.
        if mode != "reopen":
            gui.click("gardenFavorite")
            assert controller.gardenArchive["count"] == 1
            gui.click("gardenFavoritesTab")
        changes = QSignalSpy(controller.gardenArchiveChanged)
        with GardenStore(profile / "garden.sqlite3") as garden:
            garden.credit("background", 1)
        controller._on_garden_tick()
        gui.idle()
        assert changes.count() == 0  # Wallet checkpoints do not reset month lists.
        if mode == "restore":
            archive = create_backup(profile, profile / "copies", now)
            old_identity = controller.gardenBoard["identity"]
            controller.inspectBackup(QUrl.fromLocalFile(str(archive)))
            gui.idle()
            controller.restoreBackup()
            gui.idle()
            gui.click("gardenButton")
            assert controller.gardenBoard["identity"] != old_identity
            assert controller.gardenBoard["month"] == "2026-12"
            assert not controller.gardenBoard["readOnly"]
            assert controller.gardenArchive["count"] == 1
            gui.click("gardenFavoritesTab")
            assert controller.gardenBoard["month"] == "2026-11"
        gui.screenshot(profile / "garden-archive-wide.png")
        controller.saveAppearance("dusk", 150)
        gui.idle()
        window.setWidth(640)
        window.setHeight(480)
        app.processEvents()
        gui.screenshot(profile / "garden-archive-narrow.png")
        gui.click("gardenArchiveSection")
        gui.choose("gardenArchiveSection", 0)
        QTest.keyClick(window, Qt.Key.Key_Return)
        gui.wait_for(lambda: controller.gardenBoard["month"] == "2026-12", "return to current")
        gui.idle()
        assert not controller.gardenBoard["readOnly"]
        assert controller.gardenBoard["month"] == "2026-12"
        assert controller.gardenBoard["catalog"]
        controller.setGardenEnabled(False)
        gui.idle()
        assert not controller.gardenScene
        assert controller.gardenArchive["count"] == 1
        controller.setGardenEnabled(True)
        gui.idle()
        assert controller.gardenArchive["count"] == 1
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
