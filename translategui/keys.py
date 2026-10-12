"""Гаряча клавіша (модель, назви, перевірка) та імітація натискань через SendInput."""
import ctypes
import time
from dataclasses import dataclass
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetKeyNameTextW.argtypes = [wintypes.LONG, wintypes.LPWSTR, ctypes.c_int]

CTRL, SHIFT, ALT, WIN = "ctrl", "shift", "alt", "win"
MODIFIER_ORDER = (CTRL, SHIFT, ALT, WIN)
MODIFIER_TITLES = {CTRL: "Ctrl", SHIFT: "Shift", ALT: "Alt", WIN: "Win"}
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
# Клавіші-модифікатори (разом з окремими лівою й правою) → який модифікатор вони дають.
VK_TO_MODIFIER = {
    VK_CONTROL: CTRL, 0xA2: CTRL, 0xA3: CTRL,
    VK_SHIFT: SHIFT, 0xA0: SHIFT, 0xA1: SHIFT,
    VK_MENU: ALT, 0xA4: ALT, 0xA5: ALT,
    VK_LWIN: WIN, VK_RWIN: WIN,
}
MODIFIER_VKS = set(VK_TO_MODIFIER)
VK_INSERT, VK_F1, VK_F24, VK_NUMPAD0 = 0x2D, 0x70, 0x87, 0x60
# Нікому не призначена клавіша: її натискання не дає відпущеному Alt відкрити меню, а Win — «Пуск».
VK_MASK = 0xE8

KEY_TITLES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause", 0x14: "CapsLock", 0x1B: "Esc",
    0x20: "Пробіл", 0x21: "PgUp", 0x22: "PgDn", 0x23: "End", 0x24: "Home", 0x25: "←", 0x26: "↑",
    0x27: "→", 0x28: "↓", 0x2C: "PrtSc", 0x2D: "Insert", 0x2E: "Delete", 0x90: "NumLock", 0x91: "ScrollLock",
    0x6A: "Num *", 0x6B: "Num +", 0x6D: "Num -", 0x6E: "Num .", 0x6F: "Num /",
}

# Стандартні дії, які не можна забирати під переклад (Ctrl + Insert — ще й наше власне «копіювати»).
RESERVED = {
    (frozenset({CTRL}), ord("C")): "Копіювати",
    (frozenset({CTRL}), ord("V")): "Вставити",
    (frozenset({CTRL}), ord("X")): "Вирізати",
    (frozenset({CTRL}), ord("Z")): "Скасувати",
    (frozenset({CTRL}), ord("Y")): "Повторити",
    (frozenset({CTRL}), ord("A")): "Виділити все",
    (frozenset({CTRL}), ord("S")): "Зберегти",
    (frozenset({CTRL}), VK_INSERT): "Копіювати",
    (frozenset({SHIFT}), VK_INSERT): "Вставити",
    (frozenset({SHIFT}), 0x2E): "Вирізати",
    (frozenset({ALT}), 0x73): "Закрити вікно",
    (frozenset({ALT}), 0x09): "Перемикання вікон",
    (frozenset({CTRL}), 0x1B): "Меню «Пуск»",
    (frozenset({CTRL, SHIFT}), 0x1B): "Диспетчер завдань",
    (frozenset({CTRL, ALT}), 0x2E): "Безпека Windows",
    (frozenset({WIN}), ord("L")): "Блокування комп'ютера",
}


@dataclass(frozen=True)
class Hotkey:
    modifiers: frozenset
    vk: int

    @classmethod
    def parse(cls, text):
        """«ctrl+shift+0x54» → Hotkey; формат зберігання в налаштуваннях."""
        *modifiers, key = text.lower().split("+")
        if not set(modifiers) <= set(MODIFIER_ORDER):
            raise ValueError(text)
        return cls(frozenset(modifiers), int(key, 16))

    def dump(self):
        return "+".join([m for m in MODIFIER_ORDER if m in self.modifiers] + [f"0x{self.vk:02x}"])

    @property
    def vks(self):
        """Віртуальні коди, що вважаються цією клавішею: цифра у верхньому ряду й на цифровому блоці — одне й те саме."""
        if 0x30 <= self.vk <= 0x39:
            return frozenset({self.vk, self.vk - 0x30 + VK_NUMPAD0})
        if VK_NUMPAD0 <= self.vk <= VK_NUMPAD0 + 9:
            return frozenset({self.vk, self.vk - VK_NUMPAD0 + 0x30})
        return frozenset({self.vk})

    def title(self):
        return " + ".join([MODIFIER_TITLES[m] for m in MODIFIER_ORDER if m in self.modifiers] + [key_title(self.vk)])

    def problem(self):
        """Чому цю комбінацію не можна використати, або None."""
        if self.vk in MODIFIER_VKS:
            return "Додайте до модифікатора основну клавішу."
        if not self.modifiers & {CTRL, ALT, WIN} and not VK_F1 <= self.vk <= VK_F24:
            return "Потрібен Ctrl, Alt або Win (або функціональна клавіша F1–F24) — інакше комбінація заважатиме набору тексту."
        action = RESERVED.get((self.modifiers, self.vk))
        if action:
            return f"{self.title()} — це стандартна дія «{action}», її не можна забирати."
        return None

    def warning(self):
        """Попередження, яке не забороняє комбінацію, або None."""
        if {CTRL, ALT} <= self.modifiers and not VK_F1 <= self.vk <= VK_F24:
            return ("Ctrl + Alt на багатьох розкладках — це AltGr: так можна втратити введення символів "
                    "(наприклад, @ чи € на німецькій розкладці).")
        return None


DEFAULT_HOTKEY = Hotkey(frozenset({CTRL}), 0x30)


def key_title(vk):
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if VK_F1 <= vk <= VK_F24:
        return f"F{vk - VK_F1 + 1}"
    if VK_NUMPAD0 <= vk <= VK_NUMPAD0 + 9:
        return f"Num {vk - VK_NUMPAD0}"
    if vk in KEY_TITLES:
        return KEY_TITLES[vk]
    # Решта (знаки пунктуації тощо) залежить від розкладки — назву дає сама Windows.
    buffer = ctypes.create_unicode_buffer(32)
    if user32.GetKeyNameTextW(user32.MapVirtualKeyW(vk, 0) << 16, buffer, 32):
        return buffer.value
    return f"0x{vk:02X}"


def modifiers_down():
    """Які модифікатори зараз фізично натиснуті."""
    def down(vk):
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    result = set()
    if down(VK_CONTROL):
        result.add(CTRL)
    if down(VK_SHIFT):
        result.add(SHIFT)
    if down(VK_MENU):
        result.add(ALT)
    if down(VK_LWIN) or down(VK_RWIN):
        result.add(WIN)
    return frozenset(result)


def wait_modifiers_released(timeout=1.0):
    """Чекаємо, поки користувач відпустить модифікатори гарячої клавіші: інакше імітовані
    натискання змішалися б із фізично утримуваними клавішами. Довше за timeout не чекаємо."""
    deadline = time.monotonic() + timeout
    while modifiers_down() and time.monotonic() < deadline:
        time.sleep(0.015)


# --- імітація натискань ---

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    # Потрібна лише для правильного розміру об'єднання в INPUT.
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _Union(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _Union)]


def _send(*keys):
    """keys — пари (віртуальний код, прапорці KEYEVENTF_*)."""
    events = (_INPUT * len(keys))()
    for event, (vk, flags) in zip(events, keys):
        event.type = INPUT_KEYBOARD
        event.ki = _KEYBDINPUT(wVk=vk, dwFlags=flags)
    user32.SendInput(len(events), events, ctypes.sizeof(_INPUT))


def send_ctrl_insert():
    """Стандартне «копіювати» (на відміну від Ctrl + C, у терміналах не перериває запущену програму)."""
    _send(
        (VK_CONTROL, 0),
        (VK_INSERT, KEYEVENTF_EXTENDEDKEY),
        (VK_INSERT, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP),
        (VK_CONTROL, KEYEVENTF_KEYUP),
    )


def send_mask_key():
    """Після поглинутої комбінації з Alt чи Win: інакше відпущений Alt відкрив би меню програми, а Win — «Пуск»."""
    _send((VK_MASK, 0), (VK_MASK, KEYEVENTF_KEYUP))
