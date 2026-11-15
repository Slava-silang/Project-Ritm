"""Неблокирующая проверка активного fullscreen-окна X11 через xprop."""

import re
import shutil

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


class FullscreenMonitor(QObject):
    changed = Signal(bool, str)

    def __init__(self, platform, parent=None):
        super().__init__(parent)
        self.supported = platform == "xcb" and shutil.which("xprop") is not None
        self.fullscreen = False
        self._enabled = False
        self._phase = ""
        self._process = QProcess(self)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._failed)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.poll)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._timeout)

    def setEnabled(self, enabled):
        self._enabled = enabled
        if enabled and self.supported:
            self._timer.start()
            self.poll()
        else:
            self._timer.stop()
            self._deadline.stop()
            self._process.kill()
            self.fullscreen = False
            self.changed.emit(
                False,
                ""
                if not enabled
                else (
                    "Автоскрытие доступно на X11 с xprop. "
                    "На этой платформе используйте временное скрытие."
                ),
            )

    def poll(self):
        if self._enabled and self.supported and self._process.state() == QProcess.NotRunning:
            self._phase = "root"
            self._start(["-root", "_NET_ACTIVE_WINDOW"])

    def _start(self, arguments):
        self._process.start("xprop", arguments)
        self._deadline.start(700)

    def _finished(self, code, _status):
        self._deadline.stop()
        if not self._enabled:
            return
        raw = bytes(self._process.readAllStandardOutput())
        if code != 0 or len(raw) > 8192:
            self._failed()
            return
        if self._phase == "root":
            match = re.search(rb"0x[0-9a-fA-F]+", raw)
            if match and match[0] != b"0x0":
                self._phase = "window"
                self._start(["-id", match[0].decode("ascii"), "_NET_WM_STATE"])
                return
        self.fullscreen = self._phase == "window" and b"_NET_WM_STATE_FULLSCREEN" in raw
        self.changed.emit(self.fullscreen, "")

    def _failed(self, *_):
        if self._enabled:
            self.changed.emit(
                self.fullscreen,
                "Не удалось проверить полноэкранное окно. Повторим автоматически; "
                "автоскрытие можно отключить.",
            )

    def _timeout(self):
        self._process.kill()
        self._failed()

    def close(self):
        self._enabled = False
        self._timer.stop()
        self._deadline.stop()
        self._process.kill()
        self._process.waitForFinished(1000)
