import io
import json
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from ritm.integrations.chatgpt import (
    DIRECT,
    ISSUER,
    RESOURCE,
    Callback,
    ChatGPTError,
    ChatGPTSession,
    api_error,
    challenge,
)
from ritm.platform.credentials import CredentialVault


class Keyring:
    def __init__(self):
        self.value = None

    def set_password(self, service, account, value):
        self.value = value

    def get_password(self, service, account):
        return self.value

    def delete_password(self, service, account):
        self.value = None


class Server:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.requests = []
        self.nonce = ""
        self.scope = DIRECT
        self.failure = None
        self.sse = [
            {"type": "response.output_text.delta", "delta": '{"title":"Получено"}'},
            {"type": "response.completed"},
        ]

    def id_token(self, **claims):
        return jwt.encode(
            {
                "iss": ISSUER,
                "aud": "issued-client",
                "sub": "subject",
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
                "nonce": self.nonce,
                "email": "student@example.invalid",
                **claims,
            },
            self.key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    def json(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if self.failure:
            raise self.failure
        if url.endswith("openid-configuration"):
            return {
                "issuer": ISSUER,
                "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token",
                "jwks_uri": ISSUER + "/jwks",
            }
        if url.endswith("/jwks"):
            key = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
            return {"keys": [{**key, "kid": "test-key"}]}
        if url.endswith("/token"):
            return {
                "access_token": "test-access-secret",
                "refresh_token": "rotated-refresh-secret",
                "expires_in": 600,
                "scope": self.scope,
                "id_token": self.id_token(),
            }
        if url.endswith("/models"):
            return {
                "models": [
                    {"slug": "hidden", "visibility": "hide"},
                    {"slug": "other", "display_name": "Other", "visibility": "list"},
                    {"slug": "gpt-6.1-sol", "display_name": "GPT Sol", "visibility": "list"},
                ]
            }
        raise AssertionError(url)

    def open(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return io.BytesIO(b"".join(b"data: " + json.dumps(x).encode() + b"\n\n" for x in self.sse))


def signed_session(tmp_path, token_error=False):
    vault, server = CredentialVault(tmp_path, Keyring()), Server()
    session = ChatGPTSession(vault, server)
    if token_error:
        original = server.json

        def fail_token(url, **kwargs):
            if url.endswith("/token"):
                raise ChatGPTError("network")
            return original(url, **kwargs)

        server.json = fail_token

    def browser(url):
        query = parse_qs(urlsplit(url).query)
        assert query["client_id"] == ["dynamic_agent_client"]
        assert query["resource"] == [RESOURCE]
        assert query["code_challenge_method"] == ["S256"]
        assert DIRECT in query["scope"][0].split()
        server.nonce = query["nonce"][0]
        redirect = query["redirect_uri"][0]

        def callback():
            with urlopen(
                redirect
                + "?"
                + urlencode(
                    {
                        "state": query["state"][0],
                        "client_id": "issued-client",
                        "code": "one-time-code",
                    }
                ),
                timeout=2,
            ):
                pass

        pool.submit(callback)

    with ThreadPoolExecutor() as pool:
        session.sign_in(Event(), browser)
    return session, server


def test_oidc_pkce_secure_storage_refresh_and_logout(tmp_path):
    assert (
        challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )
    session, server = signed_session(tmp_path)
    assert session.tokens["access_token"] == "test-access-secret"
    assert session.vault.registration()["client_id"] == "issued-client"
    saved = (tmp_path / "chatgpt-registration.json").read_text()
    assert "secret" not in saved and "code" not in saved
    loaded = ChatGPTSession(session.vault, server)
    loaded.load()
    assert loaded.tokens == session.tokens
    session.list_models()
    assert session.model == "gpt-6.1-sol"
    session.tokens["expires_at"] = 0
    assert session.access_token() == "test-access-secret"
    token_request = [kwargs for url, kwargs in server.requests if url.endswith("/token")][-1]
    assert token_request["data"]["grant_type"] == "refresh_token"
    assert token_request["data"]["client_id"] == "issued-client"
    session.logout()
    assert session.vault.load() is None
    assert session.vault.registration()["client_id"] == "issued-client"


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "other"},
        {"iss": "https://evil.invalid"},
        {"nonce": "wrong"},
        {"exp": 0},
        {"sub": ""},
    ],
)
def test_bad_identity_rejected(tmp_path, claims):
    server = Server()
    session = ChatGPTSession(CredentialVault(tmp_path, Keyring()), server)
    with pytest.raises(ChatGPTError, match="проверить"):
        session.verify(server.id_token(**claims), "issued-client", "")
    assert session.vault.load() is None


def test_forged_signature_and_callback_cancel_do_not_connect(tmp_path):
    server = Server()
    other = Server()
    session = ChatGPTSession(CredentialVault(tmp_path, Keyring()), server)
    with pytest.raises(ChatGPTError):
        session.verify(other.id_token(), "issued-client", "")
    cancelled = Event()
    cancelled.set()
    with CallbackContext("state") as listener:
        with pytest.raises(ChatGPTError, match="отменён"):
            listener.wait(cancelled)


class CallbackContext(Callback):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def test_completed_stream_required_and_quota_does_not_fallback(tmp_path):
    session, server = signed_session(tmp_path)
    session.list_models()
    assert session.infer("description", "rules", Event()) == '{"title":"Получено"}'
    body = server.requests[-1][1]["data"]
    assert body["store"] is False and body["stream"] is True
    assert set(body) == {"model", "input", "instructions", "store", "stream"}
    server.sse = [{"type": "response.output_text.delta", "delta": "partial"}]
    with pytest.raises(ChatGPTError, match="не завершён"):
        session.infer("description", "rules", Event())
    server.sse.append(
        {
            "type": "response.failed",
            "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}},
        }
    )
    with pytest.raises(ChatGPTError, match="лимит"):
        session.infer("description", "rules", Event())
    assert session.tokens is not None
    assert all(url.startswith((RESOURCE, ISSUER)) for url, _ in server.requests)


def test_declined_permission_and_refresh_transient_vs_terminal(tmp_path):
    session, server = signed_session(tmp_path)
    session.tokens["scope"] = "openid"
    with pytest.raises(ChatGPTError, match="не разрешено"):
        session.list_models()
    session.tokens["scope"] = DIRECT
    session.tokens["expires_at"] = 0
    server.failure = ChatGPTError("network")
    with pytest.raises(ChatGPTError):
        session.access_token()
    assert session.tokens is not None
    server.failure = ChatGPTError("login", terminal=True)
    with pytest.raises(ChatGPTError):
        session.access_token()
    assert session.tokens is None and session.vault.load() is None


def test_memory_only_fallback_and_failed_logout_is_visible(tmp_path):
    class Broken(Keyring):
        def set_password(self, *args):
            raise RuntimeError("secret")

    vault = CredentialVault(tmp_path, Broken())
    vault.save({"access_token": "do-not-write"})
    assert vault.warning and vault.load()["access_token"] == "do-not-write"
    assert list(tmp_path.iterdir()) == []

    class Locked(Keyring):
        def get_password(self, *args):
            raise RuntimeError("secret")

    vault = CredentialVault(tmp_path, Locked())
    with pytest.raises(ChatGPTError, match="удалить"):
        vault.delete()


def test_error_mapping_never_returns_server_text():
    assert api_error(429, {"detail": "secret"}).kind == "limit"
    assert (
        api_error(403, {"error": {"code": "subscription_sharing_user_not_eligible"}}).kind
        == "unavailable"
    )
    assert api_error(400, {"error": "invalid_grant"}).terminal
    assert "secret" not in str(api_error(503, {"detail": "secret"}))


def test_wrong_callback_state_and_duplicate_fields_are_ignored():
    from urllib.error import HTTPError

    listener = Callback("expected-state")
    cancelled = Event()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(listener.wait, cancelled, 3)
        for suffix in (
            "state=wrong&code=bad",
            "state=%D1%80%D1%83&code=bad",
            "state=expected-state&code=a&code=b",
        ):
            with pytest.raises(HTTPError):
                urlopen(listener.redirect + "?" + suffix, timeout=1)
            assert listener.result is None
        with urlopen(listener.redirect + "?state=expected-state&code=good", timeout=1):
            pass
        assert future.result()["code"] == "good"


def test_transport_rejects_untrusted_endpoint_without_sending_token():
    from ritm.integrations.chatgpt import Transport

    transport = Transport()
    for url in (
        "http://api.openai.com/v1/responses",
        "https://evil.invalid/token",
        "https://auth.openai.com@evil.invalid/token",
    ):
        with pytest.raises(ChatGPTError):
            transport.open(url, token="secret")


def test_connection_gui_with_fake_account(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    environment = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ui_connection_scenario.py")), str(tmp_path)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("ReferenceError", "TypeError", "Binding loop", "Cannot assign"):
        assert warning not in result.stderr, result.stderr


def test_other_model_requires_explicit_choice(tmp_path):
    session, server = signed_session(tmp_path)
    original = server.json

    def json_without_sol(url, **kwargs):
        if url.endswith("/models"):
            return {"models": [{"slug": "another-model", "visibility": "list"}]}
        return original(url, **kwargs)

    server.json = json_without_sol
    session.list_models()
    assert session.model == ""
    with pytest.raises(ChatGPTError):
        session.infer("prompt", "rules", Event())


def test_registration_survives_failed_code_exchange(tmp_path):
    with pytest.raises(ChatGPTError, match="связи"):
        signed_session(tmp_path, token_error=True)
    record = json.loads((tmp_path / "chatgpt-registration.json").read_text())
    assert record["client_id"] == "issued-client"
    assert record["host_id"].startswith("urn:uuid:")
    assert set(record) == {"client_id", "host_id"}


def test_reconsent_uses_saved_client_and_host(tmp_path):
    session, server = signed_session(tmp_path)
    host = session.vault.registration()["host_id"]
    session.tokens["scope"] = "openid"
    with ThreadPoolExecutor() as pool:

        def browser(url):
            query = parse_qs(urlsplit(url).query)
            assert query["client_id"] == ["issued-client"]
            assert query["ext_agent_host_id"] == [host]
            assert query["prompt"] == ["consent"]
            server.nonce = query["nonce"][0]

            def callback():
                with urlopen(
                    query["redirect_uri"][0]
                    + "?"
                    + urlencode({"state": query["state"][0], "code": "reauth-code"}),
                    timeout=2,
                ):
                    pass

            pool.submit(callback)

        session.sign_in(Event(), browser)
    assert DIRECT in session.tokens["scope"].split()


def test_locked_system_collection_falls_back_without_unlock_dialog(tmp_path, monkeypatch):
    import secretstorage

    from ritm.platform.credentials import protected_backend

    calls = []

    class Bus:
        def close(self):
            calls.append("closed")

    class LockedCollection:
        def is_locked(self):
            return True

        def unlock(self):
            raise AssertionError("No system unlock prompt allowed")

    monkeypatch.setattr(secretstorage, "dbus_init", lambda: Bus())
    monkeypatch.setattr(secretstorage, "get_collection_by_alias", lambda *_: LockedCollection())
    vault = CredentialVault(tmp_path, protected_backend())
    vault.save({"access_token": "memory-only"})
    assert vault.warning and vault.memory["access_token"] == "memory-only"
    assert calls == ["closed"]
    assert list(tmp_path.iterdir()) == []


def test_connection_error_reports_safe_stage_and_network_cause():
    import socket
    from urllib.error import URLError

    from ritm.integrations.chatgpt import Transport

    transport = Transport()

    class Unresolvable:
        def open(self, *args, **kwargs):
            raise URLError(socket.gaierror(-2, "private diagnostics must not be displayed"))

    transport.opener = Unresolvable()
    with pytest.raises(ChatGPTError) as failure:
        transport.json(ISSUER + "/.well-known/openid-configuration")
    message = str(failure.value)
    assert "DNS" in message and "входа" in message
    assert "private diagnostics" not in message


@pytest.mark.parametrize(
    "reason,kind,label",
    [
        (ssl.SSLEOFError(8, "private proxy diagnostic"), "tls", "TLS"),
        (
            ssl.SSLCertVerificationError(1, "private certificate"),
            "certificate",
            "сертификат",
        ),
        (TimeoutError("private address"), "timeout", "ожидания"),
        (ConnectionRefusedError("private address"), "network", "связи"),
    ],
)
def test_transport_distinguishes_safe_network_errors(reason, kind, label):
    from urllib.error import URLError

    from ritm.integrations.chatgpt import Transport

    transport = Transport()

    class Broken:
        def open(self, *args, **kwargs):
            raise URLError(reason)

    transport.opener = Broken()
    with pytest.raises(ChatGPTError) as failure:
        transport.json(ISSUER + "/.well-known/openid-configuration")
    assert failure.value.kind == kind
    assert label in str(failure.value) and "входа" in str(failure.value)
    assert "private" not in str(failure.value)


def test_transport_http_error_retains_only_safe_status_and_stage():
    from urllib.error import HTTPError

    from ritm.integrations.chatgpt import Transport

    transport = Transport()

    class Rejected:
        def open(self, *args, **kwargs):
            raise HTTPError(
                ISSUER + "/private?code=secret",
                503,
                "private",
                {},
                io.BytesIO(b'{"detail":"secret access token"}'),
            )

    transport.opener = Rejected()
    with pytest.raises(ChatGPTError) as failure:
        transport.json(ISSUER + "/.well-known/openid-configuration")
    assert failure.value.status == 503
    assert "HTTP 503" in str(failure.value) and "входа" in str(failure.value)
    assert "secret" not in str(failure.value) and "private" not in str(failure.value)
