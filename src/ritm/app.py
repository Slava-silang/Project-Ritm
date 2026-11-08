"""Точка входа и загрузка начального QML-окна."""

import argparse
import signal
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QStandardPaths, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from ritm import __version__
from ritm.ui.tasks import TaskController


def load_window(controller: TaskController) -> QQmlApplicationEngine:
    """Загрузить ресурсы установленного пакета независимо от рабочей папки."""
    engine = QQmlApplicationEngine()
    engine.setInitialProperties({"backend": controller})
    qml_path = Path(__file__).resolve().parent / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        raise RuntimeError("Не удалось открыть окно «Ритма». Проверьте установку приложения.")
    return engine


def main() -> int:
    parser = argparse.ArgumentParser(description="Ритм — задачи и время в своём темпе.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Открыть окно и автоматически закрыть его для проверки запуска.",
    )
    parser.add_argument("--profile", type=Path, help="Отдельная папка данных для проверки.")
    args = parser.parse_args()

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv[:1])
    app.setOrganizationName("ProjectRitm")
    app.setApplicationName("Ritm")
    app.setApplicationDisplayName("Ритм")
    app.setApplicationVersion(__version__)

    temporary = (
        TemporaryDirectory(prefix="ritm-smoke-") if args.smoke_test and not args.profile else None
    )
    profile = args.profile or Path(
        temporary.name
        if temporary
        else QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    )
    controller = TaskController(profile)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        signal.signal(sig, lambda *_: QTimer.singleShot(0, app.quit))
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(100)
    engine = None
    try:
        engine = load_window(controller)
        if args.smoke_test:
            QTimer.singleShot(800, engine.rootObjects()[0].close)
        return app.exec()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        controller.close()
        engine = None
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if temporary:
            temporary.cleanup()
