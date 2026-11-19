"""Снимок двух БД и повторяемое восстановление после прерывания процесса."""

import json
import os
import re
import shutil
import sqlite3
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from ritm.storage.attachments import managed_attachment_path
from ritm.storage.garden import APPLICATION_ID, SCHEMA_VERSION, GardenStore, decode_garden
from ritm.storage.profile_guard import PENDING_RESTORE
from ritm.storage.sqlite import TaskStore

GARDEN = "garden.sqlite3"
PAIR_FORMAT = "ritm-profile-2"
LOCK = ".ritm-restore-lock.sqlite3"


@contextmanager
def profile_operation(profile):
    if (profile / LOCK).is_symlink():
        raise ValueError("Служебный путь профиля содержит символическую ссылку.")
    with closing(sqlite3.connect(profile / LOCK, timeout=0)) as lock:
        try:
            lock.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            raise ValueError("Другая операция с копией профиля ещё не завершена.") from error
        try:
            yield
        finally:
            lock.rollback()


def _available(profile):
    if (profile / PENDING_RESTORE).exists() or (profile / PENDING_RESTORE).is_symlink():
        raise ValueError("Восстановление профиля не завершено. Перезапустите приложение.")


@contextmanager
def _freeze(profile):
    from ritm.storage import backup as core

    _available(profile)
    garden = (profile / GARDEN).exists()
    with ExitStack() as stack:
        for name in (core.DATABASE, GARDEN) if garden else (core.DATABASE,):
            if (profile / name).is_symlink():
                raise ValueError("База профиля не должна быть символической ссылкой.")
            connection = stack.enter_context(closing(core._sqlite(profile / name, "rw")))
            connection.execute("BEGIN IMMEDIATE")
        yield garden
        if (profile / GARDEN).exists() != garden:
            raise ValueError("Состав профиля изменился во время копирования. Повторите операцию.")


def _copy_frozen(profile, destination, garden):
    from ritm.storage import backup as core

    destination.mkdir(parents=True, exist_ok=True)
    for name in (core.DATABASE, GARDEN) if garden else (core.DATABASE,):
        core._copy_database(profile / name, destination / name, create=True)


def snapshot_databases(profile, destination):
    with _freeze(profile) as garden:
        _copy_frozen(profile, destination, garden)
    return garden


def validate_garden(path):
    from ritm.storage import backup as core

    with (
        GardenStore(Path(":memory:"), month="2026-01") as reference,
        closing(core._sqlite(path, "ro")) as db,
    ):

        def schema(connection):
            return [
                tuple(r)
                for r in connection.execute(
                    "SELECT name,type,sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]

        version = db.execute("PRAGMA user_version").fetchone()[0]
        expected = schema(reference._db)
        introduced = {
            "garden_reward_state": 2,
            "garden_source_state": 3,
            "garden_achievement_state": 5,
            "garden_source_binding": 6,
        }
        expected = [r for r in expected if version >= introduced.get(r[0], 1)]
        if (
            db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or version not in range(1, SCHEMA_VERSION + 1)
            or schema(db) != expected
            or db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
            or db.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise ValueError("База сада в копии повреждена или несовместима.")
    with GardenStore(path) as store:
        for month, payload in store._db.execute("SELECT month,payload FROM gardens"):
            if decode_garden(payload).month != month:
                raise ValueError("Месяц сохранённого сада повреждён.")
        for key, digest, identifier in store._db.execute("SELECT * FROM garden_receipts"):
            if not isinstance(key, str) or not key or not re.fullmatch("[a-f0-9]{64}", digest):
                raise ValueError("Квитанции сада повреждены.")
            if identifier is not None:
                UUID(identifier)


def validate_pair(directory):
    from ritm.storage import backup as core

    try:
        with (
            TaskStore(directory / core.DATABASE) as source,
            GardenStore(directory / GARDEN) as garden,
        ):
            cursor = garden.source_snapshot()
            identity, _ = source.garden_events_after(
                identity=cursor["identity"],
                sequence=cursor["seq"],
                event_id=cursor["event_id"],
                limit=1,
            )
            if garden._binding_snapshot() not in (None, identity):
                raise ValueError("Привязка поколения не совпадает.")
            if cursor["seq"]:
                stamp = source._db.execute(
                    "SELECT happened_us FROM garden_events WHERE seq=?", (cursor["seq"],)
                ).fetchone()[0]
                if stamp != garden.reward_snapshot().last_us:
                    raise ValueError("Время курсора не совпадает.")
            return {"identity": identity, "sequence": cursor["seq"], "event_id": cursor["event_id"]}
    except (ValueError, sqlite3.Error) as error:
        raise ValueError(
            "Задачи и сад не согласованы. Выберите полную совместимую копию."
        ) from error


def _fork_pair(directory):
    """Новое поколение блокирует пакет, прочитанный получателем до restore."""
    from ritm.storage import backup as core

    with TaskStore(directory / core.DATABASE) as source, GardenStore(directory / GARDEN) as garden:
        old = source._db.execute("SELECT identity FROM garden_source WHERE id=1").fetchone()[0]
        fresh = str(uuid4())
        with source._db:
            source._db.execute("UPDATE garden_source SET identity=? WHERE id=1", (fresh,))
        state = garden.source_snapshot()
        with garden._db:
            if state["identity"] is not None:
                if state["identity"] != old:
                    raise ValueError("Источник восстановленного сада не совпадает.")
                state["identity"] = fresh
                garden._db.execute(
                    "UPDATE garden_source_state SET payload=? WHERE id=1", (json.dumps(state),)
                )
                for namespace in ("source:", "payments-backfill:"):
                    prefix = namespace + old + ":"
                    garden._db.execute(
                        "UPDATE garden_receipts SET key=?||substr(key,?) WHERE substr(key,1,?)=?",
                        (namespace + fresh + ":", len(prefix) + 1, len(prefix), prefix),
                    )
            garden._db.execute("UPDATE garden_source_binding SET identity=? WHERE id=1", (fresh,))


def _prepare_files(profile, stage, files):
    from ritm.storage import backup as core

    after, before = stage / "after", stage / "before"
    with closing(core._sqlite(before / core.DATABASE, "ro")) as old:
        cleanup = core._files(old)
        cleanup.update(
            (UUID(i), suffix)
            for i, suffix in old.execute("SELECT id,suffix FROM attachment_cleanup")
        )
    uploads = []
    with closing(core._sqlite(after / core.DATABASE, "rw")) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN")
        db.execute("PRAGMA defer_foreign_keys=ON")
        db.execute("DELETE FROM attachment_cleanup")
        db.executemany(
            "INSERT INTO attachment_cleanup VALUES (?,?)", ((str(i), s) for i, s in cleanup.items())
        )
        for identifier, suffix in files.items():
            fresh = uuid4()
            origin = managed_attachment_path(after, identifier, suffix)
            destination = managed_attachment_path(after, fresh, suffix)
            destination.parent.mkdir(mode=0o700)
            os.replace(origin, destination)
            core._sync_directory(destination.parent)
            for table, column in (
                ("attachments", "id"),
                ("materials", "id"),
                ("material_links", "material_id"),
            ):
                db.execute(
                    f"UPDATE {table} SET {column}=? WHERE {column}=?", (str(fresh), str(identifier))
                )
            uploads.append([fresh.hex, suffix])
    if (after / "attachments").exists():
        core._sync_directory(after / "attachments")
    return uploads


def _sha(path):
    from ritm.storage import backup as core

    if path.is_symlink():
        raise ValueError("Служебный файл восстановления содержит символическую ссылку.")
    with path.open("rb") as stream:
        return core._transfer(stream, None)


def _durable(path):
    with path.open("rb+") as stream:
        os.fsync(stream.fileno())


def _marker(profile, identifier, state, digest):
    from ritm.storage import backup as core

    core._write_profile_text(
        profile / PENDING_RESTORE,
        json.dumps(
            {"version": 1, "id": identifier, "state": state, "plan": digest}, sort_keys=True
        ),
    )
    core._sync_directory(profile)


def _publish_database(source, destination):
    from ritm.storage import backup as core

    core._copy_database(source, destination, create=not destination.exists())


def _read_plan(profile):
    try:
        return _read_plan_unchecked(profile)
    except (TypeError, KeyError, AttributeError) as error:
        raise ValueError(
            "Журнал восстановления повреждён; автоматический сброс запрещён."
        ) from error


def _read_plan_unchecked(profile):
    marker = profile / PENDING_RESTORE
    if marker.is_symlink() or marker.stat().st_size > 4096:
        raise ValueError("Журнал восстановления повреждён; данные не изменены.")
    record = json.loads(marker.read_text())
    if (
        type(record) is not dict
        or set(record) != {"version", "id", "state", "plan"}
        or type(record["version"]) is not int
        or record["version"] != 1
        or record["state"] not in ("prepared", "committed", "rolled_back")
        or not isinstance(record["id"], str)
        or UUID(record["id"]).hex != record["id"]
        or not isinstance(record["plan"], str)
        or not re.fullmatch("[a-f0-9]{64}", record["plan"])
    ):
        raise ValueError("Журнал восстановления повреждён; автоматический сброс запрещён.")
    stage = profile / (".ritm-restore-" + record["id"])
    if record["state"] in ("committed", "rolled_back"):
        if stage.is_symlink():
            raise ValueError("Служебный каталог восстановления содержит символическую ссылку.")
        return record, stage, None
    if stage.is_symlink() or _sha(stage / "plan.json") != record["plan"]:
        raise ValueError("План восстановления повреждён; страховочные данные сохранены.")
    plan = json.loads((stage / "plan.json").read_text())
    if (
        type(plan) is not dict
        or set(plan) != {"id", "before", "uploads"}
        or plan["id"] != record["id"]
        or type(plan["before"]) is not dict
        or set(plan["before"]) != {"tasks.sqlite3", GARDEN}
        or type(plan["uploads"]) is not list
    ):
        raise ValueError("Неизвестный план восстановления.")
    for name, digest in plan["before"].items():
        if digest is None and name == GARDEN:
            continue
        if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
            raise ValueError("Страховочные суммы повреждены.")
        if _sha(stage / "before" / name) != digest:
            raise ValueError("Страховочная копия повреждена; автоматический сброс запрещён.")
    for entry in plan["uploads"]:
        if type(entry) is not list or len(entry) != 2 or UUID(entry[0]).hex != entry[0]:
            raise ValueError("Список восстановленных вложений повреждён.")
        managed_attachment_path(profile, UUID(entry[0]), entry[1])
    return record, stage, plan


def _cleanup(profile, stage):
    from ritm.storage import backup as core

    if stage.exists():
        shutil.rmtree(stage)
    (profile / PENDING_RESTORE).unlink()
    core._sync_directory(profile)


def _recover_locked(profile):
    from ritm.storage import backup as core

    record, stage, plan = _read_plan(profile)
    if record["state"] == "prepared":
        if plan["before"][GARDEN] is None:
            for suffix in ("", "-wal", "-shm", "-journal"):
                path = profile / (GARDEN + suffix)
                if path.is_symlink():
                    raise ValueError("Путь восстановления сада содержит символическую ссылку.")
                path.unlink(missing_ok=True)
        else:
            _publish_database(stage / "before" / GARDEN, profile / GARDEN)
        _publish_database(stage / "before" / core.DATABASE, profile / core.DATABASE)
        for identifier, suffix in plan["uploads"]:
            path = managed_attachment_path(profile, UUID(identifier), suffix)
            path.unlink(missing_ok=True)
            if path.parent.exists():
                path.parent.rmdir()
        core._sync_directory(profile)
        # A crash during cleanup must not require already deleted safety files.
        _marker(profile, record["id"], "rolled_back", record["plan"])
    _cleanup(profile, stage)


def recover_profile(profile):
    if not (profile / PENDING_RESTORE).exists() and not (profile / PENDING_RESTORE).is_symlink():
        return False
    with profile_operation(profile):
        _recover_locked(profile)
    return True


def restore_paired(profile, archive, token):
    """Вызывается с profile_operation; маркер переживает os._exit/отключение."""
    from ritm.storage import backup as core

    _available(profile)
    identifier = uuid4().hex
    stage = profile / (".ritm-restore-" + identifier)
    after, before = stage / "after", stage / "before"
    stage.mkdir(mode=0o700)
    committed = False
    try:
        after.mkdir(mode=0o700)
        preview, files = core._read_archive(archive, after, token)
        with _freeze(profile) as had_garden:
            _copy_frozen(profile, before, had_garden)
            if not preview["garden"]:
                core._copy_database(before / GARDEN, after / GARDEN, create=True)
            validate_garden(after / GARDEN)
            validate_pair(after)
            uploads = _prepare_files(profile, stage, files)
            _fork_pair(after)
            validate_pair(after)
            hashes = {
                core.DATABASE: _sha(before / core.DATABASE),
                GARDEN: _sha(before / GARDEN) if had_garden else None,
            }
            plan = {"id": identifier, "before": hashes, "uploads": uploads}
            plan_path = stage / "plan.json"
            plan_path.write_text(json.dumps(plan, sort_keys=True))
            for path in (*before.iterdir(), *after.glob("*.sqlite3"), plan_path):
                if path.is_file():
                    _durable(path)
            for directory in (before, after, stage, profile):
                core._sync_directory(directory)
            _marker(profile, identifier, "prepared", _sha(plan_path))
        for fresh, suffix in uploads:
            origin = managed_attachment_path(after, UUID(fresh), suffix)
            destination = managed_attachment_path(profile, UUID(fresh), suffix)
            destination.parent.parent.mkdir(exist_ok=True)
            destination.parent.mkdir(mode=0o700)
            os.replace(origin, destination)
            core._sync_directory(destination.parent)
        if uploads:
            core._sync_directory(profile / "attachments")
        _publish_database(after / GARDEN, profile / GARDEN)
        _publish_database(after / core.DATABASE, profile / core.DATABASE)
        core._sync_directory(profile)
        _marker(profile, identifier, "committed", _sha(plan_path))
        committed = True
        _cleanup(profile, stage)
    except Exception:
        if (profile / PENDING_RESTORE).exists():
            try:
                committed = (
                    committed
                    or json.loads((profile / PENDING_RESTORE).read_text())["state"] == "committed"
                )
                _recover_locked(profile)
            except Exception as error:
                raise ValueError(
                    "Восстановление прервано. Перезапустите приложение "
                    "после устранения ошибки записи."
                ) from error
            if committed:
                return
        elif committed:
            return
        elif stage.exists():
            shutil.rmtree(stage)
        raise
