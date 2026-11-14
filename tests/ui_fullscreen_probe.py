"""Отказ и зависание xprop не должны блокировать цикл событий."""

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess, QTimer
from PySide6.QtTest import QTest

from ritm.platform.fullscreen import FullscreenMonitor

app = QCoreApplication([])
folder = Path(sys.argv[1])
command = folder / "xprop"
command.write_text(
    "#!/usr/bin/python3\nimport sys\nsys.stderr.write('private-token')\nsys.exit(1)\n"
)
command.chmod(0o700)
os.environ["PATH"] = str(folder)
monitor = FullscreenMonitor("xcb")
results = []
monitor.changed.connect(lambda value, error: results.append((value, error)))


def wait(predicate):
    until = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < until
        app.processEvents()
        QTest.qWait(10)


try:
    monitor.fullscreen = True
    monitor.setEnabled(True)
    wait(lambda: results and results[-1][1])
    assert results[-1][0] and "private-token" not in results[-1][1]
    command.write_text("#!/usr/bin/python3\nprint('_NET_ACTIVE_WINDOW: 0x0')\n")
    wait(lambda: monitor._process.state() == QProcess.NotRunning)
    monitor.poll()
    wait(lambda: results[-1] == (False, ""))
    command.write_text("#!/usr/bin/python3\nimport time\ntime.sleep(10)\n")
    monitor.poll()
    responsive = []
    QTimer.singleShot(10, lambda: responsive.append(True))
    wait(lambda: results[-1][1])
    assert responsive
    monitor.setEnabled(False)
    assert not monitor.fullscreen
finally:
    monitor.close()
    assert monitor._process.state() == QProcess.NotRunning
