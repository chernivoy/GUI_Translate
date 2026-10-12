"""Вікно зі списком програм, у яких Translate GUI не реагує на гарячу клавішу."""
from PySide6.QtCore import QFileInfo, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileIconProvider,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from . import theme as themes
from .popup import ChevronComboBox
from .processes import windowed_programs

ICON_SIZE = QSize(20, 20)


def normalize(name):
    """«Chrome» → «chrome.exe»: порівнюємо без урахування регістру й завжди з розширенням."""
    name = name.strip().strip('"').lower()
    if name and not name.endswith(".exe"):
        name += ".exe"
    return name


class ExclusionsDialog(QDialog):
    changed = Signal(list)

    def __init__(self, excluded, theme_name, hotkey_title):
        super().__init__(None, Qt.WindowCloseButtonHint)
        self.setWindowTitle("Виключені програми — Translate GUI")
        self.setWindowIcon(themes.app_icon())
        self.resize(460, 440)
        self._icons = QFileIconProvider()
        self._programs = windowed_programs()

        heading = QLabel("Виключені програми", objectName="heading")
        hint = QLabel(f"У цих програмах Translate GUI не реагує на {hotkey_title} — комбінація працює "
                      "як звичайно.", objectName="hint", wordWrap=True)

        self._candidate = ChevronComboBox(editable=True)
        self._candidate.lineEdit().setPlaceholderText("Програма, наприклад chrome.exe")
        self._candidate.lineEdit().returnPressed.connect(self._add)
        self._add_button = QPushButton("Додати", cursor=Qt.PointingHandCursor)
        self._add_button.clicked.connect(self._add)
        add_row = QHBoxLayout()
        add_row.addWidget(self._candidate, 1)
        add_row.addWidget(self._add_button)

        self._list = QListWidget(selectionMode=QAbstractItemView.ExtendedSelection)
        self._list.setIconSize(ICON_SIZE)
        self._list.itemSelectionChanged.connect(self._update_buttons)
        self._empty = QLabel("Винятків немає — Translate GUI працює в усіх програмах.",
                             objectName="hint", wordWrap=True, alignment=Qt.AlignCenter)

        self._remove_button = QPushButton("Видалити", cursor=Qt.PointingHandCursor)
        self._remove_button.clicked.connect(self._remove)
        done = QPushButton("Готово", cursor=Qt.PointingHandCursor, default=True)
        done.setProperty("primary", True)
        done.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self._remove_button)
        buttons.addStretch()
        buttons.addWidget(done)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addSpacing(4)
        layout.addLayout(add_row)
        layout.addWidget(self._list, 1)
        layout.addWidget(self._empty, 1)
        layout.addLayout(buttons)

        for name in sorted(excluded):
            self._append(name)
        self._refresh_candidates()
        self.apply_theme(theme_name)

    def excluded(self):
        return [self._list.item(row).data(Qt.UserRole) for row in range(self._list.count())]

    def apply_theme(self, theme_name):
        theme = themes.THEMES[theme_name]
        self.setStyleSheet(themes.stylesheet(theme))
        themes.apply_accent(self, theme)
        themes.apply_accent(self._candidate.view().window(), theme)
        self._candidate.chevron_color = QColor(theme.text_secondary)
        self._add_button.setIcon(themes.glyph_icon(themes.GLYPH_ADD, theme.text, 12))
        self._remove_button.setIcon(themes.glyph_icon(themes.GLYPH_DELETE, theme.text, 13))

    # --- допоміжне ---

    def _icon_for(self, name):
        program = self._programs.get(name)
        if program and program[1]:
            return self._icons.icon(QFileInfo(program[1]))
        return self._icons.icon(QFileIconProvider.IconType.File)

    def _append(self, name):
        item = QListWidgetItem(self._icon_for(name), self._programs.get(name, (name,))[0])
        item.setData(Qt.UserRole, name)
        self._list.addItem(item)

    def _refresh_candidates(self):
        """У списку для додавання — програми з відкритими вікнами, яких ще немає серед винятків."""
        excluded = set(self.excluded())
        self._candidate.clear()
        for key, (name, path, title) in sorted(self._programs.items()):
            if key not in excluded:
                self._candidate.addItem(self._icon_for(key), name, key)
                self._candidate.setItemData(self._candidate.count() - 1, title, Qt.ToolTipRole)
        self._candidate.setCurrentIndex(-1)
        self._candidate.clearEditText()
        self._update_buttons()

    def _update_buttons(self):
        has_items = self._list.count() > 0
        self._list.setVisible(has_items)
        self._empty.setVisible(not has_items)
        self._remove_button.setEnabled(bool(self._list.selectedItems()))

    def _add(self):
        name = normalize(self._candidate.currentText())
        if not name or name in self.excluded():
            return
        self._append(name)
        self._list.sortItems()
        self._refresh_candidates()
        self.changed.emit(self.excluded())

    def _remove(self):
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))
        self._refresh_candidates()
        self.changed.emit(self.excluded())
