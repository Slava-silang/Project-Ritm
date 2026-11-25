"""Text-only Ollama connection to an existing loopback server; no downloads."""

import http.client
import json
import math
import re
import socket
import time
from queue import Empty, Queue
from threading import Event, Thread

from ritm.integrations.assistant import Connection, Request
from ritm.integrations.assistant_prompt import prepare_text

MAX_RESPONSE = 2 * 1024 * 1024
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z")


class OllamaError(ValueError):
    MESSAGES = {
        "connection": "Нет связи с локальной Ollama. Проверьте, запущена ли она.",
        "cancelled": "Запрос к локальной модели отменён.",
        "timeout": "Время ожидания локальной модели истекло.",
        "response": "Локальная модель вернула неподдерживаемый или неполный ответ.",
        "model": "Выберите установленную локальную текстовую модель Ollama.",
        "remote": "Облачные модели не поддерживаются этим локальным подключением.",
        "files": "Это подключение пока принимает только текстовые материалы.",
    }

    def __init__(self, kind):
        self.kind = kind
        super().__init__(self.MESSAGES[kind])


def _local(data):
    if data.get("remote_host") or data.get("remote_model"):
        raise OllamaError("remote")


class OllamaTransport:
    def __init__(self, port=11434):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Недопустимый порт Ollama")
        self.port = port

    def json(self, method, path, payload, cancelled, seconds):
        if (method, path) not in {
            ("GET", "/api/tags"),
            ("POST", "/api/show"),
            ("POST", "/api/chat"),
        }:
            raise ValueError("Неподдерживаемая операция Ollama")
        if not math.isfinite(seconds) or seconds <= 0:
            raise OllamaError("timeout")
        if cancelled.is_set():
            raise OllamaError("cancelled")
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=min(seconds, 2))
        queue, stopped = Queue(maxsize=1), Event()
        sockets = []

        def read():
            try:
                connection.connect()
                sockets.append(connection.sock)
                connection.sock.settimeout(seconds)
                if stopped.is_set() or cancelled.is_set():
                    return
                connection.request(method, path, body, {"Content-Type": "application/json"})
                with connection.getresponse() as response:
                    if response.status != 200:
                        raise OllamaError("connection")
                    raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise OllamaError("response")
                value = json.loads(raw)
                if not isinstance(value, dict) or "error" in value:
                    raise OllamaError("response")
                queue.put((value, None))
            except OllamaError as error:
                queue.put((None, error.kind))
            except (ValueError, UnicodeError):
                queue.put((None, "response"))
            except TimeoutError:
                queue.put((None, "timeout"))
            except Exception:
                queue.put((None, "connection"))
            finally:
                connection.close()

        thread = Thread(target=read, name="ritm-ollama-http", daemon=True)
        thread.start()
        deadline = time.monotonic() + seconds
        try:
            while True:
                if cancelled.is_set():
                    raise OllamaError("cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OllamaError("timeout")
                try:
                    result, error = queue.get(timeout=min(0.05, remaining))
                except Empty:
                    continue
                if cancelled.is_set():
                    raise OllamaError("cancelled")
                if error:
                    raise OllamaError(error)
                return result
        finally:
            stopped.set()
            # shutdown interrupts a blocked receive without triggering an auto-reconnect.
            if sockets:
                try:
                    sockets[0].shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            thread.join(timeout=0.5)


class OllamaAdapter:
    def __init__(self, model, *, transport=None, timeout=120):
        if not isinstance(model, str) or MODEL.fullmatch(model) is None:
            raise OllamaError("model")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Недопустимое время ожидания Ollama")
        self.model, self.transport, self.timeout = model, transport or OllamaTransport(), timeout

    @property
    def connection(self):
        return Connection(
            "ollama",
            self.model,
            "none",
            frozenset({"create", "split", "schedule", "update", "mail"}),
            frozenset({"text"}),
        )

    def generate(self, request: Request, cancelled: Event) -> str:
        if cancelled.is_set():
            raise OllamaError("cancelled")
        if request.files:
            raise OllamaError("files")
        if ":cloud" in self.model.lower():
            raise OllamaError("remote")
        deadline = time.monotonic() + self.timeout
        metadata = self.transport.json(
            "POST",
            "/api/show",
            {"model": self.model},
            cancelled,
            self.timeout,
        )
        _local(metadata)
        details, capabilities = metadata.get("details"), metadata.get("capabilities")
        if (
            not isinstance(details, dict)
            or details.get("format") not in ("gguf", "safetensors")
            or not isinstance(capabilities, list)
            or "completion" not in capabilities
        ):
            raise OllamaError("model")
        if cancelled.is_set():
            raise OllamaError("cancelled")
        if time.monotonic() >= deadline:
            raise OllamaError("timeout")
        prompt, instructions = prepare_text(request)
        result = self.transport.json(
            "POST",
            "/api/chat",
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": "json",
            },
            cancelled,
            deadline - time.monotonic(),
        )
        if cancelled.is_set():
            raise OllamaError("cancelled")
        _local(result)
        message = result.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if (
            result.get("done") is not True
            or result.get("done_reason") not in (None, "stop")
            or result.get("model", self.model) != self.model
            or not isinstance(content, str)
            or not content.strip()
            or len(content.encode()) > 128000
            or message.get("tool_calls")
            or message.get("role") != "assistant"
        ):
            raise OllamaError("response")
        return content


def list_models(cancelled, *, transport=None):
    value = (transport or OllamaTransport()).json("GET", "/api/tags", None, cancelled, 10)
    models = value.get("models")
    if not isinstance(models, list) or len(models) > 1000:
        raise OllamaError("response")
    result, names = [], set()
    for item in models:
        if not isinstance(item, dict):
            raise OllamaError("response")
        name = item.get("name")
        if (
            isinstance(name, str)
            and MODEL.fullmatch(name)
            and name not in names
            and ":cloud" not in name.lower()
            and not item.get("remote_model")
            and not item.get("remote_host")
        ):
            names.add(name)
            result.append({"slug": name, "display_name": name})
    return result
