import ctypes
import sys

# uiautomation при імпорті вмикає застарілий режим DPI (Per-Monitor V1), а Qt очікує V2.
# Ставимо V2 першими — тоді обидва отримають те, що треба, і координати будуть у фізичних пікселях.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
ctypes.windll.user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)

# Qt має завантажитися раніше за winrt (OCR): у зворотному порядку процес падає з access violation.
import PySide6.QtWidgets  # noqa: E402, F401

from translategui.app import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
