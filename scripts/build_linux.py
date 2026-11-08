"""Собрать standalone-папку и архив, сохранив исходную spec неизменной."""

import configparser
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path


def main() -> int:
    if sys.platform != "linux":
        raise SystemExit("Эта команда собирает Linux-версию. Для Windows нужен отдельный шаг.")

    root = Path(__file__).resolve().parents[1]
    build = root / "build"
    build.mkdir(exist_ok=True)
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.setdefault("NUITKA_CACHE_DIR", str(build / "nuitka-cache"))

    with tempfile.TemporaryDirectory(prefix="ritm-deploy-", dir=build) as temporary:
        staging = Path(temporary)
        config = configparser.ConfigParser()
        config.read(root / "pysidedeploy.spec", encoding="utf-8")
        config["app"]["project_dir"] = str(root)
        config["app"]["exec_directory"] = str(staging)
        spec = staging / "pysidedeploy.spec"
        with spec.open("w", encoding="utf-8") as output:
            config.write(output)

        subprocess.run(
            [
                str(Path(sys.executable).parent / "pyside6-deploy"),
                "-c",
                str(spec),
                "--no-install",
            ],
            cwd=root,
            env=environment,
            check=True,
        )
        executable = staging / "ritm.dist" / "ritm.bin"
        if not executable.is_file():
            raise SystemExit("Сборка не создала исполняемый файл; проверьте сообщения Nuitka.")

        check_environment = environment | {
            "QT_QPA_PLATFORM": "offscreen",
            "QSG_RHI_BACKEND": "software",
            "XDG_CONFIG_HOME": str(staging / "config"),
            "XDG_DATA_HOME": str(staging / "data"),
            "XDG_CACHE_HOME": str(staging / "cache"),
        }
        subprocess.run(
            [str(executable), "--smoke-test"],
            cwd=staging,
            env=check_environment,
            check=True,
            timeout=30,
        )

        name = f"ritm-linux-{platform.machine()}"
        destination = dist / name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(executable.parent, destination, symlinks=True)
        archive = shutil.make_archive(
            str(dist / f"ritm-{version('project-ritm')}-linux-{platform.machine()}"),
            "gztar",
            root_dir=dist,
            base_dir=name,
        )
        archive_path = Path(archive)
        with archive_path.open("rb") as content:
            digest = hashlib.file_digest(content, "sha256").hexdigest()
        archive_path.with_suffix(archive_path.suffix + ".sha256").write_text(
            f"{digest}  {archive_path.name}\n", encoding="utf-8"
        )
        print(f"Программа: {destination / 'ritm.bin'}")
        print(f"Архив: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
