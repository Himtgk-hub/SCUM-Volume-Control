from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# ---------- Wин-константы ----------

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008

HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "SCUMVolumeControl"


# ---------- Значки / имена клавиш ----------

def key_name_from_lparam(scan: int, extended: bool) -> str:
    """Имя клавиши через GetKeyNameTextW по scan-коду."""
    lparam = (scan & 0xFF) << 16
    if extended:
        lparam |= 1 << 24
    buf = ctypes.create_unicode_buffer(64)
    length = user32.GetKeyNameTextW(lparam, buf, len(buf))
    name = buf.value.strip() if length else ""
    return name or "?"


# ---------- Активное окно / процесс ----------

def foreground_pid() -> int | None:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def _process_name_by_pid(pid: int) -> str | None:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return None
    finally:
        kernel32.CloseHandle(handle)


def foreground_process_name() -> str | None:
    pid = foreground_pid()
    if pid is None:
        return None
    return _process_name_by_pid(pid)


def is_foreground_process(name: str) -> bool:
    target = _normalize(name).lower()
    current = foreground_process_name()
    return bool(current) and current.lower() == target


def _normalize(name: str) -> str:
    name = (name or "").strip()
    if name and not name.lower().endswith(".exe"):
        return name + ".exe"
    return name


# ---------- Окно игры и его монитор ----------

def find_window_by_process(process_name: str) -> int:
    """Возвращает HWND главного видимого окна процесса (первое найденное)."""
    target = _normalize(process_name).lower()
    result = [0]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        name = _process_name_by_pid(pid.value)
        if name and name.lower() == target:
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                result[0] = hwnd
                return False
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return result[0]


def monitor_rect_for_window(hwnd: int) -> tuple[int, int, int, int] | None:
    """Рабочая область монитора окна в ЛОГИЧЕСКИХ пикселях Qt (не физических)."""
    if not hwnd:
        return None
    MONITOR_DEFAULTTONEAREST = 2
    hmonitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not hmonitor:
        return None
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
        return None
    # GetMonitorInfoW возвращает ФИЗИЧЕСКИЕ пиксели; переводим в логические,
    # т.к. Qt.move() работает в логических координатах с учётом DPI.
    scale = window_scale_for(hwnd)
    r = info.rcWork  # рабочая область без панели задач
    return (int(round(r.left / scale)), int(round(r.top / scale)),
            int(round(r.right / scale)), int(round(r.bottom / scale)))


# ---------- Стили окна оверлея ----------

def apply_overlay_styles(hwnd: int) -> None:
    GWL_EXSTYLE_INDEX = GWL_EXSTYLE
    get_l = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_l = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    ex = get_l(hwnd, GWL_EXSTYLE_INDEX)
    ex |= (WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_LAYERED
           | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
    set_l(hwnd, GWL_EXSTYLE_INDEX, ex)


def raise_topmost_no_activate(hwnd: int) -> None:
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)


# ---------- Автозапуск ----------

def set_autostart(enabled: bool, exe_path: str | None = None) -> bool:
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
    except OSError as exc:
        log.error("Не удалось открыть ключ автозапуска: %s", exc)
        return False
    try:
        if enabled:
            path = exe_path or sys.executable
            value = f'"{path}" --minimized'
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
        return True
    except OSError as exc:
        log.error("Не удалось изменить автозапуск: %s", exc)
        return False
    finally:
        winreg.CloseKey(key)


def is_autostart_enabled() -> bool:
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE)
    except OSError:
        return False
    try:
        winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except FileNotFoundError:
        return False
    finally:
        winreg.CloseKey(key)


# ---------- Права администратора / перезапуск ----------

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def enable_dpi_awareness() -> None:
    """Явно включает per-monitor DPI awareness до создания QApplication."""
    try:
        user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except (AttributeError, OSError):
        pass


def window_scale_for(hwnd: int) -> float:
    """Масштаб DPI монитора окна (1.25 при 125 %)."""
    try:
        dpi = user32.GetDpiForWindow(hwnd)
    except AttributeError:
        return 1.0
    return dpi / 96.0 if dpi else 1.0


# ---------- Права администратора / перезапуск ----------

def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def restart_as_admin() -> bool:
    """Перезапуск текущего приложения с правами администратора."""
    try:
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        if getattr(sys, "frozen", False):
            exe = sys.executable
            args = params
        else:
            exe = sys.executable
            script = os.path.abspath(sys.argv[0])
            args = f'"{script}" {params}'.strip()
        ret = shell32.ShellExecuteW(None, "runas", exe, args, None, 1)
        return int(ret) > 32
    except Exception as exc:
        log.error("Не удалось перезапуститься с правами администратора: %s", exc)
        return False
