"""Спливаюче вікно з розпізнаним текстом і перекладом."""
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizeGrip,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme as themes
from .capture import MODE_CURSOR, MODE_SELECTION

CURSOR_OFFSET = 18
RETRANSLATE_DELAY_MS = 700
COPIED_FEEDBACK_MS = 1500
# Прозоре поле навколо картки, у якому малюється тінь (тіні DWM під RDP часто вимкнені).
SHADOW = 16
SHADOW_OFFSET_Y = 4
CARD_RADIUS = 12
CARD_SIZE = QSize(460, 340)
ICON_SIZE = 15


class ChevronComboBox(QComboBox):
    """Список мов зі стрілкою, намальованою кольором теми (стандартна стрілка ламається від таблиці стилів)."""

    chevron_color = QColor("#888888")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(self.chevron_color, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 14, self.height() / 2
        painter.drawPolyline([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)])


class _SizeGrip(QSizeGrip):
    """Ручка зміни розміру — три крапки по діагоналі кольором теми."""

    dot_color = QColor("#888888")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.dot_color)
        right, bottom = self.width() - 3, self.height() - 3
        for dx, dy in ((0, 0), (4, 0), (0, 4), (8, 0), (4, 4), (0, 8)):
            painter.drawEllipse(QPointF(right - dx, bottom - dy), 1.1, 1.1)


def _caption(text):
    return QLabel(text.upper(), objectName="caption")


def _icon_button(tooltip, checkable=False):
    button = QToolButton(toolTip=tooltip, checkable=checkable, cursor=Qt.PointingHandCursor)
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    return button


def _text_button(text, tooltip):
    button = _icon_button(tooltip)
    button.setText(" " + text)  # відступ між значком і текстом: таблиця стилів його не задає
    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    button.setProperty("textButton", True)
    return button


class PopupWindow(QWidget):
    language_changed = Signal(str)
    text_edited = Signal(str)
    ocr_requested = Signal()
    theme_changed = Signal(str)
    exclude_requested = Signal(str)
    mode_requested = Signal(str)

    def __init__(self, languages, current_language, theme_name):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        # Не забираємо фокус у програми, де користувач клікнув; фокус з'явиться після кліку по вікну.
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(CARD_SIZE.width() + 2 * SHADOW, CARD_SIZE.height() + 2 * SHADOW)
        self._drag_offset = None
        self._theme = themes.THEMES[theme_name]
        self._app_name = ""

        card = QFrame(objectName="card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        outer.addWidget(card)

        # Заголовок: назва, виняток для програми, перемикач теми, закріплення, закриття.
        self._exclude = _icon_button("")
        self._exclude.clicked.connect(lambda: self.exclude_requested.emit(self._app_name))
        self._theme_button = _icon_button("")
        self._theme_button.clicked.connect(self._toggle_theme)
        self._pin = _icon_button("Не закривати при кліку поза вікном", checkable=True)
        self._close = _icon_button("Закрити (Esc)")
        self._close.clicked.connect(self.hide)
        logo = QLabel()
        logo.setPixmap(themes.app_icon(18).pixmap(18, 18))
        header = QHBoxLayout()
        header.setSpacing(2)
        header.addWidget(logo)
        header.addSpacing(6)
        header.addWidget(QLabel("Translate GUI", objectName="title"))
        header.addStretch()
        header.addWidget(self._exclude)
        header.addWidget(self._theme_button)
        header.addWidget(self._pin)
        header.addWidget(self._close)

        # Оригінал: перемикач «звідки брати текст», поруч — чим його прочитано (UI Automation, OCR, буфер).
        segments = QFrame(objectName="segments")
        segments_layout = QHBoxLayout(segments)
        segments_layout.setContentsMargins(2, 2, 2, 2)
        segments_layout.setSpacing(2)
        self._modes = QButtonGroup(self, exclusive=True)
        for mode, title, tooltip in (
            (MODE_CURSOR, "Під курсором", "Напис під курсором миші"),
            (MODE_SELECTION, "Виділений текст", "Текст, виділений у момент натискання гарячої клавіші"),
        ):
            button = QToolButton(text=title, toolTip=tooltip, checkable=True, cursor=Qt.PointingHandCursor)
            button.setProperty("mode", mode)
            self._modes.addButton(button)
            segments_layout.addWidget(button)
        self._modes.buttonClicked.connect(lambda button: self.mode_requested.emit(button.property("mode")))
        self._source = QLabel(objectName="badge", visible=False)
        self._ocr = _icon_button("Розпізнати OCR — прочитати текст під курсором із зображення екрана")
        self._ocr.clicked.connect(self.ocr_requested)
        original_header = QHBoxLayout()
        original_header.setSpacing(8)
        original_header.addWidget(segments)
        original_header.addWidget(self._source, 0, Qt.AlignVCenter)
        original_header.addStretch()
        original_header.addWidget(self._ocr)

        self._original = QPlainTextEdit(placeholderText="Текст для перекладу")
        self._original.textChanged.connect(self._on_original_edited)
        self._retranslate_timer = QTimer(self, singleShot=True, interval=RETRANSLATE_DELAY_MS)
        self._retranslate_timer.timeout.connect(lambda: self.text_edited.emit(self.original_text()))

        # Переклад: мова перекладу стоїть поруч із ним.
        self._language = ChevronComboBox(cursor=Qt.PointingHandCursor)
        self._language.setMaxVisibleItems(18)
        for code, name in languages:
            self._language.addItem(name, code)
        self._language.setCurrentIndex(max(self._language.findData(current_language), 0))
        self._language.currentIndexChanged.connect(
            lambda: self.language_changed.emit(self._language.currentData())
        )
        self._copy = _text_button("Копіювати", "Копіювати переклад")
        self._copy.clicked.connect(self._copy_translation)
        self._copied_timer = QTimer(self, singleShot=True, interval=COPIED_FEEDBACK_MS)
        self._copied_timer.timeout.connect(self._reset_copy_button)
        translation_header = QHBoxLayout()
        translation_header.setSpacing(6)
        translation_header.addWidget(_caption("Переклад"))
        translation_header.addStretch()
        translation_header.addWidget(self._language)
        translation_header.addWidget(self._copy)

        self._translation = QPlainTextEdit(readOnly=True, objectName="translation")

        self._status = QLabel(objectName="status", wordWrap=True)
        self._grip = _SizeGrip(self)
        footer = QHBoxLayout()
        footer.addWidget(self._status, 1)
        footer.addWidget(self._grip, 0, Qt.AlignBottom | Qt.AlignRight)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 10, 10, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addSpacing(4)
        layout.addLayout(original_header)
        layout.addWidget(self._original, 2)
        layout.addSpacing(6)
        layout.addLayout(translation_header)
        layout.addWidget(self._translation, 3)
        layout.addLayout(footer)

        QShortcut(QKeySequence(Qt.Key_Escape), self, self.hide)
        self._apply_theme()

    # --- стан вікна ---

    def show_loading(self, cursor_pos: QPoint, app_name="", status="Шукаю текст…"):
        """app_name — exe програми, з якої взято текст; для неї кнопка ⊘ вимикає Translate GUI."""
        self._app_name = app_name
        self._exclude.setVisible(bool(app_name))
        self._exclude.setToolTip(f"Не реагувати на гарячу клавішу в «{app_name}»")
        self.set_mode(None)
        self.set_busy(True)
        self._set_original("")
        self._translation.clear()
        self._source.hide()
        self.show_status(status)
        self._move_near(cursor_pos)
        self.show()
        self.raise_()

    def show_capture(self, text, source, empty_message=None):
        """empty_message — підказка, якщо тексту немає; без неї показуємо помилку «не знайдено»."""
        self.set_busy(False)
        self._set_original(text)
        self._source.setText(source)
        self._source.setVisible(bool(source))
        if not text:
            self._translation.clear()
            if empty_message:
                self.show_status(empty_message)
            else:
                self.show_status("Тексту під курсором не знайдено. Спробуйте «Розпізнати OCR» "
                                 "або введіть текст вручну.", error=True)

    def set_busy(self, busy):
        """Поки текст читається, перемикач режиму й OCR вимкнені — щоб не перебити це читання."""
        for button in self._modes.buttons():
            button.setEnabled(not busy)
        self._ocr.setEnabled(not busy)

    def card_margin(self):
        """Ширина прозорого поля під тінь навколо картки у фізичних пікселях."""
        return round(SHADOW * self.devicePixelRatioF())

    def set_mode(self, mode):
        """Позначає в перемикачі, звідки взято текст (None — ще невідомо)."""
        self._modes.setExclusive(False)
        for button in self._modes.buttons():
            button.setChecked(button.property("mode") == mode)
        self._modes.setExclusive(True)

    def focus_input(self):
        """Курсор у поле оригіналу, щоб одразу вводити текст (Windows може не дозволити забрати фокус)."""
        self.activateWindow()
        self._original.setFocus()

    def show_translation(self, text, source_language=""):
        self._translation.setPlainText(text)
        if not text:
            self.show_status("")
        elif source_language:
            self.show_status(f"Google Translate · мова оригіналу: {source_language}")
        else:
            self.show_status("Google Translate")

    def show_status(self, text, error=False):
        self._status.setText(text)
        self._status.setProperty("error", error)
        self._status.style().polish(self._status)

    def original_text(self):
        return self._original.toPlainText().strip()

    def close_if_not_pinned(self):
        if self.isVisible() and not self._pin.isChecked():
            self.hide()

    # --- тема ---

    @property
    def theme_name(self):
        return self._theme.name

    def _toggle_theme(self):
        name = themes.LIGHT if self._theme.name == themes.DARK else themes.DARK
        self._theme = themes.THEMES[name]
        self._apply_theme()
        self.theme_changed.emit(name)

    def _apply_theme(self):
        theme = self._theme
        self.setStyleSheet(themes.stylesheet(theme))
        themes.apply_accent(self, theme)
        # Список мов — окреме вікно, палітра до нього сама не переходить.
        themes.apply_accent(self._language.view(), theme)
        themes.apply_accent(self._language.view().window(), theme)
        secondary = theme.text_secondary
        self._exclude.setIcon(themes.glyph_icon(themes.GLYPH_BLOCKED, secondary, ICON_SIZE))
        if theme.name == themes.DARK:
            self._theme_button.setIcon(themes.glyph_icon(themes.GLYPH_SUN, secondary, ICON_SIZE))
            self._theme_button.setToolTip("Світла тема")
        else:
            self._theme_button.setIcon(themes.glyph_icon(themes.GLYPH_MOON, secondary, ICON_SIZE))
            self._theme_button.setToolTip("Темна тема")
        self._pin.setIcon(themes.glyph_icon(themes.GLYPH_PIN, secondary, ICON_SIZE,
                                            checked_glyph=themes.GLYPH_PINNED, checked_color=theme.accent))
        self._close.setIcon(themes.glyph_icon(themes.GLYPH_CLOSE, secondary, ICON_SIZE - 3))
        self._ocr.setIcon(themes.glyph_icon(themes.GLYPH_CAMERA, secondary, ICON_SIZE - 1))
        self._reset_copy_button()
        self._language.chevron_color = QColor(secondary)
        self._grip.dot_color = QColor(theme.text_placeholder)
        self.update()

    # --- допоміжне ---

    def _copy_translation(self):
        QGuiApplication.clipboard().setText(self._translation.toPlainText())
        self._copy.setText(" Скопійовано")
        self._copy.setIcon(themes.glyph_icon(themes.GLYPH_CHECK, self._theme.accent, ICON_SIZE - 1))
        self._copied_timer.start()

    def _reset_copy_button(self):
        self._copy.setText(" Копіювати")
        self._copy.setIcon(themes.glyph_icon(themes.GLYPH_COPY, self._theme.text_secondary, ICON_SIZE - 1))

    def _set_original(self, text):
        self._original.blockSignals(True)
        self._original.setPlainText(text)
        self._original.blockSignals(False)
        self._retranslate_timer.stop()

    def _on_original_edited(self):
        self._retranslate_timer.start()

    def _move_near(self, pos: QPoint):
        """Ставить картку праворуч знизу від курсора, а якщо не влазить — з іншого боку."""
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        card_width, card_height = self.width() - 2 * SHADOW, self.height() - 2 * SHADOW
        x = pos.x() + CURSOR_OFFSET
        y = pos.y() + CURSOR_OFFSET
        if x + card_width > area.right():
            x = pos.x() - CURSOR_OFFSET - card_width
        if y + card_height > area.bottom():
            y = pos.y() - CURSOR_OFFSET - card_height
        self.move(max(x, area.left()) - SHADOW, max(y, area.top()) - SHADOW)

    def paintEvent(self, event):
        """М'яка тінь навколо картки: кілька заокруглених шарів зі спадною прозорістю."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        card = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW).translated(0, SHADOW_OFFSET_Y)
        for step in range(SHADOW, 0, -1):
            alpha = self._theme.shadow_alpha * (1 - step / SHADOW) ** 2 / 4
            painter.setBrush(QColor(0, 0, 0, round(alpha)))
            painter.drawRoundedRect(card.adjusted(-step, -step, step, step), CARD_RADIUS + step, CARD_RADIUS + step)

    # --- перетягування за порожні місця ---

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
