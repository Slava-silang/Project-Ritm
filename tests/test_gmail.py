import base64
import io
import json
from dataclasses import replace
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from ritm.integrations import gmail
from ritm.integrations.gmail import (
    GmailError,
    GmailReader,
    GmailTransport,
    MailScope,
    body_text,
    parse_mail,
)


def part(value, mime="text/plain", charset="utf-8", **extra):
    return {
        "mimeType": mime,
        "headers": [{"name": "Content-Type", "value": mime + "; charset=" + charset}],
        "body": {"data": base64.urlsafe_b64encode(value.encode(charset)).decode().rstrip("=")},
        **extra,
    }


def message(mid="a1", value="Сдать лабораторную завтра"):
    payload = part(value)
    payload["headers"] += [
        {"name": "Subject", "value": "Физика"},
        {"name": "From", "value": "Преподаватель <teacher@example.org>"},
        {"name": "Date", "value": "Mon, 16 Nov 2026 12:00:00 +0300"},
    ]
    return {"id": mid, "threadId": "thread1", "labelIds": ["UNREAD", "INBOX"], "payload": payload}


class Service:
    def __init__(self, pages=None, messages=None):
        self.pages = pages if pages is not None else [{"messages": [{"id": "a1"}]}]
        self.messages = messages if messages is not None else {"a1": message()}
        self.calls = []

    def get(self, path, params, token, cancelled):
        self.calls.append((path, params, token))
        if path == "profile":
            return {"emailAddress": "Student@example.org"}
        if path == "messages":
            return self.pages.pop(0)
        return self.messages[path[9:]]


def test_reader_pages_deduplicates_and_never_changes_unread():
    service = Service(
        [
            {"messages": [{"id": "a1"}], "nextPageToken": "page2"},
            {"messages": [{"id": "a1"}, {"id": "a2"}]},
        ],
        {"a1": message(), "a2": message("a2")},
    )
    batch = GmailReader(service).read(
        "FAKE_TOKEN", MailScope("from:teacher OR subject:physics"), Event()
    )
    assert [m.id for m in batch.messages] == ["a1", "a2"]
    assert batch.account == "student@example.org" and batch.unavailable == 0
    for path, params, _token in service.calls:
        if path == "messages":
            assert params["labelIds"] == "UNREAD"
            assert params["q"] == "from:teacher OR subject:physics"
            assert params["includeSpamTrash"] == "true"
        elif path != "profile":
            assert params == {"format": "full"}
    assert all("modify" not in c[0] and "attachments" not in c[0] for c in service.calls)


def test_empty_scope_and_optional_spam_trash():
    service = Service([{}])
    assert (
        GmailReader(service).read("fake", MailScope(include_spam_trash=False), Event()).messages
        == ()
    )
    assert service.calls[1][1] == {
        "q": "",
        "labelIds": "UNREAD",
        "includeSpamTrash": "false",
        "maxResults": 100,
    }


def test_read_deleted_and_newly_read_messages_are_reported():
    read = message("a2")
    read["labelIds"] = ["INBOX"]
    service = Service([{"messages": [{"id": "a1"}, {"id": "a2"}]}], {"a1": None, "a2": read})
    batch = GmailReader(service).read("fake", MailScope(), Event())
    assert not batch.messages and batch.unavailable == 2


def test_excluded_spam_is_rechecked_after_listing():
    data = message()
    data["labelIds"] = ["UNREAD", "SPAM"]
    service = Service(messages={"a1": data})
    batch = GmailReader(service).read("fake", MailScope(include_spam_trash=False), Event())
    assert batch.unavailable == 1 and not batch.messages


@pytest.mark.parametrize("account", ["@example.org", "student@", "wrong", "a b@example.org"])
def test_invalid_account_never_lists_mail(account):
    class BadAccount(Service):
        def get(self, *args):
            assert args[0] == "profile"
            return {"emailAddress": account}

    with pytest.raises(GmailError, match="аккаунт"):
        GmailReader(BadAccount()).read("fake", MailScope(), Event())


def test_plain_html_alternative_and_attachments_are_not_duplicated():
    plain = part("Пара в 10:00", charset="windows-1251")
    html = part(
        "<head><style>SECRET_CSS</style></head><p>Пара в 10:00 &amp; практика</p>"
        '<script>SECRET_SCRIPT</script><img src="https://tracking.invalid/x">',
        "text/html",
    )
    assert body_text(html) == "Пара в 10:00 & практика"
    alternative = {"mimeType": "multipart/alternative", "parts": [html, plain]}
    assert body_text(alternative) == "Пара в 10:00"
    mixed = {
        "mimeType": "multipart/mixed",
        "parts": [
            alternative,
            part("PRIVATE_ATTACHMENT", filename="notes.txt"),
            part(
                "PRIVATE_ATTACHMENT",
                headers=[{"name": "Content-Disposition", "value": "attachment"}],
            ),
            {"mimeType": "image/png", "body": {"attachmentId": "never-fetch"}},
        ],
    }
    assert body_text(mixed) == "Пара в 10:00"
    alternative["parts"][1] = part("")
    assert body_text(alternative) == "Пара в 10:00 & практика"


def test_external_text_body_not_replaced_with_partial_snippet():
    data = message()
    data["snippet"] = "неполный текст с возможной другой датой"
    data["payload"]["body"] = {"attachmentId": "external"}
    mail = parse_mail("student@example.org", data, "a1")
    assert mail.body == "" and not mail.body_available


def test_stable_source_identity_and_content_fingerprint():
    data = message()
    mail = parse_mail("student@example.org", data, "a1")
    data.update(historyId="new_history", labelIds=["UNREAD", "STARRED"])
    second = parse_mail("student@example.org", data, "a1")
    assert mail.source_key == second.source_key and mail.fingerprint == second.fingerprint
    assert replace(mail, account="another@example.org").source_key != mail.source_key
    assert replace(mail, body="Новый срок").fingerprint != mail.fingerprint
    assert "student" not in mail.source_key
    assert "Физика" not in repr(mail) and "teacher" not in repr(mail)
    assert parse_qs(urlsplit(mail.source_url).query) == {"authuser": ["student@example.org"]}
    assert urlsplit(mail.source_url).fragment == "all/a1"


@pytest.mark.parametrize(
    "scope",
    [
        lambda: MailScope("x" * 2001),
        lambda: MailScope("a\nb"),
        lambda: MailScope(include_spam_trash=1),
    ],
)
def test_invalid_scope(scope):
    with pytest.raises(GmailError):
        scope()


@pytest.mark.parametrize(
    "pages",
    [
        [{"messages": [{"id": "../modify"}]}],
        [{"messages": [None]}],
        [{"messages": "wrong"}],
        [{"nextPageToken": "again"}, {"nextPageToken": "again"}],
        [{"messages": [{"id": f"a{i}"} for i in range(501)]}],
    ],
)
def test_invalid_or_excessive_list_returns_no_partial_batch(pages):
    service = Service(pages)
    with pytest.raises(GmailError):
        GmailReader(service).read("fake", MailScope(), Event())
    assert all(not path.startswith("messages/") for path, *_ in service.calls)


@pytest.mark.parametrize(
    "payload",
    [
        {"mimeType": "text/plain", "body": {"data": "@@invalid"}},
        part(
            "ok", headers=[{"name": "Content-Type", "value": "text/plain; charset=unknown-charset"}]
        ),
        part("x" * 60001),
        {"mimeType": "multipart/mixed", "parts": [part("a")] * 201},
        {"mimeType": "multipart/mixed", "parts": "wrong"},
    ],
)
def test_bad_or_large_body_is_not_silently_truncated(payload):
    with pytest.raises(GmailError):
        body_text(payload)


def test_nested_mime_and_wrong_message_are_rejected():
    payload = part("text")
    for _ in range(22):
        payload = {"mimeType": "multipart/mixed", "parts": [payload]}
    with pytest.raises(GmailError, match="структура"):
        body_text(payload)
    with pytest.raises(GmailError, match="другое письмо"):
        parse_mail("student@example.org", message("other"), "a1")


def test_batch_limit_and_cancellation_do_not_return_partial_results(monkeypatch):
    monkeypatch.setattr(gmail, "MAX_BATCH", 10)
    with pytest.raises(GmailError, match="1 МиБ"):
        GmailReader(Service()).read("fake", MailScope(), Event())
    cancelled = Event()
    cancelled.set()
    service = Service()
    with pytest.raises(GmailError, match="отменено"):
        GmailReader(service).read("fake", MailScope(), cancelled)
    assert not service.calls
    cancelled.clear()

    class CancelService(Service):
        def get(self, *args):
            result = super().get(*args)
            if args[0].startswith("messages/"):
                cancelled.set()
            return result

    with pytest.raises(GmailError, match="отменено"):
        GmailReader(CancelService()).read("fake", MailScope(), cancelled)


class Opener:
    def __init__(self, data=b"{}", error=None):
        self.calls = []
        self.data = data
        self.error = error

    def open(self, request, timeout):
        self.calls.append(request)
        assert timeout == 5
        if self.error:
            raise self.error
        return io.BytesIO(self.data)


def transport(opener):
    result = GmailTransport()
    result.opener = opener
    return result


def test_transport_only_get_expected_host_and_no_body():
    opener = Opener(json.dumps(message()).encode())
    assert (
        transport(opener).get("messages/a1", {"format": "full"}, "FAKE_TOKEN", Event())["id"]
        == "a1"
    )
    request = opener.calls[0]
    assert request.get_method() == "GET" and request.data is None
    assert urlsplit(request.full_url).netloc == "gmail.googleapis.com"
    assert request.get_header("Authorization") == "Bearer FAKE_TOKEN"
    assert "FAKE_TOKEN" not in request.full_url


@pytest.mark.parametrize(
    "path,params",
    [
        ("https://evil.invalid", {}),
        ("messages/a1/modify", {}),
        ("messages/a1/attachments/a2", {}),
        ("messages/a1", {"format": "raw"}),
        ("messages", {"access_token": "secret"}),
        ("../messages", {}),
    ],
)
def test_transport_rejects_unexpected_endpoints_before_network(path, params):
    opener = Opener()
    with pytest.raises(GmailError):
        transport(opener).get(path, params, "fake", Event())
    assert not opener.calls


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_http_errors_never_expose_body_or_follow_redirect(status):
    error = HTTPError(
        "https://PRIVATE.invalid", status, "PRIVATE_REASON", {}, io.BytesIO(b"PRIVATE_BODY")
    )
    opener = Opener(error=error)
    with pytest.raises(GmailError) as caught:
        transport(opener).get("profile", {}, "PRIVATE_TOKEN", Event())
    assert "PRIVATE" not in str(caught.value)
    assert len(opener.calls) == 1
    assert gmail._NoRedirect().redirect_request(None, None, None, None, None, None) is None


def test_404_read_race_and_network_error():
    error = HTTPError("https://example.invalid", 404, "missing", {}, io.BytesIO())
    assert (
        transport(Opener(error=error)).get("messages/a1", {"format": "full"}, "fake", Event())
        is None
    )
    with pytest.raises(GmailError, match="защищённой связи"):
        transport(Opener(error=URLError("PRIVATE"))).get("profile", {}, "fake", Event())


@pytest.mark.parametrize("data", [b"bad-json", b"[]", b"x" * (gmail.MAX_RESPONSE + 1)])
def test_http_invalid_or_excessive_response(data):
    with pytest.raises(GmailError):
        transport(Opener(data)).get("profile", {}, "fake", Event())


def test_token_and_total_time_limits_before_network(monkeypatch):
    opener = Opener()
    with pytest.raises(GmailError):
        transport(opener).get("profile", {}, "bad\ntoken", Event())
    assert not opener.calls
    ticks = iter([0, 181])
    monkeypatch.setattr(gmail.time, "monotonic", lambda: next(ticks))
    service = Service()
    with pytest.raises(GmailError, match="много времени"):
        GmailReader(service).read("fake", MailScope(), Event())
    assert not service.calls
