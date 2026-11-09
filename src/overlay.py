from __future__ import annotations

import logging

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from winutils import (apply_overlay_styles, find_window_by_process,
                      monitor_rect_for_window, raise_topmost_no_activate)


def _point(x: int, y: int) -> QPoint:
    return QPoint(int(x), int(y))

log = logging.getLogger(__name__)

WIDTH = 240
HEIGHT = 64
MARGIN = 24
FADE_MS = 180


class OverlayWidget(QWidget):
    """Неподвижная плашка поверх игры с текущим уровнем громкости."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(WIDTH, HEIGHT)

        self._percent = 0
        self._title = "SCUM"
        self._subtitle = ""
        self._hydrated_styles = False

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(FADE_MS)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

    # ---------- публичный API ----------

    def set_title(self, title: str) -> None:
        self._title = title or ""

    def show_volume(self, percent: int, title: str, subtitle: str = "",
                    position: str = "bottom-right", duration_ms: int = 1000,
                    process_name: str = "") -> None:
        self._percent = max(0, min(100, int(percent)))
        self._title = title or "SCUM"
        self._subtitle = subtitle
        self._reposition(position, process_name)
        self.update()

        self._anim.stop()
        self.setWindowOpacity(1.0)
        self.show()
        self._apply_native_styles()
        self._hide_timer.start(max(200, int(duration_ms)))

    def hide_now(self) -> None:
        self._hide_timer.stop()
        self._anim.stop()
        self.hide()

    # ---------- жизнь окна ----------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_native_styles()

    def _apply_native_styles(self) -> None:
        hwnd = int(self.winId())
        if not hwnd:
            return
        try:
            apply_overlay_styles(hwnd)
            raise_topmost_no_activate(hwnd)
        except Exception as exc:
            log.debug("Не удалось применить стили оверлея: %s", exc)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        try:
            self._anim.finished.disconnect(self.hide)
        except (RuntimeError, TypeError):
            pass
        self._anim.finished.connect(self.hide)
        self._anim.start()

    # ---------- позиционирование ----------

    def _reposition(self, position: str, process_name: str) -> None:
        area = None
        hwnd = find_window_by_process(process_name) if process_name else 0
        if hwnd:
            area = self._qt_area_for_window(hwnd)
        if area is None:
            screen = self.screen() or self._primary()
            if screen is None:
                return
            geo = screen.availableGeometry()
            area = (geo.left(), geo.top(), geo.right(), geo.bottom())

        left, top, right, bottom = area
        x, y = self._coords(position, left, top, right, bottom)
        self.move(int(x), int(y))

    @staticmethod
    def _qt_area_for_window(hwnd: int) -> tuple[int, int, int, int] | None:
        """Рабочая область монитора окна средствами Qt (логические пиксели)."""
        from PySide6.QtGui import QGuiApplication

        rel = monitor_rect_for_window(hwnd)
        if rel is None:
            return None
        # monitor_rect_for_window уже вернул логические координаты; ищем,
        # какому QScreen это соответствует — это точнее, чем пересчёт вручную.
        center_x = (rel[0] + rel[2]) // 2
        center_y = (rel[1] + rel[3]) // 2
        screen = QGuiApplication.screenAt(_point(center_x, center_y))
        if screen is None:
            return rel
        geo = screen.availableGeometry()
        return geo.left(), geo.top(), geo.right(), geo.bottom()

    def _coords(self, position: str, left: int, top: int, right: int, bottom: int):
        w, h, m = self.width(), self.height(), MARGIN
        cx = left + (right - left - w) // 2
        cy = top + (bottom - top - h) // 2
        table = {
            "top-left": (left + m, top + m),
            "top-center": (cx, top + m),
            "top-right": (right - w - m, top + m),
            "bottom-left": (left + m, bottom - h - m),
            "bottom-center": (cx, bottom - h - m),
            "bottom-right": (right - w - m, bottom - h - m),
            "center": (cx, cy),
        }
        return table.get(position, (right - w - m, bottom - h - m))

    def _primary(self):
        from PySide6.QtGui import QGuiApplication
        screens = QGuiApplication.screens()
        return screens[0] if screens else None

    # ---------- отрисовка ----------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 14, 14)

        painter.fillPath(path, QColor(18, 20, 24, 232))
        painter.setPen(QPen(QColor(70, 190, 90, 220), 1.4))
        painter.drawPath(path)

        # Заголовок и значение
        painter.setPen(QColor(235, 238, 242))
        title_font = painter.font()
        title_font.setPointSize(11)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(20, 27, f"{self._title}   {self._percent} %")

        if self._subtitle:
            sub_font = painter.font()
            sub_font.setPointSize(8)
            sub_font.setBold(False)
            painter.setFont(sub_font)
            painter.setPen(QColor(180, 186, 196))
            painter.drawText(20, 44, self._subtitle)

        # Полоска громкости
        bar_x, bar_w, bar_h = 20, self.width() - 40, 8
        bar_y = self.height() - 20
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 40))
        painter.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 4, 4)
        fill_w = int(bar_w * self._percent / 100)
        if fill_w > 0:
            painter.setBrush(QColor(70, 190, 90))
            painter.drawRoundedRect(bar_x, bar_y, max(4, fill_w), bar_h, 4, 4)

        painter.end()
