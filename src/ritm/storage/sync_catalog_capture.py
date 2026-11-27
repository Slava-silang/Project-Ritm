"""Category catalog capture inside the owning local write transaction."""

from uuid import UUID, uuid4

from ritm.domain.tasks import Category
from ritm.integrations.sync_codec import CategoryRecord, RemovalRecord
from ritm.storage.sync_intents import SyncIntent, _stored_uuid


class SyncCatalogCaptureMixin:
    def _last_category_intent(self, identifier):
        return self._db.execute(
            "SELECT id,action FROM sync_intents WHERE target_kind='category' AND target_id=? "
            "ORDER BY seq DESC LIMIT 1",
            (str(identifier),),
        ).fetchone()

    def _known_category(self, identifier):
        snapshot = self._read_sync_snapshot()
        if snapshot is None:
            return False, None
        for entry in snapshot.entries:
            entity = entry.entity
            if (type(entity) is CategoryRecord and entity.category.id == identifier) or (
                type(entity) is RemovalRecord
                and entity.entity_kind == "category"
                and entity.entity_id == identifier
            ):
                return True, entity
        return True, None

    def _append_category_intent(self, category, action):
        parent = self._db.execute(
            "SELECT id FROM sync_intents ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        self._enqueue_sync_intent(
            SyncIntent(
                id=uuid4(),
                target_kind="category",
                target_id=category.id,
                action=action,
                payload=CategoryRecord(category=category),
                depends_on=_stored_uuid(parent[0]) if parent else None,
            )
        )

    def _capture_category_write(self, category, before_title):
        if not self._sync_capture_active() or before_title == category.title:
            return
        previous = self._last_category_intent(category.id)
        if previous is not None:
            action = "put"
        else:
            known, entity = self._known_category(category.id)
            action = (
                "create"
                if (known and entity is None or not known and before_title is None)
                else "put"
            )
        self._append_category_intent(category, action)

    def _ensure_category_dependency(self, identifier):
        if identifier is None or not self._sync_capture_active():
            return
        if self._last_category_intent(identifier) is not None:
            return
        known, entity = self._known_category(identifier)
        if type(entity) is CategoryRecord:
            return
        row = self._db.execute(
            "SELECT title FROM categories WHERE id=?", (str(identifier),)
        ).fetchone()
        if row is None:
            raise ValueError("Категория задачи больше не существует")
        # Unknown bootstrap state is not absence: a put will wait for its actual base.
        self._append_category_intent(
            Category(id=UUID(str(identifier)), title=row[0]),
            "create" if known and entity is None else "put",
        )
