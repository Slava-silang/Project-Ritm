"""Проверка установленной точки входа и QML из другой рабочей папки."""

import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest


@pytest.mark.parametrize("entrypoint", ["console", "module"])
def test_startup_from_another_directory(tmp_path: Path, entrypoint: str) -> None:
    working_directory = tmp_path / "другая папка"
    working_directory.mkdir()
    environment = os.environ.copy()
    environment.update(
        QT_QPA_PLATFORM="offscreen",
        QSG_RHI_BACKEND="software",
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    command = (
        [str(Path(sysconfig.get_path("scripts")) / ("ritm.exe" if os.name == "nt" else "ritm"))]
        if entrypoint == "console"
        else [sys.executable, "-m", "ritm"]
    )
    result = subprocess.run(
        [*command, "--smoke-test"],
        cwd=working_directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "failed to load" not in result.stderr.lower(), result.stderr
    assert "ReferenceError" not in result.stderr, result.stderr
    assert "TypeError" not in result.stderr, result.stderr
