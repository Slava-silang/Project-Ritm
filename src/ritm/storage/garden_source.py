"""Транзакционный журнал задач/фокуса, без доступа к отдельной БД сада."""

import json
from uuid import UUID, uuid4

from ritm.domain.calendar import instant_us
from ritm.storage.codec import encode_task

SOURCE_SCHEMA = (
    "CREATE TABLE garden_source (id INTEGER PRIMARY KEY CHECK(id=1), "
    "identity TEXT NOT NULL, subscribed INTEGER NOT NULL CHECK(subscribed IN (0,1)))",
    "CREATE TABLE garden_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "id TEXT NOT NULL UNIQUE, happened_us INTEGER NOT NULL, "
    "kind TEXT NOT NULL, payload TEXT NOT NULL)",
)


def create_source(db):
    for statement in SOURCE_SCHEMA:
        db.execute(statement)
    db.execute("INSERT INTO garden_source VALUES (1,?,0)", (str(uuid4()),))


class GardenSourceMixin:
    def garden_source_status(self):
        """Сохраняемая настройка из того же журнала, что входит в backup."""
        with self._db:
            self._db.execute("BEGIN")
            subscribed = self._garden_subscribed()
            row = self._db.execute(
                "SELECT kind,payload FROM garden_events WHERE kind IN ('begin','enabled') "
                "ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if subscribed != (row is not None):
                raise ValueError("Настройка сада не совпадает с журналом; сброс запрещён.")
            enabled = bool(row and row["kind"] == "begin")
            if row and row["kind"] == "enabled":
                payload = json.loads(row["payload"])
                if (
                    type(payload) is not dict
                    or set(payload) != {"enabled"}
                    or type(payload["enabled"]) is not bool
                ):
                    raise ValueError("Переключатель сада повреждён; автоматический сброс запрещён.")
                enabled = payload["enabled"]
            return {"subscribed": subscribed, "enabled": enabled}

    def _garden_subscribed(self):
        row = self._db.execute("SELECT subscribed FROM garden_source WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Настройки источника сада повреждены; автоматический сброс запрещён.")
        return bool(row["subscribed"])

    def _garden_event(self, kind, payload, now):
        if not self._garden_subscribed():
            return
        timestamp = instant_us(now)
        previous = self._db.execute("SELECT MAX(happened_us) FROM garden_events").fetchone()[0]
        timestamp = max(timestamp, previous) if previous is not None else timestamp
        self._db.execute(
            "INSERT INTO garden_events(id,happened_us,kind,payload) VALUES (?,?,?,?)",
            (str(uuid4()), timestamp, kind, json.dumps(payload, sort_keys=True)),
        )

    def _garden_focus_payload(self, identifier, *, trusted_until=None):
        row = self._db.execute(
            "SELECT * FROM focus_sessions WHERE id=?", (str(identifier),)
        ).fetchone()
        if row is None:
            return {"id": str(identifier), "session": None}
        # An ordinary update must not forget an earlier live-process checkpoint.
        previous = self._db.execute(
            "SELECT payload FROM garden_events WHERE kind='focus' "
            "AND json_extract(payload,'$.id')=? ORDER BY seq DESC LIMIT 1",
            (str(identifier),),
        ).fetchone()
        if previous:
            session = json.loads(previous[0])["session"]
            if session and session["intervals"]:
                recorded = max(end for _, end in session["intervals"])
                trusted_until = max(recorded, trusted_until or recorded)
        intervals = []
        for item in self._db.execute(
            "SELECT starts_us,ends_us FROM focus_intervals WHERE session_id=? ORDER BY position",
            (str(identifier),),
        ):
            end = item["ends_us"]
            if end is None:
                end = trusted_until
            if end is not None and end > item["starts_us"]:
                intervals.append([item["starts_us"], end])
        return {"id": str(identifier), "session": {"root": row["task_id"], "intervals": intervals}}

    def _garden_focus_event(self, identifier, now, *, trusted=False):
        if self._garden_subscribed():
            payload = self._garden_focus_payload(
                identifier, trusted_until=instant_us(now) if trusted else None
            )
            self._garden_event("focus", payload, now)

    def begin_garden_source(self, now):
        """Явная подписка; baseline не означает завершение прежних задач."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self._garden_subscribed():
                return
            self._db.execute("UPDATE garden_source SET subscribed=1 WHERE id=1")
            tasks = [
                self.get_task(UUID(row[0]))
                for row in self._db.execute("SELECT id FROM nodes WHERE kind=0 ORDER BY rowid")
            ]
            self._garden_event(
                "begin",
                {
                    "tasks": [encode_task(t) for t in tasks],
                    "focus": [self._garden_focus_payload(s.id) for s in self.list_focus_sessions()],
                },
                now,
            )

    def checkpoint_garden_focus(self, now):
        """Только из живого процесса; не вызывать до восстановления аварийной сессии."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if not self._garden_subscribed():
                raise ValueError("Источник сада ещё не включён.")
            session = self.active_focus()
            if session is not None:
                self._garden_focus_event(session.id, now, trusted=True)

    def set_garden_source_enabled(self, enabled, now):
        if type(enabled) is not bool:
            raise ValueError("Нужен логический переключатель сада.")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if not self._garden_subscribed():
                raise ValueError("Сначала включите источник сада.")
            self._garden_event("enabled", {"enabled": enabled}, now)

    def garden_events_after(self, *, identity=None, sequence=0, event_id=None, limit=100):
        if (
            type(sequence) is not int
            or sequence < 0
            or type(limit) is not int
            or not 1 <= limit <= 1000
        ):
            raise ValueError("Некорректный курсор или размер пакета.")
        with self._db:
            self._db.execute("BEGIN")
            row = self._db.execute("SELECT * FROM garden_source WHERE id=1").fetchone()
            if row is None:
                raise ValueError("Источник сада повреждён.")
            UUID(row["identity"])
            if identity is not None and identity != row["identity"]:
                raise ValueError(
                    "База задач заменена другим источником. Продолжение выплат остановлено."
                )
            if sequence:
                previous = self._db.execute(
                    "SELECT id FROM garden_events WHERE seq=?", (sequence,)
                ).fetchone()
                if previous is None or previous["id"] != event_id:
                    raise ValueError(
                        "История задач восстановлена или изменена. Курсор сада не совпадает."
                    )
            elif event_id is not None:
                raise ValueError("Неверная начальная квитанция источника.")
            events = []
            for event in self._db.execute(
                "SELECT * FROM garden_events WHERE seq>? ORDER BY seq LIMIT ?", (sequence, limit)
            ):
                events.append({**dict(event), "payload": json.loads(event["payload"])})
            return row["identity"], tuple(events)
