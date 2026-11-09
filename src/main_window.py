from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal, QUrl, QRect
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget, QTabWidget, QPlainTextEdit, QScrollArea)

from config import NOTIFICATION_MODES, VALID_POSITIONS, Config, KeyCombo
from hotkey_edit import HotkeyEdit
from resources import resource_path

log = logging.getLogger(__name__)

POSITION_LABELS = {
    "top-left": "Сверху слева",
    "top-center": "Сверху по центру",
    "top-right": "Сверху справа",
    "bottom-left": "Снизу слева",
    "bottom-center": "Снизу по центру",
    "bottom-right": "Снизу справа",
    "center": "По центру",
}

NOTIFICATION_LABELS = {
    "overlay": "Оверлей поверх игры",
    "windows": "Уведомление Windows",
    "both": "Оба варианта",
}


class MainWindow(QWidget):
    config_changed = Signal()
    hotkeys_changed = Signal()
    test_overlay_requested = Signal()
    process_changed = Signal(str)
    volume_slider_changed = Signal(int)
    hotkeys_capture_begin = Signal()
    debug_start_requested = Signal(object)
    debug_stop_requested = Signal()
    debug_folder_requested = Signal()

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._capturing_field: HotkeyEdit | None = None
        self._suspend_signals = False

        self.setWindowTitle("SCUM Volume Control · HIMTGK")
        self.resize(720, 760)
        self.setMinimumSize(640, 640)

        self._build_ui()
        self._load_from_config()

    # ---------- построение UI ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        brand = QLabel("SCUM Volume Control · HIMTGK")
        brand.setObjectName("brand")
        brand.setAlignment(Qt.AlignCenter)
        root.addWidget(brand)

        root.addWidget(self._build_process_group())
        root.addWidget(self._build_volume_group())
        tabs = self.tabs = QTabWidget()
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.addWidget(self._build_hotkeys_group())
        controls_layout.addStretch(1)
        auto_page = QWidget()
        auto_layout = QVBoxLayout(auto_page)
        auto_layout.addWidget(self._build_auto_group())
        auto_layout.addStretch(1)
        auto_scroll = QScrollArea()
        auto_scroll.setWidgetResizable(True)
        auto_scroll.setWidget(auto_page)
        notifications = QWidget()
        notifications_layout = QVBoxLayout(notifications)
        notifications_layout.addWidget(self._build_overlay_group())
        notifications_layout.addWidget(self._build_options_group())
        notifications_layout.addStretch(1)
        notifications_scroll = QScrollArea()
        notifications_scroll.setWidgetResizable(True)
        notifications_scroll.setWidget(notifications)
        tabs.addTab(controls, "Управление звуком")
        tabs.addTab(auto_scroll, "Автозвук")
        tabs.addTab(notifications_scroll, "Настройки")
        tabs.addTab(self._build_creator_tab(), "Об авторе")
        self.debug_page = self._build_debug_tab()
        self.debug_page.setParent(self)
        self.debug_page.hide()
        root.addWidget(tabs)
        footer = QLabel("Настройки сохраняются автоматически · Общая громкость Windows не меняется")
        footer.setObjectName("hint")
        footer.setWordWrap(True)
        root.addWidget(footer)

    def _build_creator_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        self.creator_logo = QLabel()
        self.creator_logo.setObjectName("creatorLogo")
        self.creator_logo.setAlignment(Qt.AlignCenter)
        logo = QPixmap(resource_path("resources", "himtgk-logo.png"))
        if not logo.isNull():
            # Transparent source artwork may include large empty margins.
            image = logo.toImage()
            bounds = QRect()
            for y in range(image.height()):
                for x in range(image.width()):
                    if image.pixelColor(x, y).alpha() > 20:
                        bounds = bounds.united(QRect(x, y, 1, 1))
            if not bounds.isNull():
                logo = logo.copy(bounds)
            self.creator_logo.setPixmap(logo.scaled(320, 160, Qt.KeepAspectRatio,
                                                   Qt.SmoothTransformation))
        else:
            self.creator_logo.setText("HIMTGK")
        self.creator_logo.setMinimumHeight(100)
        layout.addWidget(self.creator_logo)
        title = QLabel("Создатель проекта — HIMTGK")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        description = QLabel("SCUM Volume Control — управление громкостью игры и автоматическое "
                             "приглушение резких и постоянных звуков.\n"
                             "Следите за автором и поддержите развитие проекта, если программа вам полезна.")
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignCenter)
        layout.addWidget(description)
        self.creator_buttons = {}
        for name, url in (
            ("Поддержать автора · Boosty", "https://boosty.to/himtgk/donate"),
            ("Boosty · страница автора", "https://boosty.to/himtgk"),
            ("Telegram", "https://t.me/himtgk"),
            ("Discord", "https://discord.gg/PqhsFHCpf8"),
            ("VKLIVE", "https://vkplay.live/himtgk"),
            ("Twitch", "https://www.twitch.tv/himtgk"),
            ("YouTube", "https://www.youtube.com/@HIMTGK"),
        ):
            button = QPushButton(name)
            button.setToolTip(url + " — открыть в браузере")
            button.setProperty("destination", url)
            button.clicked.connect(lambda checked=False, target=url: self._open_creator_link(target))
            self.creator_buttons[name] = button
            layout.addWidget(button)
        self.creator_buttons["Поддержать автора · Boosty"].setObjectName("supportAuthor")
        note = QLabel("Поддержка добровольная. Ссылки открываются во внешнем браузере; "
                      "программа не принимает платежи и не запрашивает платёжные данные.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _open_creator_link(self, url):
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, "Не удалось открыть браузер", "Откройте ссылку вручную:\n" + url)

    def _show_debug(self):
        index = self.tabs.indexOf(self.debug_page)
        if index < 0:
            index = self.tabs.addTab(self.debug_page, "Диагностика")
            self._refresh_microphones()
        self.tabs.setCurrentIndex(index)
        self.debug_page.show()

    def _build_debug_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("По кнопке «Начать отладку» включается микрофон и запись уровня SCUM. "
                      "Говори, что делаешь: «еду на машине», «стреляю», «слишком громко». "
                      "Речь распознаётся локально; сохраняются только текст и уровни, "
                      "без аудиозаписи. При первом запуске скачивается русская модель (~45 МБ). "
                      "Распознавание может ошибаться. Логи могут содержать личные слова — проверь их перед отправкой.")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(QLabel("Микрофон"))
        self.debug_device = QComboBox()
        self.debug_device.addItem("По умолчанию в Windows", None)
        row.addWidget(self.debug_device, 1)
        self.debug_refresh = QPushButton("Обновить")
        self.debug_refresh.clicked.connect(self._refresh_microphones)
        row.addWidget(self.debug_refresh)
        layout.addLayout(row)
        buttons = QHBoxLayout()
        self.debug_start = QPushButton("Начать отладку")
        self.debug_start.clicked.connect(
            lambda: self.debug_start_requested.emit(self.debug_device.currentData()))
        self.debug_stop = QPushButton("Остановить отладку")
        self.debug_stop.setEnabled(False)
        self.debug_stop.clicked.connect(self.debug_stop_requested.emit)
        self.debug_folder = QPushButton("Открыть логи")
        self.debug_folder.clicked.connect(self.debug_folder_requested.emit)
        debug_hide = QPushButton("Скрыть диагностику")
        debug_hide.setToolTip("Скрывает вкладку, но не останавливает активную запись.")
        debug_hide.clicked.connect(self._hide_debug)
        buttons.addWidget(self.debug_start)
        buttons.addWidget(self.debug_stop)
        buttons.addWidget(self.debug_folder)
        layout.addLayout(buttons)
        layout.addWidget(debug_hide)
        self.debug_indicator = QLabel("Микрофон выключен")
        self.debug_indicator.setWordWrap(True)
        layout.addWidget(self.debug_indicator)
        self.debug_text = QPlainTextEdit()
        self.debug_text.setReadOnly(True)
        self.debug_text.setMaximumBlockCount(1000)
        layout.addWidget(self.debug_text, 1)
        return page

    def _hide_debug(self):
        if self.debug_stop.isEnabled():
            QMessageBox.information(self, "Идёт запись", "Сначала остановите отладку, чтобы не оставить микрофон включённым.")
            return
        self.tabs.removeTab(self.tabs.indexOf(self.debug_page))
        self.debug_page.hide()

    def _refresh_microphones(self):
        try:
            import sounddevice as sd
            self.debug_device.clear()
            self.debug_device.addItem("По умолчанию в Windows", None)
            for index, device in enumerate(sd.query_devices()):
                if device["max_input_channels"] > 0:
                    self.debug_device.addItem(f"{index}: {device['name']}", index)
        except Exception as exc:
            log.warning("Не удалось перечислить микрофоны: %s", exc)

    def set_debug_active(self, active):
        self.debug_start.setEnabled(not active)
        self.debug_stop.setEnabled(active)
        self.debug_device.setEnabled(not active)
        self.debug_refresh.setEnabled(not active)

    def _build_auto_group(self) -> QGroupBox:
        box = QGroupBox("Автоматическое управление громкостью")
        layout = QVBoxLayout(box)
        self.auto_check = QCheckBox("Автозвук — включён")
        self.auto_check.toggled.connect(self._on_config_field_changed)
        layout.addWidget(self.auto_check)
        auto_note = QLabel("Общий выключатель всех автоматических режимов. При отключении "
                           "возвращается ручная громкость; горячие клавиши работают независимо.")
        auto_note.setWordWrap(True)
        auto_note.setObjectName("hint")
        layout.addWidget(auto_note)
        self.auto_settings = QWidget()
        layout.addWidget(self.auto_settings)
        self.auto_check.toggled.connect(self.auto_settings.setEnabled)
        status_layout = layout
        layout = QVBoxLayout(self.auto_settings)
        layout.setContentsMargins(0, 0, 0, 0)
        peak_details = QWidget()
        peak_layout = QVBoxLayout(peak_details)
        peak_layout.setContentsMargins(0, 0, 0, 0)
        peak_toggle = QCheckBox("Настройки резких звуков и общего минимума")
        peak_toggle.toggled.connect(peak_details.setVisible)
        layout.addWidget(peak_toggle)
        layout.addWidget(peak_details)
        peak_details.hide()
        self.auto_threshold_spin = QSpinBox()
        self.auto_threshold_spin.setRange(5, 100)
        self.auto_threshold_spin.setSuffix(" %")
        self.auto_threshold_spin.setToolTip("При превышении этого уровня срабатывает ветка резких звуков.")
        self.auto_min_gain_spin = QSpinBox()
        self.auto_min_gain_spin.setRange(5, 100)
        self.auto_min_gain_spin.setSuffix(" %")
        self.auto_min_gain_spin.setToolTip("Нижний предел относительно ручной громкости: 25 % позволяет уменьшать её максимум в четыре раза.")
        self.auto_release_spin = QSpinBox()
        self.auto_release_spin.setRange(500, 10000)
        self.auto_release_spin.setSingleStep(100)
        self.auto_release_spin.setSuffix(" мс")
        self.auto_peak_target_spin = QSpinBox()
        self.auto_peak_target_spin.setRange(1, 100)
        self.auto_peak_target_spin.setSuffix(" %")
        for title, spin in (
            ("Пики: порог срабатывания", self.auto_threshold_spin),
            ("Пики: целевой уровень", self.auto_peak_target_spin),
            ("Не опускать звук ниже (доля ручной)", self.auto_min_gain_spin),
            ("Вернуть звук после пика за", self.auto_release_spin),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(title), 1)
            spin.setMinimumWidth(115)
            spin.valueChanged.connect(self._on_config_field_changed)
            row.addWidget(spin)
            peak_layout.addLayout(row)
        self.auto_noise_check = QCheckBox("Делать транспорт и постоянный шум тише")
        self.auto_noise_check.toggled.connect(self._on_config_field_changed)
        layout.addWidget(self.auto_noise_check)
        layout.addWidget(QLabel("Громкость транспорта и постоянного шума"))
        noise_row = QHBoxLayout()
        self.noise_volume_spin = QSpinBox()
        self.noise_volume_spin.setRange(5, 100)
        self.noise_volume_spin.setSuffix(" %")
        self.noise_volume_spin.valueChanged.connect(self._on_config_field_changed)
        noise_row.addWidget(QLabel("Оставлять от ручной громкости"), 1)
        noise_row.addWidget(self.noise_volume_spin)
        layout.addLayout(noise_row)
        noise_hint = QLabel("Меньше число — тише транспорт. Например: 25 % оставляет четверть "
                            "ручной громкости; при ручных 80 % это до 20 % в микшере. "
                            "Может быть ещё тише из-за пиков; общий минимум ограничивает снижение. "
                            "Это обнаружение постоянного шума, не распознавание машины.")
        noise_hint.setWordWrap(True)
        noise_hint.setObjectName("hint")
        layout.addWidget(noise_hint)
        noise_details = QWidget()
        noise_details_layout = QVBoxLayout(noise_details)
        noise_details_layout.setContentsMargins(0, 0, 0, 0)
        noise_toggle = QCheckBox("Дополнительные настройки обнаружения шума")
        noise_toggle.toggled.connect(noise_details.setVisible)
        layout.addWidget(noise_toggle)
        layout.addWidget(noise_details)
        noise_details.hide()
        self.noise_spins = {}
        for key, title, low, high, suffix in (
            ("auto_noise_threshold", "Считать шумом при уровне выше", 1, 100, " %"),
            ("auto_noise_target", "Дополнительное ограничение уровня", 1, 100, " %"),
            ("auto_noise_window_ms", "Как долго проверять постоянство", 500, 5000, " мс"),
            ("auto_noise_release_ms", "Как быстро вернуть обычный звук", 500, 10000, " мс"),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(title), 1)
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setSuffix(suffix)
            spin.setMinimumWidth(115)
            if suffix == " мс":
                spin.setSingleStep(100)
            spin.valueChanged.connect(self._on_config_field_changed)
            self.noise_spins[key] = spin
            row.addWidget(spin)
            noise_details_layout.addLayout(row)
        self.impacts_check = QCheckBox("Приглушать повторяющиеся удары")
        self.impacts_check.setToolTip("Удержание после коротких раздельных импульсов. Число зависит от ступенчатого режима. Может реагировать на стрельбу и контейнеры; это не распознавание рубки.")
        self.impacts_check.toggled.connect(self._on_config_field_changed)
        layout.addWidget(self.impacts_check)
        self.progressive_check = QCheckBox("Ступенчатое приглушение с первого удара")
        self.progressive_check.setToolTip(
            "После окончания первого импульса удерживается первая ступень, после второго — основная. "
            "При отключении удержание начинается после трёх импульсов. "
            "Первый звук не перехватывается заранее; одиночные выстрелы тоже могут вызвать удержание.")
        self.progressive_check.toggled.connect(self._on_config_field_changed)
        layout.addWidget(self.progressive_check)
        self.impact_spins = {}
        for key, title, low, high, suffix in (
            ("auto_impacts_first_gain", "После первого удара: оставить", 5, 100, " %"),
            ("auto_impacts_hold_ms", "Ждать следующий удар", 500, 4000, " мс"),
            ("auto_impacts_gain", "При повторных ударах: оставить", 5, 100, " %"),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(title), 1)
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setSuffix(suffix)
            spin.setMinimumWidth(115)
            if suffix == " мс":
                spin.setSingleStep(100)
            spin.valueChanged.connect(self._on_config_field_changed)
            self.impact_spins[key] = spin
            row.addWidget(spin)
            layout.addLayout(row)
        self.auto_status = QLabel("Автоприглушение выключено")
        self.auto_status.setWordWrap(True)
        status_layout.addWidget(self.auto_status)
        hint = QLabel("Ручная громкость и клавиши задают верхний предел. Тишина не усиливается. "
                      "Возврат линейный: не дольше указанного времени после окончания события "
                      "(для шума сначала очищается окно). Проценты — не RMS/LUFS. "
                      "Первый резкий звук может успеть прозвучать.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return box

    def _build_process_group(self) -> QGroupBox:
        box = QGroupBox("Процесс")
        lay = QVBoxLayout(box)

        row = QHBoxLayout()
        self.process_edit = QLineEdit()
        self.process_edit.setPlaceholderText("SCUM.exe")
        self.process_edit.editingFinished.connect(self._on_process_edited)
        row.addWidget(self.process_edit, 1)

        self.pick_button = QPushButton("Выбрать…")
        self.pick_button.clicked.connect(self._on_pick_process)
        row.addWidget(self.pick_button)
        lay.addLayout(row)

        self.process_status = QLabel("Проверка…")
        self.process_status.setObjectName("status")
        lay.addWidget(self.process_status)
        return box

    def _build_volume_group(self) -> QGroupBox:
        box = QGroupBox("Ручная громкость — верхний предел для автозвука")
        lay = QVBoxLayout(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Громкость приложения"), 1)
        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(0, 100)
        self.volume_spin.setSuffix(" %")
        self.volume_spin.setMinimumWidth(115)
        self.volume_spin.setKeyboardTracking(False)
        self.volume_spin.valueChanged.connect(self._on_slider_changed)
        row.addWidget(self.volume_spin)

        self.volume_label = QLabel("— %")
        self.volume_label.setFixedWidth(56)
        self.volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.volume_label)
        lay.addLayout(row)
        return box

    def _build_hotkeys_group(self) -> QGroupBox:
        box = QGroupBox("Горячие клавиши")
        lay = QVBoxLayout(box)
        self.hotkeys_check = QCheckBox("Управление горячими клавишами — включено")
        self.hotkeys_check.toggled.connect(self._on_config_field_changed)
        lay.addWidget(self.hotkeys_check)
        help_text = QLabel("Отключает только горячие клавиши. Ручное изменение громкости "
                           "и автозвук остаются доступны.")
        help_text.setObjectName("hint")
        help_text.setWordWrap(True)
        lay.addWidget(help_text)

        # Громче
        up_row = QHBoxLayout()
        up_label = QLabel("Громче")
        up_label.setFixedWidth(64)
        up_row.addWidget(up_label)
        self.hotkey_up_edit = HotkeyEdit()
        self.hotkey_up_edit.capture_started.connect(lambda: self._begin_capture(self.hotkey_up_edit))
        up_row.addWidget(self.hotkey_up_edit, 1)
        self.hotkey_up_button = QPushButton("Назначить")
        self.hotkey_up_button.clicked.connect(lambda: self._begin_capture(self.hotkey_up_edit))
        up_row.addWidget(self.hotkey_up_button)
        lay.addLayout(up_row)

        # Тише
        down_row = QHBoxLayout()
        down_label = QLabel("Тише")
        down_label.setFixedWidth(64)
        down_row.addWidget(down_label)
        self.hotkey_down_edit = HotkeyEdit()
        self.hotkey_down_edit.capture_started.connect(lambda: self._begin_capture(self.hotkey_down_edit))
        down_row.addWidget(self.hotkey_down_edit, 1)
        self.hotkey_down_button = QPushButton("Назначить")
        self.hotkey_down_button.clicked.connect(lambda: self._begin_capture(self.hotkey_down_edit))
        down_row.addWidget(self.hotkey_down_button)
        lay.addLayout(down_row)

        # Шаг
        step_row = QHBoxLayout()
        step_label = QLabel("Шаг, %")
        step_label.setFixedWidth(64)
        step_row.addWidget(step_label)
        self.step_spin = QSpinBox()
        self.step_spin.setRange(1, 50)
        self.step_spin.setMinimumWidth(90)
        self.step_spin.valueChanged.connect(self._on_step_changed)
        step_row.addWidget(self.step_spin)
        step_row.addStretch(1)
        lay.addLayout(step_row)

        self.focus_only_check = QCheckBox("Только когда целевой процесс в фокусе")
        self.focus_only_check.toggled.connect(self._on_config_field_changed)
        lay.addWidget(self.focus_only_check)

        self.suppress_check = QCheckBox("Перехватывать клавишу (не передавать игре)")
        self.suppress_check.toggled.connect(self._on_config_field_changed)
        lay.addWidget(self.suppress_check)

        hint = QLabel("Esc при записи — отмена, Backspace/Delete — очистить.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return box

    def _build_overlay_group(self) -> QGroupBox:
        box = QGroupBox("Уведомление поверх игры")
        lay = QVBoxLayout(box)

        self.overlay_check = QCheckBox("Показывать уведомление")
        self.overlay_check.toggled.connect(self._on_config_field_changed)
        lay.addWidget(self.overlay_check)

        mode_row = QHBoxLayout()
        mode_label = QLabel("Тип")
        mode_label.setFixedWidth(64)
        mode_row.addWidget(mode_label)
        self.notification_mode_combo = QComboBox()
        for mode in NOTIFICATION_MODES:
            self.notification_mode_combo.addItem(NOTIFICATION_LABELS[mode], mode)
        self.notification_mode_combo.currentIndexChanged.connect(self._on_config_field_changed)
        mode_row.addWidget(self.notification_mode_combo, 1)
        lay.addLayout(mode_row)

        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("Задержка Windows после нажатий"))
        self.notification_delay_spin = QSpinBox()
        self.notification_delay_spin.setRange(0, 5000)
        self.notification_delay_spin.setSingleStep(100)
        self.notification_delay_spin.setSuffix(" мс")
        self.notification_delay_spin.setMinimumWidth(110)
        self.notification_delay_spin.setToolTip(
            "Показать последнее значение после паузы в нажатиях. "
            "0 — показывать сразу при каждом нажатии.")
        self.notification_delay_spin.valueChanged.connect(self._on_config_field_changed)
        delay_row.addWidget(self.notification_delay_spin)
        lay.addLayout(delay_row)

        pos_row = QHBoxLayout()
        position_label = QLabel("Позиция")
        position_label.setFixedWidth(64)
        pos_row.addWidget(position_label)
        self.position_combo = QComboBox()
        for key in VALID_POSITIONS:
            self.position_combo.addItem(POSITION_LABELS.get(key, key), key)
        self.position_combo.currentIndexChanged.connect(self._on_config_field_changed)
        pos_row.addWidget(self.position_combo, 1)

        duration_label = QLabel("Время, мс")
        duration_label.setFixedWidth(68)
        pos_row.addWidget(duration_label)
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(200, 10000)
        self.duration_spin.setSingleStep(100)
        self.duration_spin.setMinimumWidth(90)
        self.duration_spin.valueChanged.connect(self._on_config_field_changed)
        pos_row.addWidget(self.duration_spin)
        lay.addLayout(pos_row)

        test_row = QHBoxLayout()
        self.test_button = QPushButton("Тест")
        self.test_button.clicked.connect(self.test_overlay_requested.emit)
        test_row.addWidget(self.test_button)
        test_row.addStretch(1)
        lay.addLayout(test_row)

        note = QLabel("Позиция и время относятся только к оверлею (окно или Borderless). "
                      "Время показа уведомлений Windows задаёт система. "
                      "Режим «Не беспокоить» и полноэкранная игра могут скрывать их.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        return box

    def _build_options_group(self) -> QGroupBox:
        box = QGroupBox("Прочее")
        lay = QVBoxLayout(box)

        self.tray_check = QCheckBox("Сворачивать в трей при закрытии")
        self.tray_check.toggled.connect(self._on_config_field_changed)
        lay.addWidget(self.tray_check)

        self.start_min_check = QCheckBox("Запускать свёрнутым")
        self.start_min_check.toggled.connect(self._on_config_field_changed)
        lay.addWidget(self.start_min_check)

        self.autostart_check = QCheckBox("Запускать вместе с Windows")
        self.autostart_check.toggled.connect(self._on_config_field_changed)
        lay.addWidget(self.autostart_check)

        self.beep_check = QCheckBox("Звуковой сигнал при изменении")
        self.beep_check.toggled.connect(self._on_config_field_changed)
        lay.addWidget(self.beep_check)
        debug_button = QPushButton("Диагностика…")
        debug_button.setToolTip("Дополнительный инструмент для записи тестов. Сам по себе не включает микрофон.")
        debug_button.clicked.connect(self._show_debug)
        lay.addWidget(debug_button)
        return box

    # ---------- загрузка значений ----------

    def _load_from_config(self) -> None:
        cfg = self._config
        self._suspend_signals = True
        self.process_edit.setText(cfg.process_name)
        self.hotkey_up_edit.apply_combo(cfg.hotkey_up)
        self.hotkey_down_edit.apply_combo(cfg.hotkey_down)
        self.step_spin.setValue(cfg.step)
        self.hotkeys_check.setChecked(cfg.hotkeys_enabled)
        self.auto_check.setChecked(cfg.auto_volume_enabled)
        self.auto_settings.setEnabled(cfg.auto_volume_enabled)
        self.auto_threshold_spin.setValue(cfg.auto_threshold)
        self.auto_min_gain_spin.setValue(cfg.auto_min_gain)
        self.auto_release_spin.setValue(cfg.auto_release_ms)
        self.auto_peak_target_spin.setValue(cfg.auto_peak_target)
        self.auto_noise_check.setChecked(cfg.auto_noise_enabled)
        self.noise_volume_spin.setValue(cfg.auto_noise_volume)
        for key, spin in self.noise_spins.items():
            spin.setValue(getattr(cfg, key))
        self.impacts_check.setChecked(cfg.auto_impacts_enabled)
        self.progressive_check.setChecked(cfg.auto_impacts_progressive)
        for key, spin in self.impact_spins.items():
            spin.setValue(getattr(cfg, key))
        self.focus_only_check.setChecked(cfg.only_when_focused)
        self.suppress_check.setChecked(cfg.suppress_keys)
        self.overlay_check.setChecked(cfg.overlay_enabled)
        mode_idx = self.notification_mode_combo.findData(cfg.notification_mode)
        self.notification_mode_combo.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)
        self.notification_delay_spin.setValue(cfg.notification_delay_ms)
        idx = self.position_combo.findData(cfg.overlay_position)
        self.position_combo.setCurrentIndex(idx if idx >= 0 else self.position_combo.count() - 1)
        self.duration_spin.setValue(cfg.overlay_duration_ms)
        self.tray_check.setChecked(cfg.minimize_to_tray)
        self.start_min_check.setChecked(cfg.start_minimized)
        self.autostart_check.setChecked(cfg.autostart)
        self.beep_check.setChecked(cfg.beep_on_change)
        self._suspend_signals = False
        self._update_switch_labels()

    def _update_switch_labels(self):
        self.auto_check.setText("Автозвук — " + ("включён" if self.auto_check.isChecked() else "выключен"))
        self.hotkeys_check.setText("Горячие клавиши — " + ("включены" if self.hotkeys_check.isChecked() else "выключены"))
        for control in (self.hotkey_up_edit, self.hotkey_down_edit,
                        self.hotkey_up_button, self.hotkey_down_button, self.step_spin,
                        self.focus_only_check, self.suppress_check):
            control.setEnabled(self.hotkeys_check.isChecked())

    # ---------- обновление состояния извне ----------

    def set_volume(self, percent: int | None) -> None:
        self._suspend_signals = True
        if percent is None:
            self.volume_label.setText("— %")
        else:
            if not self.volume_spin.hasFocus():
                self.volume_spin.setValue(percent)
            self.volume_label.setText(f"{percent} %")
        self._suspend_signals = False

    def set_process_status(self, available: bool, text: str) -> None:
        self.process_status.setText(text)
        self.process_status.setProperty("available", available)
        self.process_status.style().unpolish(self.process_status)
        self.process_status.style().polish(self.process_status)

    def set_process_list(self, names: list[str]) -> None:
        self._process_names = names

    def show_warning(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    # ---------- обработчики ----------

    def _on_process_edited(self) -> None:
        if self._suspend_signals:
            return
        name = self.process_edit.text().strip() or "SCUM"
        self._config.process_name = name
        self.process_changed.emit(name)
        self.config_changed.emit()

    def _on_pick_process(self) -> None:
        names = getattr(self, "_process_names", [])
        if not names:
            self.show_warning("Процессы не найдены",
                              "Сейчас нет приложений с активным звуком. "
                              "Запустите звук в нужном приложении и попробуйте снова.")
            return
        self._process_choice = _ProcessChooser(names, self)
        self._process_choice.chosen.connect(self._apply_picked_process)
        self._process_choice.popup()

    def _apply_picked_process(self, name: str) -> None:
        self.process_edit.setText(name)
        self._on_process_edited()

    def _on_slider_changed(self, value: int) -> None:
        if self._suspend_signals:
            return
        self.volume_label.setText(f"{value} %")
        self._config_changed_from_slider(value)

    def _config_changed_from_slider(self, value: int) -> None:
        self.volume_slider_changed.emit(value)

    def _on_step_changed(self, value: int) -> None:
        if self._suspend_signals:
            return
        self._config.step = value
        self.config_changed.emit()

    def _on_config_field_changed(self) -> None:
        if self._suspend_signals:
            return
        cfg = self._config
        cfg.hotkeys_enabled = self.hotkeys_check.isChecked()
        self._update_switch_labels()
        cfg.auto_volume_enabled = self.auto_check.isChecked()
        cfg.auto_threshold = self.auto_threshold_spin.value()
        cfg.auto_min_gain = self.auto_min_gain_spin.value()
        cfg.auto_release_ms = self.auto_release_spin.value()
        cfg.auto_peak_target = self.auto_peak_target_spin.value()
        cfg.auto_noise_enabled = self.auto_noise_check.isChecked()
        cfg.auto_noise_volume = self.noise_volume_spin.value()
        for key, spin in self.noise_spins.items():
            setattr(cfg, key, spin.value())
        cfg.auto_impacts_enabled = self.impacts_check.isChecked()
        cfg.auto_impacts_progressive = self.progressive_check.isChecked()
        for key, spin in self.impact_spins.items():
            setattr(cfg, key, spin.value())
        cfg.normalize()
        cfg.only_when_focused = self.focus_only_check.isChecked()
        cfg.suppress_keys = self.suppress_check.isChecked()
        cfg.overlay_enabled = self.overlay_check.isChecked()
        cfg.notification_mode = self.notification_mode_combo.currentData()
        cfg.notification_delay_ms = self.notification_delay_spin.value()
        cfg.overlay_position = self.position_combo.currentData()
        cfg.overlay_duration_ms = self.duration_spin.value()
        cfg.minimize_to_tray = self.tray_check.isChecked()
        cfg.start_minimized = self.start_min_check.isChecked()
        cfg.autostart = self.autostart_check.isChecked()
        cfg.beep_on_change = self.beep_check.isChecked()
        self.config_changed.emit()

    # ---------- запись горячих клавиш ----------

    def _begin_capture(self, field: HotkeyEdit) -> None:
        if self._capturing_field is field and field.is_capturing():
            return
        self._cancel_capture()
        self._capturing_field = field
        field.start_capture()
        self.hotkeys_capture_begin.emit()

    def _cancel_capture(self) -> None:
        if self._capturing_field is not None:
            self._capturing_field.cancel_capture()
            self._capturing_field = None

    def on_captured(self, combo: KeyCombo) -> None:
        field = self._capturing_field
        self._capturing_field = None
        if field is None:
            return
        if combo.is_empty():
            field.apply_combo(KeyCombo())
            self._store_combo(field, KeyCombo())
            return
        # Проверка на дубликат
        other = self.hotkey_down_edit if field is self.hotkey_up_edit else self.hotkey_up_edit
        other_combo = (self._config.hotkey_down if field is self.hotkey_up_edit
                       else self._config.hotkey_up)
        if not other_combo.is_empty() and other_combo.signature() == combo.signature():
            self.show_warning("Клавиша уже занята",
                              "Эта комбинация уже назначена на другую функцию.")
            field.apply_combo(other_combo if field is other else KeyCombo())
            self._load_from_config()
            return
        field.apply_combo(combo)
        self._store_combo(field, combo)

    def _store_combo(self, field: HotkeyEdit, combo: KeyCombo) -> None:
        if field is self.hotkey_up_edit:
            self._config.hotkey_up = combo
        else:
            self._config.hotkey_down = combo
        self.hotkeys_changed.emit()
        self.config_changed.emit()


class _SignalProxy:
    """Мини-сигнал для выпадающего списка процессов."""

    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, value) -> None:
        for cb in list(self._callbacks):
            cb(value)


class _ProcessChooser:
    """Выпадающий список процессов, у которых есть аудиосессия."""

    def __init__(self, names, parent) -> None:
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu

        self.chosen = _SignalProxy()
        self.menu = QMenu(parent)
        for name in names:
            act = self.menu.addAction(name)
            act.triggered.connect(lambda checked=False, n=name: self.chosen.emit(n))
        self._cursor_pos = QCursor.pos()

    def popup(self) -> None:
        self.menu.popup(self._cursor_pos)
