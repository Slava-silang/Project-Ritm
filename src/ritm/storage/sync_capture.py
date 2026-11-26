"""Opt-in ordinary-task capture in the caller's SQLite transaction. No transport."""

from collections.abc import Mapping
from contextlib import contextmanager
from uuid import UUID, uuid4

from ritm.domain.calendar import from_instant_us
from ritm.integrations.sync_codec import TaskRecord
from ritm.storage.sync_intents import SyncIntent, _stored_uuid, _uuid
from ritm.storage.sync_regular_capture import SyncRegularCaptureMixin

CAPTURE_OBJECTS = ("sync_capture", "sync_task_presence")
CAPTURE_SCHEMA = (
    "CREATE TABLE sync_capture (context_id INTEGER PRIMARY KEY CHECK(context_id=1) "
    "REFERENCES sync_binding(id), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)))",
    "CREATE TABLE sync_task_presence (task_id TEXT PRIMARY KEY NOT NULL, "
    "context_id INTEGER NOT NULL CHECK(context_id=1) REFERENCES sync_binding(id), "
    "state TEXT NOT NULL CHECK(state IN ('live','retained','deleted','deferred')))",
)


def _node_ids(task):
    return {child.id for child in task.subtasks} | {
        item.id for child in task.subtasks for item in child.items
    }


class SyncCaptureMixin(SyncRegularCaptureMixin):
    def is_sync_task_capture_enabled(self):
        row = self._db.execute("SELECT enabled FROM sync_capture WHERE context_id=1").fetchone()
        if row is None:
            return False
        if row["enabled"] not in (0, 1) or self.get_sync_binding() is None:
            raise ValueError("Повреждены настройки записи синхронизации")
        return bool(row["enabled"])

    def _sync_capture_active(self):
        return not getattr(self, "_sync_capture_depth", 0) and self.is_sync_task_capture_enabled()

    @contextmanager
    def suppress_sync_capture(self, origin):
        if origin not in ("incoming", "profile_restore", "bootstrap", "regular"):
            raise ValueError("Неизвестный источник изменения")
        depth = getattr(self, "_sync_capture_depth", 0)
        self._sync_capture_depth = depth + 1
        try:
            yield
        finally:
            self._sync_capture_depth = depth

    def _set_sync_task_presence(self, identifier, state):
        key = _uuid(identifier)
        if type(state) is not str or state not in ("live", "retained", "deleted", "deferred"):
            raise ValueError("Неизвестное состояние задачи синхронизации")
        if self.get_sync_binding() is None:
            raise ValueError("Синхронизация не подключена")
        self._db.execute(
            "INSERT INTO sync_task_presence VALUES (?,1,?) ON CONFLICT(task_id) "
            "DO UPDATE SET state=excluded.state",
            (key, state),
        )

    def set_sync_task_presence(self, identifier: UUID, state: str):
        """Metadata from a verified context later; this method does not authenticate a server."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._set_sync_task_presence(identifier, state)

    def get_sync_task_presence(self, identifier: UUID):
        row = self._db.execute(
            "SELECT state FROM sync_task_presence WHERE task_id=?", (_uuid(identifier),)
        ).fetchone()
        if row is None:
            return None
        if row["state"] not in ("live", "retained", "deleted", "deferred"):
            raise ValueError("Повреждено состояние задачи синхронизации")
        return row["state"]

    def configure_sync_task_capture(
        self, enabled: bool, known_tasks: Mapping[UUID, str] | None = None
    ):
        if type(enabled) is not bool or (
            known_tasks is not None and not isinstance(known_tasks, Mapping)
        ):
            raise ValueError("Нужны явные настройки записи синхронизации")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self.get_sync_binding() is None:
                raise ValueError("Сначала подтвердите подключение синхронизации")
            if enabled and not self.is_sync_task_capture_enabled() and known_tasks is None:
                raise ValueError("Перед включением нужен согласованный первоначальный состав")
            if known_tasks is not None:
                for identifier, state in known_tasks.items():
                    self._set_sync_task_presence(identifier, state)
            if enabled:
                # Unsupported recurrence lineage must not become a plain task accidentally.
                for row in self._db.execute(
                    "SELECT task_id FROM recurrence_occurrences"
                ).fetchall():
                    self._set_sync_task_presence(_stored_uuid(row[0]), "deferred")
            self._db.execute(
                "INSERT INTO sync_capture VALUES (1,?) ON CONFLICT(context_id) "
                "DO UPDATE SET enabled=excluded.enabled",
                (int(enabled),),
            )

    def _defer_sync_task_root(self, identifier):
        if self._sync_capture_active():
            self._set_sync_task_presence(identifier, "deferred")

    def _ordinary_sync_root(self, identifier):
        if self.get_sync_task_presence(identifier) == "deferred":
            return False
        if self._db.execute(
            "SELECT 1 FROM recurrence_occurrences WHERE task_id=?", (str(identifier),)
        ).fetchone():
            self._set_sync_task_presence(identifier, "deferred")
            return False
        return True

    def _append_captured_task(self, identifier, action, payload):
        parent = self._db.execute(
            "SELECT id FROM sync_intents WHERE target_kind='task' AND target_id=? "
            "ORDER BY seq DESC LIMIT 1",
            (str(identifier),),
        ).fetchone()
        self._enqueue_sync_intent(
            SyncIntent(
                id=uuid4(),
                target_kind="task",
                target_id=identifier,
                action=action,
                payload=payload,
                depends_on=_stored_uuid(parent[0]) if parent else None,
            )
        )

    def _capture_task_write(self, before, task, *, restored, regular_before=None):
        if self._sync_capture_active() and regular_before is not None:
            self._record_regular_change("save", regular_before, self._regular_sync_record(task))
            return
        if not self._sync_capture_active() or not self._ordinary_sync_root(task.id):
            return
        if before == task and not restored:
            return
        state = self.get_sync_task_presence(task.id)
        if state == "deleted" and not restored:
            raise ValueError("Удалённую задачу можно вернуть только явным восстановлением")
        if state == "retained":
            reopened = before is not None and before.completed and not task.completed
            if not (restored or reopened):
                return
            action = "restore"
        elif restored:
            action = "restore" if state is not None else "create"
        elif state is None:
            if before is not None and before.completed and task.completed:
                return  # An excluded local history edit does not create a new cloud completion.
            action = "create"
        elif before is None:
            raise ValueError("Идентификатор задачи уже участвует в синхронизации")
        else:
            action = "prune" if _node_ids(before) - _node_ids(task) else "put"
        row = self._db.execute(
            "SELECT completed_us FROM task_completion_dates WHERE task_id=?", (str(task.id),)
        ).fetchone()
        record = TaskRecord(task=task, completed_at=from_instant_us(row[0]) if row else None)
        self._append_captured_task(task.id, action, record)
        self._set_sync_task_presence(task.id, "live")

    def _capture_task_delete(self, task):
        if self._sync_capture_active():
            regular = self._regular_sync_record(task)
            if regular is not None:
                self._record_regular_change("delete", regular, None)
                return
        if not self._sync_capture_active() or not self._ordinary_sync_root(task.id):
            return
        state = self.get_sync_task_presence(task.id)
        if state is None or state == "deleted":
            return
        self._append_captured_task(task.id, "delete", None)
        self._set_sync_task_presence(task.id, "deleted")
