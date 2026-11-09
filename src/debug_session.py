"""Opt-in, local speech annotations and read-only game telemetry."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import queue
import threading
import time
import urllib.request
import zipfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from config import app_data_dir

MODEL_NAME = "vosk-model-small-ru-0.22"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_MIRROR = f"https://huggingface.co/rhasspy/vosk-models/resolve/main/ru/{MODEL_NAME}.zip"
MODEL_SHA256 = "961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4"
log = logging.getLogger(__name__)


class SpeechWorker(QThread):
    message = Signal(str)
    phrase = Signal(str, float, float)
    error = Signal(str)

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.device = device
        self.stop_event = threading.Event()

    def run(self):
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
            root = app_data_dir() / "models"
            root.mkdir(parents=True, exist_ok=True)
            model_path = root / MODEL_NAME
            if not (model_path / "am" / "final.mdl").is_file():
                self.message.emit("Загрузка русской модели (~45 МБ); микрофон ещё выключен…")
                archive = root / "russian.zip.part"
                try:
                    for index, url in enumerate((MODEL_MIRROR, MODEL_URL)):
                        try:
                            digest = hashlib.sha256()
                            with urllib.request.urlopen(url, timeout=20) as source, archive.open("wb") as target:
                                while not self.stop_event.is_set():
                                    chunk = source.read(256 * 1024)
                                    if not chunk:
                                        break
                                    target.write(chunk)
                                    digest.update(chunk)
                                if self.stop_event.is_set():
                                    return
                            if digest.hexdigest() != MODEL_SHA256:
                                raise ValueError("Контрольная сумма модели не совпала")
                            break
                        except Exception:
                            if index == 1:
                                raise RuntimeError("Не удалось скачать русскую модель. Проверьте интернет и повторите запуск отладки.")
                            self.message.emit("Первый сервер недоступен, пробую резервный…")
                    with zipfile.ZipFile(archive) as z:
                        for item in z.infolist():
                            dest = (root / item.filename).resolve()
                            if not dest.is_relative_to(root.resolve()):
                                raise ValueError("Недопустимый путь в архиве модели")
                        z.extractall(root)
                finally:
                    archive.unlink(missing_ok=True)
            if self.stop_event.is_set():
                return
            self.message.emit("Загрузка модели в память; микрофон ещё выключен…")
            SetLogLevel(-1)
            model = Model(str(model_path))
            device_info = sd.query_devices(self.device, "input")
            rate = int(device_info["default_samplerate"])
            recognizer = KaldiRecognizer(model, rate)
            recognizer.SetWords(True)
            audio_queue = queue.Queue(maxsize=100)

            def callback(indata, frames, timing, status):
                try:
                    audio_queue.put_nowait((bytes(indata), str(status)))
                except queue.Full:
                    pass

            with sd.RawInputStream(device=self.device, samplerate=rate, channels=1,
                                   dtype="int16", blocksize=max(1, rate // 5), callback=callback):
                origin = time.monotonic()
                self.message.emit("● Микрофон включён — идёт локальное распознавание")

                def publish(raw):
                    result = json.loads(raw)
                    text = result.get("text", "").strip()
                    if text:
                        words = result.get("result", [])
                        start = origin + words[0]["start"] if words else time.monotonic()
                        end = origin + words[-1]["end"] if words else time.monotonic()
                        self.phrase.emit(text, start, end)

                while not self.stop_event.is_set():
                    try:
                        data, status = audio_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if status:
                        self.message.emit(f"Микрофон: {status}")
                    if recognizer.AcceptWaveform(data):
                        publish(recognizer.Result())
                publish(recognizer.FinalResult())
        except Exception as exc:
            log.exception("Ошибка голосовой отладки")
            self.error.emit(str(exc))


class DebugSession(QObject):
    status = Signal(str)
    transcript = Signal(str)
    active_changed = Signal(bool)

    def __init__(self, audio, config, parent=None):
        super().__init__(parent)
        self.audio = audio
        self.config = config
        self.worker = None
        self.files = []
        self.started = 0.0
        self.folder = None
        self.last_error = None
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.sample)

    def start(self, device=None):
        if self.worker is not None:
            return
        self.last_error = None
        self.started = time.monotonic()
        self.folder = app_data_dir() / "debug" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.folder.mkdir(parents=True)
        (self.folder / "settings.json").write_text(
            json.dumps(asdict(self.config), ensure_ascii=False, indent=2), encoding="utf-8")
        (self.folder / "README.txt").write_text(
            "Local speech recognition; no raw microphone audio saved.\n"
            "Speech times and levels use elapsed monotonic seconds.\n"
            "Levels are sampled session peaks, NOT waveform RMS/LUFS.\n", encoding="utf-8")
        self.level_file = (self.folder / "levels.csv").open("w", encoding="utf-8", newline="")
        self.speech_file = (self.folder / "speech.jsonl").open("w", encoding="utf-8")
        self.files = [self.level_file, self.speech_file]
        self.writer = csv.writer(self.level_file)
        self.writer.writerow(["time", "elapsed_s", "process", "peak", "mixer_percent",
                              "auto_enabled", "threshold", "minimum", "release_ms",
                              "peak_target", "noise_enabled", "noise_threshold", "noise_target",
                              "noise_window_ms", "noise_release_ms", "impacts_enabled",
                              "impacts_hold_ms", "impacts_gain", "impacts_progressive", "impacts_first_gain", "noise_volume"])
        self.worker = SpeechWorker(device, self)
        self.worker.message.connect(self.status)
        self.worker.phrase.connect(self._phrase)
        self.worker.error.connect(self._error)
        self.worker.finished.connect(self._finished)
        self.active_changed.emit(True)
        self.timer.start()
        self.worker.start()

    def sample(self):
        try:
            self.writer.writerow([datetime.now().isoformat(timespec="milliseconds"),
                                  round(time.monotonic() - self.started, 3),
                                  self.config.process_name, self.audio.get_peak(self.config.process_name),
                                  self.audio.get_volume(self.config.process_name),
                                  self.config.auto_volume_enabled, self.config.auto_threshold,
                                  self.config.auto_min_gain, self.config.auto_release_ms,
                                  self.config.auto_peak_target, self.config.auto_noise_enabled,
                                  self.config.auto_noise_threshold, self.config.auto_noise_target,
                                  self.config.auto_noise_window_ms, self.config.auto_noise_release_ms,
                                  self.config.auto_impacts_enabled, self.config.auto_impacts_hold_ms,
                                  self.config.auto_impacts_gain, self.config.auto_impacts_progressive,
                                  self.config.auto_impacts_first_gain, self.config.auto_noise_volume])
            self.level_file.flush()
        except Exception as exc:
            self.status.emit(f"Ошибка записи уровней: {exc}")
            self.stop()

    def _phrase(self, text, start, end):
        if not self.files:
            return
        start = round(start - self.started, 3)
        end = round(end - self.started, 3)
        self.speech_file.write(json.dumps(dict(start_s=start, end_s=end, text=text),
                                         ensure_ascii=False) + "\n")
        self.speech_file.flush()
        self.transcript.emit(f"[{start:.1f}–{end:.1f} с] {text}")

    def _error(self, message):
        self.last_error = message
        self.transcript.emit(f"Ошибка: {message}")

    def stop(self):
        self.timer.stop()
        if self.worker:
            self.worker.stop_event.set()
            self.status.emit("Остановка; ожидание завершения микрофона…")

    def _finished(self):
        self.timer.stop()
        for stream in self.files:
            stream.close()
        self.files = []
        self.worker.deleteLater()
        self.worker = None
        self.active_changed.emit(False)
        if self.last_error:
            self.status.emit(f"Отладка не завершена: {self.last_error}. Микрофон выключен.")
        else:
            self.status.emit(f"Микрофон выключен. Логи: {self.folder}")

    def shutdown(self):
        self.stop()
        if self.worker:
            self.worker.wait()
        for stream in self.files:
            stream.close()
        self.files = []
