"""Токены только в Secret Service либо памяти; регистрация не содержит секретов."""

import hashlib
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from ritm.integrations.chatgpt import ChatGPTError


def protected_backend():
    import secretstorage
    from keyring.backends.SecretService import Keyring
    from keyring.errors import KeyringLocked

    class ExistingCollectionKeyring(Keyring):
        def get_preferred_collection(self):
            bus = secretstorage.dbus_init()
            try:
                collection = secretstorage.get_collection_by_alias(bus, "default")
                if collection.is_locked():
                    raise KeyringLocked("Locked collection")
                return collection
            except Exception:
                bus.close()
                raise

        def unlock(self, item):
            if item.is_locked():
                raise KeyringLocked("Locked item")

    return ExistingCollectionKeyring()


class CredentialVault:
    def __init__(self, profile: Path, backend=None):
        self.profile = profile
        self.account = hashlib.sha256(os.fsencode(profile.resolve())).hexdigest()
        self.service = "ProjectRitm.ChatGPT"
        self.memory = None
        self.warning = ""
        self.backend = backend
        if backend is None:
            try:
                # Нельзя выбирать сторонний/plaintext backend из пользовательской конфигурации.
                # Не создавать/разблокировать коллекцию с бесконечным системным диалогом.
                self.backend = protected_backend()
            except ImportError:
                self.warning = (
                    "Защищённое хранилище недоступно. Вход сохранится только до закрытия программы."
                )

    def registration(self):
        path = self.profile / "chatgpt-registration.json"
        if path.exists():
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or not str(data.get("host_id", "")).startswith(
                "urn:uuid:"
            ):
                raise ChatGPTError("identity")
            UUID(data["host_id"][9:])
            return data
        data = {"host_id": "urn:uuid:" + str(uuid4())}
        self.save_registration(data)
        return data

    def save_registration(self, data):
        with NamedTemporaryFile(dir=self.profile, delete=False) as stream:
            path = Path(stream.name)
            try:
                stream.write(json.dumps(data).encode())
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(path, self.profile / "chatgpt-registration.json")
            finally:
                path.unlink(missing_ok=True)

    def save(self, data):
        self.memory = data
        try:
            if self.backend is None:
                raise RuntimeError
            self.backend.set_password(self.service, self.account, json.dumps(data))
            self.warning = ""
        except Exception:
            self.warning = (
                "Не удалось защитить сохранённый вход. Он действует только до закрытия программы."
            )

    def load(self):
        if self.memory is not None:
            return self.memory
        try:
            raw = self.backend.get_password(self.service, self.account) if self.backend else None
        except Exception:
            self.warning = (
                "Не удалось открыть защищённое хранилище. "
                "Войдите заново; вход будет только в памяти."
            )
            return None
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            for field in ("client_id", "access_token", "scope", "sub", "email"):
                if not isinstance(data[field], str):
                    raise ValueError
            if not math.isfinite(data["expires_at"]):
                raise ValueError
            self.memory = data
            return data
        except (KeyError, ValueError, TypeError):
            raise ChatGPTError("identity") from None

    def delete(self):
        self.memory = None
        if self.backend is None:
            return
        try:
            if self.backend.get_password(self.service, self.account) is not None:
                self.backend.delete_password(self.service, self.account)
        except Exception:
            raise ChatGPTError("storage") from None
