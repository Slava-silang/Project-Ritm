"""Трей и настройки запуска, независимо от логики задач и QML."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ritm.platform.autostart import entry_path, read_autostart, write_autostart


class DesktopIntegration(QObject):
    changed = Signal()
    _loaded = Signal(object, str)

    def __init__(self, app, profile: Path, config: Path, command: list[str], *, supported=True):
        super().__init__()
        self.app = app
        self.window = None
        self.command = command
        self.path = entry_path(config, profile)
        self.supported = supported
        self._settings = {"enabled": False, "hidden": False}
        self._error = ""
        self._busy = True
        self._available = False
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._loaded.connect(self._apply)
        self.tray = QSystemTrayIcon(self)
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor("#397d68"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("white"))
        painter.drawText(pixmap.rect(), 0x84, "Р")
        painter.end()
        self.tray.setIcon(QIcon(pixmap))
        self.tray.setToolTip("Ритм — задачи и время")
        self.menu = QMenu()
        self.menu.addAction("Открыть Ритм", self.showWindow)
        self.menu.addSeparator()
        self.menu.addAction("Выйти", self.requestExit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refreshAvailability)
        self.timer.start(1000)
        self.refreshAvailability()
        self._run(lambda: read_autostart(self.path))

    @Property(bool, notify=changed)
    def trayAvailable(self):
        return self._available

    @Property("QVariantMap", notify=changed)
    def settings(self):
        return dict(self._settings, supported=self.supported, busy=self._busy, error=self._error)

    def attach(self, window, hidden=False):
        self.window = window
        if hidden and self._available:
            window.hide()
        else:
            self.showWindow()

    @Slot()
    def refreshAvailability(self):
        available = self.tray.isSystemTrayAvailable()
        if available != self._available:
            self._available = available
            if not available:
                self.showWindow()
            self.changed.emit()

    @Slot()
    def showWindow(self):
        if self.window is not None:
            self.window.showNormal()
            self.window.raise_()
            self.window.requestActivate()

    def _activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.showWindow()

    @Slot()
    def requestExit(self):
        self.showWindow()
        if self.window is not None:
            self.window.requestExit()

    @Slot()
    def exitNow(self):
        QTimer.singleShot(0, self.app.quit)

    @Slot(bool, bool)
    def save(self, enabled, hidden):
        if self._busy:
            return
        if not self.supported:
            self._error = "Автозапуск пока доступен только на Linux."
            self.changed.emit()
            return
        self._busy = True
        self._error = ""
        self.changed.emit()
        self._run(lambda: write_autostart(self.path, self.command, enabled, hidden))

    def _run(self, operation):
        def work():
            try:
                result, error = operation(), ""
            except (OSError, ValueError):
                result, error = (
                    None,
                    "Не удалось прочитать или изменить автозапуск. "
                    "Проверьте доступ к папке настроек.",
                )
            self._loaded.emit(result, error)

        self._pool.submit(work)

    @Slot(object, str)
    def _apply(self, result, error):
        if result is not None:
            self._settings = result
        self._error = error
        self._busy = False
        self.changed.emit()

    def close(self):
        self.timer.stop()
        self.tray.hide()
        self.menu.close()
        self._pool.shutdown(wait=True)
