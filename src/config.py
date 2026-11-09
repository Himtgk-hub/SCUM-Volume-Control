from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_DIR_NAME = "SCUMVolumeControl"

VALID_POSITIONS = ("top-left", "top-center", "top-right",
                   "bottom-left", "bottom-center", "bottom-right", "center")
NOTIFICATION_MODES = ("overlay", "windows", "both")


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_data_dir() / "config.json"


@dataclass
class KeyCombo:
    """Горячая клавиша: virtual-key code, scan code и признак extended."""
    vk: int = 0
    scan: int = 0
    extended: bool = False
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    win: bool = False
    display: str = ""

    def is_empty(self) -> bool:
        return self.vk == 0 and self.scan == 0

    def signature(self) -> tuple:
        return (self.vk, self.scan, self.extended,
                self.ctrl, self.alt, self.shift, self.win)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "KeyCombo":
        if not data:
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def default_combo(vk: int, scan: int, extended: bool, display: str) -> KeyCombo:
    return KeyCombo(vk=vk, scan=scan, extended=extended, ctrl=True, display=display)


def _default_hotkeys() -> dict[str, KeyCombo]:
    # VK_UP/VK_DOWN: стрелки вверх/вниз.
    return {
        "hotkey_up": KeyCombo(vk=0x26, scan=0x48, display="↑ Вверх"),
        "hotkey_down": KeyCombo(vk=0x28, scan=0x50, display="↓ Вниз"),
    }


@dataclass
class Config:
    process_name: str = "SCUM"
    hotkey_up: KeyCombo = field(default_factory=lambda: _default_hotkeys()["hotkey_up"])
    hotkey_down: KeyCombo = field(default_factory=lambda: _default_hotkeys()["hotkey_down"])
    step: int = 5
    hotkeys_enabled: bool = True
    auto_volume_enabled: bool = False
    auto_threshold: int = 45
    auto_min_gain: int = 25
    auto_release_ms: int = 800
    auto_peak_target: int = 20
    auto_noise_enabled: bool = True
    auto_noise_threshold: int = 7
    auto_noise_target: int = 4
    auto_noise_volume: int = 25
    auto_noise_window_ms: int = 1500
    auto_noise_release_ms: int = 1200
    auto_algorithm_version: int = 2
    auto_impacts_enabled: bool = True
    auto_impacts_hold_ms: int = 2200
    auto_impacts_gain: int = 30
    auto_impacts_progressive: bool = True
    auto_impacts_first_gain: int = 60
    overlay_enabled: bool = True
    notification_mode: str = "overlay"
    notification_delay_ms: int = 300
    overlay_position: str = "bottom-right"
    overlay_duration_ms: int = 1000
    only_when_focused: bool = False
    suppress_keys: bool = False
    minimize_to_tray: bool = True
    start_minimized: bool = False
    autostart: bool = False
    beep_on_change: bool = False
    run_as_admin: bool = False

    # ---------- загрузка / сохранение ----------

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        if not path.exists():
            cfg = cls()
            cfg.normalize()
            return cfg
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Не удалось прочитать конфиг (%s), беру значения по умолчанию", exc)
            cfg = cls()
            cfg.normalize()
            return cfg

        cfg = cls()
        for f in fields(cls):
            if f.name not in raw:
                continue
            value = raw[f.name]
            if f.name in ("hotkey_up", "hotkey_down"):
                setattr(cfg, f.name, KeyCombo.from_dict(value))
            elif f.name in ("step", "overlay_duration_ms", "notification_delay_ms",
                            "auto_threshold", "auto_min_gain", "auto_release_ms",
                            "auto_peak_target", "auto_noise_threshold", "auto_noise_target", "auto_noise_volume",
                            "auto_noise_window_ms", "auto_noise_release_ms", "auto_algorithm_version",
                            "auto_impacts_hold_ms", "auto_impacts_gain", "auto_impacts_first_gain"):
                try:
                    setattr(cfg, f.name, int(value))
                except (TypeError, ValueError):
                    pass
            elif f.name in ("overlay_enabled", "hotkeys_enabled", "only_when_focused", "suppress_keys", "auto_volume_enabled", "auto_noise_enabled", "auto_impacts_enabled", "auto_impacts_progressive",
                            "minimize_to_tray", "start_minimized", "autostart",
                            "beep_on_change", "run_as_admin"):
                setattr(cfg, f.name, bool(value))
            elif f.name == "process_name" and isinstance(value, str):
                cfg.process_name = value
            elif f.name in ("overlay_position", "notification_mode") and isinstance(value, str):
                if f.name == "overlay_position":
                    cfg.overlay_position = value
                else:
                    cfg.notification_mode = value
        if "auto_algorithm_version" not in raw:
            # New branch semantics require new starting points; other preferences stay.
            cfg.auto_threshold = 45
            cfg.auto_peak_target = 20
            cfg.auto_release_ms = 800
        cfg.auto_algorithm_version = 2
        cfg.normalize()
        return cfg

    def normalize(self) -> None:
        self.process_name = (self.process_name or "SCUM").strip() or "SCUM"
        self.step = max(1, min(50, int(self.step)))
        self.auto_threshold = max(5, min(100, int(self.auto_threshold)))
        self.auto_min_gain = max(5, min(100, int(self.auto_min_gain)))
        self.auto_release_ms = max(500, min(10000, int(self.auto_release_ms)))
        for key in ("auto_peak_target", "auto_noise_threshold", "auto_noise_target"):
            setattr(self, key, max(1, min(100, int(getattr(self, key)))))
        self.auto_peak_target = min(self.auto_peak_target, self.auto_threshold)
        self.auto_noise_target = min(self.auto_noise_target, self.auto_noise_threshold)
        self.auto_noise_volume = max(5, min(100, int(self.auto_noise_volume)))
        self.auto_noise_window_ms = max(500, min(5000, int(self.auto_noise_window_ms)))
        self.auto_noise_release_ms = max(500, min(10000, int(self.auto_noise_release_ms)))
        self.auto_impacts_hold_ms = max(500, min(4000, int(self.auto_impacts_hold_ms)))
        self.auto_impacts_gain = max(5, min(100, int(self.auto_impacts_gain)))
        self.auto_impacts_first_gain = max(self.auto_impacts_gain, min(100, int(self.auto_impacts_first_gain)))
        self.overlay_duration_ms = max(200, min(10000, int(self.overlay_duration_ms)))
        self.notification_delay_ms = max(0, min(5000, int(self.notification_delay_ms)))
        if self.overlay_position not in VALID_POSITIONS:
            self.overlay_position = "bottom-right"
        if self.notification_mode not in NOTIFICATION_MODES:
            self.notification_mode = "overlay" if self.overlay_enabled else "windows"

        # Миграция ранней версии, где по умолчанию использовались
        # Ctrl+Numpad +/-.
        if (self.hotkey_up.vk, self.hotkey_up.scan, self.hotkey_up.ctrl) == (0x6B, 0x4E, True):
            self.hotkey_up = _default_hotkeys()["hotkey_up"]
        if (self.hotkey_down.vk, self.hotkey_down.scan, self.hotkey_down.ctrl) == (0x6D, 0x4A, True):
            self.hotkey_down = _default_hotkeys()["hotkey_down"]

    def save(self) -> None:
        self.normalize()
        data = asdict(self)
        path = config_path()
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log.error("Не удалось сохранить конфиг: %s", exc)
