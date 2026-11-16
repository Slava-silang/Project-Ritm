"""Конфигурация и токены Gmail только в защищённом keyring либо памяти."""

import hashlib
import json
import os

from ritm.integrations.gmail import GmailError
from ritm.platform.credentials import protected_backend


class GmailVault:
    def __init__(self, profile, backend=None):
        self.account = hashlib.sha256(os.fsencode(profile.resolve())).hexdigest()
        self.service = "ProjectRitm.Gmail"
        self.memory, self.warning = None, ""
        self.backend = backend
        if backend is None:
            try:
                self.backend = protected_backend()
            except Exception:
                self.backend = None
                self.warning = "Защищённое хранилище недоступно. Подключение Gmail только в памяти."

    def save(self, data):
        if self.backend is not None:
            try:
                self.backend.set_password(self.service, self.account, json.dumps(data))
                self.warning = ""
                self.memory = data
                return
            except Exception:
                self.memory = data
                self.warning = (
                    "Защищённое хранилище недоступно. Изменения входа только в памяти; "
                    "ранее сохранённый вход не изменён. После закрытия войдите снова."
                )
                return
        self.memory = data
        self.warning = (
            "Подключение Gmail только в памяти. После закрытия "
            "загрузите конфигурацию и войдите снова."
        )

    def load(self):
        if self.memory is not None:
            return self.memory
        try:
            raw = self.backend.get_password(self.service, self.account) if self.backend else None
            if raw is None:
                return None
            if len(raw) > 65536:
                raise ValueError
            return json.loads(raw)
        except Exception:
            raise GmailError(
                "Не удалось открыть сохранённое подключение Gmail. Проверьте защищённое хранилище."
            ) from None

    def delete(self):
        if self.backend is not None:
            try:
                if self.backend.get_password(self.service, self.account) is not None:
                    self.backend.delete_password(self.service, self.account)
            except Exception:
                raise GmailError(
                    "Не удалось удалить сохранённый вход Gmail. Проверьте защищённое хранилище."
                ) from None
        self.memory = None
        self.warning = ""
