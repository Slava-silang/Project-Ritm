"""Official Copilot CLI login, isolated configuration and model discovery."""

import asyncio
import os
import re
import subprocess
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import Thread

from ritm.integrations.copilot import (
    SDK_VERSION,
    CopilotAdapter,
    CopilotError,
    _cleanup,
    _client,
    _environment,
    _watch,
)


class CopilotAccount:
    def __init__(self, profile, *, client_factory=None, process_factory=None, timeout=300):
        self.root = Path(profile).resolve() / "copilot-cli"
        self._factory = client_factory or _client
        self._process_factory = process_factory or subprocess.Popen
        self.timeout = timeout
        self.connected, self.login, self.model, self.models = False, "", "", []

    @property
    def installed(self):
        try:
            return version("github-copilot-sdk") == SDK_VERSION
        except PackageNotFoundError:
            return False

    def environment(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # gh must not silently supply an account from its ordinary configuration.
        gh = self.root / "gh-empty"
        gh.mkdir(mode=0o700, exist_ok=True)
        return {**_environment(), "COPILOT_HOME": str(self.root), "GH_CONFIG_DIR": str(gh)}

    def client(self, options):
        return self._factory(
            {
                **options,
                "env": self.environment(),
                "cli_args": [
                    "--config-dir",
                    str(self.root),
                    "--disable-builtin-mcps",
                    "--no-custom-instructions",
                ],
            }
        )

    def sign_in(self, cancelled, code_callback):
        if cancelled.is_set():
            raise CopilotError("cancelled")
        # Constructing the pinned SDK client only locates its bundled CLI.
        client = self.client({"auto_start": False})
        path = client.options["cli_path"]
        queue = Queue()
        with TemporaryDirectory(prefix="ritm-copilot-login-") as directory:
            process = self._process_factory(
                [
                    path,
                    "--no-auto-update",
                    "--log-level",
                    "none",
                    "login",
                    "--host",
                    "https://github.com",
                    "--config-dir",
                    str(self.root),
                ],
                cwd=directory,
                env=self.environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                bufsize=0,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            def read():
                # Retain only a bounded rolling buffer, never expose raw CLI output.
                buffer, total = "", 0
                while chunk := process.stdout.read(1):
                    total += len(chunk)
                    if total > 512000:
                        queue.put(None)
                        return
                    buffer = (buffer + chunk.decode("utf-8", "replace"))[-4096:]
                    match = re.search(
                        r"To authenticate, visit https://github\.com/login/device and enter code "
                        r"([A-Z0-9]{4}-[A-Z0-9]{4})\.",
                        buffer,
                    )
                    if match:
                        queue.put(match[1])
                        buffer = ""

            reader = Thread(target=read, name="ritm-copilot-login", daemon=True)
            reader.start()
            deadline = time.monotonic() + self.timeout
            try:
                while True:
                    if cancelled.is_set():
                        raise CopilotError("cancelled")
                    if time.monotonic() >= deadline:
                        raise CopilotError("timeout")
                    try:
                        code = queue.get_nowait()
                    except Empty:
                        pass
                    else:
                        if code is None:
                            raise CopilotError("connection")
                        code_callback(code)
                    result = process.poll()
                    if result is not None:
                        if result != 0:
                            raise CopilotError("connection")
                        break
                    cancelled.wait(0.05)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                reader.join(timeout=2)
                process.stdout.close()
        self.check(cancelled)

    def check(self, cancelled):
        if cancelled.is_set():
            raise CopilotError("cancelled")
        self.connected, self.login, self.models = False, "", []
        login, models = asyncio.run(self._check(cancelled))
        self.connected, self.login, self.models = True, login, models
        if self.model not in {item["slug"] for item in models}:
            self.model = models[0]["slug"] if models else ""

    async def _check(self, cancelled):
        with TemporaryDirectory(prefix="ritm-copilot-check-") as directory:
            client = self.client(
                {
                    "cwd": directory,
                    "auto_start": False,
                    "auto_restart": False,
                    "log_level": "none",
                    "use_stdio": True,
                    "use_logged_in_user": True,
                }
            )
            try:

                async def discover():
                    await client.start()
                    status = await client.get_auth_status()
                    if not status.isAuthenticated:
                        raise CopilotError("login")
                    models = await client.list_models()
                    result = []
                    for model in models:
                        if getattr(getattr(model, "policy", None), "state", None) == "disabled":
                            continue
                        if isinstance(model.id, str) and 0 < len(model.id) <= 200:
                            result.append({"slug": model.id, "display_name": str(model.name)[:200]})
                    return str(status.login or "")[:200], result

                return await _watch(discover(), cancelled, min(self.timeout, 60))
            except CopilotError:
                raise
            except Exception:
                raise CopilotError("connection") from None
            finally:
                if not await _cleanup(client.stop, 3):
                    await _cleanup(client.force_stop, 3)

    def adapter(self):
        if not self.connected or not self.model:
            raise CopilotError("login")
        return CopilotAdapter(self.model, client_factory=self.client)

    def disconnect(self):
        # No global CLI logout: its keychain entry may also be used outside Ritm.
        self.connected, self.login, self.model, self.models = False, "", "", []
