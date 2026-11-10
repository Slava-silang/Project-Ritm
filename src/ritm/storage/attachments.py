"""Управляемые копии вложений в профиле."""

from __future__ import annotations

import errno
import os
import re
import shutil
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from ritm.storage.sqlite import TaskStore

SAFE_SUFFIX = re.compile(r"(?:\.[a-zA-Z0-9]{1,16})?")


@dataclass(frozen=True)
class Attachment:
    id: UUID
    task_id: UUID | None
    node_id: UUID | None
    name: str
    suffix: str
    size: int


def _attachment_path(profile: Path, identifier: UUID, suffix: str) -> Path:
    if not SAFE_SUFFIX.fullmatch(suffix):
        raise ValueError("Неверное расширение сохранённого вложения")
    root = profile / "attachments"
    directory = root / identifier.hex
    path = directory / ("file" + suffix)
    if any(part.is_symlink() for part in (root, directory, path)):
        raise ValueError("Путь к вложению содержит символическую ссылку")
    return path


def copy_attachment(
    store: TaskStore, profile: Path, task_id: UUID | None, node_id: UUID | None, source: Path
) -> Attachment:
    suffix = source.suffix
    if not SAFE_SUFFIX.fullmatch(suffix):
        suffix = ""
    attachment = Attachment(uuid4(), task_id, node_id, source.name, suffix, 0)
    destination = _attachment_path(profile, attachment.id, attachment.suffix)
    created = False
    try:
        # Nonblocking open avoids waiting for an accidentally selected FIFO on Unix.
        fd = os.open(
            source, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        )
        with os.fdopen(fd, "rb") as original:
            if not stat.S_ISREG(os.fstat(original.fileno()).st_mode):
                raise ValueError("Выберите обычный локальный файл")
            destination.parent.parent.mkdir(exist_ok=True)
            destination.parent.mkdir(mode=0o700)
            created = True
            with destination.open("xb") as copy:
                shutil.copyfileobj(original, copy, length=1024 * 1024)
                size = copy.tell()
                copy.flush()
                os.fsync(copy.fileno())
        attachment = Attachment(attachment.id, task_id, node_id, source.name, suffix, size)
        store.add_attachment(attachment)
        return attachment
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
            destination.parent.rmdir()
        raise


def saved_attachment_path(profile: Path, attachment: Attachment) -> Path:
    path = _attachment_path(profile, attachment.id, attachment.suffix)
    if not path.is_file():
        raise ValueError("Копия вложения отсутствует в профиле")
    return path


def clean_deleted_attachments(store: TaskStore, profile: Path):
    for identifier, suffix in store.pending_attachment_cleanup():

        def remove(identifier=identifier, suffix=suffix):
            path = _attachment_path(profile, identifier, suffix)
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError as error:
                if error.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST):
                    raise

        try:
            store.finish_attachment_cleanup(identifier, remove)
        except (OSError, ValueError, sqlite3.Error):
            # The authorized deletion remains queued for a later retry.
            continue
