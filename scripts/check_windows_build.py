"""Verify the ZIP after extraction with no Python or Qt directories on PATH."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this check on Windows.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    archive = args.archive.resolve()
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert archive.with_suffix(".zip.sha256").read_text(encoding="utf-8").split()[0] == digest
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="Ритм перенос ", dir=root / "build") as temporary:
        working = Path(temporary)
        with ZipFile(archive) as package:
            package.extractall(working)
        executable = working / "main.dist" / "ritm.exe"
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith(("PYTHON", "QT_", "QML", "VIRTUAL_ENV"))
        }
        environment.update(
            PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
            QT_QPA_PLATFORM="offscreen",
            QT_QUICK_BACKEND="software",
        )

        def run(*arguments):
            result = subprocess.run(
                [str(executable), *arguments],
                cwd=working,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            for error in ("ReferenceError", "TypeError", "failed to load", "Traceback"):
                assert error not in result.stderr, result.stderr
            return result.stdout.strip()

        version = run("--version")
        profile = working / "тестовый профиль"
        for _ in range(2):
            run("--smoke-test", "--profile", str(profile))
        assert (profile / "tasks.sqlite3").is_file()
        source = working / "материал с пробелом.txt"
        source.write_text("Windows portable text", encoding="utf-8")
        assert json.loads(run("--extract-text", str(source)))["text"] == "Windows portable text"
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        pdf = working / "текстовый материал.pdf"
        writer = PdfWriter()
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        ),
                    }
                ),
            }
        )
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 50 700 Td (Windows portable PDF) Tj ET")
        page[NameObject("/Contents")] = content
        writer.write(pdf)
        assert "Windows portable PDF" in json.loads(run("--extract-text", str(pdf)))["text"]
        report = {
            "version": version,
            "sha256": digest,
            "relocation": True,
            "qml": True,
            "profile_restart": True,
            "text_worker": True,
            "pdf_worker": True,
            "clean_vm": False,
        }
        (root / "build/windows-verification.json").write_text(
            json.dumps(report, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
