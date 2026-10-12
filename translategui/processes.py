"""Яка програма (процес) володіє вікном під курсором; список програм із відкритими вікнами."""
import ctypes
import os
from ctypes import wintypes

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
OWN_PID = os.getpid()

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def window_pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def pid_at(x, y):
    """Процес вікна в точці (фізичні пікселі) або 0."""
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    return window_pid(hwnd) if hwnd else 0


def foreground_pid():
    """Процес активного вікна (де зараз фокус клавіатури) або 0."""
    hwnd = user32.GetForegroundWindow()
    return window_pid(hwnd) if hwnd else 0


def cursor_pos():
    """Позиція курсора миші у фізичних пікселях."""
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def process_path(pid):
    """Повний шлях до exe процесу або "" (наприклад, для захищених системних процесів)."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def process_name(pid):
    """Ім'я exe процесу («chrome.exe») або ""."""
    return os.path.basename(process_path(pid)) if pid else ""


def windowed_programs():
    """Програми з видимими вікнами: {ім'я exe в нижньому регістрі: (ім'я exe, шлях, заголовок вікна)}."""
    programs = {}

    def collect(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if not user32.IsWindowVisible(hwnd) or not length:
            return True
        pid = window_pid(hwnd)
        if pid == OWN_PID:
            return True
        path = process_path(pid)
        name = os.path.basename(path)
        if name and name.lower() not in programs:
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            programs[name.lower()] = (name, path, title.value)
        return True

    user32.EnumWindows(_EnumWindowsProc(collect), 0)
    return programs
