"""Обычные хранилища не читают и не изменяют пару во время восстановления."""

import sqlite3
from pathlib import Path

PENDING_RESTORE = ".ritm-restore.json"


def ensure_profile_available(profile):
    marker = profile / PENDING_RESTORE
    if marker.exists() or marker.is_symlink():
        raise ValueError("Восстановление профиля не завершено. Перезапустите приложение.")


class GuardedConnection(sqlite3.Connection):
    def __init__(self, database, *args, **kwargs):
        self.profile = None if str(database) == ":memory:" else Path(database).absolute().parent
        self._check_restore()
        super().__init__(database, *args, **kwargs)

    def _check_restore(self):
        if self.profile is not None:
            ensure_profile_available(self.profile)

    def execute(self, sql, parameters=(), /):
        self._check_restore()
        result = super().execute(sql, parameters)
        try:
            self._check_restore()
        except ValueError:
            self.rollback()
            raise
        return result

    def executemany(self, sql, parameters, /):
        self._check_restore()
        result = super().executemany(sql, parameters)
        try:
            self._check_restore()
        except ValueError:
            self.rollback()
            raise
        return result


def connect_profile(path):
    return sqlite3.connect(path, factory=GuardedConnection)
