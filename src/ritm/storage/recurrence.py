"""Atomic regular task schedules, used only in the storage worker."""

from datetime import date
from uuid import UUID, uuid4

from ritm.domain.recurrence import next_task, scheduled_day

RECURRENCE_SCHEMA = (
    "CREATE TABLE recurrence_rules (id TEXT PRIMARY KEY, "
    "task_id TEXT NOT NULL UNIQUE REFERENCES task_identities(id), "
    "first_day TEXT NOT NULL, "
    "interval_days INTEGER NOT NULL CHECK(interval_days BETWEEN 1 AND 36500), "
    "current_day TEXT NOT NULL)",
    "CREATE TABLE recurrence_occurrences (task_id TEXT PRIMARY KEY REFERENCES task_identities(id), "
    "rule_id TEXT NOT NULL, day TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ('waiting','active','skipped')), UNIQUE(rule_id,day))",
)


class RecurrenceMixin:
    def is_skipped_occurrence(self, identifier: UUID) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM recurrence_occurrences WHERE task_id=? AND state='skipped'",
                (str(identifier),),
            ).fetchone()
            is not None
        )

    def list_recurrences(self):
        return tuple(
            dict(row) for row in self._db.execute("SELECT * FROM recurrence_rules ORDER BY rowid")
        )

    def save_regular_task(self, task, category, *, interval, first, today):
        if interval is not None:
            scheduled_day(first, interval, today)
            if task.deadline is not None:
                raise ValueError("У регулярной задачи нет дедлайна: пропуск не является просрочкой")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            existing = self._db.execute(
                "SELECT * FROM recurrence_rules WHERE task_id=?", (str(task.id),)
            ).fetchone()
            capture_before = None
            if self._sync_capture_active():
                old_task = self.get_task(task.id)
                if old_task is not None:
                    capture_before = self._regular_sync_record(old_task) or self._sync_task_record(
                        old_task
                    )
            if category is not None:
                self._write_category(category)
            if interval is None:
                self._db.execute("DELETE FROM recurrence_rules WHERE task_id=?", (str(task.id),))
            lineage = (
                existing is not None
                or self._db.execute(
                    "SELECT 1 FROM recurrence_occurrences WHERE task_id=?", (str(task.id),)
                ).fetchone()
                is not None
            )
            if interval is not None or lineage:
                self._defer_sync_task_root(task.id)
                with self.suppress_sync_capture("regular"):
                    self._write_task(task)
            else:
                self._write_task(task)
            if interval is None:
                self._db.execute("DELETE FROM recurrence_rules WHERE task_id=?", (str(task.id),))
                if existing is not None:
                    self._db.execute(
                        "DELETE FROM recurrence_occurrences WHERE task_id=?", (str(task.id),)
                    )
                else:
                    self._db.execute(
                        "UPDATE recurrence_occurrences SET state='active' WHERE task_id=?",
                        (str(task.id),),
                    )
                if existing is not None:
                    self._record_regular_change(
                        "stop", capture_before, self._sync_task_record(task)
                    )
                return
            # Unchanged schedule never shifts when editing a completed current occurrence.
            if (
                existing
                and existing["first_day"] == first.isoformat()
                and existing["interval_days"] == interval
            ):
                self._record_regular_change("save", capture_before, self._regular_sync_record(task))
                return
            day = scheduled_day(first, interval, today)
            identifier = str(uuid4())
            self._db.execute(
                "INSERT INTO recurrence_rules VALUES (?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE "
                "SET id=excluded.id, first_day=excluded.first_day, "
                "interval_days=excluded.interval_days, current_day=excluded.current_day",
                (identifier, str(task.id), first.isoformat(), interval, day.isoformat()),
            )
            self._db.execute(
                "INSERT INTO recurrence_occurrences VALUES (?,?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE "
                "SET rule_id=excluded.rule_id,day=excluded.day,state=excluded.state",
                (str(task.id), identifier, day.isoformat(), "waiting" if day > today else "active"),
            )

            self._record_regular_change("save", capture_before, self._regular_sync_record(task))

    def refresh_recurrences(self, today: date):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            for rule in self.list_recurrences():
                old = self.get_task(UUID(rule["task_id"]))
                if old is None:
                    self._db.execute("DELETE FROM recurrence_rules WHERE id=?", (rule["id"],))
                    continue
                due = scheduled_day(
                    date.fromisoformat(rule["first_day"]), rule["interval_days"], today
                )
                current = date.fromisoformat(rule["current_day"])
                if today < due:
                    continue
                if due <= current:
                    self._db.execute(
                        "UPDATE recurrence_occurrences SET state='active' "
                        "WHERE task_id=? AND state='waiting'",
                        (str(old.id),),
                    )
                    continue
                # Keep completed history; skip the old open one without a catch-up backlog.
                if not old.completed:
                    self._db.execute(
                        "UPDATE recurrence_occurrences SET state='skipped' WHERE task_id=?",
                        (str(old.id),),
                    )
                capture_before = (
                    self._regular_sync_record(old) if self._sync_capture_active() else None
                )
                task = next_task(old)
                self._defer_sync_task_root(task.id)
                with self.suppress_sync_capture("regular"):
                    self._write_task(task)
                self._db.execute(
                    "INSERT INTO recurrence_occurrences VALUES (?,?,?,'active')",
                    (str(task.id), rule["id"], due.isoformat()),
                )
                self._db.execute(
                    "UPDATE recurrence_rules SET task_id=?,current_day=? WHERE id=?",
                    (str(task.id), due.isoformat(), rule["id"]),
                )

                self._record_regular_change(
                    "rollover", capture_before, self._regular_sync_record(task)
                )

    def recurrence_views(self):
        result = {}
        for rule in self.list_recurrences():
            result[rule["task_id"]] = {
                "repeatDays": str(rule["interval_days"]),
                "repeatStart": rule["first_day"],
                "repeatDay": rule["current_day"],
            }
        return result
