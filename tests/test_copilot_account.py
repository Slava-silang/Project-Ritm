import io
import subprocess
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from test_copilot import Client, request

from ritm.integrations.copilot import CopilotError
from ritm.integrations.copilot_account import CopilotAccount


class AccountClient(Client):
    def __init__(self, options, **kwargs):
        super().__init__({**options, "cli_path": "/fake/copilot"}, **kwargs)

    async def get_auth_status(self):
        await self.step("auth")
        return SimpleNamespace(isAuthenticated=self.fail != "login", login="student")

    async def list_models(self):
        await self.step("models")
        return [
            SimpleNamespace(id="model-a", name="Model A", policy=None),
            SimpleNamespace(id="model-b", name="Model B", policy=None),
            SimpleNamespace(id="blocked", name="Blocked", policy=SimpleNamespace(state="disabled")),
        ]


def account(tmp_path, **kwargs):
    clients = []

    def factory(options):
        value = AccountClient(options, **kwargs)
        clients.append(value)
        return value

    return CopilotAccount(tmp_path, client_factory=factory), clients


class Process:
    def __init__(self, *, result=0, hang=False, output=None):
        self.stdout = io.BytesIO(
            output
            or b"To authenticate, visit https://github.com/login/device and enter code ABCD-1234.\n"
        )
        self.started, self.result, self.hang = time.monotonic(), result, hang
        self.terminated, self.killed = False, False

    def poll(self):
        return (
            self.result
            if self.terminated or (not self.hang and time.monotonic() - self.started > 0.1)
            else None
        )

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.terminated = True

    def wait(self, timeout):
        return self.result


def test_login_uses_official_cli_and_only_exposes_device_code(tmp_path):
    value, clients = account(tmp_path)
    calls, codes, processes = [], [], []

    def factory(args, **kwargs):
        calls.append((args, kwargs))
        process = Process()
        processes.append(process)
        return process

    value._process_factory = factory
    value.sign_in(Event(), codes.append)
    assert codes == ["ABCD-1234"]
    assert value.connected and value.login == "student" and value.model == "model-a"
    assert len(value.models) == 2
    args, options = calls[0]
    assert args[0] == "/fake/copilot" and "login" in args
    assert options["stdin"] == subprocess.DEVNULL  # Never accept plaintext token storage.
    assert options["env"]["COPILOT_HOME"] == str(tmp_path / "copilot-cli")
    assert options["env"]["GH_CONFIG_DIR"] == str(tmp_path / "copilot-cli" / "gh-empty")
    assert not Path(options["cwd"]).exists()
    assert processes[0].stdout.closed
    assert clients[-1].calls == ["start", "auth", "models", "stop"]
    assert "create" not in clients[-1].calls and "send" not in clients[-1].calls
    assert "--disable-builtin-mcps" in clients[-1].options["cli_args"]
    assert "--no-custom-instructions" in clients[-1].options["cli_args"]
    value.model = "model-b"
    adapter = value.adapter()
    assert adapter.connection.provider == "copilot" and adapter.connection.model == "model-b"
    adapter.generate(request(), Event())
    assert clients[-1].options["env"]["COPILOT_HOME"] == str(value.root)


@pytest.mark.parametrize("cancel", [True, False])
def test_login_cancel_or_timeout_terminates_child(tmp_path, cancel):
    value, clients = account(tmp_path)
    process = Process(hang=True)
    value._process_factory = lambda *args, **kwargs: process
    value.timeout = 0.15
    event = Event()
    with pytest.raises(CopilotError) as error:
        value.sign_in(event, lambda _: event.set() if cancel else None)
    assert error.value.kind == ("cancelled" if cancel else "timeout")
    assert process.terminated and process.stdout.closed
    assert len(clients) == 1  # No status/model call after an incomplete login.
    assert not value.connected


def test_login_failure_does_not_forward_output(tmp_path):
    value, clients = account(tmp_path)
    process = Process(result=1, output=b"private-token-detail\n")
    value._process_factory = lambda *args, **kwargs: process
    with pytest.raises(CopilotError) as error:
        value.sign_in(Event(), lambda _: pytest.fail("Unexpected code"))
    assert "private-token-detail" not in str(error.value)
    assert not value.connected and len(clients) == 1


@pytest.mark.parametrize("stage", ["login", "models", "auth"])
def test_check_failure_clears_available_connection(tmp_path, stage):
    value, clients = account(tmp_path, fail=stage)
    value.connected = True
    with pytest.raises(CopilotError):
        value.check(Event())
    assert not value.connected and not value.models
    assert clients[0].calls[-1] == "stop"
    with pytest.raises(CopilotError):
        value.adapter()


def test_disconnect_is_local_and_has_no_global_logout(tmp_path):
    value, clients = account(tmp_path)
    value.check(Event())
    before = len(clients)
    value.disconnect()
    assert len(clients) == before and not value.connected and not value.model
    assert not value.models and not value.login


def test_sdk_constructor_respects_isolation_without_starting(tmp_path):
    pytest.importorskip("copilot")
    value = CopilotAccount(tmp_path)
    client = value.client({"auto_start": False, "use_logged_in_user": False})
    assert client.options["auto_start"] is False
    assert client.options["env"]["COPILOT_HOME"] == str(value.root)
    assert client.options["env"]["GH_CONFIG_DIR"] == str(value.root / "gh-empty")
    assert client.options["cli_args"][0:2] == ["--config-dir", str(value.root)]
    assert client._process is None


def test_copilot_files_are_not_in_task_backup(tmp_path):
    from datetime import UTC, datetime
    from zipfile import ZipFile

    from ritm.storage.backup import create_backup
    from ritm.storage.sqlite import TaskStore

    profile = tmp_path / "profile"
    with TaskStore(profile / "tasks.sqlite3"):
        pass
    private = profile / "copilot-cli"
    private.mkdir()
    (private / "config.json").write_text('{"token":"synthetic-test-value"}')
    path = create_backup(profile, tmp_path / "backups", datetime(2026, 11, 25, tzinfo=UTC))
    with ZipFile(path) as archive:
        assert not any("copilot" in name for name in archive.namelist())
        assert all(b"synthetic-test-value" not in archive.read(name) for name in archive.namelist())
