"""Точка входа и загрузка начального QML-окна."""

import argparse
import signal
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QLockFile, QStandardPaths, QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from ritm import __version__
from ritm.ui.tasks import TaskController


def load_window(
    controller: TaskController, desktop=None, assistant=None, connection=None
) -> QQmlApplicationEngine:
    """Загрузить ресурсы установленного пакета независимо от рабочей папки."""
    engine = QQmlApplicationEngine()
    engine.setInitialProperties(
        {
            "backend": controller,
            "desktop": desktop,
            "assistant": assistant,
            "connection": connection,
        }
    )
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
    parser.add_argument("--start-hidden", action="store_true", help="Скрыть окно при наличии трея.")
    args = parser.parse_args()

    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv[:1])
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
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:
        print("Не удалось открыть папку профиля «Ритма».", file=sys.stderr)
        return 1
    lock = QLockFile(str(profile / "ritm.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        print("Профиль «Ритма» уже открыт или недоступен для записи.", file=sys.stderr)
        return 1
    controller = TaskController(profile, automatic_backup=not args.smoke_test)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        signal.signal(sig, lambda *_: QTimer.singleShot(0, app.quit))
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(100)
    engine = None
    power = None
    desktop = None
    assistant = None
    connection = None
    try:
        if not args.smoke_test:
            from ritm.platform.desktop import DesktopIntegration

            command = (
                [str(Path(sys.argv[0]).resolve())]
                if "__compiled__" in globals()
                else [str(Path(sys.executable).absolute()), "-m", "ritm"]
            )
            command += ["--profile", str(profile.resolve())]
            desktop = DesktopIntegration(
                app,
                profile,
                Path(
                    QStandardPaths.writableLocation(
                        QStandardPaths.StandardLocation.GenericConfigLocation
                    )
                ),
                command,
                supported=sys.platform.startswith("linux"),
            )
        if not args.smoke_test:
            from ritm.ui.assistant import AssistantController

            assistant = AssistantController(profile)
            from ritm.ui.connection import ConnectionController

            connection = ConnectionController(profile, assistant)
        engine = load_window(controller, desktop, assistant, connection)
        if desktop is not None:
            desktop.attach(engine.rootObjects()[0], args.start_hidden)
        if sys.platform.startswith("linux") and not args.smoke_test:
            from ritm.platform.power import LinuxPowerMonitor

            power = LinuxPowerMonitor(controller)
        if args.smoke_test:
            QTimer.singleShot(800, engine.rootObjects()[0].close)
        return app.exec()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
        if assistant is not None:
            assistant.close()
        if desktop is not None:
            desktop.close()
        if power is not None:
            power.close()
        controller.close()
        engine = None
        lock.unlock()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if temporary:
            temporary.cleanup()
