"""Атомарная библиотека навыков; повреждённые данные не перезаписываются."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from ritm.domain.skills import Skill

MAX_BYTES = 1024 * 1024
MAX_SKILLS = 100


def load_skills(profile: Path) -> tuple[Skill, ...]:
    path = profile / "skills.json"
    if not path.exists():
        return ()
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "skills"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["skills"], list)
            or len(data["skills"]) > MAX_SKILLS
        ):
            raise ValueError()
        result = []
        for row in data["skills"]:
            if not isinstance(row, dict) or set(row) != {
                "id",
                "name",
                "description",
                "instruction",
            }:
                raise ValueError()
            result.append(
                Skill(UUID(row["id"]), row["name"], row["description"], row["instruction"])
            )
        if len({skill.id for skill in result}) != len(result):
            raise ValueError()
        return tuple(result)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ValueError(
            "Не удалось прочитать библиотеку навыков. Файл сохранён без изменений; "
            "исправьте его и нажмите «Обновить навыки»."
        ) from None


def save_skill(profile: Path, skill: Skill, *, expected: Skill | None):
    rows = list(load_skills(profile))
    index = next((i for i, row in enumerate(rows) if row.id == skill.id), None)
    if expected is None:
        if index is not None:
            raise ValueError("Навык уже существует. Обновите список.")
        rows.append(skill)
    else:
        if expected.id != skill.id or index is None or rows[index] != expected:
            raise ValueError("Навык изменился. Обновите список перед редактированием.")
        rows[index] = skill
    raw = json.dumps(
        {"version": 1, "skills": [row.data() for row in rows]}, ensure_ascii=False
    ).encode()
    if len(rows) > MAX_SKILLS or len(raw) > MAX_BYTES:
        raise ValueError("Библиотека заполнена: максимум 100 навыков и 1 МиБ текста")
    with NamedTemporaryFile(dir=profile, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, profile / "skills.json")
        finally:
            temporary.unlink(missing_ok=True)
    return tuple(rows)
