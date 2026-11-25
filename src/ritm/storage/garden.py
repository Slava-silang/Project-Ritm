"""Отдельная SQLite сада: точный кошелёк, снимок месяца и квитанции одной транзакцией."""

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from ritm.domain.garden import Garden, GardenItem, GardenState, apply_command, coins, natural
from ritm.domain.garden_achievements import (
    AchievementState,
    decode_achievements,
    encode_achievements,
)
from ritm.domain.garden_archive import GardenArchive, rotate_month, set_favorite
from ritm.domain.garden_payments import validate_payment_totals
from ritm.domain.garden_rewards import RewardLedger, decode_ledger, encode_ledger, reconcile_time
from ritm.storage.garden_source_state import decode_source, empty_source
from ritm.storage.profile_guard import connect_profile

APPLICATION_ID = 0x52494744
SCHEMA_VERSION = 7
FAVORITES_SCHEMA = (
    "CREATE TABLE garden_favorites (month TEXT PRIMARY KEY NOT NULL REFERENCES gardens(month))"
)
BINDING_SCHEMA = (
    "CREATE TABLE garden_source_binding (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT)"
)
ACHIEVEMENT_SCHEMA = (
    "CREATE TABLE garden_achievement_state "
    "(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
)
CURSOR_SCHEMA = (
    "CREATE TABLE garden_source_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
)
REWARD_SCHEMA = (
    "CREATE TABLE garden_reward_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
)
MAX_PAYLOAD = 4 * 1024 * 1024
SCHEMA = (
    "CREATE TABLE gardens (month TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL)",
    "CREATE TABLE garden_state (id INTEGER PRIMARY KEY CHECK(id=1), "
    "month TEXT NOT NULL REFERENCES gardens(month), revision INTEGER NOT NULL CHECK(revision>=0), "
    "numerator TEXT NOT NULL, denominator TEXT NOT NULL)",
    "CREATE TABLE garden_receipts "
    "(key TEXT PRIMARY KEY NOT NULL, digest TEXT NOT NULL, item_id TEXT)",
)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Снимок сада содержит повторные поля.")
        result[key] = value
    return result


def _fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("Снимок сада имеет неизвестный формат.")


def encode_garden(garden):
    data = asdict(garden)
    for item in data["items"]:
        item["id"] = str(item["id"])
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if len(payload.encode()) > MAX_PAYLOAD:
        raise ValueError("Сад слишком большой для сохранения.")
    return payload


def decode_garden(payload):
    try:
        if not isinstance(payload, str) or len(payload.encode()) > MAX_PAYLOAD:
            raise ValueError
        data = json.loads(payload, object_pairs_hook=_unique)
        _fields(data, {"month", "terrain", "items", "fertilizer", "milestone"})
        if not isinstance(data["items"], list) or not isinstance(data["terrain"], list):
            raise ValueError
        items = []
        for item in data["items"]:
            _fields(item, {"id", "kind", "stage", "x", "y"})
            if not isinstance(item["id"], str):
                raise ValueError
            items.append(GardenItem(**{**item, "id": UUID(item["id"])}))
        garden = Garden(**{**data, "items": tuple(items), "terrain": tuple(data["terrain"])})
        if garden.mature_cells == 80 and not garden.milestone:
            raise ValueError
        return garden
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
        raise ValueError("Снимок сада повреждён. Автоматический сброс не выполняется.") from None


def _fraction(numerator, denominator):
    try:
        for value in (numerator, denominator):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 128
                or not value.isascii()
                or not value.isdecimal()
            ):
                raise ValueError
        return coins(Fraction(int(numerator), int(denominator)))
    except (ValueError, ZeroDivisionError):
        raise ValueError("Кошелёк сада повреждён. Автоматический сброс не выполняется.") from None


@dataclass(frozen=True)
class GardenResult:
    state: GardenState
    item_id: UUID | None = None
    replayed: bool = False


class GardenStore:
    """Отдельная БД сада; экземпляр используется только в создавшем потоке.

    month обязателен при создании. Открытие никогда не переводит месяц:
    ротация вызывается явно, внутри отдельной транзакции.
    """

    def __init__(self, path: Path, *, month=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = connect_profile(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        try:
            self._initialize(month)
        except BaseException:
            self._db.close()
            raise

    def _initialize(self, month):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            app_id = self._db.execute("PRAGMA application_id").fetchone()[0]
            if version == 1 and app_id == APPLICATION_ID:
                self._snapshot()
                self._check_month(month)
                self._db.execute(REWARD_SCHEMA)
                self._db.execute(
                    "INSERT INTO garden_reward_state VALUES (1,?)", (encode_ledger(RewardLedger()),)
                )
                self._db.execute("PRAGMA user_version=2")
                version = 2
            if version == 2 and app_id == APPLICATION_ID:
                self._snapshot()
                self._reward_snapshot()
                self._check_month(month)
                self._db.execute(CURSOR_SCHEMA)
                self._db.execute(
                    "INSERT INTO garden_source_state VALUES (1,?)", (json.dumps(empty_source()),)
                )
                self._create_achievements()
                self._create_binding()
                self._db.execute("PRAGMA user_version=6")
                version = 6
            if version == 3 and app_id == APPLICATION_ID:
                self._snapshot()
                self._reward_snapshot()
                self._check_month(month)
                row = self._db.execute(
                    "SELECT payload FROM garden_source_state WHERE id=1"
                ).fetchone()
                if row is None:
                    raise ValueError("Курсор сада отсутствует; автоматический сброс запрещён.")
                source = decode_source(row[0], legacy=True)
                self._db.execute(
                    "UPDATE garden_source_state SET payload=? WHERE id=1",
                    (json.dumps(source, sort_keys=True),),
                )
                self._create_achievements()
                self._create_binding()
                self._db.execute("PRAGMA user_version=6")
                version = 6
            if version == 4 and app_id == APPLICATION_ID:
                self._snapshot()
                self._reward_snapshot()
                self._source_snapshot()
                self._check_month(month)
                self._create_achievements()
                self._create_binding()
                self._db.execute("PRAGMA user_version=6")
                version = 6
            if version == 5 and app_id == APPLICATION_ID:
                self._snapshot()
                self._reward_snapshot()
                self._source_snapshot()
                self._achievement_snapshot()
                self._check_month(month)
                self._create_binding()
                self._db.execute("PRAGMA user_version=6")
                version = 6
            if version == 6 and app_id == APPLICATION_ID:
                self._check_month(month)
                self._db.execute(FAVORITES_SCHEMA)
                self._archive_snapshot()
                self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                version = SCHEMA_VERSION
            if version == SCHEMA_VERSION and app_id == APPLICATION_ID:
                self._archive_snapshot()
                self._reward_snapshot()
                self._source_snapshot()
                self._achievement_snapshot()
                binding = self._binding_snapshot()
                if binding is not None and self._source_snapshot()["identity"] not in (
                    None,
                    binding,
                ):
                    raise ValueError("Привязка источника сада повреждена.")
                self._check_month(month)
                return
            tables = self._db.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if version or app_id or tables:
                raise ValueError("Чужая или более новая база сада; данные не изменены.")
            initial = GardenState(Garden(month))
            for statement in SCHEMA:
                self._db.execute(statement)
            self._db.execute(
                "INSERT INTO gardens VALUES (?,?)", (month, encode_garden(initial.garden))
            )
            self._db.execute("INSERT INTO garden_state VALUES (1,?,0,'0','1')", (month,))
            self._db.execute(REWARD_SCHEMA)
            self._db.execute(
                "INSERT INTO garden_reward_state VALUES (1,?)", (encode_ledger(RewardLedger()),)
            )
            self._db.execute(CURSOR_SCHEMA)
            self._db.execute(
                "INSERT INTO garden_source_state VALUES (1,?)", (json.dumps(empty_source()),)
            )
            self._create_achievements()
            self._create_binding()
            self._db.execute(FAVORITES_SCHEMA)
            self._db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _create_achievements(self):
        self._db.execute(ACHIEVEMENT_SCHEMA)
        self._db.execute(
            "INSERT INTO garden_achievement_state VALUES (1,?)",
            (encode_achievements(AchievementState()),),
        )

    def _create_binding(self):
        self._db.execute(BINDING_SCHEMA)
        self._db.execute("INSERT INTO garden_source_binding VALUES (1,NULL)")

    def _binding_snapshot(self):
        row = self._db.execute("SELECT identity FROM garden_source_binding WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Привязка источника сада отсутствует.")
        if row[0] is not None:
            if not isinstance(row[0], str) or str(UUID(row[0])) != row[0]:
                raise ValueError("Привязка источника сада повреждена.")
        return row[0]

    def _achievement_snapshot(self):
        row = self._db.execute("SELECT payload FROM garden_achievement_state WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Журнал достижений отсутствует; автоматический сброс запрещён.")
        return decode_achievements(row[0])

    def achievement_snapshot(self):
        with self._db:
            self._db.execute("BEGIN")
            return self._achievement_snapshot()

    def _check_month(self, month):
        if month is not None and self._snapshot().garden.month != month:
            raise ValueError("Открытие базы не переводит месяц сада. Нужна отдельная ротация.")

    def _reward_snapshot(self):
        row = self._db.execute("SELECT payload FROM garden_reward_state WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Журнал оплаты отсутствует. Автоматический сброс не выполняется.")
        return decode_ledger(row["payload"])

    def reward_snapshot(self):
        with self._db:
            self._db.execute("BEGIN")
            return self._reward_snapshot()

    def _source_snapshot(self):
        row = self._db.execute("SELECT payload FROM garden_source_state WHERE id=1").fetchone()
        if row is None or len(row[0].encode()) > MAX_PAYLOAD:
            raise ValueError("Курсор сада повреждён; автоматический сброс запрещён.")
        source = decode_source(row[0])
        if source["identity"] is not None and source["payments"] is not None:
            validate_payment_totals(source["payments"], self._reward_snapshot())
        return source

    def source_snapshot(self):
        with self._db:
            self._db.execute("BEGIN")
            return self._source_snapshot()

    def apply_source_event(self, identity, event, *, zone, expected_revision, transition):
        natural(expected_revision)
        UUID(identity)
        payload = json.dumps({"event": event, "zone": zone}, sort_keys=True, allow_nan=False)
        if len(payload.encode()) > MAX_PAYLOAD:
            raise ValueError("Событие источника слишком большое.")
        digest = hashlib.sha256(payload.encode()).hexdigest()
        key = f"source:{identity}:{event['seq']}"
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            current = self._snapshot()
            source, ledger = self._source_snapshot(), self._reward_snapshot()
            achievements = self._achievement_snapshot()
            binding = self._binding_snapshot()
            if binding is not None and binding != identity:
                raise ValueError("Источник сада не совпадает с восстановленным профилем.")
            replay = self._receipt(key, digest, current)
            if replay:
                return replay
            if current.revision != expected_revision:
                raise ValueError("Сад или кошелёк изменились. Обновите снимок перед действием.")
            source, reward = transition(
                source, identity, event, ledger, zone, achievement_state=achievements
            )
            self._db.execute("UPDATE garden_source_binding SET identity=? WHERE id=1", (identity,))
            if reward.achievements is None:
                if achievements.welcomed:
                    raise ValueError(
                        "После запуска достижений нельзя доставлять события без их учёта."
                    )
            else:
                encoded_achievements = encode_achievements(reward.achievements.state)
                if len(encoded_achievements.encode()) > MAX_PAYLOAD:
                    raise ValueError("Журнал достижений слишком большой.")
                self._db.execute(
                    "UPDATE garden_achievement_state SET payload=? WHERE id=1",
                    (encoded_achievements,),
                )
            source_payload, ledger_payload = (
                json.dumps(source, sort_keys=True),
                encode_ledger(reward.ledger),
            )
            if max(len(source_payload.encode()), len(ledger_payload.encode())) > MAX_PAYLOAD:
                raise ValueError("Журнал источника слишком большой.")
            self._db.execute(
                "UPDATE garden_source_state SET payload=? WHERE id=1", (source_payload,)
            )
            self._db.execute(
                "UPDATE garden_reward_state SET payload=? WHERE id=1", (ledger_payload,)
            )
            state = GardenState(
                current.garden, current.balance + reward.coins, current.revision + 1
            )
            return self._write(state, key, digest, None)

    def rebuild_source_payments(self, events, *, zone, expected_revision, transition):
        """Восстановить принадлежность старой оплаты, не менять выданную валюту."""
        natural(expected_revision)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            current = self._snapshot()
            saved, saved_ledger = self._source_snapshot(), self._reward_snapshot()
            if saved["payments"] is not None:
                return GardenResult(current, replayed=True)
            if current.revision != expected_revision or len(events) != saved["seq"]:
                raise ValueError(
                    "Снимок или история изменились перед восстановлением распределения."
                )
            rebuilt, ledger = empty_source(), RewardLedger()
            for event in events:
                encoded = json.dumps(
                    {"event": event, "zone": zone}, sort_keys=True, allow_nan=False
                )
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                key = f"source:{saved['identity']}:{event['seq']}"
                if self._receipt(key, digest, current) is None:
                    raise ValueError("Для прежней оплаты не найдена подтверждённая квитанция.")
                rebuilt, reward = transition(rebuilt, saved["identity"], event, ledger, zone)
                ledger = reward.ledger
            old = {k: v for k, v in saved.items() if k != "payments"}
            new = {k: v for k, v in rebuilt.items() if k != "payments"}
            if old != new or ledger != saved_ledger:
                raise ValueError("История не соответствует сохранённому журналу оплаты.")
            if any(rebuilt["payments"]["claims"].values()):
                raise ValueError(
                    "Восстановление распределения не должно выдавать прежние надбавки."
                )
            payload = json.dumps(rebuilt, sort_keys=True)
            if len(payload.encode()) > MAX_PAYLOAD:
                raise ValueError("Распределение оплаты слишком большое.")
            self._db.execute("UPDATE garden_source_state SET payload=? WHERE id=1", (payload,))
            digest = hashlib.sha256(payload.encode()).hexdigest()
            key = f"payments-backfill:{saved['identity']}:{saved['seq']}"
            state = GardenState(current.garden, current.balance, current.revision + 1)
            return self._write(state, key, digest, None)

    def _snapshot(self):
        row = self._db.execute(
            "SELECT s.*,g.payload FROM garden_state s "
            "JOIN gardens g ON g.month=s.month WHERE s.id=1"
        ).fetchone()
        if row is None:
            raise ValueError("Не найден сохранённый сад. Автоматический сброс не выполняется.")
        garden = decode_garden(row["payload"])
        if garden.month != row["month"]:
            raise ValueError("Месяц снимка не соответствует базе сада.")
        return GardenState(garden, _fraction(row["numerator"], row["denominator"]), row["revision"])

    def snapshot(self):
        with self._db:
            self._db.execute("BEGIN")
            return self._snapshot()

    def _archive_snapshot(self):
        current = self._snapshot()
        history = []
        for month, payload in self._db.execute("SELECT month,payload FROM gardens ORDER BY month"):
            garden = decode_garden(payload)
            if garden.month != month:
                raise ValueError("Месяц снимка не соответствует базе сада.")
            if month != current.garden.month:
                history.append(garden)
        favorites = tuple(
            row[0] for row in self._db.execute("SELECT month FROM garden_favorites ORDER BY month")
        )
        return GardenArchive(current, tuple(history), favorites)

    def archive_snapshot(self):
        with self._db:
            self._db.execute("BEGIN")
            return self._archive_snapshot()

    def _write_archive(self, archive):
        gardens = (*archive.history, archive.current.garden)
        self._db.executemany(
            "INSERT INTO gardens VALUES (?,?) "
            "ON CONFLICT(month) DO UPDATE SET payload=excluded.payload",
            [(g.month, encode_garden(g)) for g in gardens],
        )
        state = archive.current
        self._db.execute(
            "UPDATE garden_state SET month=?,revision=?,numerator=?,denominator=? WHERE id=1",
            (
                state.garden.month,
                state.revision,
                str(state.balance.numerator),
                str(state.balance.denominator),
            ),
        )
        self._db.execute("DELETE FROM garden_favorites")
        self._db.executemany(
            "INSERT INTO garden_favorites VALUES (?)", [(m,) for m in archive.favorites]
        )
        placeholders = ",".join("?" for _ in gardens)
        self._db.execute(
            f"DELETE FROM gardens WHERE month NOT IN ({placeholders})", [g.month for g in gardens]
        )

    def _guard_identity(self, expected_identity):
        if expected_identity is not None:
            if (
                type(expected_identity) is not str
                or str(UUID(expected_identity)) != expected_identity
            ):
                raise ValueError("Некорректное поколение сада.")
            if self._source_snapshot()["identity"] != expected_identity:
                raise ValueError("Сад восстановлен или заменён. Обновите снимок перед действием.")

    def rotate_month(self, month, *, expected_identity=None):
        """Явный монотонный переход; открытие БД и UI его пока не вызывают."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._guard_identity(expected_identity)
            current = self._archive_snapshot()
            updated = rotate_month(current, month)
            if updated is not current:
                self._write_archive(updated)
            return updated

    def _receipt(self, key, digest, state):
        row = self._db.execute("SELECT * FROM garden_receipts WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["digest"] != digest:
            raise ValueError("Этот идентификатор операции уже использован с другим содержимым.")
        return GardenResult(state, UUID(row["item_id"]) if row["item_id"] else None, True)

    def _write(self, state, key, digest, item_id):
        self._db.execute(
            "UPDATE gardens SET payload=? WHERE month=?",
            (encode_garden(state.garden), state.garden.month),
        )
        self._db.execute(
            "UPDATE garden_state SET revision=?,numerator=?,denominator=? WHERE id=1",
            (state.revision, str(state.balance.numerator), str(state.balance.denominator)),
        )
        self._db.execute(
            "INSERT INTO garden_receipts VALUES (?,?,?)",
            (key, digest, str(item_id) if item_id else None),
        )
        return GardenResult(state, item_id)

    def execute(
        self,
        identifier: UUID,
        command: dict,
        *,
        expected_revision: int,
        expected_identity=None,
        expected_month=None,
    ):
        if not isinstance(identifier, UUID):
            raise ValueError("Нужен UUID операции сада.")
        natural(expected_revision)
        if expected_identity is not None and (
            type(expected_identity) is not str or str(UUID(expected_identity)) != expected_identity
        ):
            raise ValueError("Некорректное поколение сада.")
        try:
            payload = json.dumps(command, sort_keys=True, ensure_ascii=True, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Некорректная команда сада.") from None
        if len(payload) > 4096:
            raise ValueError("Команда сада слишком большая.")
        digest = hashlib.sha256(payload.encode()).hexdigest()
        key = "operation:" + str(identifier)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._guard_identity(expected_identity)
            current = self._snapshot()
            replay = self._receipt(key, digest, current)
            if replay:
                return replay
            if current.revision != expected_revision:
                raise ValueError("Сад или кошелёк изменились. Обновите снимок перед действием.")
            if expected_month is not None and expected_month != current.garden.month:
                raise ValueError("Архивный сад доступен только для просмотра.")
            if type(command) is dict and command.get("action") == "favorite":
                _fields(command, {"action", "month", "enabled"})
                archive = self._archive_snapshot()
                updated = set_favorite(archive, command["month"], command["enabled"])
                if updated is not archive:
                    self._write_archive(updated)
                return self._write(updated.current, key, digest, None)
            state, item_id = apply_command(current, command, identifier)
            return self._write(state, key, digest, item_id)

    def credit(self, event_key: str, amount):
        """Внутренний мост будущих начислений; без анализа фокуса/достижений и без UI."""
        if (
            not isinstance(event_key, str)
            or not 1 <= len(event_key) <= 512
            or event_key != event_key.strip()
            or any(ord(c) < 32 for c in event_key)
        ):
            raise ValueError("Некорректный ключ начисления.")
        amount = coins(amount)
        if amount == 0:
            raise ValueError("Начисление должно быть положительным.")
        digest = hashlib.sha256(f"{amount.numerator}/{amount.denominator}".encode()).hexdigest()
        key = "credit:" + event_key
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            current = self._snapshot()
            replay = self._receipt(key, digest, current)
            if replay:
                return replay
            state = GardenState(current.garden, current.balance + amount, current.revision + 1)
            return self._write(state, key, digest, None)

    def reconcile_focus(
        self,
        identifier: UUID,
        intervals,
        *,
        now_us,
        zone: str,
        expected_revision: int,
        enabled=None,
    ):
        """Внутренний протокол T39c1; ещё не вызывается обычным приложением."""
        if not isinstance(identifier, UUID) or not isinstance(zone, str):
            raise ValueError("Нужен UUID сверки и явный часовой пояс.")
        natural(expected_revision)
        intervals = tuple(tuple(pair) for pair in intervals)
        payload = json.dumps(
            {"intervals": intervals, "now_us": now_us, "zone": zone, "enabled": enabled},
            sort_keys=True,
            allow_nan=False,
        )
        if len(payload.encode()) > MAX_PAYLOAD:
            raise ValueError("Снимок интервалов слишком большой.")
        digest = hashlib.sha256(payload.encode()).hexdigest()
        key = "focus:" + str(identifier)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            current = self._snapshot()
            ledger = self._reward_snapshot()
            if self._source_snapshot()["identity"] is not None:
                raise ValueError("После подключения источника используйте журнал событий задач.")
            replay = self._receipt(key, digest, current)
            if replay:
                return replay
            if current.revision != expected_revision:
                raise ValueError("Сад или кошелёк изменились. Обновите снимок перед действием.")
            reward = reconcile_time(
                ledger, intervals, now_us=now_us, zone=ZoneInfo(zone), enabled=enabled
            )
            encoded = encode_ledger(reward.ledger)
            if len(encoded.encode()) > MAX_PAYLOAD:
                raise ValueError("Журнал оплаты слишком большой.")
            self._db.execute("UPDATE garden_reward_state SET payload=? WHERE id=1", (encoded,))
            state = GardenState(
                current.garden, current.balance + reward.coins, current.revision + 1
            )
            return self._write(state, key, digest, None)

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
