"""Ограниченное чтение Gmail. Вызывать в worker; вход и запись не входят в этот модуль."""

import base64
import binascii
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from email.message import Message
from html.parser import HTMLParser
from threading import Event
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
BASE = "https://gmail.googleapis.com/gmail/v1/users/me/"
MAX_RESPONSE = 4 * 1024 * 1024
MAX_MESSAGES = 500
MAX_TEXT = 60000
MAX_BATCH = 1024 * 1024
ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class GmailError(ValueError):
    """Только безопасные сообщения: без токенов, запросов, адресов и HTTP body."""


def check_cancelled(cancelled):
    if cancelled.is_set():
        raise GmailError("Чтение почты отменено.")


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise GmailError("Gmail вернул некорректный идентификатор письма.")
    return value


def text(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise GmailError("Gmail вернул некорректный или слишком большой текст.")
    return value


@dataclass(frozen=True)
class MailScope:
    # UNREAD — отдельный обязательный label, OR в пользовательском q его не отменяет.
    query: str = ""
    include_spam_trash: bool = True

    def __post_init__(self):
        text(self.query, 2000)
        if any(ord(c) < 32 for c in self.query) or type(self.include_spam_trash) is not bool:
            raise GmailError("Некорректный фильтр почты.")


@dataclass(frozen=True)
class Mail:
    account: str = field(repr=False)
    id: str
    thread_id: str
    subject: str = field(repr=False)
    sender: str = field(repr=False)
    date: str = field(repr=False)
    body: str = field(repr=False)
    body_available: bool

    @property
    def source_key(self):
        # ID не глобален между аккаунтами. Адрес не нужен в ключе/диагностике.
        return (
            "gmail:" + hashlib.sha256(self.account.casefold().encode()).hexdigest() + ":" + self.id
        )

    @property
    def fingerprint(self):
        # historyId/UNREAD меняются при чтении/метках без изменения содержания.
        content = (self.subject, self.sender, self.date, self.body, self.body_available)
        return hashlib.sha256(json.dumps(content, ensure_ascii=True).encode()).hexdigest()

    @property
    def source_url(self):
        return (
            "https://mail.google.com/mail/u/?"
            + urlencode({"authuser": self.account})
            + "#all/"
            + self.id
        )


@dataclass(frozen=True)
class MailBatch:
    account: str = field(repr=False)
    messages: tuple[Mail, ...]
    unavailable: int = 0  # удалены или уже прочитаны между list/get


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class GmailTransport:
    """Только GET к трём разрешённым путям. Никакого универсального HTTP-инструмента."""

    def __init__(self):
        self.opener = build_opener(_NoRedirect())

    def get(self, path, params, token, cancelled):
        check_cancelled(cancelled)
        valid = (
            (path == "profile" and not params)
            or (
                path == "messages"
                and set(params) <= {"q", "labelIds", "maxResults", "pageToken", "includeSpamTrash"}
            )
            or (
                path.startswith("messages/")
                and ID.fullmatch(path[9:])
                and params == {"format": "full"}
            )
        )
        if not valid:
            raise GmailError("Недопустимая операция чтения Gmail.")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 16384
            or any(not 33 <= ord(c) <= 126 for c in token)
        ):
            raise GmailError("Нужно войти в аккаунт Gmail.")
        url = BASE + path + ("?" + urlencode(params) if params else "")
        request = Request(
            url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}
        )
        try:
            with self.opener.open(request, timeout=5) as response:
                chunks = []
                size = 0
                end = time.monotonic() + 15
                while True:
                    check_cancelled(cancelled)
                    if time.monotonic() >= end:
                        raise GmailError("Gmail отвечает слишком долго. Повторите чтение позже.")
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise GmailError("Ответ Gmail слишком большой. Сузьте охват писем.")
                    chunks.append(chunk)
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 404 and path.startswith("messages/"):
                return None
            message = {
                401: "Вход Gmail истёк. Выполните вход снова.",
                403: "Gmail не разрешил чтение. Проверьте доступ в настройках приложения Google.",
                429: "Достигнут лимит Gmail. Повторите чтение позже.",
            }.get(status, "Не удалось прочитать Gmail. Повторите попытку позже.")
            raise GmailError(message) from None
        except OSError:
            raise GmailError(
                "Нет защищённой связи с Gmail. Проверьте сеть и повторите попытку."
            ) from None
        check_cancelled(cancelled)
        try:
            result = json.loads(b"".join(chunks))
        except (ValueError, RecursionError):
            raise GmailError("Gmail вернул некорректный ответ.") from None
        if not isinstance(result, dict):
            raise GmailError("Gmail вернул некорректный ответ.")
        return result


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.hidden += 1
        if not self.hidden and tag in ("p", "div", "br", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def headers(part):
    values = part.get("headers", [])
    if not isinstance(values, list) or len(values) > 200:
        raise GmailError("Некорректные заголовки письма Gmail.")
    result = {}
    for item in values:
        if not isinstance(item, dict):
            raise GmailError("Некорректные заголовки письма Gmail.")
        name = text(item.get("name"), 200).lower()
        value = text(item.get("value"), 16000)
        result.setdefault(name, value)
    return result


def body_text(payload):
    visited = 0

    def walk(part, depth=0):
        nonlocal visited
        visited += 1
        if depth > 20 or visited > 200 or not isinstance(part, dict):
            raise GmailError("Слишком сложная структура письма Gmail.")
        meta = headers(part)
        if part.get("filename") or meta.get("content-disposition", "").lower().startswith(
            "attachment"
        ):
            return ""
        mime = text(part.get("mimeType", ""), 200).lower()
        if mime.startswith("multipart/"):
            parts = part.get("parts", [])
            if not isinstance(parts, list) or len(parts) > 200:
                raise GmailError("Слишком сложная структура письма Gmail.")
            if mime == "multipart/alternative":
                # Одна копия содержания, plain предпочтительнее HTML.
                parts = sorted(
                    parts,
                    key=lambda p: not isinstance(p, dict) or p.get("mimeType") != "text/plain",
                )
                for child in parts:
                    value = walk(child, depth + 1)
                    if value.strip():
                        return value
                return ""
            values = [walk(child, depth + 1) for child in parts]
            return "\n".join(v for v in values if v)
        if mime not in ("text/plain", "text/html"):
            return ""
        body = part.get("body", {})
        if not isinstance(body, dict):
            raise GmailError("Некорректное содержимое письма Gmail.")
        data = text(body.get("data", ""), MAX_RESPONSE)
        if not data:
            return ""  # attachmentId не загружается автоматически, snippet не заменяет тело.
        try:
            raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
            content_type = Message()
            content_type["content-type"] = meta.get("content-type", mime)
            decoded = raw.decode(content_type.get_content_charset() or "utf-8")
        except (ValueError, LookupError, UnicodeError, binascii.Error):
            raise GmailError(
                "Не удалось прочитать кодировку или содержимое письма Gmail."
            ) from None
        if mime == "text/html":
            parser = _HTMLText()
            parser.feed(decoded)
            parser.close()
            decoded = "".join(parser.parts)
        return decoded

    value = walk(payload).strip()
    if len(value) > MAX_TEXT:
        raise GmailError("Письмо длиннее 60 000 символов. Сузьте охват писем.")
    return value


def parse_mail(account, data, expected_id):
    if not isinstance(data, dict) or data.get("id") != expected_id:
        raise GmailError("Gmail вернул другое письмо вместо запрошенного.")
    identifier(expected_id)
    thread = identifier(data.get("threadId"))
    payload = data.get("payload")
    if not isinstance(payload, dict):
        raise GmailError("Некорректное содержимое письма Gmail.")
    meta = headers(payload)
    body = body_text(payload)
    return Mail(
        account,
        expected_id,
        thread,
        meta.get("subject", ""),
        meta.get("from", ""),
        meta.get("date", ""),
        body,
        bool(body),
    )


class GmailReader:
    def __init__(self, transport=None):
        self.transport = transport if transport is not None else GmailTransport()

    def read(self, token: str, scope: MailScope, cancelled: Event) -> MailBatch:
        """Полный ограниченный снимок, без частичного успеха при ошибке/переполнении."""
        if not isinstance(scope, MailScope):
            raise GmailError("Некорректный фильтр почты.")
        end = time.monotonic() + 180

        def get(path, params):
            check_cancelled(cancelled)
            if time.monotonic() >= end:
                raise GmailError("Чтение Gmail заняло слишком много времени. Сузьте охват писем.")
            value = self.transport.get(path, params, token, cancelled)
            check_cancelled(cancelled)
            return value

        profile = get("profile", {})
        if not isinstance(profile, dict):
            raise GmailError("Не удалось определить аккаунт Gmail.")
        account = text(profile.get("emailAddress"), 320).strip().casefold()
        if (
            account.count("@") != 1
            or not all(account.split("@"))
            or any(c.isspace() or ord(c) < 32 for c in account)
        ):
            raise GmailError("Не удалось определить аккаунт Gmail.")
        ids = []
        seen = set()
        pages = set()
        page = ""
        while True:
            params = {
                "q": scope.query,
                "labelIds": "UNREAD",
                "maxResults": 100,
                "includeSpamTrash": str(scope.include_spam_trash).lower(),
            }
            if page:
                params["pageToken"] = page
            result = get("messages", params)
            if not isinstance(result, dict) or not isinstance(result.get("messages", []), list):
                raise GmailError("Некорректный список писем Gmail.")
            for item in result.get("messages", []):
                if not isinstance(item, dict):
                    raise GmailError("Некорректный список писем Gmail.")
                mid = identifier(item.get("id"))
                if mid not in seen:
                    ids.append(mid)
                    seen.add(mid)
                if len(ids) > MAX_MESSAGES:
                    raise GmailError(
                        "Найдено больше 500 писем. Сузьте охват, например по дате или отправителю."
                    )
            page = text(result.get("nextPageToken", ""), 4096)
            if not page:
                break
            if page in pages or len(pages) >= 100:
                raise GmailError("Не удалось получить полный список писем Gmail. Повторите чтение.")
            pages.add(page)
        messages = []
        unavailable = 0
        size = 0
        for mid in ids:
            data = get("messages/" + mid, {"format": "full"})
            if data is None:
                unavailable += 1
                continue
            if not isinstance(data, dict) or not isinstance(data.get("labelIds"), list):
                raise GmailError("Некорректный ответ Gmail.")
            labels = data["labelIds"]
            if "UNREAD" not in labels or (
                not scope.include_spam_trash and ("SPAM" in labels or "TRASH" in labels)
            ):
                unavailable += 1
                continue
            mail = parse_mail(account, data, mid)
            size += sum(len(s.encode()) for s in (mail.subject, mail.sender, mail.date, mail.body))
            if size > MAX_BATCH:
                raise GmailError("Содержание писем больше 1 МиБ. Сузьте охват писем.")
            messages.append(mail)
        return MailBatch(account, tuple(messages), unavailable)
