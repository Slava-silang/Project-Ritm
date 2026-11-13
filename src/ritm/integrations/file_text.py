"""Ограниченное чтение текста в отдельном отменяемом процессе."""

import codecs
import json
import os
import stat
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from threading import Event

SUPPORTED = {".txt", ".md", ".markdown", ".pdf"}
MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT = 60000
MAX_PAGES = 100


class FileTextError(ValueError):
    pass


def extract_text(path: Path) -> str:
    if path.suffix.lower() not in SUPPORTED:
        raise FileTextError("Поддерживаются TXT, Markdown и PDF с текстовым слоем.")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise FileTextError("Не удалось открыть обычный файл.")
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise FileTextError("Файл больше 8 МиБ. Выберите файл меньшего размера.")
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise FileTextError("PDF защищён паролем. Подготовьте копию без пароля.")
            if len(reader.pages) > MAX_PAGES:
                raise FileTextError("В PDF больше 100 страниц. Выберите нужный фрагмент.")
            pages = []
            length = 0
            for page in reader.pages:
                text = page.extract_text() or ""
                length += len(text)
                if length > MAX_TEXT:
                    raise FileTextError("В файле больше 60 000 символов. Выберите нужный фрагмент.")
                pages.append(text)
            text = "\n\n".join(pages)
            if not text.strip():
                raise FileTextError(
                    "В PDF нет текстового слоя. Сканы и изображения пока не читаются."
                )
        except FileTextError:
            raise
        except Exception:
            raise FileTextError(
                "Не удалось прочитать PDF. Файл повреждён или имеет неподдерживаемый формат."
            ) from None
    else:
        try:
            if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
                text = data.decode("utf-16")
            else:
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = data.decode("cp1251")
        except UnicodeError:
            raise FileTextError(
                "Не удалось прочитать кодировку. Сохраните текст как UTF-8."
            ) from None
        if any(ord(char) < 32 and char not in "\n\r\t\f" for char in text):
            raise FileTextError("Файл содержит двоичные данные вместо текста.")
    if len(text) > MAX_TEXT:
        raise FileTextError("В файле больше 60 000 символов. Выберите нужный фрагмент.")
    if not text.strip():
        raise FileTextError("В выбранном файле нет текста.")
    return text


def worker_main(path: Path) -> int:
    # Парсер не получает БД, токены или доступ к инструментам помощника.
    try:
        if sys.platform.startswith("linux"):
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        result = {"text": extract_text(path)}
    except FileTextError as error:
        result = {"error": str(error)}
    except Exception:
        result = {"error": "Не удалось прочитать сохранённую копию файла."}
    print(json.dumps(result, ensure_ascii=True))
    return 0


def read_text(path: Path, cancelled: Event, *, seconds=25) -> str:
    command = (
        [str(Path(sys.argv[0]).resolve()), "--extract-text"]
        if "__compiled__" in globals()
        else [sys.executable, "-m", "ritm.integrations.file_text"]
    )
    with subprocess.Popen(
        [*command, str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    ) as process:
        end = time.monotonic() + seconds
        while True:
            if cancelled.is_set() or time.monotonic() >= end:
                process.kill()
                process.communicate()
                raise FileTextError(
                    "Чтение файла отменено."
                    if cancelled.is_set()
                    else "Чтение файла заняло слишком много времени."
                )
            try:
                output, _ = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
    if cancelled.is_set():
        raise FileTextError("Чтение файла отменено.")
    try:
        if process.returncode != 0 or len(output) > MAX_TEXT * 12:
            raise ValueError
        result = json.loads(output)
        if "error" in result:
            raise FileTextError(result["error"])
        text = result["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise ValueError
        return text
    except (ValueError, KeyError, TypeError) as error:
        if isinstance(error, FileTextError):
            raise
        raise FileTextError("Не удалось прочитать сохранённую копию файла.") from None


if __name__ == "__main__":
    raise SystemExit(worker_main(Path(sys.argv[1])))
