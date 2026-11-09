from __future__ import annotations

import logging

from comtypes import CLSCTX_ALL, CoCreateInstance, CoInitialize
from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
from pycaw.constants import CLSID_MMDeviceEnumerator, DEVICE_STATE, EDataFlow, ERole
from pycaw.pycaw import AudioSession, AudioUtilities, ISimpleAudioVolume, IAudioMeterInformation

log = logging.getLogger(__name__)


def _co_init() -> None:
    try:
        CoInitialize()
    except OSError:
        pass


class AudioController:
    """Управление громкостью приложения через системный микшер Windows (WASAPI)."""

    def __init__(self) -> None:
        _co_init()

    # ---------- публичный API ----------

    @staticmethod
    def normalize(name: str) -> str:
        name = (name or "").strip()
        if name and not name.lower().endswith(".exe"):
            return name + ".exe"
        return name

    def list_audio_processes(self) -> list[str]:
        """Имена процессов, у которых сейчас есть аудиосессия (без дубликатов)."""
        names: set[str] = set()
        for session in self._iter_sessions():
            proc = getattr(session, "Process", None)
            if proc is not None:
                try:
                    names.add(proc.name())
                except Exception:
                    continue
        return sorted(names, key=str.lower)

    def get_volume(self, process_name: str) -> int | None:
        target = self.normalize(process_name).lower()
        found = False
        volume = 0.0
        for session in self._iter_sessions():
            proc = getattr(session, "Process", None)
            if proc is None:
                continue
            try:
                if proc.name().lower() != target:
                    continue
            except Exception:
                continue
            try:
                vol = session.SimpleAudioVolume.GetMasterVolume()
            except Exception:
                continue
            volume = vol if not found else max(volume, vol)
            found = True
        if not found:
            return None
        return int(round(volume * 100))

    def set_volume(self, process_name: str, percent: int, *, unmute: bool = True) -> int | None:
        percent = max(0, min(100, int(percent)))
        scalar = percent / 100.0
        target = self.normalize(process_name).lower()
        found = False
        for session in self._iter_sessions():
            proc = getattr(session, "Process", None)
            if proc is None:
                continue
            try:
                if proc.name().lower() != target:
                    continue
            except Exception:
                continue
            try:
                volume = session.SimpleAudioVolume
                volume.SetMasterVolume(scalar, None)
                if percent > 0 and unmute:
                    volume.SetMute(0, None)
                found = True
            except Exception as exc:
                log.debug("Не удалось задать громкость сессии: %s", exc)
        return percent if found else None

    def change_volume(self, process_name: str, delta: int) -> int | None:
        current = self.get_volume(process_name)
        if current is None:
            return None
        return self.set_volume(process_name, current + delta)

    def is_available(self, process_name: str) -> bool:
        return self.get_volume(process_name) is not None

    def get_peak(self, process_name: str) -> float | None:
        """Session peak before session-volume attenuation, never system-wide audio."""
        target = self.normalize(process_name).lower()
        peaks = []
        for session in self._iter_sessions():
            try:
                if session.Process is None or session.Process.name().lower() != target:
                    continue
                meter = session._ctl.QueryInterface(IAudioMeterInformation)
                peaks.append(float(meter.GetPeakValue()))
            except Exception:
                continue
        return max(peaks) if peaks else None

    # ---------- внутреннее ----------

    def _iter_sessions(self):
        """Сессии со всех активных устройств вывода."""
        try:
            device_enum = CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                                           CLSCTX_ALL)
        except Exception as exc:
            log.debug("Не удалось создать перечислитель устройств: %s", exc)
            return
        try:
            collection = device_enum.EnumAudioEndpoints(EDataFlow.eRender.value,
                                                        DEVICE_STATE.ACTIVE.value)
            count = collection.GetCount()
        except Exception as exc:
            log.debug("Не удалось перечислить устройства вывода: %s", exc)
            return

        for i in range(count):
            try:
                device = collection.Item(i)
                manager = device.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
                manager = manager.QueryInterface(IAudioSessionManager2)
                enumerator = manager.GetSessionEnumerator()
                session_count = enumerator.GetCount()
            except Exception as exc:
                log.debug("Пропускаю устройство %d: %s", i, exc)
                continue
            for j in range(session_count):
                try:
                    control = enumerator.GetSession(j)
                    control = control.QueryInterface(IAudioSessionControl2)
                    if control is None:
                        continue
                    yield AudioSession(control)
                except Exception:
                    continue
