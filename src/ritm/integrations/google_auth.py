"""OAuth Desktop/PKCE для одного источника Gmail; без OIDC/пароля/платного API."""

import base64
import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener

from ritm.integrations.gmail import (
    READONLY_SCOPE,
    GmailError,
    GmailReader,
    GmailTransport,
    _NoRedirect,
    check_cancelled,
)

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"


class LoginRequired(GmailError):
    pass


def secret(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 16384
        or any(not 33 <= ord(c) <= 126 for c in value)
    ):
        raise GmailError("Некорректные данные входа Google. Выполните вход заново.")
    return value


def client_config(data):
    if not isinstance(data, dict) or set(data) != {"client_id", "client_secret"}:
        raise GmailError("Выберите конфигурацию Google для настольного приложения.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", secret(data["client_id"])):
        raise GmailError("Некорректный клиент Google для настольного приложения.")
    return {"client_id": data["client_id"], "client_secret": secret(data["client_secret"])}


def read_client(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise GmailError("Выберите обычный JSON-файл конфигурации Google.")
        content = stream.read(65537)
    try:
        if len(content) > 65536:
            raise ValueError
        data = json.loads(content)
        installed = data["installed"]
        if "web" in data or not isinstance(installed, dict):
            raise ValueError
        if installed.get("auth_uri", AUTH) not in (
            AUTH,
            "https://accounts.google.com/o/oauth2/auth",
        ) or installed.get("token_uri", TOKEN) not in (
            TOKEN,
            "https://accounts.google.com/o/oauth2/token",
        ):
            raise ValueError
        return client_config({key: installed[key] for key in ("client_id", "client_secret")})
    except (ValueError, KeyError, TypeError, RecursionError):
        raise GmailError(
            "Выберите JSON клиента Google типа Desktop app, скачанный из Google Cloud."
        ) from None


class GoogleCallback:
    def __init__(self, state):
        self.result = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(1)

            def log_message(self, *_args):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                valid = (
                    len(self.path) < 8192
                    and parsed.path == "/callback"
                    and self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
                    and all(len(v) == 1 for v in query.values())
                    and secrets.compare_digest(query.get("state", [""])[0].encode(), state.encode())
                    and (bool(query.get("code", [""])[0]) != bool(query.get("error", [""])[0]))
                )
                if valid and owner.result is None:
                    owner.result = {k: v[0] for k, v in query.items()}
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write("Вернитесь в Ритм. Это окно можно закрыть.".encode())
                except OSError:
                    pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.timeout = 0.2
        self.redirect = f"http://127.0.0.1:{self.server.server_port}/callback"

    def wait(self, cancelled):
        end = time.monotonic() + 180
        while self.result is None:
            check_cancelled(cancelled)
            if time.monotonic() >= end:
                raise GmailError("Время входа Google истекло. Повторите вход.")
            self.server.handle_request()
        check_cancelled(cancelled)
        return self.result

    def close(self):
        self.server.server_close()


class GoogleTokenTransport:
    def __init__(self):
        self.opener = build_opener(_NoRedirect())

    def exchange(self, data, cancelled):
        check_cancelled(cancelled)
        request = Request(
            TOKEN,
            data=urlencode(data).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with self.opener.open(request, timeout=5) as response:
                chunks, size = [], 0
                end = time.monotonic() + 15
                while True:
                    check_cancelled(cancelled)
                    if time.monotonic() >= end:
                        raise GmailError("Google отвечает слишком долго. Повторите вход.")
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > 65536:
                        raise GmailError("Некорректный ответ входа Google.")
        except HTTPError as error:
            # Тело ошибки не читается и не выводится, invalid_grant не отличаем от отзыва.
            status = error.code
            error.close()
            if status in (400, 401):
                raise LoginRequired(
                    "Вход Google недействителен. Проверьте конфигурацию и войдите снова."
                ) from None
            raise GmailError("Google не завершил вход. Повторите попытку позже.") from None
        except OSError:
            raise GmailError("Нет защищённой связи с Google. Проверьте сеть.") from None
        check_cancelled(cancelled)
        try:
            result = json.loads(b"".join(chunks))
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, RecursionError):
            raise GmailError("Некорректный ответ входа Google.") from None


def token_result(data, *, previous=None):
    if not isinstance(data, dict):
        raise GmailError("Некорректный ответ входа Google.")
    expires = data.get("expires_in")
    scope = data.get("scope", previous["scope"] if previous else "")
    if (
        type(expires) is not int
        or not 1 <= expires <= 86400
        or not isinstance(scope, str)
        or set(scope.split()) != {READONLY_SCOPE}
        or str(data.get("token_type", "")).lower() != "bearer"
    ):
        raise GmailError("Google не предоставил разрешение только на чтение Gmail. Повторите вход.")
    return {
        "access_token": secret(data.get("access_token")),
        "refresh_token": secret(
            data.get("refresh_token", previous["refresh_token"] if previous else None)
        ),
        "scope": READONLY_SCOPE,
        "expires_at": time.time() + expires,
        "email": previous["email"] if previous else "",
    }


class GmailSession:
    def __init__(self, vault, *, http=None, api=None, callback=None):
        self.vault = vault
        self.http = http if http is not None else GoogleTokenTransport()
        self.api = api if api is not None else GmailTransport()
        self.callback = callback or GoogleCallback
        self.config, self.tokens = None, None

    def _save(self, config, tokens, cancelled):
        check_cancelled(cancelled)
        self.vault.save({"config": config, "tokens": tokens})
        self.config, self.tokens = config, tokens

    def configure(self, path, cancelled):
        if self.tokens is not None:
            raise GmailError("Перед сменой конфигурации удалите прежнее подключение Gmail.")
        config = read_client(path)
        self._save(config, None, cancelled)

    def restore(self, cancelled):
        data = self.vault.load()
        check_cancelled(cancelled)
        if data is None:
            raise GmailError(
                "Сохранённого подключения Gmail нет. Загрузите конфигурацию и войдите."
            )
        try:
            if not isinstance(data, dict) or set(data) != {"config", "tokens"}:
                raise ValueError
            config = client_config(data["config"])
            tokens = data["tokens"]
            if tokens is not None:
                if not isinstance(tokens, dict) or set(tokens) != {
                    "access_token",
                    "refresh_token",
                    "scope",
                    "expires_at",
                    "email",
                }:
                    raise ValueError
                secret(tokens["access_token"])
                secret(tokens["refresh_token"])
                if tokens["scope"] != READONLY_SCOPE or not isinstance(tokens["email"], str):
                    raise ValueError
                expiry = tokens["expires_at"]
                if type(expiry) not in (int, float) or not math.isfinite(expiry):
                    raise ValueError
            self.config, self.tokens = config, tokens
        except (ValueError, KeyError, TypeError):
            raise GmailError(
                "Сохранённое подключение Gmail повреждено. Удалите его и войдите снова."
            ) from None

    def sign_in(self, cancelled, open_browser):
        if self.config is None:
            raise GmailError("Сначала загрузите JSON конфигурации Google Desktop app.")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        listener = self.callback(state)
        try:
            check_cancelled(cancelled)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            open_browser(
                AUTH
                + "?"
                + urlencode(
                    {
                        "client_id": self.config["client_id"],
                        "redirect_uri": listener.redirect,
                        "response_type": "code",
                        "scope": READONLY_SCOPE,
                        "state": state,
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                        "prompt": "consent",
                        "access_type": "offline",
                    }
                )
            )
            result = listener.wait(cancelled)
            check_cancelled(cancelled)
            if result.get("error") or not result.get("code"):
                raise GmailError("Вход Google не разрешён. Можно повторить вход.")
            data = self.http.exchange(
                {
                    **self.config,
                    "code": secret(result["code"]),
                    "code_verifier": verifier,
                    "redirect_uri": listener.redirect,
                    "grant_type": "authorization_code",
                },
                cancelled,
            )
            tokens = token_result(data)
            check_cancelled(cancelled)
            profile = self.api.get("profile", {}, tokens["access_token"], cancelled)
            email = profile.get("emailAddress", "") if isinstance(profile, dict) else ""
            if (
                not isinstance(email, str)
                or len(email) > 320
                or email.count("@") != 1
                or not all(email.split("@"))
                or any(c.isspace() or ord(c) < 32 for c in email)
            ):
                raise GmailError("Не удалось определить аккаунт Gmail.")
            tokens["email"] = email.casefold()
            self._save(self.config, tokens, cancelled)
        finally:
            listener.close()

    def access_token(self, cancelled):
        check_cancelled(cancelled)
        if self.tokens is None:
            raise LoginRequired("Сначала войдите в Gmail.")
        if self.tokens["expires_at"] <= time.time() + 30:
            try:
                result = self.http.exchange(
                    {
                        **self.config,
                        "refresh_token": self.tokens["refresh_token"],
                        "grant_type": "refresh_token",
                    },
                    cancelled,
                )
            except LoginRequired:
                self._save(self.config, None, cancelled)
                raise
            self._save(self.config, token_result(result, previous=self.tokens), cancelled)
        return self.tokens["access_token"]

    def read(self, scope, cancelled):
        result = GmailReader(self.api).read(self.access_token(cancelled), scope, cancelled)
        if result.account != self.tokens["email"]:
            raise GmailError("Аккаунт Gmail изменился. Выполните вход снова.")
        return result

    def logout(self):
        self.vault.delete()
        self.config, self.tokens = None, None
