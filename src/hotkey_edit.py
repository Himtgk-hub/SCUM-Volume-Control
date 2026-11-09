from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit

from config import KeyCombo


class HotkeyEdit(QLineEdit):
    """Поле отображения и записи горячей клавиши.

    Запись инициируется кнопкой «Назначить» (метод start_capture), сама
    клавиша ловится глобальным хуком, поэтому в поле мы только показываем
    результат и не перехватываем ввод с клавиатуры окна.
    """

    capture_started = Signal()
    capture_cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Не назначено")
        self._capturing = False

    def start_capture(self) -> None:
        self._capturing = True
        self.setPlaceholderText("Нажмите клавишу…")
        self.setText("")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()
        self.capture_started.emit()

    def apply_combo(self, combo: KeyCombo) -> None:
        self._capturing = False
        self.setText(combo.display if not combo.is_empty() else "")

    def cancel_capture(self) -> None:
        if self._capturing:
            self._capturing = False
            self.capture_cancelled.emit()

    def is_capturing(self) -> bool:
        return self._capturing

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Локальный Esc отменяет запись; всё остальное игнорируем, чтобы
        # случайный ввод с клавиатуры окна не попал в текст поля.
        if event.key() == Qt.Key_Escape and self._capturing:
            self.cancel_capture()
            return
        super().keyPressEvent(event)
