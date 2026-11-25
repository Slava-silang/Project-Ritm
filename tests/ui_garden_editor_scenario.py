"""Действия сада через QML и реальные временные базы, без личного профиля/сети."""

import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from gui_support import GuiHarness
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ritm.app import load_window
from ritm.application.garden_runtime import sync_garden
from ritm.storage.backup import create_backup
from ritm.storage.garden import GardenStore
from ritm.storage.sqlite import TaskStore
from ritm.ui import tasks as ui_tasks
from ritm.ui.tasks import TaskController

profile, mode = Path(sys.argv[1]), sys.argv[2]
now = datetime(2026, 11, 20, 10, tzinfo=UTC)
if mode not in ("edit", "reopen"):
    with TaskStore(profile / "tasks.sqlite3") as source:
        sync_garden(source, profile, now, zone="UTC", enabled=True)
    with GardenStore(profile / "garden.sqlite3") as garden:
        garden.credit("fixture", 10000)

QQuickStyle.setStyle("Basic")
app = QApplication([])
main_thread = threading.get_ident()
loads = []
original_load = ui_tasks.load_garden_assets


def load():
    assert threading.get_ident() != main_thread
    loads.append(True)
    return original_load()


ui_tasks.load_garden_assets = load
controller = TaskController(profile, clock=lambda: now, zone=UTC)
engine = load_window(controller)
window = engine.rootObjects()[0]
window.resize(1280, 820)
gui = GuiHarness(app, window, controller)
pane = gui.item("gardenPane")


def idle():
    gui.idle()
    assert not controller.error, controller.error


def opened():
    gui.click("gardenButton")
    gui.wait_for(lambda: controller.gardenBoard["ready"], "garden editor ready")
    idle()


def snapshot():
    with GardenStore(profile / "garden.sqlite3") as garden:
        return garden.snapshot()


def choose(identifier):
    gui.click("gardenInventoryTab")
    gui.click("gardenChoose_" + str(identifier))


def buy(kind):
    gui.click("gardenShopTab")
    previous = {item.id for item in snapshot().garden.items}
    gui.click("gardenBuy_" + kind)
    idle()
    added = [item for item in snapshot().garden.items if item.id not in previous]
    assert len(added) == 1 and added[0].kind == kind
    return added[0].id


def point(x, y, click=True):
    scene = controller.gardenScene
    cell = scene["cells"][y * 10 + x]
    zoom = pane.property("zoom")
    viewport = gui.item("gardenViewport")
    viewport.setProperty(
        "contentX",
        max(
            0,
            min(
                cell["centerX"] * zoom - viewport.width() / 2,
                viewport.property("contentWidth") - viewport.width(),
            ),
        ),
    )
    viewport.setProperty(
        "contentY",
        max(
            0,
            min(
                cell["centerY"] * zoom - viewport.height() / 2,
                viewport.property("contentHeight") - viewport.height(),
            ),
        ),
    )
    app.processEvents()
    QTest.qWait(50)
    position = (
        gui.item("gardenFieldMouse").mapToScene(QPointF(cell["centerX"], cell["centerY"])).toPoint()
    )
    assert 0 <= position.x() < window.width() and 0 <= position.y() < window.height(), position
    QTest.mouseMove(window, position)
    app.processEvents()
    if click:
        QTest.mouseClick(
            window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, position
        )
        app.processEvents()
        gui.idle()


def place(identifier, x, y):
    choose(identifier)
    gui.click("gardenMove")
    point(x, y)
    idle()
    assert snapshot().garden.item(identifier).cells
    assert snapshot().garden.item(identifier).x == x


try:
    idle()
    assert not loads and not controller.gardenScene
    if mode == "edit":
        gui.click("gardenButton")
        assert not pane.property("narrow")
        assert not gui.item("gardenWorld").property("active")
        assert not (profile / "garden.sqlite3").exists()
        gui.click("gardenEnabled")
        gui.wait_for(lambda: controller.gardenBoard["ready"])
        assert loads and controller.garden["balance"] == "100.00"
        assert not gui.item("gardenBuy_oak").isEnabled()
        daisy = buy("daisy")
        assert snapshot().balance == 20
        assert snapshot().garden.item(daisy).stage == 1
        place(daisy, 5, 5)
        gui.choose("gardenMode", 3)
        point(5, 5)
        assert "свободной" in pane.property("localError")
        assert snapshot().garden.terrain[55] == "grass"
        point(7, 7)
        assert snapshot().garden.terrain[77] == "water"
        gui.choose("gardenMode", 0)
        with GardenStore(profile / "garden.sqlite3") as garden:
            garden.credit("fixture", 10000)
        controller.reload()
        idle()
        lily = buy("water_lily")
        place(lily, 7, 7)
        tree = buy("oak")
        choose(tree)
        gui.click("gardenMove")
        before = snapshot()
        point(9, 9)
        assert "границы" in pane.property("localError")
        assert snapshot() == before
        QTest.keyClick(window, Qt.Key.Key_Escape)
        app.processEvents()
        assert not pane.property("placingId") and not snapshot().garden.item(tree).placed
        place(tree, 1, 1)
        bench = buy("bench")
        place(bench, 4, 4)
        assert snapshot().garden.item(bench).cells == {(4, 4), (4, 5)}
        assert snapshot().garden.planted_cells == 6
        gui.click("gardenShopTab")
        gui.fill("gardenFertilizerQuantity", "28")
        gui.click("gardenBuyFertilizer")
        assert snapshot().garden.fertilizer == 28
        choose(daisy)
        for stage in (2, 3, 4):
            gui.click("gardenGrow")
            assert snapshot().garden.item(daisy).stage == stage
        assert snapshot().garden.fertilizer == 0
        assert controller.gardenBoard["matureCells"] == 1
        gui.click("gardenStash")
        assert not snapshot().garden.item(daisy).placed
        place(daisy, 5, 5)
        gui.click("gardenSell")
        before = snapshot()
        gui.click("gardenCancelSale")
        assert snapshot() == before
        gui.click("gardenSell")
        gui.click("gardenConfirmSale")
        assert snapshot().balance == before.balance + 40
        assert all(item.id != daisy for item in snapshot().garden.items)
        gui.click("gardenZoom2")
        assert pane.property("zoom") == 2
        assert not gui.item("gardenSprite_0").property("smooth")
        gui.click("gardenZoom1")
        window.setWidth(1280)
        window.setHeight(900)
        gui.wait_for(lambda: window.width() == 1280)
        gui.screenshot(profile / "garden-editor-wide.png")
        before = snapshot()
        gui.click("gardenEnabled")
        assert not gui.item("gardenWorld").property("active")
        assert not controller.gardenScene and controller._garden_asset_cache is None
        gui.click("gardenEnabled")
        assert snapshot().garden == before.garden and snapshot().balance == before.balance
        controller.saveAppearance("dark", 150)
        idle()
        window.setWidth(640)
        window.setHeight(480)
        gui.wait_for(lambda: pane.property("narrow") and window.width() == 640)
        gui.screenshot(profile / "garden-editor-narrow.png")
        image = window.grabWindow()
        viewport = gui.item("gardenViewport")
        corner = viewport.mapToScene(QPointF())
        ratio = image.width() / window.width()
        green = 0
        for py in range(
            int(corner.y() * ratio) + 4, int((corner.y() + viewport.height()) * ratio) - 4, 4
        ):
            for px in range(
                int(corner.x() * ratio) + 4, int((corner.x() + viewport.width()) * ratio) - 4, 4
            ):
                color = image.pixelColor(px, py)
                green += (
                    color.green() > 80
                    and color.green() > color.red() * 1.1
                    and color.green() > color.blue() * 1.1
                )
        assert green > 30, ("Field disappeared after resize", green, viewport.property("contentY"))
        gui.click("gardenTools")
        gui.click("gardenInventoryTab")
        gui.screenshot(profile / "garden-editor-drawer.png")
    elif mode == "reopen":
        opened()
        assert controller.appearance == {"theme": "dark", "scale": 150}
        saved = snapshot().garden
        assert {item.kind for item in saved.items} == {"bench", "oak", "water_lily"}
        assert all(item.placed for item in saved.items) and saved.fertilizer == 0
        assert controller.gardenBoard["plantedCells"] == 5
        assert gui.item("gardenWorld").property("active")
    elif mode == "keyboard":
        opened()
        item = buy("daisy")
        choose(item)
        gui.click("gardenMove")
        viewport = gui.item("gardenViewport")
        viewport.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        app.processEvents()
        target = pane.property("hover")
        assert target["valid"]
        before = snapshot()
        QTest.keyClick(window, Qt.Key.Key_Return)
        idle()
        planted = snapshot().garden.item(item)
        assert (planted.x, planted.y) == (target["x"], target["y"])
        assert snapshot().balance == before.balance
        choose(item)
        gui.click("gardenMove")
        QTest.keyClick(window, Qt.Key.Key_Escape)
        app.processEvents()
        assert not pane.property("placingId")
        assert snapshot().garden.item(item) == planted
        # Buttons are activated through the keyboard too; sale still needs confirmation.
        gui.item("gardenSell").forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_Space)
        app.processEvents()
        gui.item("gardenCancelSale").forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_Space)
        idle()
        assert snapshot().garden.item(item) == planted
    elif mode == "failures":
        opened()
        original = GardenStore.execute

        def committed_then_failed(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise OSError("lost acknowledgement")

        GardenStore.execute = committed_then_failed
        before = snapshot()
        controller.gardenCommand({"action": "buy", "kind": "daisy"})
        gui.idle()
        assert controller.gardenCommandPending and controller.error
        after = snapshot()
        assert after.balance == before.balance - 80 and len(after.garden.items) == 1
        pending = dict(controller._garden_pending)
        controller.gardenCommand({"action": "buy", "kind": "oak"})
        assert controller._garden_pending == pending and snapshot() == after
        GardenStore.execute = original
        gui.click("retryGardenCommand")
        idle()
        assert not controller.gardenCommandPending and snapshot() == after
        # A separate committed change makes the displayed revision stale.
        with GardenStore(profile / "garden.sqlite3") as garden:
            garden.credit("other", 10)
        controller.gardenCommand({"action": "buy", "kind": "oak"})
        gui.idle()
        assert "изменились" in controller.error and not controller.gardenCommandPending
        assert len(snapshot().garden.items) == 1
        assert controller.gardenBoard["revision"] == snapshot().revision
    elif mode == "restore":
        opened()
        archive = create_backup(profile, profile / "backups", now)
        old_identity = controller.gardenBoard["identity"]
        item = buy("daisy")
        choose(item)
        gui.click("gardenMove")
        controller.inspectBackup(QUrl.fromLocalFile(str(archive)))
        idle()
        controller.restoreBackup()
        idle()
        assert controller.gardenBoard["identity"] != old_identity
        assert not pane.property("placingId") and not pane.property("selectedId")
        assert not snapshot().garden.items
    else:

        def failed_load():
            assert threading.get_ident() != main_thread
            raise OSError("missing resources")

        ui_tasks.load_garden_assets = failed_load
        gui.click("gardenButton")
        idle()
        assert controller.gardenBoard["error"] and not controller.gardenBoard["ready"]
        assert not gui.item("gardenWorld").property("active")
        assert controller.garden["balance"] == "10100.00"
        ui_tasks.load_garden_assets = load
        gui.click("retryGarden")
        idle()
        assert controller.gardenBoard["ready"]
        gui.click("gardenProgressButton")
        assert "Оплачено часов" in gui.item("gardenWorkProgress").property("text")
        gui.click("closeGardenProgress")
        scenes = []
        controller.gardenSceneChanged.connect(lambda: scenes.append(True))
        with GardenStore(profile / "garden.sqlite3") as garden:
            garden.credit("wallet-only", 10)
        controller.reload()
        idle()
        assert not scenes  # money changes must not rebuild the image delegates
        gui.click("closeGarden")
        idle()
        assert not controller.gardenScene and controller._garden_asset_cache is None
finally:
    controller.close()
    window.close()
    engine = None
    app.processEvents()
