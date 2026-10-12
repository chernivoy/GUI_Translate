"""Збирання однофайлового exe: python build.py → dist\\TranslateGUI.exe (потрібен pip install pyinstaller)."""
import io
import os
import sys

import PySide6.QtWidgets  # noqa: F401  — Qt завантажується першим, як і в main.py
import PyInstaller.__main__
from PIL import Image
from PySide6.QtCore import QBuffer, QSize
from PySide6.QtGui import QGuiApplication

from translategui.theme import app_icon

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, "build")
ICON = os.path.join(BUILD, "app.ico")
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def make_icon():
    """Значок exe — той самий, що в треї, у всіх розмірах, які бере Провідник."""
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 — потрібен для малювання
    images = []
    for size in ICON_SIZES:
        buffer = QBuffer()
        buffer.open(QBuffer.WriteOnly)
        app_icon(size).pixmap(QSize(size, size), 1.0).save(buffer, "PNG")
        images.append(Image.open(io.BytesIO(buffer.data().data())))
    os.makedirs(BUILD, exist_ok=True)
    images[-1].save(ICON, format="ICO", sizes=[(s, s) for s in ICON_SIZES], append_images=images[:-1])


def build():
    PyInstaller.__main__.run([
        os.path.join(ROOT, "main.py"),
        "--name", "TranslateGUI",
        "--onefile", "--windowed", "--noconfirm", "--clean",
        "--icon", ICON,
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", BUILD,
        "--specpath", BUILD,
        # pynput вибирає реалізацію для Windows під час роботи — аналізатор PyInstaller її не бачить.
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse._win32",
        # Готові обгортки UI Automation від comtypes: без них exe генерував би їх при кожному запуску.
        "--collect-submodules", "comtypes.gen",
        "--collect-all", "winrt",
    ])


if __name__ == "__main__":
    make_icon()
    build()
