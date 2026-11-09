from __future__ import annotations

import ctypes
import logging
import logging.handlers
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from audio import AudioController
from auto_volume import AutoVolume
from debug_session import DebugSession
from config import Config, KeyCombo, app_data_dir
from hotkeys import HotkeyManager
from main_window import MainWindow
from overlay import OverlayWidget
from resources import resource_path
from tray import TrayIcon, make_tray_icon
from windows_notifications import WindowsNotifications
from winutils import (enable_dpi_awareness, is_admin, is_autostart_enabled,
                      is_foreground_process, restart_as_admin, set_autostart)

APP_NAME = "SCUM Volume Control"


def setup_logging() -> None:
    log_file = app_data_dir() / "app.log"
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def single_instance_guard() -> bool:
    """True, если это единственный экземпляр."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "Global\\SCUMVolumeControl_Mutex")
    last_error = ctypes.get_last_error()
    ERROR_ALREADY_EXISTS = 183
    return last_error != ERROR_ALREADY_EXISTS


class Application:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.config = Config.load()
        self.audio = AudioController()
        self.hotkeys = HotkeyManager()
        self.window = MainWindow(self.config)
        self.debug_session = DebugSession(self.audio, self.config, app)
        self.debug_session.status.connect(self.window.debug_indicator.setText)
        self.debug_session.transcript.connect(self.window.debug_text.appendPlainText)
        self.debug_session.active_changed.connect(self.window.set_debug_active)
        self.window.debug_start_requested.connect(self.debug_session.start)
        self.window.debug_stop_requested.connect(self.debug_session.stop)
        self.window.debug_folder_requested.connect(self._open_debug_folder)
        self.app.aboutToQuit.connect(self.debug_session.shutdown)
        self.auto_volume = AutoVolume(self.audio, self.config, app)
        self.auto_volume.status.connect(self.window.auto_status.setText)
        self.app.aboutToQuit.connect(self._stop_auto_volume)
        self.overlay = OverlayWidget()
        self.tray = TrayIcon(make_tray_icon())
        self.windows_notifications = WindowsNotifications(self.tray.notify, app)

        self._last_percent: int | None = None
        self._last_action_ts = 0.0

        self._wire()
        self._apply_style()

    # ---------- связывание сигналов ----------

    def _wire(self) -> None:
        w = self.window
        w.config_changed.connect(self._schedule_save)
        w.hotkeys_changed.connect(self._apply_hotkeys)
        w.hotkeys_capture_begin.connect(self.hotkeys.begin_capture)
        w.test_overlay_requested.connect(self._test_overlay)
        w.process_changed.connect(self._on_process_changed)
        w.volume_slider_changed.connect(self._on_slider_volume)

        self.hotkeys.captured.connect(w.on_captured, Qt.QueuedConnection)
        self.hotkeys.volume_up.connect(self._on_volume_up, Qt.QueuedConnection)
        self.hotkeys.volume_down.connect(self._on_volume_down, Qt.QueuedConnection)

        self.tray.open_requested.connect(self._show_window)
        self.tray.volume_up_requested.connect(self._on_volume_up)
        self.tray.volume_down_requested.connect(self._on_volume_down)
        self.tray.quit_requested.connect(self.quit)
        self.tray.activated.connect(self._on_tray_activated)

    def _apply_style(self) -> None:
        qss_path = resource_path("resources", "style.qss")
        try:
            qss = Path(qss_path).read_text(encoding="utf-8")
            self.app.setStyleSheet(qss)
        except OSError:
            logging.getLogger(__name__).warning("Не найден файл стилей: %s", qss_path)

    # ---------- запуск ----------

    def start(self) -> None:
        icon = make_tray_icon()
        self.app.setWindowIcon(icon)
        self.tray.show()
        self.window.setWindowIcon(icon)

        self.config.autostart = is_autostart_enabled()
        self.window._load_from_config()

        self._apply_hotkeys()
        self.hotkeys.set_suppress(self.config.suppress_keys)
        self.hotkeys.start()

        self._poll_timer = QTimer(self.app)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll_state)
        self._poll_timer.start()

        self._focused_timer = QTimer(self.app)
        self._focused_timer.setInterval(2000)
        self._focused_timer.timeout.connect(self._refresh_focus_filter)
        self._focused_timer.start()

        start_minimized = self.config.start_minimized or "--minimized" in sys.argv
        if not start_minimized:
            self.window.show()
            self._center_window()

        self._on_process_changed(self.config.process_name)
        self._poll_state()

        if not is_admin():
            self.tray.notify(APP_NAME,
                             "Запущено без прав администратора. Если SCUM запущен "
                             "от администратора, горячие клавиши могут не работать.")

    def _center_window(self) -> None:
        screen = self.app.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        frame = self.window.frameGeometry()
        frame.moveCenter(area.center())
        self.window.move(frame.topLeft())

    # ---------- громкость ----------

    def _on_volume_up(self) -> None:
        if not self.config.hotkeys_enabled:
            return
        self._apply_delta(+self.config.step)

    def _on_volume_down(self) -> None:
        if not self.config.hotkeys_enabled:
            return
        self._apply_delta(-self.config.step)

    def _apply_delta(self, delta: int) -> None:
        import time
        now = time.monotonic()
        if now - self._last_action_ts < 0.08:
            return
        self._last_action_ts = now

        name = self.config.process_name
        if self.config.auto_volume_enabled:
            self.auto_volume.tick()
            current = self.auto_volume.base
            percent = (self.auto_volume.set_manual(current + delta)
                       if current is not None else None)
        else:
            percent = self.audio.change_volume(name, delta)
        if percent is None:
            self._show_missing(name)
            return
        self._last_percent = percent
        self.window.set_volume(percent)
        self.tray.set_volume_tooltip(name, percent)
        self._maybe_beep()
        self._show_overlay(percent)

    def _on_slider_volume(self, value: int) -> None:
        if self.config.auto_volume_enabled:
            self.auto_volume.tick()
            percent = self.auto_volume.set_manual(value)
        else:
            percent = self.audio.set_volume(self.config.process_name, value)
        if percent is not None:
            self._last_percent = percent
            self.tray.set_volume_tooltip(self.config.process_name, percent)

    # ---------- оверлей ----------

    def _show_overlay(self, percent: int, subtitle: str = "") -> None:
        if not self.config.overlay_enabled:
            return
        title = self._short_name(self.config.process_name)
        if not subtitle:
            if percent >= 100:
                subtitle = "MAX"
            elif percent <= 0:
                subtitle = "MUTE"
        if self.config.notification_mode in ("overlay", "both") and self.config.overlay_enabled:
            self.overlay.show_volume(percent, title, subtitle,
                                     self.config.overlay_position,
                                     self.config.overlay_duration_ms,
                                     self.config.process_name)
        if self.config.notification_mode in ("windows", "both"):
            text = f"{title}: {percent} %"
            if subtitle:
                text += f" — {subtitle}"
            self.windows_notifications.schedule(
                "Громкость приложения", text, self.config.notification_delay_ms)

    def _show_missing(self, name: str) -> None:
        self.window.set_volume(None)
        self.tray.set_volume_tooltip(name, None)
        if not self.config.overlay_enabled:
            return
        if self.config.notification_mode in ("overlay", "both") and self.config.overlay_enabled:
            self.overlay.show_volume(0, self._short_name(name), "аудиосессия не найдена",
                                     self.config.overlay_position,
                                     self.config.overlay_duration_ms,
                                     name)
        if self.config.notification_mode in ("windows", "both"):
            self.windows_notifications.schedule(
                "Громкость приложения", f"{name}: аудиосессия не найдена",
                self.config.notification_delay_ms)

    def _test_overlay(self) -> None:
        percent = self._last_percent if self._last_percent is not None else 50
        self._show_overlay(percent)

    def _maybe_beep(self) -> None:
        if not self.config.beep_on_change:
            return
        try:
            import winsound
            winsound.MessageBeep(0x00000040)  # MB_ICONASTERISK
        except Exception:
            pass

    # ---------- опрос состояния ----------

    def _poll_state(self) -> None:
        name = self.config.process_name
        percent = self.audio.get_volume(name)
        if percent is not None:
            ceiling = self.auto_volume.base if self.config.auto_volume_enabled else None
            self._last_percent = ceiling if ceiling is not None else percent
            self.window.set_volume(self._last_percent)
            self.tray.set_volume_tooltip(name, percent)
            self.window.set_process_status(True, f"Процесс найден, громкость {percent} %")
        else:
            self.window.set_process_status(False, "Аудиосессия не найдена — запустите игру")

    def _refresh_focus_filter(self) -> None:
        self.hotkeys.set_focus_filter(self.config.only_when_focused,
                                      self.config.process_name)

    # ---------- процесс / конфиг ----------

    def _on_process_changed(self, name: str) -> None:
        self.auto_volume.restore()
        self.hotkeys.set_focus_filter(self.config.only_when_focused, name)
        self.overlay.set_title(self._short_name(name))
        self._poll_state()

    def _apply_hotkeys(self) -> None:
        self.hotkeys.set_hotkeys(
            self.config.hotkey_up if self.config.hotkeys_enabled else KeyCombo(),
            self.config.hotkey_down if self.config.hotkeys_enabled else KeyCombo())
        self.hotkeys.set_suppress(self.config.suppress_keys)
        self.hotkeys.set_focus_filter(self.config.only_when_focused,
                                      self.config.process_name)

    def _schedule_save(self) -> None:
        self._apply_hotkeys()
        if not self.config.auto_volume_enabled:
            self.auto_volume.restore()
        self.windows_notifications.cancel()
        if not self.config.overlay_enabled or self.config.notification_mode == "windows":
            self.overlay.hide_now()
        if not hasattr(self, "_save_timer"):
            self._save_timer = QTimer(self.app)
            self._save_timer.setSingleShot(True)
            self._save_timer.setInterval(300)
            self._save_timer.timeout.connect(self.config.save)
        self._save_timer.start()

        if self.config.autostart != is_autostart_enabled():
            exe = sys.executable if getattr(sys, "frozen", False) else None
            set_autostart(self.config.autostart, exe)

    @staticmethod
    def _short_name(name: str) -> str:
        return name[:-4] if name.lower().endswith(".exe") else name

    # ---------- окно / трей ----------

    def _show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _on_tray_activated(self, reason) -> None:
        pass

    def quit(self) -> None:
        self._stop_auto_volume()
        self.windows_notifications.cancel()
        self.hotkeys.stop()
        self.overlay.hide_now()
        self._poll_timer.stop()
        self.config.save()
        self.app.quit()

    def _stop_auto_volume(self) -> None:
        self.auto_volume.timer.stop()
        self.auto_volume.restore()

    def _open_debug_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        folder = self.debug_session.folder or (app_data_dir() / "debug")
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def main() -> int:
    setup_logging()
    log = logging.getLogger(__name__)

    enable_dpi_awareness()

    if not single_instance_guard():
        log.info("Программа уже запущена")
        ctypes.windll.user32.MessageBoxW(
            None, "SCUM Volume Control уже запущена.", APP_NAME, 0x40)
        return 0

    # Разрешаем DPI-масштабирование.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    def excepthook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.error("Необработанное исключение:\n%s", text)
        QMessageBox.critical(None, "Ошибка", text)

    sys.excepthook = excepthook

    controller = Application(app)
    controller.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
