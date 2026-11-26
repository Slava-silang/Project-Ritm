"""Read one SQLite snapshot into sync DTOs; never refresh, bind, queue or transmit."""

from datetime import date
from uuid import UUID

from ritm.integrations.sync_codec import (
    BlockRecord,
    CalendarRecord,
    CategoryRecord,
    OccurrenceRecord,
    RegularRecord,
    TaskRecord,
    decode_entity,
    encode_entity,
    history_id,
)
from ritm.integrations.sync_projection import LocalData, project_local


def _id(raw):
    if type(raw) is not str:
        raise ValueError("Некорректный идентификатор источника синхронизации")
    value = UUID(raw)
    if str(value) != raw:
        raise ValueError("Некорректный идентификатор источника синхронизации")
    return value


def _day(raw):
    if type(raw) is not str:
        raise ValueError("Некорректный день сохранённого расписания")
    value = date.fromisoformat(raw)
    if value.isoformat() != raw:
        raise ValueError("Некорректный день сохранённого расписания")
    return value


def _checked(record):
    return decode_entity(encode_entity(record))


class SyncSourceMixin:
    def read_sync_source(self, calendar: CalendarRecord | None = None) -> LocalData:
        if self._db.in_transaction:
            raise ValueError("Снимок синхронизации нельзя читать внутри другой транзакции")
        if calendar is not None and type(calendar) is not CalendarRecord:
            raise ValueError("Нужна явная общая настройка часового пояса")
        try:
            with self._db:
                self._db.execute("BEGIN")
                # First read pins the snapshot before any per-tree/category/date reads.
                roots = tuple(
                    _id(row[0])
                    for row in self._db.execute("SELECT id FROM nodes WHERE kind=0 ORDER BY rowid")
                )
                root_ids = set(roots)
                occurrences = {}
                for row in self._db.execute(
                    "SELECT task_id,rule_id,day,state FROM recurrence_occurrences ORDER BY rowid"
                ):
                    identifier = _id(row["task_id"])
                    if row["state"] not in ("active", "waiting", "skipped"):
                        raise ValueError("Неизвестное состояние повтора")
                    occurrences[identifier] = (_id(row["rule_id"]), _day(row["day"]), row["state"])
                skipped = frozenset(
                    identifier
                    for identifier, row in occurrences.items()
                    if identifier in root_ids and row[2] == "skipped"
                )
                deleted = frozenset(
                    _id(row[0])
                    for row in self._db.execute(
                        "SELECT i.id FROM task_identities i "
                        "LEFT JOIN nodes n ON n.id=i.id AND n.kind=0 WHERE n.id IS NULL"
                    )
                )
                dates = self.list_completion_dates()
                tasks = {}
                for identifier in roots:
                    if identifier not in skipped:
                        task = self.get_task(identifier)
                        if task is None:
                            raise ValueError("Исчез корень внутри снимка")
                        tasks[identifier] = TaskRecord(
                            task=task, completed_at=dates.get(identifier)
                        )
                regulars = []
                current_roots = set()
                for row in self.list_recurrences():
                    identifier, rule = _id(row["task_id"]), _id(row["id"])
                    day = _day(row["current_day"])
                    occurrence = occurrences.get(identifier)
                    if (
                        identifier not in tasks
                        or occurrence is None
                        or occurrence[:2] != (rule, day)
                        or occurrence[2] == "skipped"
                    ):
                        raise ValueError("Расписание не соответствует текущему повтору")
                    current_roots.add(identifier)
                    regulars.append(
                        RegularRecord(
                            id=rule,
                            first_day=_day(row["first_day"]),
                            interval_days=row["interval_days"],
                            current_day=day,
                            current=tasks[identifier],
                        )
                    )
                history = []
                for identifier, (rule, day, state) in occurrences.items():
                    if identifier not in tasks or identifier in current_roots:
                        continue
                    if state == "waiting":
                        raise ValueError("Будущий экземпляр потерял своё расписание")
                    record = tasks[identifier]
                    if record.task.completed and record.task.deadline is None:
                        history.append(
                            OccurrenceRecord(
                                id=history_id(rule, day), rule_id=rule, day=day, current=record
                            )
                        )
                    # Reopened roots and former slots with a user-assigned deadline stay ordinary.
                categories = tuple(CategoryRecord(category=c) for c in self.list_categories())
                blocks = tuple(
                    BlockRecord(block=b)
                    for b in self.list_work_blocks()
                    if b.task_id not in skipped
                )
            # Validation/canonicalization after the read transaction releases SQLite locks.
            return LocalData(
                tasks=tuple(_checked(r) for r in tasks.values()),
                regulars=tuple(_checked(r) for r in regulars),
                history=tuple(_checked(r) for r in history),
                categories=tuple(_checked(r) for r in categories),
                blocks=tuple(_checked(r) for r in blocks),
                calendar=_checked(calendar) if calendar is not None else None,
                skipped_ids=skipped,
                deleted_ids=deleted,
            )
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
            self._db._check_restore()
            raise ValueError(
                "Не удалось подготовить согласованный снимок задач. Проверьте сохранённые данные."
            ) from None

    def prepare_sync_preview(
        self,
        now,
        calendar: CalendarRecord | None = None,
        selected_completed: frozenset[UUID] | None = None,
    ):
        """Data only; first-link backup/preview/approval UI remains a separate step."""
        return project_local(self.read_sync_source(calendar), now, selected_completed)
