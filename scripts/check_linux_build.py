"""Проверить самостоятельную сборку в Linux без установленного Python."""

import argparse
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


def check_relocation(directory: Path) -> None:
    expected = subprocess.run(
        [str(directory / "ritm.bin"), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="ritm-relocation-") as temporary:
        root = Path(temporary)
        moved = root / "перенос программы" / "Ритм"
        shutil.copytree(directory, moved, symlinks=True)
        environment = os.environ.copy() | {
            "QT_QPA_PLATFORM": "offscreen",
            "QSG_RHI_BACKEND": "software",
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CACHE_HOME": str(root / "cache"),
        }
        result = subprocess.run(
            [str(moved / "ritm.bin"), "--version"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        if result.stdout != expected:
            raise SystemExit("Версия программы после переноса изменилась.")
        subprocess.run(
            [str(moved / "ritm.bin"), "--smoke-test"],
            cwd=root,
            env=environment,
            check=True,
            timeout=30,
        )
    print("Перенос в папку с кириллицей и пробелом проверен.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-image", action="store_true", help="Подготовить тестовый образ.")
    parser.add_argument("--directory", type=Path, help="Папка проверяемой standalone-сборки.")
    parser.add_argument("--relocation-only", action="store_true", help="Проверить только перенос.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = (args.directory or root / "dist" / f"ritm-linux-{platform.machine()}").resolve()
    if not (directory / "ritm.bin").is_file():
        raise SystemExit("Сначала соберите приложение командой scripts/build_linux.py.")

    check_relocation(directory)
    if args.relocation_only:
        return 0

    image = "ritm-smoke:ubuntu24.04"
    if args.build_image:
        subprocess.run(
            [
                "docker",
                "build",
                "--file",
                str(root / "packaging/linux/Dockerfile.smoke"),
                "--tag",
                image,
                str(root / "packaging/linux"),
            ],
            check=True,
        )

    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--user=65534:65534",
            "--tmpfs",
            "/tmp:rw,mode=1777",
            "--mount",
            f"type=bind,source={directory},target=/app,readonly",
            image,
            "sh",
            "-c",
            "if command -v python3 >/dev/null || command -v python >/dev/null; then "
            "echo 'Ошибка: в тестовом образе найден Python' >&2; exit 1; fi; "
            "/app/ritm.bin --version && exec /app/ritm.bin --smoke-test",
        ],
        check=True,
        timeout=60,
    )
    print("Проверка пройдена: Ubuntu 24.04, без установленного Python, исходников и сети.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
