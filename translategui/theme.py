"""Світла й темна теми вікна перекладу: кольори, таблиця стилів і значки."""
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPalette, QPixmap

LIGHT, DARK = "light", "dark"

# Гліфи шрифту значків Windows 11 (Segoe Fluent Icons); у Windows 10 ті самі коди має Segoe MDL2 Assets.
ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
GLYPH_SUN = ""
GLYPH_MOON = ""
GLYPH_PIN = ""
GLYPH_PINNED = ""
GLYPH_CLOSE = ""
GLYPH_COPY = ""
GLYPH_CHECK = ""
GLYPH_CAMERA = ""
GLYPH_BLOCKED = ""
GLYPH_ADD = ""
GLYPH_DELETE = ""

TEXT_FONTS = ("Segoe UI Variable Text", "Segoe UI")
TITLE_FONTS = ("Segoe UI Variable Display", "Segoe UI")


@dataclass(frozen=True)
class Theme:
    name: str
    card: str
    card_border: str
    field: str
    field_border: str
    field_border_hover: str
    translation_field: str
    text: str
    text_secondary: str
    text_placeholder: str
    accent: str
    accent_soft: str
    accent_text: str
    selection: str
    hover: str
    pressed: str
    error: str
    shadow_alpha: int


THEMES = {
    LIGHT: Theme(
        name=LIGHT,
        card="#F8F9FB",
        card_border="#E1E4E9",
        field="#FFFFFF",
        field_border="#DADDE3",
        field_border_hover="#C3C8D0",
        translation_field="#F0F5FC",
        text="#1D2025",
        text_secondary="#5F6670",
        text_placeholder="#8E949E",
        accent="#0F6CBD",
        accent_soft="rgba(15, 108, 189, 26)",
        accent_text="#FFFFFF",
        selection="#CFE2F6",
        hover="rgba(0, 0, 0, 13)",
        pressed="rgba(0, 0, 0, 24)",
        error="#C42B1C",
        shadow_alpha=34,
    ),
    # Темна, але не чорна: м'які сірі поверхні, текст не чисто білий — контраст тексту від 6:1.
    DARK: Theme(
        name=DARK,
        card="#2F3136",
        card_border="#45484F",
        field="#383B41",
        field_border="#4A4E56",
        field_border_hover="#5D626C",
        translation_field="#34404E",
        text="#ECEEF1",
        text_secondary="#AEB3BC",
        text_placeholder="#878D97",
        accent="#6CB6FF",
        accent_soft="rgba(108, 182, 255, 36)",
        accent_text="#0D1B2A",
        selection="#3F5F86",
        hover="rgba(255, 255, 255, 18)",
        pressed="rgba(255, 255, 255, 30)",
        error="#FF8F85",
        shadow_alpha=80,
    ),
}


def system_theme():
    """Тема Windows на момент запуску."""
    is_dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    return DARK if is_dark else LIGHT


def _first_available(families):
    installed = set(QFontDatabase.families())
    return next((family for family in families if family in installed), families[-1])


def text_font_family():
    return _first_available(TEXT_FONTS)


def stylesheet(theme: Theme):
    text_font = text_font_family()
    title_font = _first_available(TITLE_FONTS)
    return f"""
    QWidget {{
        font-family: "{text_font}";
        font-size: 10pt;
        color: {theme.text};
    }}
    #card {{
        background: {theme.card};
        border: 1px solid {theme.card_border};
        border-radius: 12px;
    }}
    #title {{
        font-family: "{title_font}";
        font-size: 10.5pt;
        font-weight: 600;
    }}
    #caption {{
        color: {theme.text_secondary};
        font-size: 8pt;
        font-weight: 600;
        letter-spacing: 0.6px;
    }}
    #badge {{
        background: {theme.accent_soft};
        color: {theme.accent};
        font-size: 8pt;
        font-weight: 600;
        border-radius: 8px;
        padding: 1px 8px;
    }}
    #status {{
        color: {theme.text_secondary};
        font-size: 8.5pt;
    }}
    #status[error="true"] {{
        color: {theme.error};
    }}
    QPlainTextEdit {{
        background: {theme.field};
        border: 1px solid {theme.field_border};
        border-radius: 8px;
        padding: 6px 8px;
        selection-background-color: {theme.selection};
        selection-color: {theme.text};
    }}
    QPlainTextEdit:hover {{
        border-color: {theme.field_border_hover};
    }}
    QPlainTextEdit:focus {{
        border-color: {theme.accent};
    }}
    QPlainTextEdit#translation {{
        background: {theme.translation_field};
        font-size: 11.5pt;
    }}
    QToolButton {{
        background: transparent;
        border: none;
        border-radius: 6px;
        padding: 5px;
        color: {theme.text_secondary};
    }}
    QToolButton[textButton="true"] {{
        padding: 4px 9px 4px 7px;
        font-size: 9pt;
    }}
    QToolButton:hover {{
        background: {theme.hover};
        color: {theme.text};
    }}
    QToolButton:pressed {{
        background: {theme.pressed};
    }}
    QToolButton:checked {{
        background: {theme.accent_soft};
    }}
    #segments {{
        background: {theme.field};
        border: 1px solid {theme.field_border};
        border-radius: 8px;
    }}
    #segments QToolButton {{
        padding: 3px 10px;
        font-size: 9pt;
        border-radius: 6px;
    }}
    #segments QToolButton:checked {{
        background: {theme.accent_soft};
        color: {theme.accent};
        font-weight: 600;
    }}
    QComboBox {{
        background: {theme.field};
        border: 1px solid {theme.field_border};
        border-radius: 6px;
        padding: 3px 26px 3px 9px;
        font-size: 9pt;
        min-height: 20px;
    }}
    QComboBox:hover {{
        border-color: {theme.field_border_hover};
    }}
    QComboBox:focus {{
        border-color: {theme.accent};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox::down-arrow {{
        image: none;
    }}
    QComboBox QAbstractItemView {{
        background: {theme.field};
        border: 1px solid {theme.field_border};
        border-radius: 6px;
        padding: 4px;
        outline: 0;
        selection-background-color: {theme.selection};
        selection-color: {theme.text};
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 4px 2px 4px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {theme.field_border_hover};
        border-radius: 3px;
        min-height: 24px;
        margin: 0 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {theme.text_placeholder};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        height: 0;
        background: transparent;
    }}
    QMenu {{
        background: {theme.field};
        border: 1px solid {theme.field_border};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 5px 24px 5px 12px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background: {theme.hover};
    }}
    QMenu::item:disabled {{
        color: {theme.text_placeholder};
    }}
    QMenu::separator {{
        height: 1px;
        background: {theme.field_border};
        margin: 4px 8px;
    }}
    QToolTip {{
        background: {theme.field};
        color: {theme.text};
        border: 1px solid {theme.field_border};
        border-radius: 6px;
        padding: 4px 8px;
    }}
    QDialog {{
        background: {theme.card};
    }}
    #heading {{
        font-family: "{title_font}";
        font-size: 13pt;
        font-weight: 600;
    }}
    #hint {{
        color: {theme.text_secondary};
    }}
    QListWidget {{
        background: {theme.field};
        border: 1px solid {theme.field_border};
        border-radius: 8px;
        padding: 4px;
        outline: 0;
    }}
    QListWidget::item {{
        padding: 6px 8px;
        border-radius: 6px;
    }}
    QListWidget::item:hover {{
        background: {theme.hover};
    }}
    QListWidget::item:selected {{
        background: {theme.selection};
        color: {theme.text};
    }}
    #recorder {{
        background: {theme.field};
        border: 1px solid {theme.field_border};
        border-radius: 8px;
        padding: 10px;
        font-size: 13pt;
        font-weight: 600;
        selection-background-color: transparent;
    }}
    #recorder:hover {{
        border-color: {theme.field_border_hover};
    }}
    #recorder:focus {{
        border: 2px solid {theme.accent};
        padding: 9px;
    }}
    QComboBox QLineEdit {{
        background: transparent;
        border: none;
        padding: 0;
        selection-background-color: {theme.selection};
        selection-color: {theme.text};
    }}
    QPushButton {{
        background: {theme.field};
        border: 1px solid {theme.field_border};
        border-radius: 6px;
        padding: 6px 14px;
    }}
    QPushButton:hover {{
        border-color: {theme.field_border_hover};
        background: {theme.hover};
    }}
    QPushButton:pressed {{
        background: {theme.pressed};
    }}
    QPushButton:disabled {{
        color: {theme.text_placeholder};
    }}
    QPushButton[primary="true"] {{
        background: {theme.accent};
        border-color: {theme.accent};
        color: {theme.accent_text};
        font-weight: 600;
    }}
    QPushButton[primary="true"]:hover {{
        border-color: {theme.text};
    }}
    """


def apply_accent(widget, theme: Theme):
    """Позначку вибраного пункту в списках стиль Windows малює системним акцентом — підміняємо на акцент теми."""
    palette = widget.palette()
    palette.setColor(QPalette.Accent, QColor(theme.accent))
    palette.setColor(QPalette.Highlight, QColor(theme.accent))
    widget.setPalette(palette)


def apply_app_color_scheme(name):
    """Системні частини вікон (заголовок діалогу, меню трею) — у тій самій темі, що й вікно перекладу."""
    QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark if name == DARK else Qt.ColorScheme.Light)


def app_icon(size=64):
    """Значок програми: «Tr» на синьому заокругленому квадраті (для трею й заголовка вікна)."""
    ratio = max((screen.devicePixelRatio() for screen in QGuiApplication.screens()), default=1.0)
    pixmap = QPixmap(QSize(size, size) * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#0F6CBD"))
    inset = size / 16
    painter.drawRoundedRect(QRectF(inset, inset, size - 2 * inset, size - 2 * inset), size / 5, size / 5)
    painter.setPen(QColor("white"))
    font = QFont(_first_available(TITLE_FONTS))
    font.setPixelSize(round(size * 0.48))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "Tr")
    painter.end()
    return QIcon(pixmap)


def glyph_icon(glyph, color, size=16, checked_glyph=None, checked_color=None):
    """Значок із гліфа шрифту значків; для кнопок-перемикачів — окремий вигляд увімкненого стану."""
    icon = QIcon()
    icon.addPixmap(_glyph_pixmap(glyph, color, size), QIcon.Normal, QIcon.Off)
    if checked_glyph:
        icon.addPixmap(_glyph_pixmap(checked_glyph, checked_color or color, size), QIcon.Normal, QIcon.On)
    return icon


def _glyph_pixmap(glyph, color, size):
    # Малюємо одразу в найбільшому масштабі серед моніторів, щоб значки були чіткими при 150–200%.
    ratio = max((screen.devicePixelRatio() for screen in QGuiApplication.screens()), default=1.0)
    pixmap = QPixmap(QSize(size, size) * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    font = QFont(_first_available(ICON_FONTS))
    font.setPixelSize(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, glyph)
    painter.end()
    return pixmap
