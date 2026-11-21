"""Снимок достижений для интерфейса, без начислений и обращения к хранилищу."""

from ritm.domain.garden_achievements import HOUR, THRESHOLDS, MonthProgress


def hours_text(value):
    hundredths = value * 100 // HOUR
    return f"{hundredths // 100}.{hundredths % 100:02}"


def achievement_view(state, current_month):
    months = {m.month: m for m in state.months}
    months.setdefault(current_month, MonthProgress(current_month))
    periods = []
    for month in sorted(months, reverse=True):
        saved = months[month]
        goals = []
        for category, title, value, unit in (
            ("tasks", "Главные задачи", saved.roots, 1),
            ("focus", "Оплаченные часы", saved.paid_us, HOUR),
        ):
            for threshold, reward in THRESHOLDS:
                key = f"{category}:{threshold}"
                progress = str(saved.roots) if category == "tasks" else hours_text(value)
                goals.append(
                    {
                        "key": key,
                        "title": f"{title}: {threshold}",
                        "progress": f"{progress} / {threshold}",
                        "ratio": min(value / (threshold * unit), 1),
                        "done": key in saved.claimed,
                        "reward": reward,
                    }
                )
        periods.append(
            {
                "month": month,
                "title": month + (" · текущий" if month == current_month else ""),
                "earned": sum(g["reward"] for g in goals if g["done"]),
                "maximum": 2 * sum(reward for _, reward in THRESHOLDS),
                "goals": goals,
            }
        )
    permanent = [
        {
            "key": "welcome",
            "title": "Знакомство с садом",
            "condition": "Первое включение учёта сада",
            "done": state.welcomed,
            "reward": "100 монет",
        },
        {
            "key": "first_task",
            "title": "Первая новая задача",
            "condition": "Создать задачу после включения учёта",
            "done": state.first_task,
            "reward": "300 монет",
        },
        {
            "key": "space",
            "title": "Космос",
            "condition": f"Оплаченные часы за всё время: {hours_text(state.paid_us)} / 30",
            "done": "space" in state.themes,
            "reward": "Тема «Космос» навсегда",
        },
        {
            "key": "jungle",
            "title": "Джунгли",
            "condition": f"Уникальные главные задачи за всё время: {len(state.roots)} / 15",
            "done": "jungle" in state.themes,
            "reward": "Тема «Джунгли» навсегда",
        },
    ]
    return {"currentMonth": current_month, "periods": periods, "permanent": permanent}
