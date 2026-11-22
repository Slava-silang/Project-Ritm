"""Месячные снимки и избранное: чистые переходы без БД и пересчёта наград."""

import re
from dataclasses import dataclass, replace
from datetime import date

from ritm.domain.garden import Garden, GardenState

RECENT_MONTHS = 12
FAVORITE_LIMIT = 20


def _month_index(month):
    if type(month) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
        raise ValueError("Месяц архива должен иметь формат ГГГГ-ММ.")
    try:
        date(int(month[:4]), int(month[5:]), 1)
    except ValueError:
        raise ValueError("Некорректный месяц архива.") from None
    return (int(month[:4]) - 1) * 12 + int(month[5:]) - 1


def _month_name(index):
    return f"{index // 12 + 1:04}-{index % 12 + 1:02}"


def _oldest(month):
    return _month_name(max(0, _month_index(month) - RECENT_MONTHS + 1))


@dataclass(frozen=True)
class GardenArchive:
    """Кошелёк/revision только у текущего сада; история содержит лишь имущество.

    Отметка избранного ссылается на месяц, а не создаёт вторую копию участка.
    Журналы оплаты, достижений, источника и квитанций находятся вне этой модели.
    """

    current: GardenState
    history: tuple[Garden, ...] = ()
    favorites: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.current) is not GardenState:
            raise ValueError("Нужен текущий сохранённый сад.")
        if type(self.history) is not tuple or any(type(g) is not Garden for g in self.history):
            raise ValueError("Архив должен содержать неизменяемые снимки садов.")
        months = [g.month for g in self.history]
        if months != sorted(set(months)) or any(m >= self.current.garden.month for m in months):
            raise ValueError("Месяцы архива повторяются, не упорядочены или не завершены.")
        if type(self.favorites) is not tuple or len(self.favorites) > FAVORITE_LIMIT:
            raise ValueError("В избранном может быть не больше 20 садов.")
        for month in self.favorites:
            _month_index(month)
        if self.favorites != tuple(sorted(set(self.favorites))):
            raise ValueError("Отметки избранного повторяются или не упорядочены.")
        if any(m not in {*months, self.current.garden.month} for m in self.favorites):
            raise ValueError("Для избранного отсутствует снимок сада.")
        cutoff = _oldest(self.current.garden.month)
        if any(m < cutoff and m not in self.favorites for m in months):
            raise ValueError("Обычный архив ограничен текущим и 11 предыдущими месяцами.")


def recent_months(archive):
    """Текущий месяц первым; пропуски представлены без сохранения пустых записей."""
    end = _month_index(archive.current.garden.month)
    return tuple(_month_name(i) for i in range(end, max(-1, end - RECENT_MONTHS), -1))


def view_month(archive, month):
    """Возвращает immutable имущество, без исторического кошелька/команд записи."""
    _month_index(month)
    if month == archive.current.garden.month:
        return archive.current.garden
    for garden in archive.history:
        if garden.month == month:
            return garden
    if month in recent_months(archive):
        return Garden(month)
    raise ValueError("Месяц недоступен в обычном архиве или избранном.")


def _retain(history, current_month, favorites):
    cutoff = _oldest(current_month)
    return tuple(
        garden
        for garden in sorted(history, key=lambda g: g.month)
        if garden.month >= cutoff or garden.month in favorites
    )


def rotate_month(archive, month):
    """Месяц передаётся по местной дате; назад и повторно переход не выполняется."""
    _month_index(month)
    if month <= archive.current.garden.month:
        return archive
    history = _retain((*archive.history, archive.current.garden), month, archive.favorites)
    current = GardenState(Garden(month), archive.current.balance, archive.current.revision + 1)
    return GardenArchive(current, history, archive.favorites)


def set_favorite(archive, month, enabled):
    _month_index(month)
    if type(enabled) is not bool:
        raise ValueError("Выберите, сохранять ли сад в избранном.")
    if month > archive.current.garden.month:
        raise ValueError("Будущий сад нельзя добавить в избранное.")
    if (month in archive.favorites) == enabled:
        return archive
    favorites = set(archive.favorites)
    history = archive.history
    if enabled:
        garden = view_month(archive, month)
        if len(favorites) == FAVORITE_LIMIT:
            raise ValueError("Уже сохранено 20 садов. Сначала уберите одну отметку избранного.")
        favorites.add(month)
        if month != archive.current.garden.month and not any(g.month == month for g in history):
            history = (*history, garden)
    else:
        favorites.remove(month)
    current = replace(archive.current, revision=archive.current.revision + 1)
    return GardenArchive(
        current,
        _retain(history, current.garden.month, favorites),
        tuple(sorted(favorites)),
    )
