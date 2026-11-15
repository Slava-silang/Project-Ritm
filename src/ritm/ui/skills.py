"""Фоновая библиотека и явный выбор одного навыка на текущий запуск."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

from PySide6.QtCore import Property, QObject, Signal, Slot

from ritm.domain.skills import Skill
from ritm.storage.skills import load_skills, save_skill


class SkillController(QObject):
    changed = Signal()
    selectionChanged = Signal()
    saved = Signal(str)
    _ready = Signal(object, str, str)

    def __init__(self, profile, *, locked=lambda: False):
        super().__init__()
        self.profile, self.locked = profile, locked
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ritm-skills")
        self._ready.connect(self._finish)
        self._items = ()
        self._selected = ""
        self._busy = self._closed = False
        self._error = ""
        self.load()

    @property
    def selected(self):
        return next((row for row in self._items if str(row.id) == self._selected), None)

    @Property("QVariantMap", notify=changed)
    def state(self):
        return {
            "busy": self._busy,
            "error": self._error,
            "items": [row.data() for row in self._items],
            "selectedId": self._selected,
            "selected": self.selected.data() if self.selected else {},
        }

    def _run(self, work, identifier=""):
        self._busy, self._error = True, ""
        self.changed.emit()

        def execute():
            try:
                self._ready.emit(work(), identifier, "")
            except ValueError as error:
                self._ready.emit(None, identifier, str(error))
            except OSError:
                self._ready.emit(
                    None,
                    identifier,
                    "Не удалось прочитать или сохранить навыки. Прежняя библиотека не изменена.",
                )

        self._pool.submit(execute)

    @Slot()
    def load(self):
        if not self._busy and not self._closed and not self.locked():
            self._run(lambda: load_skills(self.profile))

    @Slot(str)
    def select(self, identifier):
        if self._busy or self._closed or self.locked():
            return
        if identifier and not any(str(row.id) == identifier for row in self._items):
            self._error = "Навык не найден. Обновите список."
        elif identifier != self._selected:
            self._selected, self._error = identifier, ""
            self.selectionChanged.emit()
        self.changed.emit()

    @Slot(str, str, str, str)
    def save(self, identifier, name, description, instruction):
        if self._busy or self._closed or self.locked():
            return
        try:
            expected = next((row for row in self._items if str(row.id) == identifier), None)
            if identifier and expected is None:
                raise ValueError("Навык не найден. Обновите список.")
            skill = Skill(
                UUID(identifier) if identifier else uuid4(), name, description, instruction
            )
        except ValueError as error:
            self._error = str(error)
            self.changed.emit()
            return
        self._run(lambda: save_skill(self.profile, skill, expected=expected), str(skill.id))

    @Slot(object, str, str)
    def _finish(self, result, identifier, error):
        if self._closed:
            return
        before = self.selected
        self._busy, self._error = False, error
        if not error:
            self._items = result
            if self.selected is None:
                self._selected = ""
            if before != self.selected:
                self.selectionChanged.emit()
        self.changed.emit()
        if not error and identifier:
            self.saved.emit(identifier)

    def close(self):
        self._closed = True
        self._pool.shutdown(wait=True, cancel_futures=True)
