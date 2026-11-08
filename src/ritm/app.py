"""Точка входа и загрузка начального QML-окна."""

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from ritm import __version__


def load_window() -> QQmlApplicationEngine:
    """Загрузить ресурсы установленного пакета независимо от рабочей папки."""
    engine = QQmlApplicationEngine()
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
    args = parser.parse_args()

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv[:1])
    app.setOrganizationName("ProjectRitm")
    app.setApplicationName("Ritm")
    app.setApplicationDisplayName("Ритм")
    app.setApplicationVersion(__version__)

    try:
        engine = load_window()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1

    if args.smoke_test:
        QTimer.singleShot(800, engine.rootObjects()[0].close)

    return app.exec()
