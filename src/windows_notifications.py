"""Прежние уведомления из трея с задержкой после последнего нажатия."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer


class WindowsNotifications(QObject):
    def __init__(self, notify: Callable[[str, str], None], parent=None):
        super().__init__(parent)
        self._notify = notify
        self._pending: tuple[str, str] | None = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._send)

    def schedule(self, title: str, message: str, delay_ms: int = 300) -> None:
        self._pending = (title, message)
        self.timer.stop()
        if delay_ms <= 0:
            self._send()
        else:
            self.timer.start(delay_ms)

    def cancel(self) -> None:
        self.timer.stop()
        self._pending = None

    def _send(self) -> None:
        if self._pending is None:
            return
        title, message = self._pending
        self._pending = None
        self._notify(title, message)
