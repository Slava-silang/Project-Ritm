"""Только явно выбранные управляемые копии; пути не передаются модели."""

from pathlib import Path
from threading import Event
from uuid import UUID

from ritm.integrations.assistant import SourceText
from ritm.integrations.file_text import MAX_TEXT, SUPPORTED, FileTextError, read_text
from ritm.storage.attachments import saved_attachment_path
from ritm.storage.sqlite import TaskStore


def available_files(store: TaskStore, context: str):
    task = store.get_task(UUID(context)) if context else None
    names = {}
    if task:
        names[task.id] = task.title
        for child in task.subtasks:
            names[child.id] = child.title
            names.update((item.id, item.title) for item in child.items)
    attached = [entry for entry in store.list_attachments() if task and entry.task_id == task.id]
    entries = {
        str(entry.id): (entry, "Вложения: " + names.get(entry.node_id, "задача"))
        for entry in attached
    }
    for entry in store.list_materials():
        entries.setdefault(str(entry.id), (entry, "Материалы"))
    return entries


def file_catalog(store: TaskStore, context: str):
    return [
        {
            "id": identifier,
            "name": entry.name,
            "source": origin,
            "supported": entry.suffix.lower() in SUPPORTED,
        }
        for identifier, (entry, origin) in available_files(store, context).items()
    ]


def selected_texts(profile: Path, context: str, identifiers: tuple[str, ...], cancelled: Event):
    if len(identifiers) > 5 or len(set(identifiers)) != len(identifiers):
        raise FileTextError("Выберите не более пяти разных файлов.")
    with TaskStore(profile / "tasks.sqlite3") as store:
        available = available_files(store, context)
        if set(identifiers) - available.keys():
            raise FileTextError("Выбранный файл больше недоступен. Обновите список файлов.")
        chosen = [available[identifier][0] for identifier in identifiers]
    result = []
    total = 0
    for entry in chosen:
        if cancelled.is_set():
            raise FileTextError("Чтение файла отменено.")
        try:
            path = saved_attachment_path(profile, entry)
            text = read_text(path, cancelled)
        except FileTextError as error:
            raise FileTextError(f"{entry.name}: {error}") from None
        except (OSError, ValueError):
            raise FileTextError(
                f"Не найдена сохранённая копия «{entry.name}». Загрузите файл заново."
            ) from None
        total += len(text)
        if total > MAX_TEXT:
            raise FileTextError(
                "В выбранных файлах больше 60 000 символов. Выберите меньше файлов или фрагмент."
            )
        result.append(SourceText(entry.name, text))
    return tuple(result)
