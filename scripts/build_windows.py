"""Build a Windows standalone folder using the locked packaging environment."""

import hashlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this script on Windows.")
    root = Path(__file__).resolve().parents[1]
    build = root / "build" / "windows"
    build.mkdir(parents=True, exist_ok=True)
    environment = os.environ | {"NUITKA_CACHE_DIR": str(build / "nuitka-cache")}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "nuitka",
            "--mode=standalone",
            "--enable-plugin=pyside6",
            "--include-qt-plugins=qml",
            "--include-package=ritm",
            "--include-package=tzdata",
            "--noinclude-qt-translations",
            # Same unused QML families excluded by pyside6-deploy's scanner.
            "--noinclude-dlls=Qt6Quick3D*",
            "--noinclude-dlls=Qt6Charts*",
            "--noinclude-dlls=Qt6WebEngine*",
            "--noinclude-dlls=Qt6Test*",
            "--noinclude-dlls=Qt6Sensors*",
            "--noinclude-dlls=*/qml/QtQuickEffectMaker/*",
            f"--include-data-dir={root / 'src/ritm/qml'}=ritm/qml",
            "--mingw64",
            "--assume-yes-for-downloads",
            "--jobs=4",
            "--output-filename=ritm.exe",
            f"--output-dir={build}",
            f"--report={build / 'compilation-report.xml'}",
            str(root / "src/main.py"),
        ],
        cwd=root,
        env=environment,
        check=True,
    )
    folder = build / "main.dist"
    subprocess.run(
        [str(folder / "ritm.exe"), "--smoke-test"],
        cwd=build,
        env=environment | {"QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
        check=True,
        timeout=60,
    )
    from ritm import __version__

    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    archive = Path(
        shutil.make_archive(
            str(dist / f"ritm-{__version__}-windows-{platform.machine().lower()}"),
            "zip",
            root_dir=build,
            base_dir=folder.name,
        )
    )
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(
        f"{digest}  {archive.name}\n",
        encoding="utf-8",
    )
    print(f"Executable: {folder / 'ritm.exe'}")
    print(f"Archive: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
