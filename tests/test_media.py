import base64
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from PySide6.QtGui import QColor, QImage
from test_chatgpt import signed_session

from ritm.application.drafts import parse_draft
from ritm.application.media import (
    MAX_BYTES,
    capability,
    image_from_bytes,
    png_bytes,
    prepare_sources,
    read_copy,
    transformed_image,
)
from ritm.application.tasks import TaskService
from ritm.domain.tasks import Task
from ritm.integrations.assistant import Connection, Request, SourceFile
from ritm.integrations.chatgpt import ChatGPTAdapter
from ritm.integrations.file_text import FileTextError
from ritm.storage.attachments import copy_attachment
from ritm.storage.sqlite import TaskStore

RAW = Connection("test", "vision", "none", input_kinds=frozenset({"text", "image", "file"}))


def make_image(path, width=60, height=40):
    image = QImage(width, height, QImage.Format_RGB32)
    for x in range(width):
        for y in range(height):
            image.setPixelColor(x, y, QColor(x * 255 // width, y * 255 // height, 100))
    assert image.save(str(path))
    return path.read_bytes()


def test_selected_binary_sources_are_exact_and_scoped(tmp_path):
    photo = tmp_path / "photo.png"
    original = make_image(photo)
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\nscanned document")
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        owner, other = Task(title="Задача"), Task(title="Другая")
        store.create_task(owner)
        store.create_task(other)
        picture = copy_attachment(store, tmp_path, owner.id, owner.id, photo)
        shared = copy_attachment(store, tmp_path, None, None, pdf)
        private = copy_attachment(store, tmp_path, other.id, other.id, photo)
    texts, files = prepare_sources(
        tmp_path, str(owner.id), (str(picture.id), str(shared.id)), Event(), RAW
    )
    assert texts == ()
    assert files[0].data == original and files[0].kind == "image"
    assert files[1].data == pdf.read_bytes() and files[1].kind == "file"
    assert "data=" not in repr(files[0])
    with pytest.raises(FileTextError, match="недоступен"):
        prepare_sources(tmp_path, str(owner.id), (str(private.id),), Event(), RAW)
    with pytest.raises(FileTextError, match="пяти"):
        prepare_sources(tmp_path, "", (str(shared.id),) * 2, Event(), RAW)
    assert capability(".PNG", Connection("test", "text-only", "none")) == ""
    with pytest.raises(FileTextError, match="моделью"):
        prepare_sources(
            tmp_path,
            str(owner.id),
            (str(picture.id),),
            Event(),
            Connection("test", "unknown", "none"),
        )


def test_rotation_crop_preserves_original_and_validates_bounds(tmp_path):
    path = tmp_path / "photo.png"
    original = make_image(path)
    image = transformed_image(original, 1, [0, 0, 50, 100])
    assert (image.width(), image.height()) == (20, 60)
    assert path.read_bytes() == original
    assert image_from_bytes(png_bytes(image)).size() == image.size()
    for crop in ([50, 0, 80, 100], [0, 0, 0, 100], [0.5, 0, 20, 20]):
        with pytest.raises(FileTextError, match="обрезки"):
            transformed_image(original, 0, crop)
    with pytest.raises(FileTextError):
        transformed_image(original, 5, [0, 0, 100, 100])
    with pytest.raises(FileTextError):
        image_from_bytes(b"not an image")


def test_reading_limits_cancellation_and_symlink(tmp_path):
    path = tmp_path / "oversize.bin"
    with path.open("wb") as stream:
        stream.truncate(MAX_BYTES + 1)
    with pytest.raises(FileTextError, match="8 МиБ"):
        read_copy(path, Event())
    path.write_bytes(b"file")
    cancelled = Event()
    cancelled.set()
    with pytest.raises(FileTextError, match="отменена"):
        read_copy(path, cancelled)
    if hasattr(os, "O_NOFOLLOW"):
        link = tmp_path / "link"
        link.symlink_to(path)
        with pytest.raises(OSError):
            read_copy(link, Event())


def test_multimodal_subscription_request_uses_inline_content(tmp_path):
    session, server = signed_session(tmp_path)
    session.list_models()
    adapter = ChatGPTAdapter(session)
    source = tmp_path / "image.png"
    data = make_image(source)
    pdf = b"%PDF-1.4\n document"
    request = Request(
        uuid4(),
        "Разбей задание",
        "create",
        files=(
            SourceFile("image.png", "image/png", data, "image"),
            SourceFile("scan.pdf", "application/pdf", pdf),
        ),
    )
    assert adapter.generate(request, Event())
    body = server.requests[-1][1]["data"]
    parts = body["input"][0]["content"]
    assert [p["type"] for p in parts] == ["input_text", "input_image", "input_file"]
    assert base64.b64decode(parts[1]["image_url"].split(",")[1]) == data
    assert base64.b64decode(parts[2]["file_data"].split(",")[1]) == pdf
    assert parts[2]["filename"] == "scan.pdf"
    assert body["store"] is False and body["stream"] is True
    assert not any(url.endswith("/files") for url, _ in server.requests)
    assert "data:image" not in body["instructions"]
    assert json.loads(parts[0]["text"])["history"] == []
    previous = len(server.requests)
    session.model = "unknown-model"
    with pytest.raises(ValueError, match="не поддерживает"):
        adapter.generate(request, Event())
    assert len(server.requests) == previous


def test_material_links_and_proposal_are_atomic_and_idempotent(tmp_path):
    source = tmp_path / "book.docx"
    source.write_bytes(b"document")
    draft = parse_draft(uuid4(), '{"title":"Из файла"}')
    path = tmp_path / "tasks.sqlite3"
    with TaskStore(path) as store:
        material = copy_attachment(store, tmp_path, None, None, source)
        service = TaskService(store)
        store._db.execute(
            "CREATE TRIGGER reject_link BEFORE INSERT ON material_links "
            "BEGIN SELECT RAISE(ABORT,'test'); END"
        )
        store._db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            service.apply_proposal(draft, material_ids=(material.id,))
        assert not store.list_tasks()
        assert store._db.execute("SELECT count(*) FROM applied_proposals").fetchone()[0] == 0
        store._db.execute("DROP TRIGGER reject_link")
        store._db.commit()
        assert service.apply_proposal(draft, material_ids=(material.id,)) == draft.task.id
    with TaskStore(path) as store:
        assert (
            TaskService(store).apply_proposal(draft, material_ids=(material.id,)) == draft.task.id
        )
        assert len(store.list_attachments()) == 1
        with pytest.raises(ValueError, match="другим содержимым"):
            TaskService(store).apply_proposal(draft)


def test_image_import_preview_resend_and_apply_gui(tmp_path):
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("RITM_UI_PLATFORM", "offscreen"),
        "QT_QUICK_BACKEND": "software",
        **{f"XDG_{key}_HOME": str(tmp_path / key.lower()) for key in ("DATA", "CACHE", "CONFIG")},
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ui_media_scenario.py")), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for warning in ("TypeError", "ReferenceError", "Binding loop", "failed to load"):
        assert warning not in result.stderr, result.stderr


def test_combined_size_and_image_pixel_limits(tmp_path):
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        identifiers = []
        for index in range(4):
            source = tmp_path / f"{index}.docx"
            with source.open("wb") as stream:
                stream.truncate(6 * 1024 * 1024)
            identifiers.append(str(copy_attachment(store, tmp_path, None, None, source).id))
    with pytest.raises(FileTextError, match="20 МиБ"):
        prepare_sources(tmp_path, "", tuple(identifiers), Event(), RAW)
    oversized = QImage(4000, 4000, QImage.Format_RGB32)
    oversized.fill(QColor("white"))
    with pytest.raises(FileTextError, match="12 миллионов"):
        image_from_bytes(png_bytes(oversized))


def test_missing_material_rolls_back_new_task(tmp_path):
    draft = parse_draft(uuid4(), '{"title":"Материал пропал"}')
    with TaskStore(tmp_path / "tasks.sqlite3") as store:
        with pytest.raises(ValueError, match="больше не доступен"):
            TaskService(store).apply_proposal(draft, material_ids=(uuid4(),))
        assert not store.list_tasks()
        assert store._db.execute("SELECT count(*) FROM applied_proposals").fetchone()[0] == 0
