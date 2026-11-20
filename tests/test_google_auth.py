import base64
import hashlib
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

import pytest

from ritm.integrations.gmail import READONLY_SCOPE, GmailError, MailScope
from ritm.integrations.google_auth import (
    TOKEN,
    GmailSession,
    GoogleCallback,
    GoogleTokenTransport,
    LoginRequired,
    read_client,
    token_result,
)
from ritm.platform.gmail_credentials import GmailVault

CONFIG = {"client_id": "synthetic-123.apps.googleusercontent.com", "client_secret": "FAKE_SECRET"}


class Backend:
    def __init__(self):
        self.values = {}

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        del self.values[service, account]


class Callback:
    def __init__(self, state):
        self.state, self.closed = state, False
        self.redirect = "http://127.0.0.1:12345/callback"

    def wait(self, cancelled):
        return {"code": "FAKE_CODE", "state": self.state}

    def close(self):
        self.closed = True


class TokenHTTP:
    def __init__(self):
        self.calls = []

    def exchange(self, data, cancelled):
        self.calls.append(data)
        result = {
            "access_token": "FAKE_ACCESS",
            "expires_in": 3600,
            "scope": READONLY_SCOPE,
            "token_type": "Bearer",
        }
        if data["grant_type"] == "authorization_code":
            result["refresh_token"] = "FAKE_REFRESH"
        return result


class API:
    def __init__(self):
        self.calls = []

    def get(self, path, params, token, cancelled):
        self.calls.append((path, params))
        assert token == "FAKE_ACCESS"
        return {"emailAddress": "student@example.org"} if path == "profile" else {}


def session(profile, backend=None):
    return GmailSession(
        GmailVault(profile, backend or Backend()), http=TokenHTTP(), api=API(), callback=Callback
    )


def config_file(profile):
    path = profile / "synthetic-client.json"
    path.write_text(
        json.dumps(
            {
                "installed": {
                    **CONFIG,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": TOKEN,
                    "redirect_uris": ["http://localhost"],
                }
            }
        )
    )
    return path


def test_pkce_login_refresh_restore_logout_without_automatic_mail(tmp_path):
    backend = Backend()
    current = session(tmp_path, backend)
    assert not current.api.calls and not current.http.calls
    current.configure(config_file(tmp_path), Event())
    urls = []
    current.sign_in(Event(), urls.append)
    query = parse_qs(urlsplit(urls[0]).query)
    assert query["scope"] == [READONLY_SCOPE] and query["code_challenge_method"] == ["S256"]
    assert query["prompt"] == ["consent"] and query["access_type"] == ["offline"]
    verifier = current.http.calls[0]["code_verifier"]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert query["code_challenge"] == [expected]
    assert "FAKE_SECRET" not in urls[0] and "FAKE_CODE" not in urls[0]
    assert current.api.calls == [("profile", {})]
    assert current.tokens["email"] == "student@example.org"
    restored = session(tmp_path, backend)
    restored.restore(Event())
    assert restored.tokens == current.tokens
    assert not restored.api.calls and not restored.http.calls
    restored.tokens["expires_at"] = 0
    result = restored.read(MailScope(), Event())
    assert not result.messages
    assert restored.http.calls[0]["grant_type"] == "refresh_token"
    assert restored.tokens["refresh_token"] == "FAKE_REFRESH"
    assert not [p for p in tmp_path.iterdir() if p.name != "synthetic-client.json"]
    restored.logout()
    assert not backend.values and restored.tokens is None and restored.config is None


def test_cancelled_or_denied_login_never_saves_tokens(tmp_path):
    current = session(tmp_path)
    current.configure(config_file(tmp_path), Event())
    cancelled = Event()
    with pytest.raises(GmailError, match="отменено"):
        current.sign_in(cancelled, lambda _url: cancelled.set())
    assert current.tokens is None and current.vault.load()["tokens"] is None
    assert not current.api.calls and not current.http.calls

    class Denied(Callback):
        def wait(self, cancelled):
            return {"error": "access_denied"}

    current.callback = Denied
    with pytest.raises(GmailError, match="не разрешён"):
        current.sign_in(Event(), lambda _: None)
    assert current.tokens is None


def test_expired_refresh_requires_login_and_clears_saved_token(tmp_path):
    current = session(tmp_path)
    current.configure(config_file(tmp_path), Event())
    current.sign_in(Event(), lambda _: None)
    current.tokens["expires_at"] = 0

    class Expired(TokenHTTP):
        def exchange(self, data, cancelled):
            raise LoginRequired("Вход недействителен")

    current.http = Expired()
    with pytest.raises(LoginRequired):
        current.access_token(Event())
    assert current.tokens is None and current.vault.load()["tokens"] is None


@pytest.mark.parametrize(
    "data",
    [
        {"web": CONFIG},
        {"installed": {**CONFIG, "token_uri": "https://evil.invalid"}},
        {"installed": {**CONFIG, "client_id": "wrong"}},
        [],
    ],
)
def test_wrong_client_or_foreign_endpoint_rejected(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(GmailError):
        read_client(path)


@pytest.mark.parametrize(
    "patch",
    [
        {"scope": ""},
        {"scope": READONLY_SCOPE + " other"},
        {"expires_in": True},
        {"expires_in": -1},
        {"access_token": "bad\nheader"},
        {"token_type": "unknown"},
    ],
)
def test_token_permissions_and_types_validated(patch):
    data = TokenHTTP().exchange({"grant_type": "authorization_code"}, Event())
    with pytest.raises(GmailError):
        token_result({**data, **patch})


def test_vault_separate_service_profiles_memory_and_failed_delete(tmp_path):
    backend = Backend()
    vault = GmailVault(tmp_path, backend)
    vault.save({"config": CONFIG, "tokens": None})
    assert next(iter(backend.values))[0] == "ProjectRitm.Gmail"
    assert GmailVault(tmp_path / "other", backend).load() is None

    class Failing(Backend):
        def set_password(self, *args):
            raise RuntimeError("PRIVATE")

        def delete_password(self, *args):
            raise RuntimeError("PRIVATE")

    broken = Failing()
    memory = GmailVault(tmp_path, broken)
    memory.save({"config": CONFIG, "tokens": None})
    assert memory.warning and memory.load()["config"] == CONFIG
    broken.values[memory.service, memory.account] = "old entry"
    memory.save({"new": True})
    assert memory.load() == {"new": True} and "не изменён" in memory.warning
    with pytest.raises(GmailError, match="удалить"):
        memory.delete()
    assert memory.memory is not None


def test_real_loopback_rejects_state_host_duplicates_then_accepts():
    callback = GoogleCallback("EXPECTED_STATE")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(callback.wait, Event())
        try:
            for suffix in (
                "?code=SECRET&state=wrong",
                "?code=SECRET&state=EXPECTED_STATE&state=again",
            ):
                with pytest.raises(HTTPError) as error:
                    urlopen(callback.redirect + suffix, timeout=3)
                assert error.value.code == 400
                error.value.close()
            wrong_host = Request(
                callback.redirect + "?code=FAKE&state=EXPECTED_STATE",
                headers={"Host": "evil.invalid"},
            )
            with pytest.raises(HTTPError) as error:
                urlopen(wrong_host, timeout=3)
            assert error.value.code == 400
            error.value.close()
            with urlopen(
                callback.redirect
                + "?"
                + urlencode({"code": "FAKE_CODE", "state": "EXPECTED_STATE"}),
                timeout=3,
            ) as response:
                assert b"FAKE_CODE" not in response.read()
                assert response.headers["Cache-Control"] == "no-store"
            assert future.result(timeout=3)["code"] == "FAKE_CODE"
        finally:
            callback.close()


def test_token_http_only_fixed_post_and_safe_errors():
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == TOKEN and request.get_method() == "POST"
            assert parse_qs(request.data.decode())["client_secret"] == ["FAKE_SECRET"]
            return io.BytesIO(b'{"access_token":"FAKE"}')

    transport = GoogleTokenTransport()
    transport.opener = Opener()
    assert transport.exchange(CONFIG, Event())["access_token"] == "FAKE"

    class BadOpener:
        def open(self, request, timeout):
            raise HTTPError(TOKEN, 400, "PRIVATE_REASON", {}, io.BytesIO(b"PRIVATE_BODY"))

    transport.opener = BadOpener()
    with pytest.raises(LoginRequired) as caught:
        transport.exchange(CONFIG, Event())
    assert "PRIVATE" not in str(caught.value)


def test_gmail_source_gui(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{key}_HOME": str(tmp_path / key.lower()) for key in ("DATA", "CACHE", "CONFIG")},
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ui_gmail_scenario.py")), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
        assert warning not in result.stderr, result.stderr
