"""SQLite v1. Каждый экземпляр используется только в создавшем его потоке."""

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task

APPLICATION_ID = 0x5249544D
SCHEMA_VERSION = 1
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


class TaskStore:
    def __init__(self, path: Path):
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
            tables = self._db.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if version != 0 or app_id != 0 or tables:
                raise ValueError("Неизвестная или более новая схема базы; данные не изменены")
            for statement in SCHEMA:
                self._db.execute(statement)
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

    def update_task(self, identifier: UUID, transform: Callable[[Task], Task]) -> Task:
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
            self._write_task(updated)
            return updated

    def _write_task(self, task: Task):
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
        self._db.execute("DELETE FROM nodes WHERE parent_id=?", (str(task.id),))
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
