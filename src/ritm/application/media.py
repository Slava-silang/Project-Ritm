"""Подготовка выбранных вложений без доступа к произвольным файлам из ответа модели."""

import base64
import os
import stat
from pathlib import Path
from threading import Event

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect
from PySide6.QtGui import QImageReader, QTransform

from ritm.application.file_context import available_files, selected_texts
from ritm.integrations.assistant import SourceFile
from ritm.integrations.file_text import FileTextError
from ritm.storage.attachments import saved_attachment_path
from ritm.storage.sqlite import TaskStore

MAX_BYTES = 8 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_PIXELS = 12_000_000
IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
DOCUMENTS = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".json": "application/json",
    ".html": "text/html",
    ".xml": "application/xml",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".rtf": "application/rtf",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".py": "text/x-python",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".java": "text/x-java",
}


def capability(suffix, connection):
    suffix = suffix.lower()
    if suffix in IMAGES:
        return "image" if "image" in connection.input_kinds else ""
    if suffix in DOCUMENTS and "file" in connection.input_kinds:
        return "file"
    if suffix in (".txt", ".md", ".markdown", ".pdf"):
        return "text"
    return ""


def read_copy(path: Path, cancelled: Event):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_BYTES:
            raise FileTextError("Выберите непустой обычный файл размером до 8 МиБ.")
        chunks, size = [], 0
        while True:
            if cancelled.is_set():
                raise FileTextError("Подготовка файлов отменена.")
            chunk = stream.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                raise FileTextError("Файл превышает 8 МиБ.")
            chunks.append(chunk)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise FileTextError("Файл изменился. Выберите его заново.")
        return b"".join(chunks)


def image_from_bytes(data):
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > MAX_PIXELS:
        raise FileTextError("Изображение повреждено или превышает 12 миллионов пикселей.")
    if bytes(reader.format()).lower() not in (b"png", b"jpeg", b"webp") or reader.imageCount() > 1:
        raise FileTextError("Поддерживаются неподвижные PNG, JPEG и WebP.")
    image = reader.read()
    if image.isNull():
        raise FileTextError("Не удалось прочитать изображение.")
    return image


def transformed_image(data, turns, crop):
    if type(turns) is not int or turns not in range(4):
        raise FileTextError("Неверный поворот изображения.")
    if len(crop) != 4 or any(type(v) is not int for v in crop):
        raise FileTextError("Неверная область обрезки.")
    x, y, width, height = crop
    if not (0 <= x < 100 and 0 <= y < 100 and 1 <= width <= 100 - x and 1 <= height <= 100 - y):
        raise FileTextError("Область обрезки должна находиться внутри изображения.")
    image = image_from_bytes(data).transformed(QTransform().rotate(turns * 90))
    return image.copy(
        QRect(
            image.width() * x // 100,
            image.height() * y // 100,
            max(1, image.width() * width // 100),
            max(1, image.height() * height // 100),
        )
    )


def png_bytes(image):
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise FileTextError("Не удалось подготовить изображение.")
    return bytes(buffer.data())


def chosen_entry(profile, context, identifier):
    with TaskStore(profile / "tasks.sqlite3") as store:
        match = available_files(store, context).get(identifier)
        if match is None:
            raise FileTextError("Файл больше недоступен. Обновите список.")
        return match[0]


def prepare_sources(profile, context, identifiers, cancelled, connection):
    if len(identifiers) > 5 or len(set(identifiers)) != len(identifiers):
        raise FileTextError("Выберите не более пяти разных файлов.")
    files, texts, text_ids, total = [], (), [], 0
    for identifier in identifiers:
        entry = chosen_entry(profile, context, identifier)
        kind = capability(entry.suffix, connection)
        if not kind:
            raise FileTextError(
                f"«{entry.name}»: формат не поддерживается выбранной моделью. "
                "Выберите другую модель или снимите отметку."
            )
        if kind == "text":
            text_ids.append(identifier)
            continue
        try:
            data = read_copy(saved_attachment_path(profile, entry), cancelled)
            if kind == "image":
                image_from_bytes(data)
        except (OSError, ValueError) as error:
            reason = str(error) if isinstance(error, FileTextError) else "копия недоступна"
            raise FileTextError(f"«{entry.name}»: {reason}") from None
        total += len(data)
        if total > MAX_TOTAL:
            raise FileTextError("Суммарный размер файлов превышает 20 МиБ.")
        files.append(
            SourceFile(
                entry.name,
                (IMAGES if kind == "image" else DOCUMENTS)[entry.suffix.lower()],
                data,
                kind,
            )
        )
    if text_ids:
        texts = selected_texts(profile, context, tuple(text_ids), cancelled)
    return texts, tuple(files)


def data_url(mime, data):
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
