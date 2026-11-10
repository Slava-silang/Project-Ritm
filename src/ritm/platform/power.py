"""События сна logind и блокировки Cinnamon через Qt D-Bus."""

import os

from PySide6.QtCore import SLOT, QObject, Slot
from PySide6.QtDBus import (
    QDBusConnection,
    QDBusMessage,
    QDBusPendingCallWatcher,
    QDBusUnixFileDescriptor,
)

LOGIN = "org.freedesktop.login1"
MANAGER = LOGIN + ".Manager"
LOGIN_PATH = "/org/freedesktop/login1"
CINNAMON = "org.cinnamon.ScreenSaver"
CINNAMON_PATH = "/org/cinnamon/ScreenSaver"


class LinuxPowerMonitor(QObject):
    def __init__(self, controller, *, system_bus=None, session_bus=None):
        super().__init__()
        self._controller = controller
        self._system = system_bus if system_bus is not None else QDBusConnection.systemBus()
        self._session = session_bus if session_bus is not None else QDBusConnection.sessionBus()
        self._fd = None
        self._pending = None
        self._inhibit_attempted = False
        self._sleeping = False
        self._closed = False
        self._sleep_subscription = self._system.connect(
            LOGIN, LOGIN_PATH, MANAGER, "PrepareForSleep", self, SLOT("sleepChanged(bool)")
        )
        self._lock_subscription = self._session.connect(
            CINNAMON, CINNAMON_PATH, CINNAMON, "ActiveChanged", self, SLOT("lockChanged(bool)")
        )
        controller.focusChanged.connect(self._sync_inhibitor)
        controller.systemPauseDone.connect(self._release_after_sleep)
        self._sync_inhibitor()

    def _needs_inhibitor(self):
        focus = self._controller.focus
        return (
            self._sleep_subscription
            and not self._closed
            and not self._sleeping
            and focus
            and not focus["paused"]
            and not focus["recovery"]
        )

    def _sync_inhibitor(self):
        if self._sleeping:
            return
        if not self._needs_inhibitor():
            self._release_inhibitor()
            self._inhibit_attempted = False
        elif self._fd is None and self._pending is None and not self._inhibit_attempted:
            self._inhibit_attempted = True
            message = QDBusMessage.createMethodCall(LOGIN, LOGIN_PATH, MANAGER, "Inhibit")
            message.setArguments(["sleep", "Ритм", "Сохранение паузы фокус-сессии", "delay"])
            self._pending = QDBusPendingCallWatcher(self._system.asyncCall(message, 2000), self)
            self._pending.finished.connect(self._inhibitor_reply)

    def _inhibitor_reply(self, watcher):
        arguments = watcher.reply().arguments()
        if self._needs_inhibitor() and arguments:
            descriptor = arguments[0]
            if isinstance(descriptor, QDBusUnixFileDescriptor) and descriptor.isValid():
                self._fd = os.dup(descriptor.fileDescriptor())
        self._pending = None
        watcher.deleteLater()

    def _release_inhibitor(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _release_after_sleep(self):
        if self._sleeping:
            self._release_inhibitor()

    @Slot(bool)
    def sleepChanged(self, preparing):
        self._sleeping = preparing
        if preparing:
            self._controller.pauseForSystem("sleep")
        else:
            self._inhibit_attempted = False
            self._sync_inhibitor()

    @Slot(bool)
    def lockChanged(self, locked):
        if locked:
            self._controller.pauseForSystem("lock")

    def close(self):
        self._closed = True
        self._release_inhibitor()
        if self._sleep_subscription:
            self._system.disconnect(
                LOGIN, LOGIN_PATH, MANAGER, "PrepareForSleep", self, SLOT("sleepChanged(bool)")
            )
        if self._lock_subscription:
            self._session.disconnect(
                CINNAMON, CINNAMON_PATH, CINNAMON, "ActiveChanged", self, SLOT("lockChanged(bool)")
            )
