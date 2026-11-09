"""SQLite с версионированной схемой. Каждый экземпляр используется только в создавшем его потоке."""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from ritm.domain.calendar import WorkBlock, from_instant_us, instant_us
from ritm.domain.focus import FocusSession
from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task
from ritm.storage.codec import decode_task, encode_task

APPLICATION_ID = 0x5249544D
SCHEMA_VERSION = 6
SCHEMA = (
    "CREATE TABLE categories (id TEXT PRIMARY KEY NOT NULL, "
    "title TEXT NOT NULL CHECK(length(trim(title))>0))",
    """CREATE TABLE nodes (
        id TEXT PRIMARY KEY NOT NULL,
        parent_id TEXT REFERENCES nodes(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK(position>=0),
        kind INTEGER NOT NULL CHECK(kind IN (0,1,2)),
        title TEXT NOT NULL CHECK(length(trim(title))>0),
        completed INTEGER NOT NULL CHECK(completed IN (0,1)),
        description TEXT,
        category_id TEXT REFERENCES categories(id),
        difficulty INTEGER CHECK(difficulty BETWEEN 1 AND 10),
        expected_us INTEGER CHECK(expected_us>0),
        deadline TEXT,
        weight TEXT,
        UNIQUE(parent_id,position),
        CHECK((kind=0 AND parent_id IS NULL AND description IS NOT NULL AND weight IS NULL)
           OR (kind IN (1,2) AND parent_id IS NOT NULL AND description IS NULL
               AND category_id IS NULL AND difficulty IS NULL AND expected_us IS NULL
               AND deadline IS NULL)),
        CHECK(kind=1 OR weight IS NULL)
    )""",
    "CREATE INDEX nodes_parent ON nodes(parent_id)",
    """CREATE TRIGGER node_parent_insert BEFORE INSERT ON nodes
       WHEN NEW.kind>0 AND NOT EXISTS (
           SELECT 1 FROM nodes WHERE id=NEW.parent_id AND kind=NEW.kind-1)
       BEGIN SELECT RAISE(ABORT,'invalid parent level'); END""",
    """CREATE TRIGGER node_identity_update BEFORE UPDATE OF kind,parent_id ON nodes
       WHEN NEW.kind!=OLD.kind OR NEW.parent_id IS NOT OLD.parent_id
       BEGIN SELECT RAISE(ABORT,'node identity is immutable'); END""",
)


TRASH_SCHEMA = """CREATE TABLE trash (
    id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    title TEXT NOT NULL,
    kind INTEGER NOT NULL CHECK(kind IN (0,1,2)),
    deleted_at TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT
)"""


CALENDAR_SCHEMA = (
    "CREATE TABLE task_identities (id TEXT PRIMARY KEY NOT NULL)",
    "INSERT INTO task_identities SELECT id FROM nodes WHERE kind=0 UNION SELECT task_id FROM trash",
    """CREATE TABLE work_blocks (
        id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL REFERENCES task_identities(id),
        starts_us INTEGER NOT NULL CHECK(typeof(starts_us)='integer'
            AND starts_us BETWEEN -62135596800000000 AND 253402300799999999),
        ends_us INTEGER NOT NULL CHECK(typeof(ends_us)='integer'
            AND ends_us BETWEEN -62135596800000000 AND 253402300799999999 AND ends_us>starts_us)
    )""",
    "CREATE INDEX work_blocks_task ON work_blocks(task_id,starts_us)",
    "CREATE INDEX work_blocks_time ON work_blocks(starts_us,ends_us)",
)


EVENT_SCHEMA = (
    """CREATE TABLE work_blocks_v4 (
        id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT REFERENCES task_identities(id),
        starts_us INTEGER NOT NULL CHECK(typeof(starts_us)='integer'
            AND starts_us BETWEEN -62135596800000000 AND 253402300799999999),
        ends_us INTEGER NOT NULL CHECK(typeof(ends_us)='integer'
            AND ends_us BETWEEN -62135596800000000 AND 253402300799999999 AND ends_us>starts_us),
        title TEXT NOT NULL DEFAULT '',
        CHECK((task_id IS NOT NULL AND title='') OR (task_id IS NULL AND length(trim(title))>0))
    )""",
    "INSERT INTO work_blocks_v4(id,task_id,starts_us,ends_us) "
    "SELECT id,task_id,starts_us,ends_us FROM work_blocks",
    "DROP TABLE work_blocks",
    "ALTER TABLE work_blocks_v4 RENAME TO work_blocks",
    "CREATE INDEX work_blocks_task ON work_blocks(task_id,starts_us)",
    "CREATE INDEX work_blocks_time ON work_blocks(starts_us,ends_us)",
)


FOCUS_SCHEMA = (
    """CREATE TABLE focus_sessions (
        id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL REFERENCES task_identities(id),
        started_us INTEGER NOT NULL,
        target_us INTEGER CHECK(target_us>0),
        finished_us INTEGER CHECK(finished_us>=started_us),
        overtime INTEGER NOT NULL DEFAULT 0 CHECK(overtime IN (0,1)),
        revision INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE UNIQUE INDEX focus_one_active ON focus_sessions((1)) WHERE finished_us IS NULL",
    """CREATE TABLE focus_intervals (
        session_id TEXT NOT NULL REFERENCES focus_sessions(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        starts_us INTEGER NOT NULL,
        ends_us INTEGER CHECK(ends_us>=starts_us),
        PRIMARY KEY(session_id,position)
    )""",
    "CREATE UNIQUE INDEX focus_one_open_interval ON focus_intervals(session_id) "
    "WHERE ends_us IS NULL",
)


COMPLETION_SCHEMA = """CREATE TABLE task_completion_dates (
    task_id TEXT PRIMARY KEY NOT NULL REFERENCES task_identities(id),
    completed_us INTEGER NOT NULL
)"""


class TaskStore:
    def __init__(self, path: Path, *, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(UTC))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        try:
            self._initialize()
        except BaseException:
            self._db.close()
            raise

    def _initialize(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            app_id = self._db.execute("PRAGMA application_id").fetchone()[0]
            if version == SCHEMA_VERSION and app_id == APPLICATION_ID:
                self._db.commit()
                return
            if version in (1, 2, 3, 4, 5) and app_id == APPLICATION_ID:
                if version == 1:
                    self._db.execute(TRASH_SCHEMA)
                if version < 3:
                    for statement in CALENDAR_SCHEMA:
                        self._db.execute(statement)
                if version < 4:
                    for statement in EVENT_SCHEMA:
                        self._db.execute(statement)
                if version < 5:
                    for statement in FOCUS_SCHEMA:
                        self._db.execute(statement)
                self._db.execute(COMPLETION_SCHEMA)
                self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self._db.commit()
                return
            tables = self._db.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if version != 0 or app_id != 0 or tables:
                raise ValueError("Неизвестная или более новая схема базы; данные не изменены")
            for statement in SCHEMA:
                self._db.execute(statement)
            self._db.execute(TRASH_SCHEMA)
            for statement in (*CALENDAR_SCHEMA, *EVENT_SCHEMA, *FOCUS_SCHEMA):
                self._db.execute(statement)
            self._db.execute(COMPLETION_SCHEMA)
            self._db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()

    def save_category(self, category: Category):
        with self._db:
            self._write_category(category)

    def _write_category(self, category: Category):
        self._db.execute(
            "INSERT INTO categories VALUES (?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title",
            (str(category.id), category.title),
        )

    def list_categories(self) -> tuple[Category, ...]:
        return tuple(
            Category(id=UUID(row["id"]), title=row["title"])
            for row in self._db.execute("SELECT * FROM categories ORDER BY rowid")
        )

    def save_task(self, task: Task):
        with self._db:
            self._write_task(task)

    def create_task(self, task: Task, category: Category | None = None):
        """Создание не может перезаписать существующую задачу или её потомка."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self._db.execute(
                "SELECT 1 FROM nodes WHERE id=? UNION ALL SELECT 1 FROM task_identities WHERE id=?",
                (str(task.id), str(task.id)),
            ).fetchone():
                raise ValueError("Задача с таким идентификатором уже существует")
            if category is not None:
                self._write_category(category)
            self._write_task(task)

    def update_task(
        self, identifier: UUID, transform: Callable[[Task], Task], category: Category | None = None
    ) -> Task:
        """Прочитать, преобразовать и записать одним изменением.

        transform — чистая функция; не должна обращаться к этому соединению.
        Блокировка записи берётся до чтения, исключая потерю параллельных изменений.
        """
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            task = self.get_task(identifier)
            if task is None:
                raise KeyError(identifier)
            updated = transform(task)
            if type(updated) is not Task or updated.id != identifier:
                raise ValueError("Преобразование должно сохранить задачу и её ID")
            if category is not None:
                self._write_category(category)
            self._write_task(updated)
            return updated

    def list_completion_dates(self) -> dict[UUID, datetime]:
        return {
            UUID(row["task_id"]): from_instant_us(row["completed_us"])
            for row in self._db.execute("SELECT task_id,completed_us FROM task_completion_dates")
        }

    def _write_task(self, task: Task, *, restore_root: bool = False):
        task_id = str(task.id)
        previous = self._db.execute(
            "SELECT completed FROM nodes WHERE id=? AND kind=0", (task_id,)
        ).fetchone()
        self._db.execute("INSERT OR IGNORE INTO task_identities VALUES (?)", (str(task.id),))
        duration = task.expected_duration
        microseconds = (
            None
            if duration is None
            else ((duration.days * 86400 + duration.seconds) * 1_000_000 + duration.microseconds)
        )
        if microseconds is not None and microseconds > 2**63 - 1:
            raise ValueError("Длительность превышает диапазон SQLite")
        result = self._db.execute(
            """INSERT INTO nodes
               (id,parent_id,position,kind,title,completed,description,category_id,
                difficulty,expected_us,deadline,weight)
               VALUES (?,NULL,0,0,?,?,?,?,?,?,?,NULL)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                completed=excluded.completed,description=excluded.description,
                category_id=excluded.category_id,difficulty=excluded.difficulty,
                expected_us=excluded.expected_us,deadline=excluded.deadline
               WHERE nodes.kind=0""",
            (
                str(task.id),
                task.title,
                int(task.completed),
                task.description,
                str(task.category_id) if task.category_id else None,
                task.difficulty,
                microseconds,
                task.deadline.isoformat() if task.deadline else None,
            ),
        )
        if not result.rowcount:
            raise ValueError("ID задачи занят подзадачей")
        if not task.completed:
            self._db.execute("DELETE FROM task_completion_dates WHERE task_id=?", (task_id,))
        elif not restore_root and (previous is None or not previous["completed"]):
            self._db.execute(
                """INSERT INTO task_completion_dates VALUES (?,?)
                   ON CONFLICT(task_id) DO UPDATE SET completed_us=excluded.completed_us""",
                (task_id, instant_us(self._clock())),
            )
        self._db.execute("DELETE FROM nodes WHERE parent_id=?", (task_id,))
        for position, child in enumerate(task.subtasks):
            self._save_child(child, task.id, position, 1, child.weight)
            for item_position, item in enumerate(child.items):
                self._save_child(item, child.id, item_position, 2, None)

    def _save_child(self, child, parent_id, position, kind, weight):
        self._db.execute(
            """INSERT INTO nodes (id,parent_id,position,kind,title,completed,weight)
               VALUES (?,?,?,?,?,?,?)""",
            (
                str(child.id),
                str(parent_id),
                position,
                kind,
                child.title,
                int(child.completed),
                str(weight) if weight is not None else None,
            ),
        )

    def get_task(self, identifier: UUID) -> Task | None:
        # One statement reads the whole tree from a consistent SQLite snapshot.
        rows = self._db.execute(
            """WITH RECURSIVE tree AS (
                   SELECT * FROM nodes WHERE id=? AND kind=0
                   UNION ALL SELECT n.* FROM nodes n JOIN tree t ON n.parent_id=t.id)
                   SELECT * FROM tree ORDER BY position""",
            (str(identifier),),
        ).fetchall()
        if not rows:
            return None
        root = next(row for row in rows if row["kind"] == 0)
        children = tuple(
            Subtask(
                id=UUID(row["id"]),
                title=row["title"],
                completed=bool(row["completed"]),
                weight=Decimal(row["weight"]) if row["weight"] is not None else None,
                items=tuple(
                    ChecklistItem(
                        id=UUID(item["id"]), title=item["title"], completed=bool(item["completed"])
                    )
                    for item in rows
                    if item["parent_id"] == row["id"]
                ),
            )
            for row in rows
            if row["kind"] == 1
        )
        return Task(
            id=UUID(root["id"]),
            title=root["title"],
            completed=bool(root["completed"]),
            description=root["description"],
            category_id=UUID(root["category_id"]) if root["category_id"] else None,
            difficulty=root["difficulty"],
            expected_duration=timedelta(microseconds=root["expected_us"])
            if root["expected_us"] is not None
            else None,
            deadline=datetime.fromisoformat(root["deadline"]) if root["deadline"] else None,
            subtasks=children,
        )

    def list_tasks(self) -> tuple[Task, ...]:
        with self._db:
            self._db.execute("BEGIN")
            identifiers = [
                UUID(row[0])
                for row in self._db.execute("SELECT id FROM nodes WHERE kind=0 ORDER BY rowid")
            ]
            return tuple(self.get_task(identifier) for identifier in identifiers)

    def archive_node(
        self,
        task_id: UUID,
        node_id: UUID,
        transform: Callable[[Task], Task | None],
        deleted_at: datetime,
    ) -> str:
        if deleted_at.utcoffset() is None:
            raise ValueError("Время удаления должно содержать часовой пояс")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            before = self.get_task(task_id)
            if before is None:
                raise KeyError(task_id)
            candidates = [
                (before, 0),
                *((x, 1) for x in before.subtasks),
                *((x, 2) for child in before.subtasks for x in child.items),
            ]
            node, kind = next(
                ((x, level) for x, level in candidates if x.id == node_id), (None, None)
            )
            if node is None:
                raise KeyError(node_id)
            after = transform(before)
            if (after is None) != (node_id == task_id):
                raise ValueError("Изменение должно удалять только выбранный уровень")
            if after is not None and (type(after) is not Task or after.id != before.id):
                raise ValueError("Изменение не должно менять ID задачи")
            identifier = str(uuid4())
            self._db.execute(
                "INSERT INTO trash VALUES (?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    str(task_id),
                    str(node_id),
                    node.title,
                    kind,
                    deleted_at.astimezone(UTC).isoformat(),
                    encode_task(before),
                    encode_task(after) if after is not None else None,
                ),
            )
            if after is None:
                self._db.execute("DELETE FROM nodes WHERE id=?", (str(task_id),))
            else:
                self._write_task(after)
            return identifier

    def list_trash(self) -> tuple[dict, ...]:
        return tuple(
            dict(row)
            for row in self._db.execute(
                "SELECT id,task_id,node_id,title,kind,deleted_at FROM trash "
                "ORDER BY deleted_at DESC,id"
            )
        )

    def restore_entry(
        self, identifier: UUID, transform: Callable[[Task | None, Task, Task | None, UUID], Task]
    ) -> Task:
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            entry = self._db.execute(
                "SELECT * FROM trash WHERE id=?", (str(identifier),)
            ).fetchone()
            if entry is None:
                raise KeyError(identifier)
            before = decode_task(entry["before_json"])
            after = decode_task(entry["after_json"]) if entry["after_json"] is not None else None
            current = self.get_task(before.id)
            restored = transform(current, before, after, UUID(entry["node_id"]))
            if restored.id != before.id:
                raise ValueError("Восстановление не должно менять ID задачи")
            self._write_task(restored, restore_root=current is None and entry["kind"] == 0)
            self._db.execute("DELETE FROM trash WHERE id=?", (str(identifier),))
            return restored

    def list_work_blocks(
        self,
        *,
        task_id: UUID | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> tuple[WorkBlock, ...]:
        start = instant_us(window_start) if window_start is not None else None
        end = instant_us(window_end) if window_end is not None else None
        if start is not None and end is not None and end <= start:
            raise ValueError("Конец периода должен быть позже начала")
        conditions = ["(b.task_id IS NULL OR n.kind=0)"]
        parameters = []
        if task_id is not None:
            conditions.append("b.task_id=?")
            parameters.append(str(task_id))
        if start is not None:
            conditions.append("b.ends_us>?")
            parameters.append(start)
        if end is not None:
            conditions.append("b.starts_us<?")
            parameters.append(end)
        rows = self._db.execute(
            "SELECT b.* FROM work_blocks b LEFT JOIN nodes n ON n.id=b.task_id WHERE "
            + " AND ".join(conditions)
            + " ORDER BY b.starts_us,b.id",
            parameters,
        )
        return tuple(self._read_work_block(row) for row in rows)

    @staticmethod
    def _read_work_block(row) -> WorkBlock:
        return WorkBlock(
            id=UUID(row["id"]),
            task_id=UUID(row["task_id"]) if row["task_id"] is not None else None,
            title=row["title"],
            starts_at=from_instant_us(row["starts_us"]),
            ends_at=from_instant_us(row["ends_us"]),
        )

    def get_work_block(self, identifier: UUID) -> WorkBlock | None:
        row = self._db.execute(
            "SELECT b.* FROM work_blocks b LEFT JOIN nodes n ON n.id=b.task_id "
            "WHERE b.id=? AND (b.task_id IS NULL OR n.kind=0)",
            (str(identifier),),
        ).fetchone()
        return self._read_work_block(row) if row is not None else None

    def _block_context(self, block: WorkBlock) -> tuple[Task | None, tuple[WorkBlock, ...]]:
        task = self.get_task(block.task_id) if block.task_id is not None else None
        if block.task_id is not None and task is None:
            raise KeyError(block.task_id)
        nearby = self.list_work_blocks(window_start=block.starts_at, window_end=block.ends_at)
        return task, nearby

    def create_work_block(self, block: WorkBlock) -> tuple[Task | None, tuple[WorkBlock, ...]]:
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if block.task_id is not None and self.get_task(block.task_id) is None:
                raise KeyError(block.task_id)
            self._db.execute(
                "INSERT INTO work_blocks(id,task_id,starts_us,ends_us,title) VALUES (?,?,?,?,?)",
                (
                    str(block.id),
                    str(block.task_id) if block.task_id is not None else None,
                    instant_us(block.starts_at),
                    instant_us(block.ends_at),
                    block.title,
                ),
            )
            return self._block_context(block)

    def update_work_block(
        self, block: WorkBlock, *, expected: WorkBlock | None = None
    ) -> tuple[Task | None, tuple[WorkBlock, ...]]:
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            current = self.get_work_block(block.id)
            if current is None:
                raise KeyError(block.id)
            if expected is not None and current != expected:
                raise ValueError("Блок уже изменился. Обновите календарь и повторите действие")
            if current.task_id != block.task_id:
                raise ValueError("Перенос времени не должен менять задачу рабочего блока")
            self._db.execute(
                "UPDATE work_blocks SET starts_us=?,ends_us=?,title=? WHERE id=?",
                (
                    instant_us(block.starts_at),
                    instant_us(block.ends_at),
                    block.title,
                    str(block.id),
                ),
            )
            return self._block_context(block)

    def work_block_context(
        self, identifier: UUID
    ) -> tuple[Task | None, WorkBlock, tuple[WorkBlock, ...]]:
        with self._db:
            self._db.execute("BEGIN")
            block = self.get_work_block(identifier)
            if block is None:
                raise KeyError(identifier)
            task, nearby = self._block_context(block)
            return task, block, nearby

    def list_focus_sessions(self) -> tuple[FocusSession, ...]:
        rows = self._db.execute(
            """SELECT s.*, MAX(CASE WHEN i.ends_us IS NULL THEN i.starts_us END) AS running,
                      COALESCE(SUM(i.ends_us-i.starts_us),0) AS worked
               FROM focus_sessions s LEFT JOIN focus_intervals i ON i.session_id=s.id
               GROUP BY s.id ORDER BY s.started_us DESC, s.rowid DESC"""
        )
        return tuple(
            FocusSession(
                UUID(row["id"]),
                UUID(row["task_id"]),
                row["started_us"],
                row["target_us"],
                row["finished_us"],
                row["running"],
                row["worked"],
                bool(row["overtime"]),
                row["revision"],
            )
            for row in rows
        )

    def change_focus(
        self,
        action: str,
        now: datetime,
        expected: tuple[str, int] | None,
        *,
        task_id: UUID | None = None,
        target_us: int | None = None,
        recovered_end: datetime | None = None,
    ):
        now_us = instant_us(now)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            active = next((x for x in self.list_focus_sessions() if x.finished_us is None), None)
            actual = (str(active.id), active.revision) if active else None
            if expected != actual:
                raise ValueError("Сессия уже изменилась. Обновите список и повторите действие")
            if action == "start":
                if task_id is None or self.get_task(task_id) is None:
                    raise KeyError(task_id)
                if target_us is not None and not 0 < target_us <= 2**63 - 1:
                    raise ValueError("Укажите положительную длительность")
                if active:
                    self._finish_focus(active, now_us)
                identifier = str(uuid4())
                self._db.execute(
                    "INSERT INTO focus_sessions(id,task_id,started_us,target_us) VALUES (?,?,?,?)",
                    (identifier, str(task_id), now_us, target_us),
                )
                self._db.execute(
                    "INSERT INTO focus_intervals VALUES (?,0,?,NULL)", (identifier, now_us)
                )
                return
            if active is None:
                raise ValueError("Нет активной фокус-сессии")
            identifier = str(active.id)
            if action == "recover":
                last_known = self._db.execute(
                    "SELECT MAX(COALESCE(ends_us,starts_us)) "
                    "FROM focus_intervals WHERE session_id=?",
                    (identifier,),
                ).fetchone()[0]
                end = last_known if recovered_end is None else instant_us(recovered_end)
                if recovered_end is not None and not last_known <= end <= now_us:
                    raise ValueError(
                        "Окончание должно быть между последней отметкой и текущим временем"
                    )
                self._finish_focus(active, end)
                return
            if action == "finish":
                self._finish_focus(active, now_us)
                return
            if action == "pause" and active.running_since_us is not None:
                self._close_focus_interval(identifier, now_us)
            elif action == "resume" and active.running_since_us is None:
                self._db.execute(
                    """INSERT INTO focus_intervals
                       SELECT ?,MAX(position)+1,MAX(?,MAX(ends_us)),NULL
                       FROM focus_intervals WHERE session_id=?""",
                    (identifier, now_us, identifier),
                )
            elif action == "overtime" and active.target_reached(now_us):
                self._db.execute("UPDATE focus_sessions SET overtime=1 WHERE id=?", (identifier,))
            else:
                raise ValueError("Это действие недоступно в текущем состоянии сессии")
            self._db.execute(
                "UPDATE focus_sessions SET revision=revision+1 WHERE id=?", (identifier,)
            )

    def _close_focus_interval(self, identifier: str, now_us: int):
        self._db.execute(
            """UPDATE focus_intervals SET ends_us=MAX(starts_us,?)
               WHERE session_id=? AND ends_us IS NULL""",
            (now_us, identifier),
        )

    def _finish_focus(self, session: FocusSession, now_us: int):
        identifier = str(session.id)
        self._close_focus_interval(identifier, now_us)
        self._db.execute(
            """UPDATE focus_sessions SET finished_us=MAX(?,(
                   SELECT MAX(ends_us) FROM focus_intervals WHERE session_id=?
               )), revision=revision+1 WHERE id=?""",
            (now_us, identifier, identifier),
        )

    def list_finished_focus_intervals(self) -> dict[UUID, list[tuple[int, int]]]:
        result = {}
        rows = self._db.execute(
            """SELECT i.session_id,i.starts_us,i.ends_us FROM focus_intervals i
               JOIN focus_sessions s ON s.id=i.session_id
               WHERE s.finished_us IS NOT NULL ORDER BY i.session_id,i.position"""
        )
        for row in rows:
            result.setdefault(UUID(row["session_id"]), []).append(
                (row["starts_us"], row["ends_us"])
            )
        return result

    def add_focus_record(self, task_id: UUID, start: datetime, end: datetime, now: datetime):
        start_us, end_us = instant_us(start), instant_us(end)
        if not start_us < end_us <= instant_us(now):
            raise ValueError("Окончание должно быть позже начала и не в будущем")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self.get_task(task_id) is None:
                raise KeyError(task_id)
            identifier = str(uuid4())
            self._db.execute(
                "INSERT INTO focus_sessions(id,task_id,started_us,finished_us) VALUES (?,?,?,?)",
                (identifier, str(task_id), start_us, end_us),
            )
            self._db.execute(
                "INSERT INTO focus_intervals VALUES (?,0,?,?)", (identifier, start_us, end_us)
            )

    def edit_focus_duration(self, identifier: UUID, revision: int, duration_us: int, now: datetime):
        if duration_us <= 0:
            raise ValueError("Длительность должна быть больше нуля")
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            session = self._db.execute(
                """SELECT s.finished_us,s.revision FROM focus_sessions s
                   JOIN nodes n ON n.id=s.task_id AND n.kind=0
                   WHERE s.id=?""",
                (str(identifier),),
            ).fetchone()
            if session is None or session["finished_us"] is None or session["revision"] != revision:
                raise ValueError("Запись уже изменилась. Обновите список и повторите действие")
            intervals = self._db.execute(
                "SELECT * FROM focus_intervals WHERE session_id=? ORDER BY position",
                (str(identifier),),
            ).fetchall()
            worked = sum(row["ends_us"] - row["starts_us"] for row in intervals)
            if duration_us == worked:
                return
            if duration_us > worked:
                last = intervals[-1]
                end = last["ends_us"] + duration_us - worked
                if end > instant_us(now):
                    raise ValueError("Исправленное окончание попадает в будущее")
            else:
                remaining = duration_us
                for last in intervals:
                    span = last["ends_us"] - last["starts_us"]
                    if remaining <= span:
                        break
                    remaining -= span
                end = last["starts_us"] + remaining
                self._db.execute(
                    "DELETE FROM focus_intervals WHERE session_id=? AND position>?",
                    (str(identifier), last["position"]),
                )
            self._db.execute(
                "UPDATE focus_intervals SET ends_us=? WHERE session_id=? AND position=?",
                (end, str(identifier), last["position"]),
            )
            self._db.execute(
                "UPDATE focus_sessions SET finished_us=?,revision=revision+1 WHERE id=?",
                (end, str(identifier)),
            )
