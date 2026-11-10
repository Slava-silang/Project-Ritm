"""Управление настоящими QML-контролами в тестовом процессе."""

import time

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


class GuiHarness:
    def __init__(self, app, window, controller):
        self.app, self.window, self.controller = app, window, controller

    def wait_for(self, predicate, message="condition"):
        deadline = time.monotonic() + 10
        while not predicate():
            self.app.processEvents()
            QTest.qWait(10)
            assert time.monotonic() < deadline, f"Timeout: {message}; error={self.controller.error}"
        self.app.processEvents()
        QTest.qWait(50)

    def idle(self):
        self.wait_for(lambda: not self.controller.busy, "storage idle")

    def item(self, name):
        candidates = self.window.findChildren(QObject, name)
        if candidates:
            return candidates[0]
        pending = [self.window.contentItem()]
        while pending:
            candidate = pending.pop()
            if candidate.objectName() == name:
                return candidate
            if isinstance(candidate, QQuickItem):
                pending.extend(candidate.childItems())
        raise AssertionError(f"Missing QML item {name}; detail={self.controller.detail}")

    def fill(self, name, value):
        self.item(name).setProperty("text", value)
        self.app.processEvents()

    def reveal(self, target):
        ancestor = target.parentItem()
        while ancestor is not None:
            if ancestor.metaObject().indexOfProperty("contentY") >= 0:
                center = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).y()
                top = ancestor.mapToScene(QPointF(0, 0)).y()
                if not top + 5 < center < top + ancestor.height() - 5:
                    current = ancestor.property("contentY")
                    maximum = max(0, ancestor.property("contentHeight") - ancestor.height())
                    ancestor.setProperty(
                        "contentY",
                        max(0, min(maximum, current + center - top - ancestor.height() / 2)),
                    )
                    self.app.processEvents()
                    QTest.qWait(30)
            ancestor = ancestor.parentItem()

    def click(self, name):
        target = self.item(name)
        assert target.isVisible() and target.isEnabled(), name
        self.reveal(target)
        point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
        assert 0 <= point.x() < self.window.width() and 0 <= point.y() < self.window.height(), (
            name,
            point,
        )
        QTest.mouseClick(
            self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
        )
        self.app.processEvents()
        QTest.qWait(80)
        self.idle()

    def drag(self, name, dx=0, dy=0, target_name=None, escape=False):
        source = self.item(name)
        self.reveal(source)
        start = source.mapToScene(QPointF(source.width() / 2, source.height() / 2)).toPoint()
        if target_name:
            target = self.item(target_name)
            end = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
        else:
            end = start + QPointF(dx, dy).toPoint()
        assert 0 <= end.x() < self.window.width() and 0 <= end.y() < self.window.height(), (
            name,
            start,
            end,
        )
        QTest.mousePress(
            self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
        )
        for step in range(1, 9):
            QTest.mouseMove(self.window, start + (end - start) * step / 8, 20)
            self.app.processEvents()
        if escape:
            QTest.keyClick(self.window, Qt.Key.Key_Escape)
        QTest.mouseRelease(
            self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end
        )
        self.app.processEvents()
        self.idle()

    def choose(self, name, index):
        control = self.item(name)
        control.forceActiveFocus()
        QTest.keyClick(self.window, Qt.Key.Key_Home)
        for _ in range(index):
            QTest.keyClick(self.window, Qt.Key.Key_Down)
        self.app.processEvents()
        QTest.qWait(50)

    def screenshot(self, path):
        QTest.qWait(100)
        assert self.window.grabWindow().save(str(path))
