import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Timer

import pytest

from ritm.integrations.ollama import MAX_RESPONSE, OllamaError, OllamaTransport


@contextmanager
def server(mode="normal"):
    calls, release, entered = [], Event(), Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            calls.append((self.path, dict(self.headers)))
            if mode == "headers":
                entered.set()
                release.wait(5)
                return
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "https://example.invalid")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if mode == "body":
                self.wfile.write(b'{"models":')
                self.wfile.flush()
                entered.set()
                release.wait(5)
                return
            data = b"[invalid" if mode == "invalid" else json.dumps({"models": []}).encode()
            if mode == "oversized":
                data = b"x" * (MAX_RESPONSE + 1)
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield OllamaTransport(instance.server_port), calls, entered
    finally:
        release.set()
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=2)


def test_loopback_json_does_not_use_proxy_or_credentials(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://example.invalid:1")
    monkeypatch.setenv("OLLAMA_API_KEY", "synthetic-never-send")
    with server() as (transport, calls, _):
        assert transport.json("GET", "/api/tags", None, Event(), 1) == {"models": []}
        assert calls[0][0] == "/api/tags"
        assert "Authorization" not in calls[0][1]
        assert "synthetic-never-send" not in str(calls)


@pytest.mark.parametrize("mode", ["headers", "body"])
@pytest.mark.parametrize("cancel", [True, False])
def test_cancel_and_timeout_interrupt_headers_and_body(mode, cancel):
    with server(mode) as (transport, calls, entered):
        event = Event()
        timer = Timer(0.1, event.set)
        if cancel:
            timer.start()
        start = time.monotonic()
        try:
            with pytest.raises(OllamaError) as error:
                transport.json("GET", "/api/tags", None, event, 2 if cancel else 0.2)
        finally:
            if cancel:
                timer.cancel()
                timer.join()
        assert error.value.kind == ("cancelled" if cancel else "timeout")
        assert time.monotonic() - start < 1
        assert entered.is_set() and len(calls) == 1
        assert not any(t.name == "ritm-ollama-http" for t in threading.enumerate())


@pytest.mark.parametrize("mode", ["redirect", "invalid", "oversized"])
def test_bad_response_and_redirect_not_followed(mode):
    with server(mode) as (transport, calls, _):
        with pytest.raises(OllamaError):
            transport.json("GET", "/api/tags", None, Event(), 1)
        assert len(calls) == 1


def test_cancelled_request_never_reaches_server():
    with server() as (transport, calls, _):
        event = Event()
        event.set()
        with pytest.raises(OllamaError, match="отменён"):
            transport.json("GET", "/api/tags", None, event, 1)
        assert calls == []
