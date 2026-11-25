import copy
import json
from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest

from ritm.application.drafts import parse_draft
from ritm.integrations.assistant import Request, SourceFile
from ritm.integrations.assistant_prompt import prepare_text
from ritm.integrations.ollama import OllamaAdapter, OllamaError, OllamaTransport, list_models


class Transport:
    def __init__(self):
        self.calls = []
        self.metadata = {"details": {"format": "gguf"}, "capabilities": ["completion"]}
        self.answer = {
            "model": "local:latest",
            "done": True,
            "done_reason": "stop",
            "message": {"role": "assistant", "content": '{"title":"Локальный план"}'},
        }
        self.models = {"models": [{"name": "local:latest"}]}
        self.cancel_after_show = False

    def json(self, method, path, payload, cancelled, seconds):
        self.calls.append((method, path, payload, seconds))
        if path == "/api/show":
            if self.cancel_after_show:
                cancelled.set()
            return copy.deepcopy(self.metadata)
        return copy.deepcopy(self.answer if path == "/api/chat" else self.models)


def request(operation="create"):
    return Request(
        uuid4(),
        "Составь план",
        operation,
        mail={"subject": "Задание", "sender": "", "date": "", "body": "Текст"}
        if operation == "mail"
        else None,
    )


@pytest.mark.parametrize("operation", ["create", "split", "schedule", "update", "mail"])
def test_shared_context_and_explicit_json_without_tools(operation):
    transport = Transport()
    adapter = OllamaAdapter("local:latest", transport=transport)
    req = request(operation)
    answer = adapter.generate(req, Event())
    prompt, instructions = prepare_text(req)
    assert transport.calls[0][:3] == ("POST", "/api/show", {"model": "local:latest"})
    data = transport.calls[1][2]
    assert data == {
        "model": "local:latest",
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": "json",
    }
    assert json.loads(answer)["title"] == "Локальный план"
    assert adapter.connection.provider == "ollama"
    assert adapter.connection.authentication == "none"
    assert adapter.connection.input_kinds == {"text"}
    assert transport.calls[1][3] <= transport.calls[0][3]


def test_response_is_only_a_draft():
    req = request()
    response = OllamaAdapter("local:latest", transport=Transport()).generate(req, Event())
    assert parse_draft(req.id, response).task.title == "Локальный план"


@pytest.mark.parametrize(
    "metadata",
    [
        {"remote_host": "https://example.invalid"},
        {"remote_model": "remote"},
        {"details": None},
        {"details": {"format": "unknown"}},
        {"details": {"format": "gguf"}, "capabilities": ["embedding"]},
        {"details": {"format": "gguf"}, "capabilities": "completion"},
    ],
)
def test_remote_and_non_text_models_never_receive_task_context(metadata):
    transport = Transport()
    transport.metadata = metadata
    with pytest.raises(OllamaError):
        OllamaAdapter("local:latest", transport=transport).generate(request(), Event())
    assert len(transport.calls) == 1
    assert "prompt" not in transport.calls[0][2]


@pytest.mark.parametrize(
    "answer",
    [
        {"done": False},
        {"done_reason": "length"},
        {"done_reason": []},
        {"message": None},
        {"message": {"content": ""}},
        {"model": "different"},
        {"message": {"role": "assistant", "content": "x" * 128001}},
        {"message": {"role": "assistant", "content": "{}", "tool_calls": [{"name": "run"}]}},
        {"remote_host": "https://example.invalid"},
    ],
)
def test_invalid_incomplete_and_tool_responses_rejected(answer):
    transport = Transport()
    transport.answer.update(answer)
    with pytest.raises(OllamaError):
        OllamaAdapter("local:latest", transport=transport).generate(request(), Event())


def test_cancelled_and_binary_and_cloud_requests_never_send():
    transport = Transport()
    event = Event()
    event.set()
    with pytest.raises(OllamaError):
        OllamaAdapter("local:latest", transport=transport).generate(request(), event)
    with pytest.raises(OllamaError):
        OllamaAdapter("local:latest", transport=transport).generate(
            replace(request(), files=(SourceFile("image", "image/png", b"file", "image"),)), Event()
        )
    with pytest.raises(OllamaError):
        OllamaAdapter("other:cloud", transport=transport).generate(request(), Event())
    assert transport.calls == []
    transport.cancel_after_show = True
    with pytest.raises(OllamaError, match="отменён"):
        OllamaAdapter("local:latest", transport=transport).generate(request(), Event())
    assert len(transport.calls) == 1


def test_model_list_excludes_cloud_duplicates_invalid_names():
    transport = Transport()
    transport.models = {
        "models": [
            {"name": "local:latest"},
            {"name": "local:latest"},
            {"name": "remote:cloud"},
            {"name": "alias", "remote_host": "https://example.invalid"},
            {"name": "alias2", "remote_model": "remote"},
            {"name": "\n"},
            {"name": ""},
        ]
    }
    assert list_models(Event(), transport=transport) == [
        {"slug": "local:latest", "display_name": "local:latest"}
    ]
    assert transport.calls[0][:3] == ("GET", "/api/tags", None)


@pytest.mark.parametrize(
    "value", [{}, {"models": None}, {"models": [None]}, {"models": [{}] * 1001}]
)
def test_invalid_model_lists(value):
    transport = Transport()
    transport.models = value
    with pytest.raises(OllamaError):
        list_models(Event(), transport=transport)


@pytest.mark.parametrize("model", [None, "", "x\ny", "x" * 201])
def test_invalid_model_name(model):
    with pytest.raises(OllamaError):
        OllamaAdapter(model)


def test_transport_rejects_other_operations_before_network():
    transport = OllamaTransport()
    for method, path in (
        ("POST", "/api/pull"),
        ("POST", "/api/delete"),
        ("GET", "https://example.invalid"),
    ):
        with pytest.raises(ValueError):
            transport.json(method, path, {}, Event(), 1)
