"""Окно Qt Quick, его положение и обычные команды фокуса без ИИ."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic

from PySide6.QtCore import Property, QEvent, QObject, QPoint, QRect, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QBitmap, QGuiApplication, QImage, QRegion
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QMenu

from ritm.domain.companion import LABELS, Reactions
from ritm.platform.fullscreen import FullscreenMonitor
from ritm.storage.companion import DEFAULT, PETS, SIZES, load_companion, pet_asset, save_companion


def fit_position(x, y, size, screens, height=None):
    height = height or size
    if not screens:
        return (0, 0)
    if x is None or y is None:
        rect = screens[0]
        x, y = rect.right() - size - 16, rect.bottom() - height - 16
    rect = next((r for r in screens if r.contains(QPoint(x + size // 2, y + height // 2))), None)
    if rect is None:
        rect = min(screens, key=lambda r: abs(r.center().x() - x) + abs(r.center().y() - y))
    return (
        max(rect.left(), min(x, rect.right() - size + 1)),
        max(rect.top(), min(y, rect.bottom() - height + 1)),
    )


class CompanionController(QObject):
    changed = Signal()
    _ready = Signal(str, object, str)

    def __init__(self, profile, backend, *, open_main=lambda: None, clock=monotonic):
        super().__init__()
        self.profile, self.backend, self.open_main = profile, backend, open_main
        self._settings = dict(DEFAULT)
        self._error = ""
        self._errors = {}
        self._pending = 0
        self._closed = False
        self._loaded = False
        self._hidden = False
        self._drag = None
        self._image = QImage()
        self._clock = clock
        self._reactions = Reactions()
        self._fullscreen = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-companion")
        self._ready.connect(self._finish)
        self._engine = QQmlApplicationEngine()
        self._engine.setInitialProperties({"companion": self})
        self._engine.load(
            QUrl.fromLocalFile(
                str(Path(__file__).resolve().parents[1] / "qml" / "CompanionWindow.qml")
            )
        )
        if not self._engine.rootObjects():
            self._pool.shutdown(wait=True)
            raise RuntimeError("Не удалось открыть окно компаньона")
        self.window = self._engine.rootObjects()[0]
        self.window.imageReadyChanged.connect(self._render_ready)
        self.window.installEventFilter(self)
        self.menu = QMenu()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.showAgain)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._persist)
        self._reaction_timer = QTimer(self)
        self._reaction_timer.setInterval(1000)
        self._reaction_timer.timeout.connect(self._react)
        self._reaction_timer.start()
        backend.focusChanged.connect(self._react)
        backend.taskCompleted.connect(self._completed)
        app = QGuiApplication.instance()
        self._monitor = FullscreenMonitor(app.platformName(), self)
        self._monitor.changed.connect(self._fullscreen_changed)
        app.screenAdded.connect(self._screen_changed)
        app.screenRemoved.connect(self._screen_changed)
        self._screens = set(app.screens())
        for screen in self._screens:
            screen.availableGeometryChanged.connect(self._screen_changed)
        self._run("load", lambda: load_companion(profile))

    @Property("QVariantMap", notify=changed)
    def state(self):
        return dict(
            self._settings,
            error=self._error,
            busy=self._pending > 0,
            hidden=self._hidden,
            fullscreen=self._fullscreen,
            mode=self._reactions.mode,
            status=LABELS[self._reactions.mode],
            phrase=self._reactions.text,
            name=PETS[self._settings["pet"]],
            image=QUrl.fromLocalFile(str(pet_asset(self._settings["pet"]))).toString(),
        )

    def _run(self, action, operation):
        self._pending += 1
        self.changed.emit()

        def work():
            try:
                result, error = operation(), ""
            except (OSError, ValueError) as failure:
                result, error = (
                    None,
                    (
                        str(failure)
                        if isinstance(failure, ValueError)
                        else "Не удалось сохранить или загрузить настройки компаньона."
                    ),
                )
            self._ready.emit(action, result, error)

        self._pool.submit(work)

    @Slot(str, object, str)
    def _finish(self, action, result, error):
        if self._closed:
            return
        self._pending -= 1
        self._errors[action] = error
        self._error = " ".join(value for value in self._errors.values() if value)
        if action == "load":
            self._loaded = True
            if result is not None:
                self._settings = result
            self._monitor.setEnabled(
                self._settings["enabled"] and self._settings["hide_fullscreen"]
            )
            self._load_image()
        elif action == "image":
            self._image = result if result is not None else QImage()
            self._layout()
        self.changed.emit()

    def _visible_for_speech(self):
        return (
            self._settings["enabled"]
            and not self._hidden
            and not self._fullscreen
            and not self._image.isNull()
            and bool(self.window.property("imageReady"))
        )

    def _render_ready(self):
        if not self._closed:
            self._layout()
            self._react()

    def _react(self):
        if self._closed:
            return
        before = (self._reactions.mode, self._reactions.text)
        self._reactions.update(
            self.backend.focus, self._clock(), self._settings, self._visible_for_speech()
        )
        if before != (self._reactions.mode, self._reactions.text):
            self.changed.emit()

    def _completed(self, _identifier):
        if not self._closed and self._settings["enabled"]:
            self._reactions.celebrate(self._clock(), self._settings, self._visible_for_speech())
            self._react()
            self.changed.emit()

    def _fullscreen_changed(self, fullscreen, error):
        if self._closed:
            return
        if fullscreen == self._fullscreen and error == self._errors.get("fullscreen", ""):
            return
        if fullscreen and self._drag is not None:
            self._drag = None
            self._settings.update(x=self.window.x(), y=self.window.y())
            self._save_timer.start(200)
        self._fullscreen = fullscreen
        self._errors["fullscreen"] = error
        self._error = " ".join(value for value in self._errors.values() if value)
        self._layout()
        self._react()
        self.changed.emit()

    def _load_image(self):
        pet = self._settings["pet"]

        def read():
            image = QImage(str(pet_asset(pet)))
            if image.isNull():
                raise ValueError("Картинка персонажа недоступна. Выберите другого компаньона.")
            return image

        self._run("image", read)

    def _layout(self):
        if self._closed:
            return
        size = self._settings["size"]
        self.window.resize(size, size + 100)
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        x, y = fit_position(self._settings["x"], self._settings["y"], size, screens, size + 100)
        self._settings.update(x=x, y=y)
        self.window.setPosition(x, y)
        if not self._image.isNull():
            image = self._image.scaled(
                size - 32, size - 32, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            region = (
                QRegion(QBitmap.fromImage(image.createAlphaMask()))
                if image.hasAlphaChannel()
                else QRegion(image.rect())
            )
            region.translate((size - image.width()) // 2, (size - image.height()) // 2)
            region |= QRegion(QRect(8, size, size - 16, 88))
            self.window.setMask(region)
        self.window.setVisible(
            self._settings["enabled"]
            and not self._hidden
            and not self._fullscreen
            and not self._image.isNull()
            and bool(self.window.property("imageReady"))
        )

    def _screen_changed(self, *_):
        if self._closed or not self._loaded:
            return
        current = set(QGuiApplication.screens())
        for screen in current - self._screens:
            screen.availableGeometryChanged.connect(self._screen_changed)
        self._screens = current
        self._layout()
        if not self._errors.get("load"):
            self._save_timer.start(200)

    def _persist(self):
        data = dict(self._settings)
        self._run("save", lambda: save_companion(self.profile, data))

    @Slot(bool, str, int)
    @Slot(bool, str, int, bool, str, int, bool, bool)
    def configure(
        self,
        enabled,
        pet,
        size,
        muted=None,
        frequency=None,
        break_minutes=None,
        reduce_motion=None,
        hide_fullscreen=None,
    ):
        if self._pending or self._closed:
            return
        from ritm.storage.companion import validate_companion

        try:
            settings = dict(self._settings, enabled=enabled, pet=pet, size=size)
            if muted is not None:
                settings.update(
                    muted=muted,
                    frequency=frequency,
                    break_minutes=break_minutes,
                    reduce_motion=reduce_motion,
                    hide_fullscreen=hide_fullscreen,
                )
            self._settings = validate_companion(settings)
        except ValueError as error:
            self._error = str(error)
            self.changed.emit()
            return
        self._hidden = False
        self._errors["load"] = ""
        self._hide_timer.stop()
        self._monitor.setEnabled(enabled and self._settings["hide_fullscreen"])
        self._layout()
        self.window.hide()
        self._load_image()
        self._persist()
        self._react()

    @Slot()
    def showAgain(self):
        if not self._closed:
            self._hidden = False
            self._hide_timer.stop()
            self._layout()
            self.changed.emit()

    @Slot()
    def hideTemporarily(self):
        self._hidden = True
        self.window.hide()
        self._hide_timer.start(300000)
        self._react()
        self.changed.emit()

    @Slot()
    def disable(self):
        self._settings["enabled"] = False
        self._monitor.setEnabled(False)
        self._react()
        self._hide_timer.stop()
        self.window.hide()
        self._persist()

    @Slot()
    def toggleFocus(self):
        if self.backend.busy:
            return
        focus = self.backend.focus
        if focus.get("recovery"):
            self.open_main()
        elif focus:
            self.backend.changeFocus("resume" if focus["paused"] else "pause")
        elif self.backend.detail.get("id") and not self.backend.detail.get("completed"):
            self.backend.startFocus(self.backend.detail["id"], "", False)
        else:
            self.open_main()

    def showMenu(self, point):
        self.menu.clear()
        self.menu.addAction("Открыть Ритм", self.open_main)
        focus = self.backend.focus
        action = self.menu.addAction(
            "Открыть восстановление сессии"
            if focus.get("recovery")
            else "Продолжить фокус"
            if focus.get("paused")
            else "Приостановить фокус"
            if focus
            else "Начать фокус выбранной задачи",
            self.toggleFocus,
        )
        action.setEnabled(not self.backend.busy)
        pets = self.menu.addMenu("Персонаж")
        for pet, label in PETS.items():
            action = pets.addAction(
                label, lambda pet=pet: self.configure(True, pet, self._settings["size"])
            )
            action.setCheckable(True)
            action.setChecked(pet == self._settings["pet"])
            action.setEnabled(not self._pending)
        sizes = self.menu.addMenu("Размер")
        for size in SIZES:
            action = sizes.addAction(
                str(size), lambda size=size: self.configure(True, self._settings["pet"], size)
            )
            action.setEnabled(not self._pending)
        mute = self.menu.addAction("Отключить реплики")
        mute.setCheckable(True)
        mute.setChecked(self._settings["muted"])
        mute.triggered.connect(self._mute)
        self.menu.addAction("Скрыть на 5 минут", self.hideTemporarily)
        self.menu.addAction("Выключить компаньона", self.disable)
        self.menu.popup(point)

    def _mute(self, checked):
        self._settings["muted"] = checked
        self._react()
        self._persist()

    def eventFilter(self, watched, event):
        if watched is self.window and not self._closed:
            kind = event.type()
            if kind == QEvent.Close:
                event.ignore()
                self.disable()
                return True
            if kind == QEvent.MouseButtonPress:
                if event.button() == Qt.RightButton:
                    self.showMenu(event.globalPosition().toPoint())
                    return True
                if event.button() == Qt.LeftButton:
                    self._drag = event.globalPosition().toPoint() - self.window.position()
                    return True
            if kind == QEvent.MouseMove and self._drag is not None:
                point = event.globalPosition().toPoint() - self._drag
                self.window.setPosition(point)
                return True
            if kind == QEvent.MouseButtonRelease and self._drag is not None:
                self._drag = None
                self._settings.update(x=self.window.x(), y=self.window.y())
                self._layout()
                self._save_timer.start(200)
                return True
        return super().eventFilter(watched, event)

    def close(self):
        self._closed = True
        self._reaction_timer.stop()
        self._monitor.close()
        self._hide_timer.stop()
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._persist()
        self.menu.close()
        self.window.removeEventFilter(self)
        self.window.close()
        self._pool.shutdown(wait=True)
        self._engine = None
