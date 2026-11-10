import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_close_menu_terminates_process(tmp_path):
    code = f"""
import runpy
from PySide6.QtCore import QTimer, QPoint
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication
probe = runpy.run_path({str(ROOT / "scripts/companion_probe.py")!r})
app = QApplication([])
widget = probe["CompanionProbe"]()
widget.show()
def open_menu():
    QTimer.singleShot(100, lambda: app.activePopupWidget().actions()[0].trigger())
    app.sendEvent(widget, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(60,60)))
QTimer.singleShot(100, open_menu)
raise SystemExit(app.exec())
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=os.environ
        | {
            "QT_QPA_PLATFORM": os.environ.get("RITM_SHUTDOWN_PLATFORM", "offscreen"),
            "XDG_CACHE_HOME": str(tmp_path),
        },
        capture_output=True,
        timeout=4,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("termination", [signal.SIGINT, signal.SIGTERM])
def test_terminal_signal_terminates_process(tmp_path, termination):
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/companion_probe.py")],
        env=os.environ
        | {
            "QT_QPA_PLATFORM": os.environ.get("RITM_SHUTDOWN_PLATFORM", "offscreen"),
            "XDG_CACHE_HOME": str(tmp_path),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Give Qt time to enter its event loop; failure to stay alive is also a failure.
        import time

        time.sleep(0.6)
        assert process.poll() is None
        process.send_signal(termination)
        _, error = process.communicate(timeout=4)
        assert process.returncode == 0, error
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
