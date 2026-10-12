"""Вікно налаштування гарячої клавіші: клікнути поле й натиснути потрібну комбінацію."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from . import theme as themes
from .keys import (
    ALT,
    CTRL,
    DEFAULT_HOTKEY,
    MODIFIER_ORDER,
    MODIFIER_TITLES,
    MODIFIER_VKS,
    SHIFT,
    VK_TO_MODIFIER,
    WIN,
    Hotkey,
)

VK_ESCAPE = 0x1B
QT_MODIFIERS = ((Qt.ControlModifier, CTRL), (Qt.ShiftModifier, SHIFT), (Qt.AltModifier, ALT), (Qt.MetaModifier, WIN))


class HotkeyRecorder(QLineEdit):
    """Поле, що записує натиснуту комбінацію. Бере віртуальний код клавіші від Windows — той самий,
    за яким комбінацію потім ловить хук, — тож результат не залежить від розкладки."""

    recorded = Signal(object)

    def __init__(self, hotkey):
        super().__init__(readOnly=True, alignment=Qt.AlignCenter, objectName="recorder",
                         cursor=Qt.PointingHandCursor)
        self._hotkey = hotkey
        self.setPlaceholderText("Натисніть комбінацію клавіш…")
        self.setText(hotkey.title())

    def set_hotkey(self, hotkey):
        self._hotkey = hotkey
        self.setText(hotkey.title())

    def event(self, event):
        # Перехоплюємо всі клавіші (Tab, Alt тощо): інакше Qt використав би їх для фокуса чи меню.
        if event.type() == QEvent.ShortcutOverride:
            event.accept()
            return True
        if event.type() == QEvent.KeyPress:
            self._key_pressed(event)
            return True
        if event.type() == QEvent.KeyRelease:
            return True
        return super().event(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.clear()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.setText(self._hotkey.title())

    def _key_pressed(self, event):
        modifiers = frozenset(name for flag, name in QT_MODIFIERS if event.modifiers() & flag)
        vk = event.nativeVirtualKey()
        if vk == VK_ESCAPE and not modifiers:  # Esc — відмовитися від запису
            self.clearFocus()
            return
        if vk in MODIFIER_VKS or not vk:
            # Поки натиснуті лише модифікатори — показуємо, що вже є. Qt не додає до modifiers()
            # модифікатор, клавішу якого саме натиснули, тож беремо його з коду клавіші.
            modifiers |= {VK_TO_MODIFIER[vk]} if vk in VK_TO_MODIFIER else set()
            self.setText(" + ".join(MODIFIER_TITLES[m] for m in MODIFIER_ORDER if m in modifiers) + " + …")
            return
        self._hotkey = Hotkey(modifiers, vk)
        self.setText(self._hotkey.title())
        self.recorded.emit(self._hotkey)


class HotkeyDialog(QDialog):
    changed = Signal(object)

    def __init__(self, hotkey, theme_name):
        super().__init__(None, Qt.WindowCloseButtonHint)
        self.setWindowTitle("Гаряча клавіша — Translate GUI")
        self.setWindowIcon(themes.app_icon())
        self.resize(440, 260)
        self._candidate = hotkey

        heading = QLabel("Гаряча клавіша", objectName="heading")
        hint = QLabel("Відкриває переклад виділеного тексту, а якщо нічого не виділено — напису під курсором. "
                      "Клікніть поле й натисніть нову комбінацію.", objectName="hint", wordWrap=True)
        self._recorder = HotkeyRecorder(hotkey)
        self._recorder.recorded.connect(self._validate)
        self._message = QLabel(objectName="status", wordWrap=True)

        reset = QPushButton(f"Скинути ({DEFAULT_HOTKEY.title()})", cursor=Qt.PointingHandCursor)
        reset.clicked.connect(self._reset)
        cancel = QPushButton("Скасувати", cursor=Qt.PointingHandCursor)
        cancel.clicked.connect(self.close)
        self._save = QPushButton("Зберегти", cursor=Qt.PointingHandCursor, default=True)
        self._save.setProperty("primary", True)
        self._save.clicked.connect(self._apply)
        buttons = QHBoxLayout()
        buttons.addWidget(reset)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(self._save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addSpacing(4)
        layout.addWidget(self._recorder)
        layout.addWidget(self._message)
        layout.addStretch()
        layout.addLayout(buttons)

        self.apply_theme(theme_name)
        self._validate(hotkey)
        self._save.setFocus()  # щоб поле не почало запис одразу при відкритті

    def apply_theme(self, theme_name):
        theme = themes.THEMES[theme_name]
        self.setStyleSheet(themes.stylesheet(theme))
        themes.apply_accent(self, theme)

    def _validate(self, hotkey):
        self._candidate = hotkey
        problem = hotkey.problem()
        self._show_message(problem or hotkey.warning() or "", error=bool(problem))
        self._save.setEnabled(not problem)

    def _show_message(self, text, error):
        self._message.setText(text)
        self._message.setVisible(bool(text))
        self._message.setProperty("error", error)
        self._message.style().polish(self._message)

    def _reset(self):
        self._recorder.set_hotkey(DEFAULT_HOTKEY)
        self._validate(DEFAULT_HOTKEY)

    def _apply(self):
        self.changed.emit(self._candidate)
        self.close()
