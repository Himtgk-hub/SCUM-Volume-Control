"""Conservative peak-driven attenuation, not a look-ahead audio limiter."""
from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal


class DualEnvelope:
    """Two peak-envelope branches; does not classify vehicles or measure LUFS."""

    def __init__(self):
        self.peak_gain = self.noise_gain = 1.0
        self.noise_active = False
        self.history = deque()
        self.clock = 0.0
        self.noise_level = 0.0
        self.impact_gain = 1.0
        self.impact_start = None
        self.impact_last_end = None
        self.impact_count = 0
        self.impact_until = 0.0
        self.impact_low_time = 0.0
        self.impact_rejected = False
        self.impact_mode = None

    def _impacts(self, level, dt, floor, cfg):
        # Count short, separated impulses, not samples of one long gun burst.
        # This is an envelope heuristic, not recognition of an axe or weapon.
        mode = (cfg.auto_impacts_enabled, cfg.auto_impacts_progressive)
        changed = self.impact_mode != mode
        self.impact_mode = mode
        if not cfg.auto_impacts_enabled or changed:
            self.impact_start = self.impact_last_end = None
            self.impact_count = 0
            self.impact_until = 0.0
            self.impact_low_time = 0.0
            self.impact_rejected = False
            self.impact_gain = 1.0
            if not cfg.auto_impacts_enabled:
                return
        if level >= cfg.auto_threshold / 100:
            self.impact_low_time = 0.0
            if self.impact_start is None:
                self.impact_start = self.clock
                self.impact_rejected = False
            if self.clock - self.impact_start > 0.45:
                self.impact_rejected = True
                self.impact_count = 0
                self.impact_until = 0.0
        elif self.impact_start is not None:
            self.impact_low_time += dt
            if self.impact_low_time >= 0.16:
                end = self.clock - self.impact_low_time
                if not self.impact_rejected:
                    interval = end - self.impact_last_end if self.impact_last_end is not None else None
                    self.impact_count = (self.impact_count + 1
                                         if interval is not None and 0.65 <= interval <= 3.0 else 1)
                    self.impact_last_end = end
                    if cfg.auto_impacts_progressive or self.impact_count >= 3:
                        self.impact_until = end + cfg.auto_impacts_hold_ms / 1000
                self.impact_start = None
                self.impact_low_time = 0.0
        if self.impact_last_end is not None and self.clock - self.impact_last_end > 3.0:
            self.impact_count = 0
        hold_gain = cfg.auto_impacts_gain
        if cfg.auto_impacts_progressive and self.impact_count == 1:
            hold_gain = cfg.auto_impacts_first_gain
        target = max(floor, hold_gain / 100) if self.clock < self.impact_until else 1.0
        self.impact_gain = (target if target < self.impact_gain else
                            min(target, self.impact_gain + dt / 0.5))

    def step(self, peak, base, dt, cfg):
        dt = max(0.001, min(0.3, dt))
        self.clock += dt
        level = max(0.0, min(1.0, peak)) * base / 100
        floor = cfg.auto_min_gain / 100
        desired_peak = 1.0
        if level >= cfg.auto_threshold / 100:
            desired_peak = max(floor, min(1.0, cfg.auto_peak_target / 100 / max(level, 1e-8)))
        # Linear return: from the minimum gain to unity within the labelled time.
        self.peak_gain = (desired_peak if desired_peak < self.peak_gain else
                          min(desired_peak, self.peak_gain + dt / (cfg.auto_release_ms / 1000)))
        self.history.append((self.clock, dt, min(level, 0.2), level))
        window = cfg.auto_noise_window_ms / 1000
        while self.history and self.history[0][0] <= self.clock - window:
            self.history.popleft()
        duration = sum(item[1] for item in self.history)
        self.noise_level = sum(item[1] * item[2] for item in self.history) / max(duration, 1e-8)
        threshold = cfg.auto_noise_threshold / 100
        occupancy = sum(item[1] for item in self.history if item[3] >= threshold) / max(duration, 1e-8)
        if not cfg.auto_noise_enabled:
            self.noise_active = False
        elif self.noise_active:
            if self.noise_level < threshold * 0.65:
                self.noise_active = False
        elif duration >= window * 0.95 and self.noise_level >= threshold and occupancy >= 0.7:
            self.noise_active = True
        desired_noise = 1.0
        if self.noise_active:
            desired_noise = max(floor, min(cfg.auto_noise_volume / 100,
                                          cfg.auto_noise_target / 100 / max(self.noise_level, 1e-8)))
        if desired_noise < self.noise_gain:
            self.noise_gain = max(desired_noise, self.noise_gain - dt / 0.4)
        else:
            self.noise_gain = min(desired_noise, self.noise_gain + dt / (cfg.auto_noise_release_ms / 1000))
        self._impacts(level, dt, floor, cfg)
        return min(self.peak_gain, self.noise_gain, self.impact_gain)


class AutoVolume(QObject):
    status = Signal(str)

    def __init__(self, audio, config, parent=None):
        super().__init__(parent)
        self.audio = audio
        self.config = config
        self.name = None
        self.base = None
        self.last_written = None
        self.gain = 1.0
        self.envelope = DualEnvelope()
        self.last_time = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def restore(self):
        if self.name and self.base is not None:
            current = self.audio.get_volume(self.name)
            if current is not None and current == self.last_written:
                self.audio.set_volume(self.name, self.base, unmute=False)
        self.name = None
        self.base = None
        self.last_written = None
        self.gain = 1.0
        self.envelope = DualEnvelope()

    def set_manual(self, value):
        """Hotkeys/slider adjust the ceiling, not the temporarily attenuated value."""
        if self.name and self.base is not None:
            self.base = max(0, min(100, int(value)))
            result = round(self.base * self.gain)
            self.last_written = self.audio.set_volume(self.name, result, unmute=False)
            return self.base
        return self.audio.set_volume(self.config.process_name, value)

    def tick(self):
        now = time.monotonic()
        dt = min(0.3, now - self.last_time)
        self.last_time = now
        if not self.config.auto_volume_enabled:
            if self.name:
                self.restore()
            self.status.emit("Автоприглушение выключено")
            return
        if self.name != self.config.process_name:
            self.restore()
            self.name = self.config.process_name
        current = self.audio.get_volume(self.name)
        if current is None:
            # A restarted process must establish a new baseline.
            self.base = self.last_written = None
            self.gain = 1.0
            self.envelope = DualEnvelope()
            self.status.emit("Ожидание аудиосессии игры")
            return
        if self.base is None or (self.last_written is not None and current != self.last_written):
            self.base = current
            self.gain = 1.0
            self.envelope = DualEnvelope()
        peak = self.audio.get_peak(self.name)
        if peak is None:
            self.restore()
            self.status.emit("Измеритель уровня недоступен — громкость не изменяется")
            return
        self.gain = self.envelope.step(peak, self.base, dt, self.config)
        value = round(self.base * self.gain)
        if current != value:
            self.last_written = self.audio.set_volume(self.name, value, unmute=False)
        else:
            self.last_written = current
        branch = "пики" if self.envelope.peak_gain < self.envelope.noise_gain else "длительный шум"
        if self.envelope.impact_gain < min(self.envelope.peak_gain, self.envelope.noise_gain):
            branch = "повторяющиеся удары"
        if self.gain >= 0.995:
            branch = "без приглушения"
        self.status.emit(
            f"Пик {peak * 100:.0f} % · фон {self.envelope.noise_level * 100:.1f} % · "
            f"предел {self.base} % · сейчас {value} % · {branch}")
