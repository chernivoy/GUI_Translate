"""Виділений текст через буфер обміну: імітуємо «копіювати», читаємо текст і повертаємо попередній вміст буфера.

Копіюємо комбінацією Ctrl + Insert, а не Ctrl + C: у терміналах Ctrl + C без виділення перериває
запущену програму, а Ctrl + Insert — стандартне «копіювати» без такого побічного ефекту.
"""
import ctypes
import logging
import time
from ctypes import wintypes

from .keys import send_ctrl_insert, wait_modifiers_released

log = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.EnumClipboardFormats.restype = wintypes.UINT
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
# Формати, що зберігаються не блоком пам'яті, а дескрипторами GDI тощо. Скопіювати їх байтами не можна,
# але система сама відтворює їх з інших форматів (наприклад, растр — із CF_DIB).
HANDLE_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}
PRIVATE_FORMATS = range(0x200, 0x400)

# Скільки чекати, поки програма скопіює виділене; якщо нічого не виділено, саме стільки триває очікування.
COPY_TIMEOUT_S = 0.35


def copy_selection():
    """Текст, виділений в активній програмі, або "". Вміст буфера обміну після цього той самий, що й до."""
    saved = _save()
    if saved is None:
        return ""  # буфер зайнятий іншою програмою — краще нічого не робити, ніж зіпсувати його вміст
    sequence = user32.GetClipboardSequenceNumber()
    wait_modifiers_released()
    send_ctrl_insert()
    deadline = time.monotonic() + COPY_TIMEOUT_S
    while user32.GetClipboardSequenceNumber() == sequence:
        if time.monotonic() > deadline:
            return ""  # програма нічого не скопіювала — буфер не змінювався, відновлювати нічого
        time.sleep(0.02)
    time.sleep(0.05)  # програма може ще дописувати інші формати
    text = _read_text()
    _restore(saved)
    return text


def _open():
    for _ in range(25):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    log.warning("Буфер обміну зайнятий іншою програмою")
    return False


def _save():
    """Усі формати буфера як (формат, байти); None, якщо буфер не вдалося відкрити."""
    if not _open():
        return None
    try:
        items = []
        fmt = user32.EnumClipboardFormats(0)
        while fmt:
            if fmt not in HANDLE_FORMATS and fmt not in PRIVATE_FORMATS:
                handle = user32.GetClipboardData(fmt)
                pointer = kernel32.GlobalLock(handle) if handle else None
                if pointer:
                    try:
                        items.append((fmt, ctypes.string_at(pointer, kernel32.GlobalSize(handle))))
                    finally:
                        kernel32.GlobalUnlock(handle)
            fmt = user32.EnumClipboardFormats(fmt)
        return items
    finally:
        user32.CloseClipboard()


def _restore(items):
    if not _open():
        return
    try:
        user32.EmptyClipboard()
        for fmt, data in items:
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                continue
            ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(fmt, handle):
                kernel32.GlobalFree(handle)  # якщо буфер не прийняв пам'ять, звільняємо її самі
    finally:
        user32.CloseClipboard()


def _read_text():
    if not _open():
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        pointer = kernel32.GlobalLock(handle) if handle else None
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
