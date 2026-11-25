"""Copilot lifecycle with injected clients; never starts the real CLI."""

import asyncio
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ritm.application.drafts import parse_draft
from ritm.integrations.assistant import ChatTurn, Request, SourceFile, SourceText
from ritm.integrations.assistant_prompt import prepare_text
from ritm.integrations.copilot import CopilotAdapter, CopilotError


class Client:
    def __init__(self, options, *, fail=None, pause=None, cancel=None, content='{"title":"План"}'):
        self.options = options
        self.fail, self.pause, self.cancel, self.content = fail, pause, cancel, content
        self.calls = []
        self.session_id = "temporary-session"
        self.config = None

    async def step(self, name):
        self.calls.append(name)
        if name == self.pause:
            if self.cancel:
                self.cancel.set()
            await asyncio.Event().wait()
        if name == self.fail:
            raise RuntimeError("private-provider-detail")

    async def start(self):
        await self.step("start")

    async def get_auth_status(self):
        await self.step("auth")
        return SimpleNamespace(isAuthenticated=self.fail != "login")

    async def create_session(self, config):
        self.config = config
        await self.step("create")
        return self

    async def send_and_wait(self, options, timeout=None):
        self.message, self.timeout = options, timeout
        await self.step("send")
        return SimpleNamespace(data=SimpleNamespace(content=self.content))

    async def abort(self):
        await self.step("abort")

    async def delete_session(self, identifier):
        assert identifier == self.session_id
        await self.step("delete")

    async def stop(self):
        await self.step("stop")

    async def force_stop(self):
        await self.step("force")


def setup_client(**kwargs):
    clients = []

    def factory(options):
        client = Client(options, **kwargs)
        clients.append(client)
        return client

    return clients, factory


def request(operation="create"):
    return Request(uuid4(), "Подготовь план", operation)


@pytest.mark.parametrize("operation", ["create", "split", "schedule", "update", "mail"])
def test_exact_context_and_cleanup(operation, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "not-a-real-token")
    monkeypatch.setenv("COPILOT_API_URL", "https://example.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    clients, factory = setup_client()
    adapter = CopilotAdapter("selected-model", client_factory=factory)
    req = request(operation)
    if operation == "mail":
        req = replace(req, mail={"subject": "Задание", "sender": "", "date": "", "body": "Текст"})
    else:
        req = replace(
            req,
            materials=(SourceText("Текст.md", "Условие"),),
            history=(ChatTurn("user", "Уточнение"),),
        )
    assert adapter.generate(req, Event()) == '{"title":"План"}'
    client = clients[0]
    prompt, instructions = prepare_text(req)
    assert client.message == {"prompt": prompt}
    assert client.config["system_message"] == {"mode": "replace", "content": instructions}
    assert client.config["available_tools"] == []
    assert client.config["mcp_servers"] == {}
    assert client.config["infinite_sessions"] == {"enabled": False}
    assert client.config["hooks"]["on_pre_tool_use"]({}, {}) == {"permissionDecision": "deny"}
    assert client.config["model"] == "selected-model"
    assert client.options["log_level"] == "none"
    assert client.options["use_logged_in_user"] is True
    assert not {"GITHUB_TOKEN", "COPILOT_API_URL", "OPENAI_API_KEY"} & client.options["env"].keys()
    assert client.calls == ["start", "auth", "create", "send", "abort", "delete", "stop"]
    assert not Path(client.options["cwd"]).exists()
    assert adapter.connection.provider == "copilot"
    assert adapter.connection.input_kinds == {"text"}


def test_result_remains_a_draft():
    clients, factory = setup_client(content='{"title":"План","subtasks":[],"weights":null}')
    req = request()
    response = CopilotAdapter("model", client_factory=factory).generate(req, Event())
    assert parse_draft(req.id, response).task.title == "План"
    assert clients[0].calls.count("send") == 1


@pytest.mark.parametrize("stage", ["start", "auth", "create", "send"])
def test_errors_do_not_expose_provider_details(stage):
    clients, factory = setup_client(fail=stage)
    with pytest.raises(CopilotError) as error:
        CopilotAdapter("model", client_factory=factory).generate(request(), Event())
    assert error.value.kind == "connection"
    assert "private-provider-detail" not in str(error.value)
    assert clients[0].calls[-1] == "stop"
    assert not Path(clients[0].options["cwd"]).exists()


def test_login_required_does_not_send():
    clients, factory = setup_client(fail="login")
    with pytest.raises(CopilotError, match="Нужно войти"):
        CopilotAdapter("model", client_factory=factory).generate(request(), Event())
    assert clients[0].calls == ["start", "auth", "stop"]


@pytest.mark.parametrize("stage", ["start", "auth", "create", "send"])
@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancellation_at_each_stage(stage, cancel):
    event = Event()
    clients, factory = setup_client(pause=stage, cancel=event if cancel else None)
    with pytest.raises(CopilotError) as error:
        CopilotAdapter("model", client_factory=factory, timeout=0.15).generate(request(), event)
    assert error.value.kind == ("cancelled" if cancel else "timeout")
    client = clients[0]
    assert client.calls[-1] == "stop"
    if stage == "send":
        assert client.calls[-3:] == ["abort", "delete", "stop"]
    assert not Path(client.options["cwd"]).exists()


@pytest.mark.parametrize("stage", ["abort", "delete", "stop"])
def test_cleanup_failure_still_stops_client(stage):
    clients, factory = setup_client(fail=stage)
    assert CopilotAdapter("model", client_factory=factory).generate(request(), Event())
    assert "stop" in clients[0].calls
    assert ("force" in clients[0].calls) == (stage == "stop")


def test_cleanup_timeout_force_stops_client():
    clients, factory = setup_client(pause="stop")
    adapter = CopilotAdapter("model", client_factory=factory, cleanup_timeout=0.02)
    assert adapter.generate(request(), Event())
    assert clients[0].calls[-1] == "force"


@pytest.mark.parametrize("content", [None, "", "   ", "x" * 128001])
def test_incomplete_or_oversized_response_is_rejected(content):
    clients, factory = setup_client(content=content)
    with pytest.raises(CopilotError) as error:
        CopilotAdapter("model", client_factory=factory).generate(request(), Event())
    assert error.value.kind == "response"
    assert clients[0].calls[-1] == "stop"


def test_cancelled_or_binary_request_never_starts_runtime():
    clients, factory = setup_client()
    adapter = CopilotAdapter("model", client_factory=factory)
    event = Event()
    event.set()
    with pytest.raises(CopilotError, match="отменён"):
        adapter.generate(request(), event)
    with pytest.raises(CopilotError, match="текстовые"):
        adapter.generate(
            replace(request(), files=(SourceFile("photo", "image/png", b"data", "image"),)), Event()
        )
    assert clients == []


@pytest.mark.parametrize("model", ["", "  ", None, "x" * 201])
def test_invalid_model(model):
    with pytest.raises(ValueError):
        CopilotAdapter(model)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        CopilotAdapter("model", timeout=timeout)


@pytest.mark.parametrize("installed", [None, "0.0.0"])
def test_optional_sdk_missing_or_wrong_version(monkeypatch, installed):
    from ritm.integrations import copilot

    def fake_version(_name):
        if installed is None:
            raise copilot.PackageNotFoundError
        return installed

    monkeypatch.setattr(copilot, "version", fake_version)
    with pytest.raises(CopilotError) as error:
        CopilotAdapter("model").generate(request(), Event())
    assert error.value.kind == "install"
