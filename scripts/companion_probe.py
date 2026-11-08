"""T03: технический прототип окна. Не является реализацией T28–T29."""

import argparse
import ctypes
import json
import re
import signal
import subprocess
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QRegion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QWidget


class CompanionProbe(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Ритм — прототип компаньона")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(120, 120)
        self.setMask(QRegion(12, 12, 96, 96, QRegion.Ellipse))
        self.offset = None
        self.presses = 0
        self.closing = False
        self.monitor = QTimer(self)
        self.monitor.timeout.connect(self.check_fullscreen)
        self.monitor.start(300)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#274f42"))
        painter.drawEllipse(12, 12, 96, 96)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(36, 44, 10, 14)
        painter.drawEllipse(74, 44, 10, 14)
        painter.drawRoundedRect(46, 76, 28, 5, 2, 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.presses += 1
            self.offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.offset)

    def mouseReleaseEvent(self, event):
        self.offset = None

    def contextMenuEvent(self, event):
        self.monitor.stop()
        menu = QMenu(self)
        menu.addAction("Закрыть прототип", self.close)
        menu.exec(event.globalPos())
        if not self.closing:
            self.monitor.start()

    def closeEvent(self, event):
        self.closing = True
        self.monitor.stop()
        super().closeEvent(event)
        QApplication.instance().quit()

    def check_fullscreen(self):
        if self.closing:
            return
        # Only a probe: production needs asynchronous platform integration.
        active = subprocess.run(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True, text=True, timeout=1
        ).stdout
        match = re.search(r"0x[0-9a-fA-F]+", active)
        if not match or match[0] == "0x0":
            return
        state = subprocess.run(
            ["xprop", "-id", match[0], "_NET_WM_STATE"],
            capture_output=True,
            text=True,
            timeout=1,
        ).stdout
        self.setVisible("_NET_WM_STATE_FULLSCREEN" not in state)


class Underlay(QWidget):
    def __init__(self):
        super().__init__()
        self.clicks = 0
        self.setWindowTitle("Ритм — проверочное окно T03")
        self.setStyleSheet("background: #f5f4ef;")

    def mousePressEvent(self, event):
        self.clicks += 1


def verify(app, companion):
    if app.platformName() != "xcb":
        raise RuntimeError("Проверка требует настоящую X11-сессию.")
    x11 = ctypes.CDLL("libX11.so.6")
    xtest = ctypes.CDLL("libXtst.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtest.XTestFakeMotionEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    xtest.XTestFakeButtonEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    display = x11.XOpenDisplay(None)
    assert display
    cursor = QCursor.pos()
    underlay = Underlay()
    try:
        underlay.setGeometry(80, 80, 360, 300)
        underlay.show()
        underlay.activateWindow()
        QTest.qWait(500)
        companion.move(underlay.mapToGlobal(QPoint(60, 60)))
        companion.show()
        QTest.qWait(400)
        ratio = app.primaryScreen().devicePixelRatio()

        def motion(point):
            xtest.XTestFakeMotionEvent(
                display, -1, round(point.x() * ratio), round(point.y() * ratio), 0
            )
            x11.XFlush(display)
            QTest.qWait(100)

        def button(pressed):
            xtest.XTestFakeButtonEvent(display, 1, int(pressed), 0)
            x11.XFlush(display)
            QTest.qWait(100)

        motion(companion.pos() + QPoint(3, 3))
        button(True)
        button(False)
        assert underlay.clicks == 1, "Прозрачный фон перехватил клик"
        underlay.raise_()
        underlay.activateWindow()
        QTest.qWait(300)
        old = companion.pos()
        motion(old + QPoint(60, 60))
        button(True)
        motion(old + QPoint(100, 90))
        button(False)
        assert companion.presses == 1, "Обычное окно перекрыло персонажа"
        assert (companion.pos() - old - QPoint(40, 30)).manhattanLength() <= 2
        image = companion.grab().toImage()
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(60, 60).alpha() == 255
        underlay.showFullScreen()
        underlay.activateWindow()
        QTest.qWait(900)
        assert not companion.isVisible(), "Не скрыт при полноэкранной работе"
        underlay.showNormal()
        QTest.qWait(900)
        assert companion.isVisible(), "Не вернулся после полноэкранного режима"
        out = Path("artifacts/t03")
        out.mkdir(parents=True, exist_ok=True)
        image.save(str(out / "companion.png"))
        (out / "verification.json").write_text(
            json.dumps(
                {
                    "platform": app.platformName(),
                    "alpha": True,
                    "click_through": True,
                    "above_raised_window": True,
                    "drag": True,
                    "fullscreen_hide_restore": True,
                },
                indent=2,
            )
            + "\n"
        )
        print("T03: прозрачность, клики, верхний слой, перенос и fullscreen — успешно")
    finally:
        companion.monitor.stop()
        companion.close()
        underlay.close()
        QCursor.setPos(cursor)
        x11.XCloseDisplay(display)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    app = QApplication([])
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(100)
    companion = CompanionProbe()
    if args.verify:
        verify(app, companion)
        return 0
    rect = app.primaryScreen().availableGeometry()
    companion.move(rect.right() - 160, rect.bottom() - 160)
    companion.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
