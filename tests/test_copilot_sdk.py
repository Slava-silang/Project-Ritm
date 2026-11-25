"""Optional SDK contract, local JSON-RPC stub only; no CLI process or login."""

import asyncio
from importlib.metadata import version
from threading import Event

import pytest
from test_copilot import request, setup_client

from ritm.integrations.copilot import SDK_VERSION, CopilotAdapter


def test_pinned_sdk_serializes_restricted_session():
    sdk = pytest.importorskip("copilot")
    assert version("github-copilot-sdk") == SDK_VERSION
    clients, factory = setup_client()
    CopilotAdapter("chosen-model", client_factory=factory).generate(request(), Event())
    config = clients[0].config
    calls = []

    class Rpc:
        async def request(self, method, payload):
            calls.append((method, payload))
            return {"sessionId": "stub-session"}

    # Constructor locates the bundled binary but does not launch it.
    client = sdk.CopilotClient({"auto_start": False})
    client._client = Rpc()
    session = asyncio.run(client.create_session(config))
    assert session.session_id == "stub-session"
    method, data = calls[0]
    assert method == "session.create"
    assert data["availableTools"] == []
    assert data["requestPermission"] is True
    assert data["hooks"] is True
    assert data["systemMessage"] == config["system_message"]
    assert data["infiniteSessions"] == {"enabled": False}
    assert data["configDir"] == config["config_dir"]
    assert data["workingDirectory"] == config["working_directory"]
    assert data["model"] == "chosen-model"
    assert "provider" not in data
    assert config["on_permission_request"]({}, {}).kind == "denied-by-rules"
