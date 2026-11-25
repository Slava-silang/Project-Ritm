"""Text-only Copilot SDK 0.1.32 bridge; called from the assistant worker."""

import asyncio
import math
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from ritm.integrations.assistant import Connection, Request
from ritm.integrations.assistant_prompt import prepare_text

SDK_VERSION = "0.1.32"


class CopilotError(ValueError):
    MESSAGES = {
        "install": "Подключение Copilot не установлено или его версия не поддерживается.",
        "login": "Нужно войти в GitHub Copilot.",
        "cancelled": "Запрос Copilot отменён.",
        "timeout": "Время ожидания Copilot истекло. Можно повторить попытку.",
        "response": "Copilot не вернул завершённое текстовое предложение.",
        "connection": (
            "Не удалось выполнить запрос Copilot. Проверьте подключение и лимиты аккаунта."
        ),
        "files": "Это подключение Copilot пока принимает только текстовые материалы.",
    }

    def __init__(self, kind):
        self.kind = kind
        super().__init__(self.MESSAGES[kind])


def _client(options):
    try:
        if version("github-copilot-sdk") != SDK_VERSION:
            raise CopilotError("install")
        from copilot import CopilotClient
    except (PackageNotFoundError, ImportError):
        raise CopilotError("install") from None
    return CopilotClient(options)


def _deny_permission(_request, _context):
    from copilot.types import PermissionRequestResult

    return PermissionRequestResult(kind="denied-by-rules")


def _environment():
    # Do not silently substitute an ambient API key/token for the selected account.
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(
            ("COPILOT_", "GITHUB_", "GH_", "OPENAI_", "ANTHROPIC_", "AZURE_OPENAI_")
        )
    }


class CopilotAdapter:
    def __init__(self, model: str, *, client_factory=None, timeout=120.0, cleanup_timeout=3.0):
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError("Нужно выбрать модель Copilot")
        if any(not math.isfinite(value) or value <= 0 for value in (timeout, cleanup_timeout)):
            raise ValueError("Недопустимое время ожидания Copilot")
        self.model = model
        self._factory = client_factory or _client
        self._timeout = timeout
        self._cleanup_timeout = cleanup_timeout

    @property
    def connection(self):
        return Connection(
            "copilot",
            self.model,
            "account",
            frozenset({"create", "split", "schedule", "update", "mail"}),
            frozenset({"text"}),
        )

    def generate(self, request: Request, cancelled: Event) -> str:
        if cancelled.is_set():
            raise CopilotError("cancelled")
        if request.files:
            raise CopilotError("files")
        prompt, instructions = prepare_text(request)
        return asyncio.run(self._generate(prompt, instructions, cancelled))

    async def _generate(self, prompt, instructions, cancelled):
        client, session = None, None
        # Never use the user's project as the coding agent's working directory.
        with TemporaryDirectory(prefix="ritm-copilot-") as directory:
            try:
                client = self._factory(
                    {
                        "cwd": directory,
                        "env": _environment(),
                        "log_level": "none",
                        "auto_start": False,
                        "auto_restart": False,
                        "use_stdio": True,
                        "use_logged_in_user": True,
                    }
                )

                async def exchange():
                    nonlocal session
                    await client.start()
                    status = await client.get_auth_status()
                    if not status.isAuthenticated:
                        raise CopilotError("login")
                    session = await client.create_session(
                        {
                            "client_name": "ritm",
                            "model": self.model,
                            "system_message": {"mode": "replace", "content": instructions},
                            "available_tools": [],
                            "tools": [],
                            "mcp_servers": {},
                            "custom_agents": [],
                            "skill_directories": [],
                            "working_directory": directory,
                            "config_dir": str(Path(directory) / "session"),
                            "infinite_sessions": {"enabled": False},
                            "on_permission_request": _deny_permission,
                            "hooks": {"on_pre_tool_use": lambda *_: {"permissionDecision": "deny"}},
                        }
                    )
                    response = await session.send_and_wait(
                        {"prompt": prompt}, timeout=self._timeout
                    )
                    content = getattr(getattr(response, "data", None), "content", None)
                    if (
                        not isinstance(content, str)
                        or not content.strip()
                        or len(content.encode()) > 128000
                    ):
                        raise CopilotError("response")
                    return content

                return await _watch(exchange(), cancelled, self._timeout)
            except CopilotError:
                raise
            except TimeoutError:
                raise CopilotError("timeout") from None
            except Exception:
                # Provider exceptions may contain prompts, URLs or credentials.
                raise CopilotError("connection") from None
            finally:
                if client is not None:
                    if session is not None:
                        await _cleanup(session.abort, self._cleanup_timeout)
                        await _cleanup(
                            lambda: client.delete_session(session.session_id), self._cleanup_timeout
                        )
                    if not await _cleanup(client.stop, self._cleanup_timeout):
                        await _cleanup(client.force_stop, self._cleanup_timeout)


async def _watch(coroutine, cancelled, timeout):
    task = asyncio.create_task(coroutine)
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while True:
            if cancelled.is_set():
                raise CopilotError("cancelled")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise CopilotError("timeout")
            done, _ = await asyncio.wait({task}, timeout=min(0.05, remaining))
            if done:
                if cancelled.is_set():
                    raise CopilotError("cancelled")
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _cleanup(operation, timeout):
    try:
        await asyncio.wait_for(operation(), timeout=timeout)
        return True
    except Exception:
        return False
