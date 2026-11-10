"""Архивы SQLite и управляемых файлов с проверяемым восстановлением."""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from ritm.domain.calendar import from_instant_us
from ritm.domain.weights import initialize_weights
from ritm.storage.attachments import managed_attachment_path
from ritm.storage.codec import decode_task
from ritm.storage.sqlite import APPLICATION_ID, SCHEMA_VERSION, TaskStore

DATABASE = "tasks.sqlite3"
MANIFEST = "manifest.json"
FORMAT = "ritm-profile-1"
AUTO_NAME = re.compile(r"ritm-auto-(\d{4}-\d{2}-\d{2})-([a-f0-9]{32})\.zip")
CHUNK_SIZE = 1024 * 1024


def backup_folder(profile: Path) -> Path:
    settings = profile / "backup-settings.json"
    if not settings.exists():
        return (profile / "backups").absolute()
    try:
        folder = Path(json.loads(settings.read_text(encoding="utf-8"))["folder"])
        if folder.is_absolute():
            return folder
    except (ValueError, TypeError, KeyError):
        pass
    raise ValueError("Настройки папки резервных копий повреждены")


def set_backup_folder(profile: Path, folder: Path):
    folder = folder.resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("Выберите папку для резервных копий")
    _write_profile_text(profile / "backup-settings.json", json.dumps({"folder": str(folder)}))


def _write_profile_text(path: Path, content: str):
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content.encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _sqlite(path: Path, mode: str):
    db = sqlite3.connect(path.absolute().as_uri() + "?mode=" + mode, uri=True)
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def _copy_database(source: Path, destination: Path, *, create: bool = False):
    with (
        closing(_sqlite(source, "ro")) as origin,
        closing(_sqlite(destination, "rwc" if create else "rw")) as target,
    ):
        origin.backup(target)


def _files(db: sqlite3.Connection) -> dict[UUID, str]:
    rows = db.execute("SELECT id,suffix FROM attachments UNION ALL SELECT id,suffix FROM materials")
    result = {}
    for identifier, suffix in rows:
        identifier = UUID(identifier)
        if identifier in result:
            raise ValueError("В копии повторяется идентификатор файла")
        result[identifier] = suffix
    return result


def _file_paths(profile: Path, files: dict[UUID, str]) -> dict[str, Path]:
    result = {}
    for identifier, suffix in files.items():
        path = managed_attachment_path(profile, identifier, suffix)
        result[path.relative_to(profile).as_posix()] = path
    return result


def _transfer(source, destination) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return digest.hexdigest()


def _manifest(archive: ZipFile) -> tuple[dict, str]:
    info = archive.getinfo(MANIFEST)
    if info.file_size > 16 * 1024 * 1024:
        raise ValueError("Описание архива слишком большое")
    content = archive.read(info)
    record = json.loads(content)
    if (
        not isinstance(record, dict)
        or record.get("format") != FORMAT
        or record.get("kind") not in ("auto", "manual")
        or not isinstance(record.get("files"), dict)
        or datetime.fromisoformat(record["created"]).utcoffset() is None
    ):
        raise ValueError("Неизвестный формат резервной копии")
    return record, hashlib.sha256(content).hexdigest()


def create_backup(
    profile: Path, folder: Path, now: datetime, *, automatic_id: str | None = None
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    kind = "auto" if automatic_id is not None else "manual"
    stamp = (
        f"{now.date().isoformat()}-{automatic_id}"
        if automatic_id is not None
        else f"{now:%Y-%m-%d-%H%M%S}-{uuid4().hex}"
    )
    target = folder / f"ritm-{kind}-{stamp}.zip"
    with TemporaryDirectory(prefix="ritm-snapshot-") as folder_name:
        snapshot = Path(folder_name) / DATABASE
        _copy_database(profile / DATABASE, snapshot, create=True)
        files = _file_paths(profile, _validate_database(snapshot))
        files[DATABASE] = snapshot
        with NamedTemporaryFile(dir=folder, delete=False) as stream:
            temporary = Path(stream.name)
        try:
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=3) as archive:
                hashes = {}
                for name, path in files.items():
                    if not path.is_file():
                        raise ValueError("Не найдена сохранённая копия вложения или материала")
                    with (
                        path.open("rb") as source,
                        archive.open(name, "w", force_zip64=True) as dest,
                    ):
                        before = os.fstat(source.fileno())
                        hashes[name] = _transfer(source, dest)
                        after = os.fstat(source.fileno())
                        if (before.st_size, before.st_mtime_ns) != (
                            after.st_size,
                            after.st_mtime_ns,
                        ):
                            raise ValueError(
                                "Файл изменился во время копирования. Повторите операцию"
                            )
                archive.writestr(
                    MANIFEST,
                    json.dumps(
                        {
                            "format": FORMAT,
                            "kind": kind,
                            "created": now.isoformat(),
                            "files": hashes,
                        }
                    ),
                )
            with temporary.open("rb+") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def daily_backup(profile: Path, folder: Path, now: datetime):
    day = now.date().isoformat()
    last_day = profile / "backup-last-day"
    if last_day.exists() and last_day.read_text(encoding="utf-8") == day:
        return
    profile_id = profile / "backup-profile-id"
    if not profile_id.exists():
        _write_profile_text(profile_id, uuid4().hex)
    identifier = UUID(profile_id.read_text(encoding="utf-8")).hex
    expected = folder / f"ritm-auto-{day}-{identifier}.zip"
    if not expected.exists() and not expected.is_symlink():
        create_backup(profile, folder, now, automatic_id=identifier)
    elif not _recognized_daily(expected, identifier):
        raise ValueError("Имя ежедневной копии уже занято другим файлом")
    daily = sorted(
        (
            entry
            for entry in folder.glob(f"ritm-auto-*-{identifier}.zip")
            if _recognized_daily(entry, identifier)
        ),
        reverse=True,
    )
    for expired in daily[7:]:
        expired.unlink(missing_ok=True)
    _write_profile_text(last_day, day)


def _recognized_daily(path: Path, identifier: str) -> bool:
    match = AUTO_NAME.fullmatch(path.name)
    if match is None or match[2] != identifier or path.is_symlink() or not path.is_file():
        return False
    try:
        with ZipFile(path) as archive:
            record, _ = _manifest(archive)
            return record["kind"] == "auto" and record["created"][:10] == match[1]
    except (OSError, BadZipFile, ValueError, KeyError, TypeError):
        return False


def _validate_database(path: Path) -> dict[UUID, str]:
    def schema(db):
        return db.execute(
            "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()

    # This archive format was introduced with schema v9. Validate against the
    # application's own schema before opening any data through TaskStore.
    with TaskStore(Path(":memory:")) as reference, closing(_sqlite(path, "ro")) as db:
        if (
            db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            or schema(db) != [tuple(row) for row in schema(reference._db)]
            or db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
            or db.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise ValueError("База в резервной копии повреждена или несовместима")
        files = _files(db)
    with TaskStore(path) as store:
        for task in store.list_tasks():
            initialize_weights(task)
        store.list_categories()
        store.list_work_blocks()
        for session in store.list_focus_sessions():
            from_instant_us(session.started_us)
        for before, after in store._db.execute("SELECT before_json,after_json FROM trash"):
            decode_task(before)
            if after is not None:
                decode_task(after)
    return files


def _read_archive(source: Path, staging: Path, expected: str | None = None) -> tuple[dict, dict]:
    try:
        with ZipFile(source) as archive:
            record, token = _manifest(archive)
            if expected is not None and token != expected:
                raise ValueError(
                    "Файл копии изменился. Выберите его и подтвердите восстановление снова"
                )
            hashes = record["files"]
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != set(hashes) | {MANIFEST}:
                raise ValueError("Список файлов архива повреждён")
            with archive.open(DATABASE) as origin, (staging / DATABASE).open("xb") as dest:
                if _transfer(origin, dest) != hashes[DATABASE]:
                    raise ValueError("Контрольная сумма базы не совпадает")
            files = _validate_database(staging / DATABASE)
            paths = _file_paths(staging, files)
            if set(hashes) != set(paths) | {DATABASE}:
                raise ValueError("В архиве отсутствует вложение или материал")
            for name, path in paths.items():
                with archive.open(name) as origin:
                    if expected is None:
                        actual = _transfer(origin, None)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with path.open("xb") as dest:
                            actual = _transfer(origin, dest)
                            dest.flush()
                            os.fsync(dest.fileno())
                    if actual != hashes[name]:
                        raise ValueError("Контрольная сумма вложения не совпадает")
            return {"created": record["created"], "token": token}, files
    except (BadZipFile, KeyError, TypeError, RuntimeError, sqlite3.Error) as error:
        raise ValueError("Резервная копия повреждена или не поддерживается") from error


def inspect_backup(source: Path) -> dict:
    with TemporaryDirectory(prefix="ritm-inspect-") as directory:
        preview, _ = _read_archive(source, Path(directory))
        return preview


def restore_backup(profile: Path, source: Path, token: str):
    """Publish new files first, then atomically replace SQLite through its backup API."""
    with TemporaryDirectory(dir=profile, prefix=".restore-") as directory:
        staging = Path(directory)
        _, files = _read_archive(source, staging, token)
        db_path = staging / DATABASE
        installed = []
        try:
            with (
                closing(_sqlite(profile / DATABASE, "ro")) as current,
                closing(_sqlite(db_path, "rw")) as candidate,
                candidate,
            ):
                old_files = _files(current)
                old_files.update(
                    dict(
                        (UUID(identifier), suffix)
                        for identifier, suffix in current.execute(
                            "SELECT id,suffix FROM attachment_cleanup"
                        )
                    )
                )
                candidate.execute("PRAGMA foreign_keys=ON")
                candidate.execute("BEGIN")
                candidate.execute("PRAGMA defer_foreign_keys=ON")
                candidate.execute("DELETE FROM attachment_cleanup")
                candidate.executemany(
                    "INSERT INTO attachment_cleanup VALUES (?,?)",
                    ((str(identifier), suffix) for identifier, suffix in old_files.items()),
                )
                for identifier, suffix in files.items():
                    fresh = uuid4()
                    original = managed_attachment_path(staging, identifier, suffix)
                    destination = managed_attachment_path(profile, fresh, suffix)
                    destination.parent.parent.mkdir(exist_ok=True)
                    destination.parent.mkdir(mode=0o700)
                    installed.append(destination)
                    os.replace(original, destination)
                    _sync_directory(destination.parent)
                    for table, column in (
                        ("attachments", "id"),
                        ("materials", "id"),
                        ("material_links", "material_id"),
                    ):
                        candidate.execute(
                            f"UPDATE {table} SET {column}=? WHERE {column}=?",
                            (str(fresh), str(identifier)),
                        )
            if installed:
                _sync_directory(profile / "attachments")
                _sync_directory(profile)
            _copy_database(db_path, profile / DATABASE)
        except Exception:
            for path in installed:
                try:
                    path.unlink(missing_ok=True)
                    path.parent.rmdir()
                except OSError:
                    pass
            raise


def _sync_directory(path: Path):
    if hasattr(os, "O_DIRECTORY"):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
