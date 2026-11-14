"""Состояния и ограниченные реплики, независимые от окна и системных часов."""

COOLDOWNS = {"rare": 300, "normal": 120, "often": 30}
LABELS = {
    "idle": "Ожидание",
    "working": "Работаем",
    "paused": "Пауза",
    "happy": "Задача выполнена!",
}
PHRASES = {
    "start": ("Начнём с небольшого шага.", "Я рядом. Сосредоточимся на задаче."),
    "done": (
        "Готово! Можно порадоваться результату.",
        "Ещё одна задача выполнена. Отличная работа!",
    ),
    "break": (
        "Пора сделать небольшой перерыв и размяться.",
        "Предлагаю короткий перерыв. Ты хорошо поработал.",
    ),
}


class Reactions:
    def __init__(self):
        self.mode = "idle"
        self.text = ""
        self._until = 0
        self._happy_until = 0
        self._spoken = float("-inf")
        self._session = None
        self._running = False
        self._baseline = 0
        self._break_minutes = None
        self._indices = dict.fromkeys(PHRASES, 0)

    def _say(self, event, now, options, visible):
        if not visible or options["muted"] or now - self._spoken < COOLDOWNS[options["frequency"]]:
            return False
        phrases = PHRASES[event]
        self.text = phrases[self._indices[event] % len(phrases)]
        self._indices[event] += 1
        self._spoken, self._until = now, now + 8
        return True

    def celebrate(self, now, options, visible):
        self._happy_until = now + 8
        self.mode = "happy"
        self._say("done", now, options, visible)

    def update(self, focus, now, options, visible):
        session = focus.get("recoveryId")
        running = bool(focus) and not focus.get("paused") and not focus.get("recovery")
        elapsed = 0
        if focus:
            hours, minutes, seconds = map(int, focus["elapsed"].split(":"))
            elapsed = hours * 3600 + minutes * 60 + seconds
        if session != self._session or (running and not self._running):
            self._baseline = elapsed
            if running:
                self._say("start", now, options, visible)
        if not running or self._break_minutes != options["break_minutes"]:
            self._baseline = elapsed
        self._break_minutes = options["break_minutes"]
        self._baseline = min(self._baseline, elapsed)
        if running and elapsed - self._baseline >= options["break_minutes"] * 60:
            if not visible or options["muted"] or self._say("break", now, options, visible):
                self._baseline = elapsed
        self._session, self._running = session, running
        self.mode = (
            "happy"
            if now < self._happy_until
            else "working"
            if running
            else "paused"
            if focus
            else "idle"
        )
        if now >= self._until or options["muted"] or not visible:
            self.text = ""
        return self.mode, self.text
