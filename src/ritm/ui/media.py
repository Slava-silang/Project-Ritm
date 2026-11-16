"""Фоновый импорт и редактируемый предпросмотр управляемых изображений."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from uuid import uuid4

from PySide6.QtCore import Property, QObject, Qt, QUrl, Signal, Slot

from ritm.application.media import (
    IMAGES,
    MAX_BYTES,
    chosen_entry,
    data_url,
    png_bytes,
    read_copy,
    transformed_image,
)
from ritm.integrations.file_text import FileTextError
from ritm.storage.attachments import copy_attachment, saved_attachment_path
from ritm.storage.sqlite import TaskStore


class MediaController(QObject):
    changed = Signal()
    imported = Signal(str)
    _ready = Signal(object, object, str)

    def __init__(self, profile, locked):
        super().__init__()
        self.profile, self.locked = profile, locked
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-media")
        self._busy, self._closed = False, False
        self._id, self._view, self._error = None, {}, ""
        self._ready.connect(self._finish)

    @Property("QVariantMap", notify=changed)
    def state(self):
        return {**self._view, "busy": self._busy, "error": self._error}

    @Slot()
    def reset(self):
        self._id, self._view, self._error = None, {}, ""
        self.changed.emit()

    def _run(self, job):
        if self._busy or self._closed or self.locked():
            return
        identifier = self._id = uuid4()
        self._busy, self._error = True, ""
        self.changed.emit()

        def work():
            try:
                result = job()
                self._ready.emit(identifier, result, "")
            except FileTextError as error:
                self._ready.emit(identifier, {}, str(error))
            except Exception:
                self._ready.emit(
                    identifier, {}, "Не удалось подготовить файл. Проверьте копию и папку профиля."
                )

        self._pool.submit(work)

    @Slot(QUrl)
    def importFile(self, url):
        def job():
            if not url.isLocalFile():
                raise FileTextError("Выберите локальный файл.")
            with TaskStore(self.profile / "tasks.sqlite3") as store:
                entry = copy_attachment(store, self.profile, None, None, Path(url.toLocalFile()))
            return {"created": str(entry.id), "name": entry.name}

        self._run(job)

    @Slot(str, str, int, "QVariantList", bool)
    def preview(self, context, identifier, turns, crop, save):
        def job():
            entry = chosen_entry(self.profile, context, identifier)
            if entry.suffix.lower() not in IMAGES:
                raise FileTextError("Предпросмотр доступен для PNG, JPEG и WebP.")
            data = read_copy(saved_attachment_path(self.profile, entry), Event())
            image = transformed_image(data, turns, list(crop))
            if save:
                encoded = png_bytes(image)
                if len(encoded) > MAX_BYTES:
                    raise FileTextError("Копия превышает 8 МиБ. Обрежьте меньшую область.")
                with TemporaryDirectory(prefix="ritm-image-") as folder:
                    path = Path(folder) / "Изображение — копия.png"
                    path.write_bytes(encoded)
                    with TaskStore(self.profile / "tasks.sqlite3") as store:
                        saved = copy_attachment(store, self.profile, None, None, path)
                return {"created": str(saved.id), "name": saved.name}
            thumbnail = image.scaled(900, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return {
                "id": identifier,
                "name": entry.name,
                "width": image.width(),
                "height": image.height(),
                "url": data_url("image/png", png_bytes(thumbnail)),
            }

        self._run(job)

    @Slot(object, object, str)
    def _finish(self, identifier, result, error):
        self._busy = False
        if self._closed or identifier != self._id:
            self.changed.emit()
            return
        self._view, self._error = result, error
        self.changed.emit()
        if not error and result.get("created"):
            self.imported.emit(result["created"])

    def close(self):
        self._closed = True
        self._pool.shutdown(wait=True, cancel_futures=True)
