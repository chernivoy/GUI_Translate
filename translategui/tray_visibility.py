"""Windows 11 ховає значки нових програм під стрілку «^» в області сповіщень. Виносимо свій на панель
завдань (далі користувач може сховати його в «Параметри → Панель завдань → Інші значки»)."""
import ctypes
import logging
import os
import re
import winreg
from ctypes import wintypes

from comtypes import GUID

log = logging.getLogger(__name__)

# Explorer заводить тут підрозділ на кожен значок, щойно програма його покаже.
# IsPromoted: 1 — значок на панелі; 0 або немає — під стрілкою. Зміну Explorer підхоплює одразу.
NOTIFY_ICON_SETTINGS = r"Control Panel\NotifyIconSettings"
# Шлях у записі може починатися з ідентифікатора відомої папки: «{6D809377-…}\Програма\app.exe».
KNOWN_FOLDER_PREFIX = re.compile(r"^(\{[0-9A-Fa-f-]{36}\})(.*)$")

_shell32 = ctypes.windll.shell32
_shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                          ctypes.POINTER(ctypes.c_wchar_p)]
_ole32 = ctypes.windll.ole32
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]


def _known_folder(folder_id):
    path = ctypes.c_wchar_p()
    if _shell32.SHGetKnownFolderPath(ctypes.byref(GUID(folder_id)), 0, None, ctypes.byref(path)) != 0:
        return None
    try:
        return path.value
    finally:
        _ole32.CoTaskMemFree(path)


def _expand(path):
    match = KNOWN_FOLDER_PREFIX.match(path)
    if match:
        folder = _known_folder(match.group(1))
        if folder:
            path = folder + match.group(2)
    return os.path.normcase(os.path.abspath(path))


def _value(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def promote_tray_icon(executable):
    """Виносить значок програми executable на панель. Повертає False, якщо Explorer ще не завів
    запис про значок (тоді варто спробувати пізніше)."""
    target = os.path.normcase(os.path.abspath(executable))
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_ICON_SETTINGS)
    except OSError:
        return True  # Windows 10 і старші: там такого налаштування немає
    with root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                return False
            index += 1
            with winreg.OpenKey(root, name, access=winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                path = _value(key, "ExecutablePath")
                if not path or _expand(path) != target:
                    continue
                winreg.SetValueEx(key, "IsPromoted", 0, winreg.REG_DWORD, 1)
                log.info("Значок у треї винесено на панель завдань")
                return True
