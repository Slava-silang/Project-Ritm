"""Отдельная SQLite сада: точный кошелёк, снимок месяца и квитанции одной транзакцией."""

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from uuid import UUID

from ritm.domain.garden import Garden, GardenItem, GardenState, apply_command, coins, natural

APPLICATION_ID = 0x52494744
SCHEMA_VERSION = 1
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
    """Не открывается обычным приложением до интеграции T39c/T39d.

    month обязателен при создании. Смена месяца здесь не является командой:
    будущая ротация T39e не должна сбрасывать имущество/кошелёк неявным open.
    Экземпляр используется только в создавшем потоке.
    """

    def __init__(self, path: Path, *, month=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
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
            if version == SCHEMA_VERSION and app_id == APPLICATION_ID:
                state = self._snapshot()
                if month is not None and state.garden.month != month:
                    raise ValueError(
                        "Открытие базы не переводит месяц сада. Нужна отдельная ротация."
                    )
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
            self._db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

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

    def execute(self, identifier: UUID, command: dict, *, expected_revision: int):
        if not isinstance(identifier, UUID):
            raise ValueError("Нужен UUID операции сада.")
        natural(expected_revision)
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
            current = self._snapshot()
            replay = self._receipt(key, digest, current)
            if replay:
                return replay
            if current.revision != expected_revision:
                raise ValueError("Сад или кошелёк изменились. Обновите снимок перед действием.")
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

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
