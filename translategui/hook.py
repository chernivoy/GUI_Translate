"""Глобальні хуки: гаряча клавіша (за замовчуванням Ctrl + 0) і кліки мишею.

Колбеки викликаються в потоці хука, тому мають лише передати подію далі:
якщо хук обробляє подію надто довго, Windows його мовчки вимикає.
"""
from pynput import keyboard, mouse

from .keys import DEFAULT_HOTKEY, modifiers_down
from .processes import OWN_PID, foreground_pid, pid_at, process_name

WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_INJECTED = 0x10


class MouseHook:
    """Кліки мишею: клік поза нашим вікном закриває його. Нічого не блокує."""

    def __init__(self, on_outside_click):
        self._on_outside_click = on_outside_click
        self._listener = None

    def start(self):
        self._listener = mouse.Listener(win32_event_filter=self._filter)
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()

    def _filter(self, msg, data):
        if msg in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN) and pid_at(data.pt.x, data.pt.y) != OWN_PID:
            self._on_outside_click()
        # False — не передавати подію в обробники pynput: усе потрібне вже зроблено тут.
        return False


class HotkeyHook:
    """Ловить гарячу клавішу й не пропускає її до активної програми (щоб, наприклад, Ctrl + 0 не скидав
    масштаб у браузері).

    У програмах-винятках, у самому вікні перекладу (там її й записують у налаштуваннях) і коли
    її вимкнено в треї, комбінація проходить до програми як звичайно. Модифікатори мають збігатися
    точно: інакше, наприклад, AltGr + 0 (Ctrl + Alt + 0, «}» на німецькій розкладці) спрацьовувало б як Ctrl + 0.
    """

    def __init__(self, on_hotkey):
        self._on_hotkey = on_hotkey
        self._listener = None
        self._swallow_up = set()  # клавіші, чиє відпускання теж треба поглинути
        self.enabled = True
        # І комбінація, і винятки замінюються цілими новими об'єктами — потік хука бачить або старі, або нові.
        self.hotkey = DEFAULT_HOTKEY
        self.excluded = frozenset()  # імена exe в нижньому регістрі

    def start(self):
        self._listener = keyboard.Listener(win32_event_filter=self._filter)
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()

    def _applies_to_foreground(self):
        pid = foreground_pid()
        return pid != OWN_PID and process_name(pid).lower() not in self.excluded

    def _filter(self, msg, data):
        vk = data.vkCode
        hotkey = self.hotkey
        if msg in (WM_KEYUP, WM_SYSKEYUP) and vk in self._swallow_up:
            self._swallow_up.discard(vk)
            self._listener.suppress_event()
        elif (msg in (WM_KEYDOWN, WM_SYSKEYDOWN) and vk in hotkey.vks
              and not data.flags & LLKHF_INJECTED  # власні імітовані натискання пропускаємо
              and self.enabled and modifiers_down() == hotkey.modifiers and self._applies_to_foreground()):
            if vk not in self._swallow_up:  # автоповтор утримуваної клавіші — лише одне спрацювання
                self._swallow_up.add(vk)
                self._on_hotkey()
            self._listener.suppress_event()
        # False — не передавати подію в обробники pynput: усе потрібне вже зроблено тут.
        return False
