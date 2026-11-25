"""Open or render the independent theme mockups with the project Qt."""

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QRawFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest


def capture(window: QQuickWindow, destination: Path) -> None:
    image = window.grabWindow()
    if image.isNull() or not image.save(str(destination)):
        raise RuntimeError(f"Failed to capture {destination}")


def render(window: QQuickWindow, output: Path, names: tuple[str, ...]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    button = window.findChild(QQuickItem, "newTask")
    editor = window.findChild(QObject, "previewEditor")
    title = window.findChild(QQuickItem, "previewTitle")
    if button is None or editor is None or title is None:
        raise RuntimeError("Could not find the new task button, editor, or title field")
    for variant, name in enumerate(names):
        window.setProperty("variant", variant)
        QTest.qWait(200)
        capture(window, output / f"{name}-tasks.png")
        position = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=position.toPoint())
        QTest.qWait(220)
        if not editor.property("opened") or not title.hasActiveFocus():
            raise RuntimeError(f"Task title did not receive focus in {name}")
        if window.property("lightPreview") and editor.property("x") <= window.property("sideWidth"):
            raise RuntimeError(f"The editor crossed the navigation boundary in {name}")
        QTest.mouseMove(window, QPoint(10, 75))
        capture(window, output / f"{name}-dialog.png")
        QTest.keyClick(window, Qt.Key.Key_Escape)
        QTest.qWait(130)
        if editor.property("visible"):
            raise RuntimeError(f"Escape did not dismiss the editor in {name}")
    ctrl = Qt.KeyboardModifier.ControlModifier
    shift = Qt.KeyboardModifier.ShiftModifier
    QTest.keyClick(window, Qt.Key.Key_M, ctrl | shift)
    QTest.keyClick(window, Qt.Key.Key_N, ctrl)
    QTest.qWait(30)
    if not window.property("reduceMotion") or not editor.property("opened"):
        raise RuntimeError("The create-task shortcut did not open the editor with reduced motion")
    QTest.keyClick(window, Qt.Key.Key_Escape)
    QTest.qWait(30)
    if editor.property("visible"):
        raise RuntimeError("Escape did not close the editor with reduced motion")


def render_font(app: QGuiApplication) -> str:
    if sys.platform == "win32" and app.platformName() == "offscreen":
        # The Windows offscreen platform does not discover installed fonts.
        fonts = Path(os.environ["WINDIR"]) / "Fonts"
        for name in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf"):
            font_path = fonts / name
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))
        family = "Segoe UI"
    else:
        family = app.font().family()
    if not QRawFont.fromFont(QFont(family)).supportsCharacter(0x042F):
        raise RuntimeError(f"The render font {family!r} has no Cyrillic glyphs")
    return family


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render", type=Path, metavar="DIRECTORY")
    parser.add_argument("--variant", choices=("1", "2", "3"), default="1")
    parser.add_argument("--theme", choices=("dark", "light"), default="dark")
    options = parser.parse_args()
    if options.render:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
        os.environ.setdefault("QT_SCALE_FACTOR", "1")

    app = QGuiApplication([])
    engine = QQmlApplicationEngine()
    engine.setInitialProperties(
        {
            "variant": int(options.variant) - 1,
            "lightPreview": options.theme == "light",
            "uiFont": render_font(app) if options.render else app.font().family(),
            "visible": True,
        }
    )
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("Preview.qml").resolve())))
    if not engine.rootObjects():
        return 1
    if options.render:
        names = (
            ("mint", "linen", "sky")
            if options.theme == "light"
            else ("graphite", "dusk", "notebook")
        )
        render(engine.rootObjects()[0], options.render, names)
        return 0
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
