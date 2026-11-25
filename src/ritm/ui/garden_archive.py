"""Лёгкие списки месяцев без Qt, графики и исторических кошельков."""

from ritm.domain.garden_archive import recent_months


def empty_archive():
    return {"recent": [], "favorites": [], "count": 0, "selectedMonth": "", "currentMonth": ""}


def archive_view(archive, selected=""):
    current = archive.current.garden.month
    available = set(recent_months(archive)) | set(archive.favorites)
    selected = selected if selected in available else current

    def entry(month):
        favorite = month in archive.favorites
        return {
            "month": month,
            "title": month
            + (" · текущий" if month == current else "")
            + (" ★" if favorite else ""),
            "favorite": favorite,
        }

    return {
        "recent": [entry(m) for m in recent_months(archive)],
        "favorites": [entry(m) for m in reversed(archive.favorites)],
        "count": len(archive.favorites),
        "selectedMonth": selected,
        "currentMonth": current,
    }
