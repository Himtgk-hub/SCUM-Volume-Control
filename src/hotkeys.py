from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

from config import KeyCombo
from winutils import key_name_from_lparam

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C

MODIFIER_VKS = {VK_SHIFT, VK_CONTROL, VK_MENU,
                VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
                VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", ctypes.c_void_p),
                ("time", wintypes.DWORD),
                ("pt_x", wintypes.LONG),
                ("pt_y", wintypes.LONG)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                              wintypes.WPARAM, ctypes.c_void_p)

# Явные прототипы, чтобы lParam передавался как 64-битный указатель.
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  wintypes.WPARAM, ctypes.c_void_p]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p,
                                     wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL


class HotkeyManager(QObject):
    """Низкоуровневый хук клавиатуры (WH_KEYBOARD_LL).

    Сигналы эмитятся из потока хука; в главном потоке нужно подключаться
    через Qt.QueuedConnection (по умолчанию для межпоточных соединений).
    """

    volume_up = Signal()
    volume_down = Signal()
    capture_progress = Signal(object)   # КeyCombo — промежуточное состояние записи
    captured = Signal(object)           # KeyCombo — запись завершена

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._hook_proc = None
        self._running = False

        self._lock = threading.Lock()
        self._capturing = False
        self._hotkey_up: tuple | None = None
        self._hotkey_down: tuple | None = None
        self._only_when_focused = False
        self._process_name = ""
        self._suppress = False

        self._pressed: set[int] = set()
        self._capture_mods_frozen = False

    # ---------- управление ----------

    def set_hotkeys(self, up: KeyCombo, down: KeyCombo) -> None:
        with self._lock:
            self._hotkey_up = None if up.is_empty() else up.signature()
            self._hotkey_down = None if down.is_empty() else down.signature()

    def set_focus_filter(self, enabled: bool, process_name: str) -> None:
        with self._lock:
            self._only_when_focused = enabled
            self._process_name = process_name

    def set_suppress(self, enabled: bool) -> None:
        with self._lock:
            self._suppress = enabled

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="KeyboardHook", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = 0
        self._pressed.clear()

    # ---------- запись горячей клавиши ----------

    def begin_capture(self) -> None:
        with self._lock:
            self._capturing = True
            self._capture_mods_frozen = False

    def cancel_capture(self) -> None:
        with self._lock:
            self._capturing = False

    def is_capturing(self) -> bool:
        return self._capturing

    # ---------- поток хука ----------

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        self._hook_proc = HOOKPROC(self._callback)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, None, 0)
        if not self._hook:
            log.error("Не удалось установить хук клавиатуры (ошибка %d)", ctypes.get_last_error())
            self._running = False
            return
        log.info("Хук клавиатуры установлен")

        msg = MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        log.info("Хук клавиатуры снят")

    def _callback(self, n_code, w_param, l_param) -> int:
        if n_code < 0:
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        try:
            kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        except Exception:
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        message = w_param
        is_down = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
        is_up = message in (WM_KEYUP, WM_SYSKEYUP)
        vk = int(kb.vkCode)
        scan = int(kb.scanCode)
        extended = bool(kb.flags & LLKHF_EXTENDED)
        injected = bool(kb.flags & LLKHF_INJECTED)

        swallow = False
        try:
            if self._capturing:
                swallow = self._handle_capture(vk, scan, extended, is_down, is_up,
                                               injected)
            elif is_down:
                swallow = self._handle_hotkey(vk, scan, extended)
        except Exception as exc:  # хук не должен падать / подвисать
            log.debug("Ошибка обработки клавиши: %s", exc)

        if swallow:
            return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    # ---------- обработка ----------

    def _handle_capture(self, vk: int, scan: int, extended: bool,
                        is_down: bool, is_up: bool, injected: bool = False) -> bool:
        if is_down:
            if vk in self._pressed:
                return True  # игнорируем автоповтор модификатора
            self._pressed.add(vk)
            if vk in MODIFIER_VKS:
                return True  # модификатор сам по себе не завершает запись
            if vk == 0x1B:  # Esc — отмена
                self._capturing = False
                self.captured.emit(KeyCombo())
                return True
            if vk in (0x08, 0x2E):  # Backspace / Delete — очистить привязку
                self._capturing = False
                self.captured.emit(KeyCombo())
                return True
            combo = self._build_combo(vk, scan, extended)
            self._capturing = False
            self.captured.emit(combo)
            return True
        if is_up:
            self._pressed.discard(vk)
        return True

    def _build_combo(self, vk: int, scan: int, extended: bool) -> KeyCombo:
        mods = self._collect_modifiers(vk)
        display = self._format_display(vk, scan, extended, mods)
        return KeyCombo(vk=vk, scan=scan, extended=extended,
                        ctrl="Ctrl" in mods, alt="Alt" in mods,
                        shift="Shift" in mods, win="Win" in mods,
                        display=display)

    def _collect_modifiers(self, main_vk: int) -> list[str]:
        mods: list[str] = []
        state = self._pressed | self._read_modifier_state()
        if state.intersection({VK_CONTROL, VK_LCONTROL, VK_RCONTROL}):
            mods.append("Ctrl")
        if state.intersection({VK_MENU, VK_LMENU, VK_RMENU}):
            mods.append("Alt")
        if state.intersection({VK_SHIFT, VK_LSHIFT, VK_RSHIFT}):
            mods.append("Shift")
        if state.intersection({VK_LWIN, VK_RWIN}):
            mods.append("Win")
        return mods

    @staticmethod
    def _read_modifier_state() -> set[int]:
        state: set[int] = set()
        if (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000) or \
           (user32.GetAsyncKeyState(VK_LCONTROL) & 0x8000) or \
           (user32.GetAsyncKeyState(VK_RCONTROL) & 0x8000):
            state.add(VK_CONTROL)
        if (user32.GetAsyncKeyState(VK_MENU) & 0x8000) or \
           (user32.GetAsyncKeyState(VK_LMENU) & 0x8000) or \
           (user32.GetAsyncKeyState(VK_RMENU) & 0x8000):
            state.add(VK_MENU)
        if (user32.GetAsyncKeyState(VK_SHIFT) & 0x8000) or \
           (user32.GetAsyncKeyState(VK_LSHIFT) & 0x8000) or \
           (user32.GetAsyncKeyState(VK_RSHIFT) & 0x8000):
            state.add(VK_SHIFT)
        if (user32.GetAsyncKeyState(VK_LWIN) & 0x8000) or \
           (user32.GetAsyncKeyState(VK_RWIN) & 0x8000):
            state.add(VK_LWIN)
        return state

    def _format_display(self, vk: int, scan: int, extended: bool, mods: list[str]) -> str:
        name = key_name_from_lparam(scan, extended)
        parts = list(mods)
        parts.append(name)
        return "+".join(parts)

    def _handle_hotkey(self, vk: int, scan: int, extended: bool) -> bool:
        with self._lock:
            up_sig = self._hotkey_up
            down_sig = self._hotkey_down
            only_focused = self._only_when_focused
            process = self._process_name
            suppress = self._suppress

        sig = self._signature_for(vk, scan, extended)
        if sig is None:
            return False

        matched: str | None = None
        if up_sig and self._match(sig, up_sig):
            matched = "up"
        elif down_sig and self._match(sig, down_sig):
            matched = "down"

        if matched is None:
            return False

        if only_focused and process:
            from winutils import is_foreground_process
            if not is_foreground_process(process):
                return False

        if matched == "up":
            self.volume_up.emit()
        else:
            self.volume_down.emit()
        return suppress

    def _signature_for(self, vk: int, scan: int, extended: bool) -> tuple | None:
        if vk in MODIFIER_VKS:
            return None
        mods = self._read_modifier_state()
        return (vk, scan, extended,
                VK_CONTROL in mods, VK_MENU in mods, VK_SHIFT in mods,
                (VK_LWIN in mods or VK_RWIN in mods))

    @staticmethod
    def _match(actual: tuple, expected: tuple) -> bool:
        # Сравниваем VK + модификаторы; scan/extended используем как уточнение,
        # но допускаем небольшое расхождение scan (некоторые раскладки дают разный scan).
        if actual[0] != expected[0]:
            return False
        return actual[3:7] == expected[3:7]
