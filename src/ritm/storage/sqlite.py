"""SQLite с версионированной схемой. Каждый экземпляр используется только в создавшем его потоке."""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from ritm.domain.calendar import WorkBlock, from_instant_us, instant_us
from ritm.domain.focus import FocusSession
from ritm.domain.pomodoro import Pomodoro
from ritm.domain.sources import SourceLink, SourceRef
from ritm.domain.tasks import Category, ChecklistItem, Subtask, Task
from ritm.storage.attachments import Attachment
from ritm.storage.codec import decode_task, encode_task
from ritm.storage.garden_source import GardenSourceMixin, create_source
from ritm.storage.profile_guard import connect_profile
from ritm.storage.recurrence import RECURRENCE_SCHEMA, RecurrenceMixin
from ritm.storage.sync_capture import CAPTURE_SCHEMA, SyncCaptureMixin
from ritm.storage.sync_intents import SYNC_SCHEMA, SyncIntentMixin
from ritm.storage.sync_outbox import OUTBOX_SCHEMA, SyncOutboxMixin
from ritm.storage.sync_source import SyncSourceMixin

APPLICATION_ID = 0x5249544D
SCHEMA_VERSION = 17
SOURCE_SCHEMA = (
    "CREATE TABLE source_links (key TEXT PRIMARY KEY NOT NULL, fingerprint TEXT NOT NULL, "
    "url TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('task','event')), "
    "target_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1))",
    "CREATE INDEX source_links_target ON source_links(kind,target_id)",
    "CREATE TABLE mail_proposals (id TEXT PRIMARY KEY NOT NULL, digest TEXT NOT NULL, "
    "source_key TEXT NOT NULL REFERENCES source_links(key), target_id TEXT NOT NULL)",
)
POMODORO_SCHEMA = (
    "CREATE TABLE pomodoro (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
)
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


ATTACHMENT_SCHEMA = """CREATE TABLE attachments (
    id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL REFERENCES task_identities(id),
    node_id TEXT NOT NULL,
    name TEXT NOT NULL,
    suffix TEXT NOT NULL,
    size INTEGER NOT NULL CHECK(size>=0)
)"""

MATERIAL_SCHEMA = (
    """CREATE TABLE materials (
        id TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL,
        suffix TEXT NOT NULL,
        size INTEGER NOT NULL CHECK(size>=0)
    )""",
    """CREATE TABLE material_links (
        task_id TEXT NOT NULL REFERENCES task_identities(id),
        node_id TEXT NOT NULL,
        material_id TEXT NOT NULL REFERENCES materials(id),
        PRIMARY KEY(task_id,node_id,material_id)
    )""",
)

CLEANUP_SCHEMA = """CREATE TABLE attachment_cleanup (
    id TEXT PRIMARY KEY NOT NULL,
    suffix TEXT NOT NULL
)"""

PROPOSAL_SCHEMA = """CREATE TABLE applied_proposals (
    id TEXT PRIMARY KEY NOT NULL,
    digest TEXT NOT NULL CHECK(length(digest)=64),
    task_id TEXT NOT NULL
)"""

ATTACHMENT_OWNER = """
    FROM nodes n
    LEFT JOIN nodes parent ON parent.id=n.parent_id
    LEFT JOIN nodes grandparent ON grandparent.id=parent.parent_id
"""


class TaskStore(
    SyncCaptureMixin,
    SyncOutboxMixin,
    SyncSourceMixin,
    SyncIntentMixin,
    RecurrenceMixin,
    GardenSourceMixin,
):
    def __init__(self, path: Path, *, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(UTC))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = connect_profile(path)
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
            if version in range(1, 17) and app_id == APPLICATION_ID:
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
                if version < 6:
                    self._db.execute(COMPLETION_SCHEMA)
                if version < 7:
                    self._db.execute(ATTACHMENT_SCHEMA)
                if version < 8:
                    for statement in MATERIAL_SCHEMA:
                        self._db.execute(statement)
                if version < 9:
                    self._db.execute(CLEANUP_SCHEMA)
                if version < 10:
                    self._db.execute(PROPOSAL_SCHEMA)
                if version < 11:
                    self._db.execute(POMODORO_SCHEMA)
                if version < 12:
                    for statement in SOURCE_SCHEMA:
                        self._db.execute(statement)
                if version < 14:
                    for statement in RECURRENCE_SCHEMA:
                        self._db.execute(statement)
                if version < 15:
                    for statement in SYNC_SCHEMA:
                        self._db.execute(statement)
                if version < 16:
                    for statement in OUTBOX_SCHEMA:
                        self._db.execute(statement)
                if version < 17:
                    for statement in CAPTURE_SCHEMA:
                        self._db.execute(statement)
                if version < 13:
                    create_source(self._db)
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
            self._db.execute(ATTACHMENT_SCHEMA)
            for statement in MATERIAL_SCHEMA:
                self._db.execute(statement)
            self._db.execute(CLEANUP_SCHEMA)
            self._db.execute(PROPOSAL_SCHEMA)
            self._db.execute(POMODORO_SCHEMA)
            for statement in SOURCE_SCHEMA:
                self._db.execute(statement)
            for statement in RECURRENCE_SCHEMA:
                self._db.execute(statement)
            for statement in SYNC_SCHEMA:
                self._db.execute(statement)
            for statement in OUTBOX_SCHEMA:
                self._db.execute(statement)
            for statement in CAPTURE_SCHEMA:
                self._db.execute(statement)
            create_source(self._db)
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
            self._db.execute("BEGIN IMMEDIATE")
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

    def apply_proposal(
        self,
        identifier: UUID,
        digest: str,
        before: Task | None,
        task: Task,
        *,
        material_ids: tuple[UUID, ...] = (),
    ) -> UUID:
        """Проверка снимка, запись дерева, ссылки и квитанция — одна транзакция."""
        if material_ids:
            digest = hashlib.sha256(
                (digest + "|" + ",".join(sorted(str(i) for i in set(material_ids)))).encode()
            ).hexdigest()
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            receipt = self._db.execute(
                "SELECT digest,task_id FROM applied_proposals WHERE id=?", (str(identifier),)
            ).fetchone()
            if receipt is not None:
                if receipt["digest"] != digest:
                    raise ValueError("Этот черновик уже применён с другим содержимым")
                return UUID(receipt["task_id"])
            if before is None:
                if self._db.execute(
                    "SELECT 1 FROM task_identities WHERE id=?", (str(task.id),)
                ).fetchone():
                    raise ValueError("Задача с таким идентификатором уже существует")
            elif task.id != before.id or self.get_task(before.id) != before:
                raise ValueError("Задача изменилась или удалена. Сформируйте новый черновик.")
            self._write_task(task)
            for material_id in material_ids:
                if not self.list_materials(material_id):
                    raise ValueError("Материал больше не доступен. Выберите файлы заново.")
                self._db.execute(
                    "INSERT INTO material_links VALUES (?,?,?) ON CONFLICT DO NOTHING",
                    (str(task.id), str(task.id), str(material_id)),
                )
            self._db.execute(
                "INSERT INTO applied_proposals VALUES (?,?,?)",
                (str(identifier), digest, str(task.id)),
            )
            return task.id

    @staticmethod
    def _read_source_link(row):
        return SourceLink(
            SourceRef(row["key"], row["fingerprint"], row["url"]),
            row["kind"],
            UUID(row["target_id"]),
            row["revision"],
        )

    def get_source_link(self, key: str) -> SourceLink | None:
        row = self._db.execute("SELECT * FROM source_links WHERE key=?", (key,)).fetchone()
        return self._read_source_link(row) if row is not None else None

    def list_source_links(self) -> tuple[SourceLink, ...]:
        return tuple(
            self._read_source_link(row)
            for row in self._db.execute("SELECT * FROM source_links ORDER BY key")
        )

    def apply_mail_proposal(self, draft, *, expected_overlaps=None) -> UUID:
        """Изменение одной записи, связь источника и квитанция атомарны."""
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            receipt = self._db.execute(
                "SELECT digest,target_id FROM mail_proposals WHERE id=?", (str(draft.id),)
            ).fetchone()
            if receipt is not None:
                if receipt["digest"] != draft.digest:
                    raise ValueError("Предложение по письму уже применено с другим содержимым.")
                return UUID(receipt["target_id"])
            current_link = self.get_source_link(draft.source.key)
            if current_link != draft.link:
                raise ValueError(
                    "Письмо уже обработано или связь изменилась. Обновите предложение."
                )
            if draft.link is not None and (
                draft.link.target_id != draft.after.id or draft.link.kind != draft.kind
            ):
                raise ValueError("Предложение не должно менять связанную с письмом запись.")
            if draft.before is None:
                if draft.link is not None:
                    raise ValueError("Связанная запись удалена. Повторное создание запрещено.")
                if isinstance(draft.after, Task):
                    known = self._db.execute(
                        "SELECT 1 FROM task_identities WHERE id=?", (str(draft.after.id),)
                    ).fetchone()
                else:
                    known = self._db.execute(
                        "SELECT 1 FROM work_blocks WHERE id=?", (str(draft.after.id),)
                    ).fetchone()
                if known:
                    raise ValueError("Запись из письма уже существует.")
            else:
                current = (
                    self.get_task(draft.before.id)
                    if isinstance(draft.before, Task)
                    else self.get_work_block(draft.before.id)
                )
                if current != draft.before or draft.after.id != draft.before.id:
                    raise ValueError(
                        "Связанная запись изменилась или удалена. Обновите предложение."
                    )
            if isinstance(draft.after, Task):
                self._write_task(draft.after)
            else:
                block = draft.after
                if block.task_id is not None:
                    raise ValueError("Письмо может предложить только самостоятельное событие.")
                if expected_overlaps is not None:
                    nearby = tuple(
                        b
                        for b in self.list_work_blocks(
                            window_start=block.starts_at, window_end=block.ends_at
                        )
                        if b.id != block.id
                    )
                    if nearby != expected_overlaps:
                        raise ValueError(
                            "Календарь изменился. Обновите предпросмотр перед подтверждением."
                        )
                self._db.execute(
                    "INSERT INTO work_blocks(id,task_id,starts_us,ends_us,title) "
                    "VALUES (?,NULL,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET starts_us=excluded.starts_us,"
                    "ends_us=excluded.ends_us,title=excluded.title",
                    (
                        str(block.id),
                        instant_us(block.starts_at),
                        instant_us(block.ends_at),
                        block.title,
                    ),
                )
            revision = current_link.revision + 1 if current_link else 1
            self._db.execute(
                "INSERT INTO source_links VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "fingerprint=excluded.fingerprint,url=excluded.url,revision=excluded.revision",
                (
                    draft.source.key,
                    draft.source.fingerprint,
                    draft.source.url,
                    draft.kind,
                    str(draft.after.id),
                    revision,
                ),
            )
            self._db.execute(
                "INSERT INTO mail_proposals VALUES (?,?,?,?)",
                (str(draft.id), draft.digest, draft.source.key, str(draft.after.id)),
            )
            return draft.after.id

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

    def apply_schedule(self, draft) -> UUID:
        """Записать весь подтверждённый набор и квитанцию без частичного результата."""
        context = draft.context
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            receipt = self._db.execute(
                "SELECT digest,task_id FROM applied_proposals WHERE id=?", (str(draft.id),)
            ).fetchone()
            if receipt:
                if receipt["digest"] != draft.digest or receipt["task_id"] != str(context.task.id):
                    raise ValueError("Этот черновик уже применён с другим содержимым")
                return context.task.id
            planned = sum(
                (b.duration for b in self.list_work_blocks(task_id=context.task.id)), timedelta()
            )
            if (
                self.get_task(context.task.id) != context.task
                or self.list_work_blocks(window_start=context.starts_at, window_end=context.ends_at)
                != context.busy
                or max(timedelta(), context.task.expected_duration - planned) != context.remaining
            ):
                raise ValueError("Задача или занятость изменились. Сформируйте расписание заново.")
            if any(b.starts_at < self._clock() for b in draft.blocks):
                raise ValueError("Начало блока уже прошло. Сформируйте расписание заново.")
            for block in draft.blocks:
                self._db.execute(
                    "INSERT INTO work_blocks(id,task_id,starts_us,ends_us,title) "
                    "VALUES (?,?,?,?,?)",
                    (
                        str(block.id),
                        str(block.task_id),
                        instant_us(block.starts_at),
                        instant_us(block.ends_at),
                        "",
                    ),
                )
            self._db.execute(
                "INSERT INTO applied_proposals VALUES (?,?,?)",
                (str(draft.id), draft.digest, str(context.task.id)),
            )
            return context.task.id

    def list_completion_dates(self) -> dict[UUID, datetime]:
        return {
            UUID(row["task_id"]): from_instant_us(row["completed_us"])
            for row in self._db.execute("SELECT task_id,completed_us FROM task_completion_dates")
        }

    def _write_task(self, task: Task, *, restore_root: bool = False, restore_event: bool = False):
        if (
            task.deadline is not None
            and self._db.execute(
                "SELECT 1 FROM recurrence_rules WHERE task_id=?", (str(task.id),)
            ).fetchone()
        ):
            raise ValueError("У регулярной задачи нет дедлайна")
        capture = self._sync_capture_active()
        garden_subscribed = self._garden_subscribed()
        before = self.get_task(task.id) if garden_subscribed or capture else None
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
        if garden_subscribed and before != task:
            self._garden_event(
                "task",
                {
                    "id": task_id,
                    "before": encode_task(before) if before else None,
                    "after": encode_task(task),
                    "restore": restore_root or restore_event,
                },
                self._clock(),
            )

        if capture:
            self._capture_task_write(before, task, restored=restore_root or restore_event)

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
                for row in self._db.execute(
                    "SELECT id FROM nodes WHERE kind=0 AND id NOT IN "
                    "(SELECT task_id FROM recurrence_occurrences "
                    "WHERE state IN ('waiting','skipped')) ORDER BY rowid"
                )
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
                self._capture_task_delete(before)
                # Restoring a deleted current template must not revive its series membership.
                self._db.execute(
                    "DELETE FROM recurrence_occurrences WHERE task_id=? AND EXISTS "
                    "(SELECT 1 FROM recurrence_rules WHERE task_id=?)",
                    (str(task_id), str(task_id)),
                )
                self._db.execute("DELETE FROM recurrence_rules WHERE task_id=?", (str(task_id),))
                self._db.execute(
                    "UPDATE recurrence_occurrences SET state='active' WHERE task_id=?",
                    (str(task_id),),
                )
                self._db.execute("DELETE FROM nodes WHERE id=?", (str(task_id),))
                self._garden_event(
                    "task",
                    {
                        "id": str(task_id),
                        "before": encode_task(before),
                        "after": None,
                        "restore": False,
                    },
                    deleted_at,
                )
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

    def purge_trash(self, identifiers: set[UUID]):
        """Удалить выбранные архивы и поставить их копии в очередь очистки."""
        if not identifiers:
            raise ValueError("Выберите записи для удаления")
        selected = {str(identifier) for identifier in identifiers}
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            entries = {row["id"]: row for row in self._db.execute("SELECT * FROM trash")}
            if selected - entries.keys():
                raise ValueError("Корзина изменилась. Обновите список и выберите записи снова")
            roots = {entries[key]["task_id"] for key in selected if entries[key]["kind"] == 0}
            branches = {
                (entries[key]["task_id"], entries[key]["node_id"])
                for key in selected
                if entries[key]["kind"] == 1
            }
            for key in entries.keys() - selected:
                entry = entries[key]
                if entry["task_id"] in roots:
                    raise ValueError("Выберите также отдельно удалённые пункты этой задачи")
                if entry["kind"] == 2 and any(
                    task_id == entry["task_id"] for task_id, _ in branches
                ):
                    before = decode_task(entry["before_json"])
                    if any(
                        (entry["task_id"], str(step.id)) in branches
                        and any(str(item.id) == entry["node_id"] for item in step.items)
                        for step in before.subtasks
                    ):
                        raise ValueError("Выберите также отдельно удалённые пункты этой подзадачи")

            for task_id in roots:
                self._require_archived_node(task_id)
                if self._db.execute(
                    "SELECT 1 FROM focus_sessions WHERE task_id=? AND finished_us IS NULL",
                    (task_id,),
                ).fetchone():
                    raise ValueError("Сначала завершите или восстановите фокус-сессию этой задачи")

            for key in selected:
                entry = entries[key]
                task_id, node_id = entry["task_id"], entry["node_id"]
                if task_id in roots:
                    continue
                node_ids = [node_id]
                if entry["kind"] == 1:
                    before = decode_task(entry["before_json"])
                    branch = next(step for step in before.subtasks if str(step.id) == node_id)
                    node_ids.extend(str(item.id) for item in branch.items)
                for archived_id in node_ids:
                    self._require_archived_node(archived_id)
                    self._purge_attachment_rows(task_id, archived_id)

            for task_id in roots:
                self._purge_attachment_rows(task_id)
                self._db.execute("DELETE FROM work_blocks WHERE task_id=?", (task_id,))
                removed_sessions = [
                    row[0]
                    for row in self._db.execute(
                        "SELECT id FROM focus_sessions WHERE task_id=?", (task_id,)
                    )
                ]
                self._db.execute("DELETE FROM focus_sessions WHERE task_id=?", (task_id,))
                for identifier in removed_sessions:
                    self._garden_focus_event(identifier, self._clock())
                self._db.execute("DELETE FROM task_completion_dates WHERE task_id=?", (task_id,))
                self._db.execute("DELETE FROM task_identities WHERE id=?", (task_id,))
            self._db.executemany("DELETE FROM trash WHERE id=?", ((key,) for key in selected))

    def _require_archived_node(self, identifier: str):
        if self._db.execute("SELECT 1 FROM nodes WHERE id=?", (identifier,)).fetchone():
            raise ValueError("Запись уже восстановлена. Обновите список корзины")

    def _purge_attachment_rows(self, task_id: str, node_id: str | None = None):
        condition = " WHERE task_id=?" if node_id is None else " WHERE task_id=? AND node_id=?"
        args = (task_id,) if node_id is None else (task_id, node_id)
        self._db.execute(
            "INSERT INTO attachment_cleanup SELECT id,suffix FROM attachments" + condition, args
        )
        self._db.execute("DELETE FROM attachments" + condition, args)
        self._db.execute("DELETE FROM material_links" + condition, args)

    def pending_attachment_cleanup(self) -> tuple[tuple[UUID, str], ...]:
        return tuple(
            (UUID(row["id"]), row["suffix"])
            for row in self._db.execute("SELECT id,suffix FROM attachment_cleanup")
        )

    def finish_attachment_cleanup(self, identifier: UUID, remove: Callable[[], None]):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            key = str(identifier)
            if not self._db.execute(
                "SELECT 1 FROM attachment_cleanup WHERE id=?", (key,)
            ).fetchone():
                return
            if self._db.execute(
                "SELECT 1 FROM materials WHERE id=? UNION ALL SELECT 1 FROM attachments WHERE id=?",
                (key, key),
            ).fetchone():
                raise ValueError("Копия всё ещё используется")
            remove()
            self._db.execute("DELETE FROM attachment_cleanup WHERE id=?", (key,))

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
            self._write_task(
                restored, restore_root=current is None and entry["kind"] == 0, restore_event=True
            )
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

    def active_focus(self):
        return next((s for s in self.list_focus_sessions() if s.finished_us is None), None)

    def get_pomodoro(self):
        row = self._db.execute("SELECT payload FROM pomodoro WHERE id=1").fetchone()
        if row is None:
            return Pomodoro()
        try:
            payload = json.loads(row[0])
            if not isinstance(payload, dict) or payload.keys() != Pomodoro().to_dict().keys():
                raise ValueError("Неполный снимок Pomodoro")
            return Pomodoro(**payload)
        except (ValueError, TypeError) as error:
            raise ValueError("Настройки Pomodoro повреждены; данные не перезаписаны") from error

    def _save_pomodoro(self, state):
        state = replace(state, revision=state.revision + 1)
        self._db.execute(
            "INSERT INTO pomodoro VALUES (1,?) ON CONFLICT(id) "
            "DO UPDATE SET payload=excluded.payload",
            (json.dumps(state.to_dict()),),
        )

    def checkpoint_pomodoro(self, now, *, pause=False, startup=False):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            state = self.get_pomodoro()
            if state.phase in ("short", "long") and state.running_since_us is not None:
                self._save_pomodoro(
                    state.checkpoint_break(instant_us(now), pause=pause, startup=startup)
                )

    def change_pomodoro(
        self,
        action,
        now,
        revision,
        expected,
        *,
        task_id=None,
        work=25,
        short=5,
        long=15,
        replace_current=False,
    ):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            state = self.get_pomodoro()
            active = self.active_focus()
            actual = (str(active.id), active.revision) if active else None
            if revision != state.revision or expected != actual:
                raise ValueError("Pomodoro или сессия уже изменились. Обновите список")
            now_us = instant_us(now)
            elapsed = state.elapsed(now_us, active)
            if action in ("start", "work"):
                if action == "start":
                    if state.phase != "idle":
                        raise ValueError("Pomodoro уже запущен")
                    state = Pomodoro(work=work, short=short, long=long, revision=state.revision)
                elif state.phase not in ("work", "short", "long") or (
                    state.phase in ("short", "long") and elapsed < state.target_us
                ):
                    raise ValueError("Перерыв ещё не завершён")
                if active and not replace_current:
                    raise ValueError("Подтвердите завершение текущей сессии")
                self._change_focus("start", now, expected, task_id=task_id)
                after = self.active_focus()
                state = replace(
                    state,
                    phase="work",
                    session_id=str(after.id),
                    base_us=0,
                    elapsed_us=elapsed if state.phase == "work" else 0,
                    running_since_us=None,
                )
            elif action == "break":
                if state.phase != "work" or elapsed < state.target_us:
                    raise ValueError("Рабочий интервал ещё не завершён")
                if active:
                    self._change_focus("finish", now, expected)
                cycle = (state.cycle + 1) % 4
                state = replace(
                    state,
                    phase="long" if cycle == 0 else "short",
                    cycle=cycle,
                    elapsed_us=0,
                    base_us=0,
                    session_id="",
                    running_since_us=now_us,
                )
            elif action in ("pause", "resume") and state.phase in ("short", "long"):
                if (action == "pause") != (state.running_since_us is not None):
                    raise ValueError("Состояние перерыва уже изменилось")
                state = state.checkpoint_break(now_us, pause=action == "pause")
            elif action == "stop":
                if state.phase == "work" and active:
                    self._change_focus("finish", now, expected)
                state = Pomodoro(
                    work=state.work, short=state.short, long=state.long, revision=state.revision
                )
            else:
                raise ValueError("Это действие Pomodoro сейчас недоступно")
            self._save_pomodoro(state)

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
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            state = self.get_pomodoro()
            if state.phase in ("short", "long") and action in ("start", "resume"):
                raise ValueError(
                    "Сначала завершите перерыв кнопкой «Начать работу» или выключите Pomodoro"
                )
            before = self.active_focus()
            self._change_focus(
                action,
                now,
                expected,
                task_id=task_id,
                target_us=target_us,
                recovered_end=recovered_end,
            )
            if state.phase == "work":
                old = next(
                    (s for s in self.list_focus_sessions() if before and s.id == before.id), None
                )
                # A recovery uses the explicitly confirmed end, never the current wall clock.
                worked = old.elapsed(instant_us(now)) if old else 0
                elapsed = state.elapsed_us
                if old and str(old.id) == state.session_id:
                    elapsed += max(0, worked - state.base_us)
                after = self.active_focus()
                self._save_pomodoro(
                    replace(
                        state,
                        elapsed_us=elapsed,
                        session_id=str(after.id) if after else "",
                        base_us=after.elapsed(instant_us(now)) if after else 0,
                    )
                )

    def _change_focus(
        self, action, now, expected, *, task_id=None, target_us=None, recovered_end=None
    ):
        now_us = instant_us(now)
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
            self._garden_focus_event(identifier, now)
            return
        if active is None:
            raise ValueError("Нет активной фокус-сессии")
        identifier = str(active.id)
        if action == "recover":
            last_known = self._db.execute(
                "SELECT MAX(COALESCE(ends_us,starts_us)) FROM focus_intervals WHERE session_id=?",
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
        self._db.execute("UPDATE focus_sessions SET revision=revision+1 WHERE id=?", (identifier,))
        self._garden_focus_event(identifier, now)

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
        self._garden_focus_event(identifier, from_instant_us(now_us))

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
            self._garden_focus_event(identifier, now)

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
            self._garden_focus_event(identifier, now)

    def _require_attachment_owner(self, task_id: UUID, node_id: UUID):
        owner = self._db.execute(
            "SELECT COALESCE(grandparent.id,parent.id,n.id)" + ATTACHMENT_OWNER + " WHERE n.id=?",
            (str(node_id),),
        ).fetchone()
        if owner is None or owner[0] != str(task_id):
            raise KeyError(node_id)

    def add_attachment(self, attachment: Attachment):
        fields = (attachment.name, attachment.suffix, attachment.size)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if attachment.task_id is None and attachment.node_id is None:
                self._db.execute(
                    "INSERT INTO materials VALUES (?,?,?,?)", (str(attachment.id), *fields)
                )
            elif attachment.task_id is not None and attachment.node_id is not None:
                self._require_attachment_owner(attachment.task_id, attachment.node_id)
                self._db.execute(
                    "INSERT INTO attachments VALUES (?,?,?,?,?,?)",
                    (str(attachment.id), str(attachment.task_id), str(attachment.node_id), *fields),
                )
            else:
                raise ValueError("Укажите задачу и пункт либо добавьте файл в материалы")

    def link_material(self, task_id: UUID, node_id: UUID, material_id: UUID):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self._require_attachment_owner(task_id, node_id)
            if not self.list_materials(material_id):
                raise ValueError("Материал больше не доступен")
            self._db.execute(
                "INSERT INTO material_links VALUES (?,?,?) ON CONFLICT DO NOTHING",
                (str(task_id), str(node_id), str(material_id)),
            )

    def list_materials(self, identifier: UUID | None = None) -> tuple[Attachment, ...]:
        query = "SELECT id,NULL AS task_id,NULL AS node_id,name,suffix,size FROM materials"
        args = () if identifier is None else (str(identifier),)
        if args:
            query += " WHERE id=?"
        return tuple(
            self._read_attachment(row) for row in self._db.execute(query + " ORDER BY rowid", args)
        )

    def list_attachments(self, identifier: UUID | None = None) -> tuple[Attachment, ...]:
        direct = (
            "SELECT a.*" + ATTACHMENT_OWNER + " JOIN attachments a ON a.node_id=n.id "
            "AND a.task_id=COALESCE(grandparent.id,parent.id,n.id)"
        )
        linked = (
            "SELECT a.id,l.task_id,l.node_id,a.name,a.suffix,a.size"
            + ATTACHMENT_OWNER
            + " JOIN material_links l ON l.node_id=n.id "
            "AND l.task_id=COALESCE(grandparent.id,parent.id,n.id)"
            " JOIN materials a ON a.id=l.material_id"
        )
        where = " WHERE a.id=?" if identifier is not None else ""
        values = (str(identifier),) if identifier is not None else ()
        return tuple(
            self._read_attachment(row)
            for query, order in ((direct, "a.rowid"), (linked, "l.rowid"))
            for row in self._db.execute(query + where + " ORDER BY " + order, values)
        )

    @staticmethod
    def _read_attachment(row) -> Attachment:
        return Attachment(
            UUID(row["id"]),
            UUID(row["task_id"]) if row["task_id"] is not None else None,
            UUID(row["node_id"]) if row["node_id"] is not None else None,
            row["name"],
            row["suffix"],
            row["size"],
        )
