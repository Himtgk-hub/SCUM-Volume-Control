from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QBrush, QPolygon, QPen

from resources import resource_path


def make_tray_icon() -> QIcon:
    path = resource_path("resources", "icon.ico")
    icon = QIcon(path)
    if not icon.isNull():
        return icon
    # Резервная иконка-динамик, если файл не найден.
    pix = QPixmap(32, 32)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    green = QColor(70, 190, 90)
    painter.setBrush(QBrush(green))
    painter.setPen(Qt.NoPen)
    # Центрированный динамик: широкая симметричная форма без смещения влево.
    painter.drawPolygon(QPolygon([
        QPoint(6, 13), QPoint(12, 13), QPoint(20, 6),
        QPoint(20, 26), QPoint(12, 19), QPoint(6, 19),
    ]))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(green, 2.2, Qt.SolidLine, Qt.RoundCap))
    painter.drawArc(17, 9, 12, 14, -65 * 16, 130 * 16)
    painter.setPen(QPen(QColor(70, 190, 90, 145), 1.8, Qt.SolidLine, Qt.RoundCap))
    painter.drawArc(16, 5, 18, 22, -62 * 16, 124 * 16)
    painter.end()
    return QIcon(pix)


class TrayIcon(QSystemTrayIcon):
    """Иконка в трее с меню управления."""

    open_requested = Signal()
    volume_up_requested = Signal()
    volume_down_requested = Signal()
    quit_requested = Signal()

    def __init__(self, app_icon: QIcon, parent=None) -> None:
        super().__init__(app_icon, parent)
        self.setToolTip("SCUM Volume Control")

        menu = QMenu()

        act_open = QAction("Открыть", menu)
        act_open.triggered.connect(self.open_requested.emit)
        menu.addAction(act_open)

        menu.addSeparator()

        act_up = QAction("Громче", menu)
        act_up.triggered.connect(self.volume_up_requested.emit)
        menu.addAction(act_up)

        act_down = QAction("Тише", menu)
        act_down.triggered.connect(self.volume_down_requested.emit)
        menu.addAction(act_down)

        menu.addSeparator()

        act_quit = QAction("Выход", menu)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(act_quit)

        self._menu = menu
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()

    def set_volume_tooltip(self, process_name: str, percent: int | None) -> None:
        if percent is None:
            self.setToolTip(f"{process_name}: процесс не найден")
        else:
            self.setToolTip(f"{process_name}: {percent} %")

    def notify(self, title: str, message: str) -> None:
        try:
            self.showMessage(title, message, self.icon(), 3000)
        except Exception:
            pass
