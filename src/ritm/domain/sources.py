"""Идентичность внешнего письма и постоянная связь, без хранения его тела."""

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit
from uuid import UUID


@dataclass(frozen=True)
class SourceRef:
    key: str
    fingerprint: str
    url: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.key, str) or not re.fullmatch(
            r"gmail:[a-f0-9]{64}:[A-Za-z0-9_-]{1,128}", self.key
        ):
            raise ValueError("Некорректный ключ источника Gmail.")
        if not isinstance(self.fingerprint, str) or not re.fullmatch(
            r"[a-f0-9]{64}", self.fingerprint
        ):
            raise ValueError("Некорректный отпечаток письма.")
        if (
            not isinstance(self.url, str)
            or len(self.url) > 2048
            or any(c.isspace() or ord(c) < 32 for c in self.url)
        ):
            raise ValueError("Некорректная ссылка на письмо.")
        try:
            url = urlsplit(self.url)
            query = parse_qs(url.query, keep_blank_values=True, max_num_fields=1)
            account = query.get("authuser", [""])
            valid = (
                url.scheme == "https"
                and url.netloc == "mail.google.com"
                and url.path == "/mail/u/"
                and set(query) == {"authuser"}
                and len(account) == 1
                and len(account[0]) <= 320
                and account[0].count("@") == 1
                and all(account[0].split("@"))
                and not any(c.isspace() or ord(c) < 32 for c in account[0])
                and url.fragment == "all/" + self.key.rsplit(":", 1)[1]
                and hashlib.sha256(account[0].casefold().encode()).hexdigest()
                == self.key.split(":")[1]
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Некорректная ссылка на письмо.")

    @classmethod
    def from_mail(cls, mail):
        return cls(mail.source_key, mail.fingerprint, mail.source_url)


@dataclass(frozen=True)
class SourceLink:
    source: SourceRef
    kind: str
    target_id: UUID
    revision: int

    def __post_init__(self):
        if not isinstance(self.source, SourceRef) or self.kind not in ("task", "event"):
            raise ValueError("Некорректная связь источника.")
        if (
            not isinstance(self.target_id, UUID)
            or type(self.revision) is not int
            or self.revision < 1
        ):
            raise ValueError("Некорректная версия связи источника.")
