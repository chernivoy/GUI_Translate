"""Текст під курсором: спершу через UI Automation, а якщо елемент його не віддає — через OCR."""
import ctypes
import logging
import math
import re
from ctypes import wintypes
from dataclasses import dataclass
from difflib import SequenceMatcher

import mss
import uiautomation as auto
from PIL import Image, ImageDraw

from .clipboard import copy_selection
from .processes import OWN_PID, pid_at

log = logging.getLogger(__name__)

SOURCE_UIA = "UI Automation"
SOURCE_OCR = "OCR"
SOURCE_CLIPBOARD = "Буфер обміну"
# Звідки взято текст: напис під курсором миші чи виділений текст.
MODE_CURSOR, MODE_SELECTION = "cursor", "selection"

# Найбільший напис, який ще вважаємо «підписом» (у пікселях при масштабі 100%).
LABEL_MAX_WIDTH = 1000
LABEL_MAX_HEIGHT = 80
# Область навколо курсора, яку розпізнаємо OCR, коли розмір напису невідомий.
OCR_HALF_WIDTH = 600
OCR_HALF_HEIGHT = 60
# Наскільки далеко від курсора може бути розпізнаний рядок, щоб його взяти.
OCR_MAX_DISTANCE = 25
# Проміжок між словами одного напису — 0.2–0.45 висоти слова, між пунктами меню — від 0.95.
WORD_GAP_SPLIT = 0.7
MAX_TEXT_LENGTH = 4500
# Ім'я класу .NET/Java на початку «назви»: щонайменше три ідентифікатори через крапку, хоч одна велика
# літера (щоб не плутати з іменами файлів на кшталт «report.final.pdf»), далі — можливо, «: вміст».
# WPF так називає елементи без власного підпису: «System.Windows.Controls.TabItem: Header:, Inhalt:».
TYPE_PREFIX = re.compile(r"^(?=[\w.]*[A-Z])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){2,}(?::|\s|$)\s*")
# Службовий хвіст комірки таблиці WPF/DevExpress: «…, Eintrag: 13, angezeigter Spaltenindex: 2»
# (англійською «…, Item: 13, Column Display Index: 2»).
GRID_CELL_SUFFIX = re.compile(r",\s*[^,]+:\s*\d+\s*,\s*[^,]*(index|индекс|індекс)[^,]*:\s*\d+\s*$", re.IGNORECASE)
# Наскільки глибоко перевіряємо предків елемента.
MAX_ANCESTORS = 30
# Межі обходу дерева вниз, коли за точкою знайшовся контейнер без тексту.
MAX_DESCENT_NODES = 400
MAX_DESCENT_DEPTH = 20

# Name таких елементів описує весь елемент (заголовок вікна чи панелі, увесь рядок таблиці),
# а не напис під курсором, — довіряємо йому, лише коли елемент розміром із напис.
CONTAINER_TYPES = {
    auto.ControlType.WindowControl,
    auto.ControlType.PaneControl,
    auto.ControlType.DocumentControl,
    auto.ControlType.CustomControl,
    auto.ControlType.GroupControl,
    auto.ControlType.ListControl,
    auto.ControlType.TreeControl,
    auto.ControlType.TableControl,
    auto.ControlType.DataGridControl,
    auto.ControlType.TabControl,
    auto.ControlType.ToolBarControl,
    auto.ControlType.MenuBarControl,
    auto.ControlType.StatusBarControl,
    auto.ControlType.HeaderControl,
    auto.ControlType.DataItemControl,
    auto.ControlType.ListItemControl,
    auto.ControlType.TreeItemControl,
}
# Внутрішній ідентифікатор у стилі camelCase/PascalCase («HeaderPanel», «dataPresenter»).
CAMEL_CASE = re.compile(r"^[A-Za-z_]\w*[a-z][A-Z]\w*$")
# Позначка клавіші швидкого доступу WPF: «_Datei», «Datei _öffnen».
ACCESS_KEY = re.compile(r"(^|(?<=\s))_(?=\w)")
# У полів введення показаний текст — це Value, а Name — лише підпис поля.
VALUE_FIRST_TYPES = {auto.ControlType.EditControl, auto.ControlType.ComboBoxControl}
# Таблиці й списки: Ctrl + Insert у них копіює рядки (з заголовками) чи файли, а не виділений текст.
GRID_TYPES = {
    auto.ControlType.DataGridControl,
    auto.ControlType.TableControl,
    auto.ControlType.DataItemControl,
    auto.ControlType.ListControl,
    auto.ControlType.ListItemControl,
    auto.ControlType.TreeControl,
    auto.ControlType.TreeItemControl,
}
GRID_SEARCH_DEPTH = 5
# Наскільки схожим має бути текст UI Automation на розпізнане OCR, щоб вважати елемент видимим.
VISIBLE_MATCH = 0.6

user32 = ctypes.windll.user32
shcore = ctypes.windll.shcore
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
MONITOR_DEFAULTTONEAREST = 2


@dataclass
class Capture:
    text: str
    source: str
    mode: str = MODE_CURSOR


@dataclass
class Screenshot:
    image: Image.Image
    left: int
    top: int


@dataclass
class _Box:
    text: str
    left: float
    top: float
    right: float
    bottom: float

    def distance_to(self, x, y):
        dx = max(self.left - x, 0, x - self.right)
        dy = max(self.top - y, 0, y - self.bottom)
        return math.hypot(dx, dy)


def dpi_scale(x, y):
    """Масштаб екрана (1.0 = 100%) на моніторі, де знаходиться точка."""
    monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONEAREST)
    dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
    if shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
        return dpi_x.value / 96
    return 1.0


def window_rect(hwnd):
    """Межі вікна у фізичних пікселях."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def describe_displays():
    """Монітори у фізичних пікселях з їхнім масштабом — для журналу."""
    with mss.MSS() as sct:
        monitors = sct.monitors[1:]
    return "; ".join(
        f'{m["width"]}x{m["height"]} у ({m["left"]}, {m["top"]}), '
        f'масштаб {dpi_scale(m["left"] + m["width"] // 2, m["top"] + m["height"] // 2):.0%}'
        for m in monitors
    )


def grab_screen_around(x, y, exclude=None):
    """Знімок області навколо курсора. Робиться одразу після кліку, поки вікно перекладу ще не з'явилося.

    exclude — прямокутник (left, top, right, bottom) нашого власного вікна, якщо воно вже відкрите:
    його зафарбовуємо, щоб OCR не прочитав текст самої програми.
    """
    scale = dpi_scale(x, y)
    half_w = int(max(LABEL_MAX_WIDTH, OCR_HALF_WIDTH) * scale)
    half_h = int(max(LABEL_MAX_HEIGHT, OCR_HALF_HEIGHT) * scale * 2)
    with mss.MSS() as sct:
        monitor = next(
            (m for m in sct.monitors[1:]
             if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]),
            sct.monitors[0],
        )
        left = max(x - half_w, monitor["left"])
        top = max(y - half_h, monitor["top"])
        right = min(x + half_w, monitor["left"] + monitor["width"])
        bottom = min(y + half_h, monitor["top"] + monitor["height"])
        shot = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    if exclude:
        background = image.resize((1, 1)).getpixel((0, 0))
        ImageDraw.Draw(image).rectangle(
            (exclude[0] - left, exclude[1] - top, exclude[2] - left, exclude[3] - top), fill=background
        )
    return Screenshot(image, left, top)


def _same_text(seen, expected):
    """Чи схоже розпізнане OCR (часто неточне й неповне) на текст з UI Automation."""
    seen, expected = _letters(seen), _letters(expected)
    if len(seen) < 3 or len(expected) < 3:
        return False
    match = SequenceMatcher(None, seen, expected, autojunk=False).find_longest_match(0, len(seen), 0, len(expected))
    return match.size >= VISIBLE_MATCH * min(len(seen), len(expected))


def _letters(text):
    return "".join(char for char in text.lower() if char.isalnum())


def _clean(text):
    lines = (line.strip() for line in text.replace("\r", "\n").splitlines())
    return "\n".join(line for line in lines if line)[:MAX_TEXT_LENGTH]


def _is_label_sized(rect, scale):
    return (0 < rect.width() <= LABEL_MAX_WIDTH * scale
            and 0 < rect.height() <= LABEL_MAX_HEIGHT * scale)


def _is_reliable(control, scale):
    """Чи можна вірити Name цього елемента як напису під курсором."""
    return control.ControlType not in CONTAINER_TYPES or _is_label_sized(control.BoundingRectangle, scale)


def _usable(text):
    """Відкидає «назви», які не є написом: WPF/WinForms часто віддають ім'я класу
    (наприклад, «MyCompany.Controls.CustomControls.MdiItem2»)."""
    text = GRID_CELL_SUFFIX.sub("", (text or "").strip()).strip()
    prefix = TYPE_PREFIX.match(text)
    if prefix:
        # «…ListBoxItem: Текст» → «Текст»; «…TabItem: Header:, Inhalt:» (лише порожні поля) → нічого.
        rest = text[prefix.end():].strip()
        text = "" if ":" in rest else rest
    return text


def _is_visible_at(control, x, y):
    """Чи лежить точка всередині всіх предків елемента.

    Деякі таблиці (наприклад, DevExpress) віддають за точкою рядок, прокручений за межі видимої
    області й прихований під іншою панеллю: такий рядок лежить поза межами самої таблиці.
    Але так само «виходять» за межі предків і випадні списки та спливні панелі, тож False означає
    лише «підозріло» — остаточно вирішує звірка з тим, що видно на екрані (OCR).
    """
    node = control.GetParentControl()
    for _ in range(MAX_ANCESTORS):
        if node is None or node.ControlType == auto.ControlType.WindowControl:
            return True
        rect = node.BoundingRectangle
        if rect.width() > 0 and rect.height() > 0 and not rect.contains(x, y):
            return False
        node = node.GetParentControl()
    return True


def _is_service_name(name, control):
    """«Назва», що збігається з класом чи ідентифікатором елемента або класом його батька, — службова
    («TextEdit» у порожніх комірках DevExpress, «HeaderPanel»)."""
    if not name or " " in name:
        return False
    if name == control.ClassName or (CAMEL_CASE.match(name) and name == control.AutomationId):
        return True
    parent = control.GetParentControl()
    return parent is not None and name == parent.ClassName


def _element_text(control):
    generic = control.LocalizedControlType.lower()

    def clean(text):
        text = ACCESS_KEY.sub("", _usable(text))
        # «Назва», що дорівнює типу елемента («Застосунок», «Application»), нічого не каже про напис.
        if _is_service_name(text, control) or text.lower() == generic:
            return ""
        return text

    name = clean(control.Name)
    value = ""
    value_pattern = control.GetPattern(auto.PatternId.ValuePattern)
    if value_pattern:
        value = clean(value_pattern.Value)
    if control.ControlType in VALUE_FIRST_TYPES:
        return value or name
    if name or value:
        return name or value
    legacy = control.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
    if legacy:
        return clean(legacy.Name) or clean(legacy.Value)
    return ""


def _children_text(control):
    """Текст дочірніх підписів: у WPF текст вкладки чи рядка списку часто лежить у вкладеному TextBlock."""
    parts = (_usable(child.Name) for child in control.GetChildren()
             if child.ControlType == auto.ControlType.TextControl)
    return " ".join(part for part in parts if part)


def _descend_to_text(root, x, y, scale):
    """Перший згори елемент із текстом, що містить точку, — обхід дерева вниз від root.

    Повертає (текст, межі) або None. Береться перший, а не найглибший елемент: усередині комірки
    таблиці часто лежать службові елементи з назвами на кшталт «TextEdit». Заходимо лише
    в елементи, що містять точку, і обмежуємо кількість переглянутих, щоб не застрягти у великих списках.
    """
    budget = [MAX_DESCENT_NODES]

    def visit(node, depth):
        if depth > MAX_DESCENT_DEPTH:
            return None
        for child in node.GetChildren():
            budget[0] -= 1
            if budget[0] < 0:
                return None
            rect = child.BoundingRectangle
            if not rect.contains(x, y):
                continue
            # Великий елемент без власного напису (панель, область) нічого не скаже про текст під курсором.
            if _is_label_sized(rect, scale) and _is_reliable(child, scale):
                text = _element_text(child)
                if text:
                    return text, rect
            found = visit(child, depth + 1)
            if found:
                return found
        return None

    return visit(root, 0)


def _uia_selection():
    """Виділення у фокусованому елементі (або його батькові, якщо фокус на вкладеному елементі).

    Повертає (чи вміє елемент повідомляти про виділення, текст виділення, чи фокус у таблиці/списку).
    """
    focused = auto.GetFocusedControl()
    supported = False
    for node in (focused, focused.GetParentControl() if focused else None):
        pattern = node.GetPattern(auto.PatternId.TextPattern) if node else None
        if pattern:
            supported = True
            text = "".join(selection.GetText(MAX_TEXT_LENGTH) for selection in pattern.GetSelection())
            if text.strip():
                return True, text, False
    return supported, "", _is_in_grid(focused)


def _is_in_grid(control):
    """Фокус у таблиці чи списку: там «копіювати» бере рядки чи файли, а не виділений текст."""
    node = control
    for _ in range(GRID_SEARCH_DEPTH):
        if node is None:
            return False
        if node.ControlType in GRID_TYPES:
            return True
        node = node.GetParentControl()
    return False


def _text_pattern_at(control, x, y):
    """Абзац під курсором у текстових елементах (Блокнот, Word, поля введення)."""
    pattern = control.GetPattern(auto.PatternId.TextPattern)
    if not pattern:
        return ""
    line = pattern.RangeFromPoint(x, y)
    if not line:
        return ""
    line.ExpandToEnclosingUnit(auto.TextUnit.Line, waitTime=0)
    # RangeFromPoint повертає найближчий текст навіть для порожнього місця — перевіряємо, що курсор на ньому.
    if not any(rect.contains(x, y) for rect in line.GetBoundingRectangles()):
        return ""
    paragraph = pattern.RangeFromPoint(x, y)
    paragraph.ExpandToEnclosingUnit(auto.TextUnit.Paragraph, waitTime=0)
    text = paragraph.GetText(MAX_TEXT_LENGTH + 1)
    if len(text) > MAX_TEXT_LENGTH:
        text = line.GetText(MAX_TEXT_LENGTH)
    return text.strip()


def _line_boxes(lines, scale):
    """Ділить рядки OCR на окремі написи там, де між словами великий проміжок (меню, панелі інструментів)."""
    boxes = []
    for words in lines:
        heights = sorted(word.bottom - word.top for word in words)
        split_gap = max(heights[len(heights) // 2], 8 * scale) * WORD_GAP_SPLIT
        groups = [[words[0]]]
        for prev, word in zip(words, words[1:]):
            if word.left - prev.right > split_gap:
                groups.append([])
            groups[-1].append(word)
        for group in groups:
            boxes.append(_Box(
                " ".join(w.text for w in group),
                min(w.left for w in group), min(w.top for w in group),
                max(w.right for w in group), max(w.bottom for w in group),
            ))
    return boxes


class TextCapturer:
    """Працює лише в одному робочому потоці: об'єкти COM і WinRT прив'язані до потоку, де створені."""

    def __init__(self, ocr_language=None):
        auto.InitializeUIAutomationInCurrentThread()
        self._ocr_language = ocr_language
        self._ocr = None

    def set_ocr_language(self, language_tag):
        self._ocr_language = language_tag
        if self._ocr:
            self._ocr.set_language(language_tag)

    def selected_text(self):
        """Текст, виділений в активній програмі.

        Якщо фокусований елемент сам повідомляє про виділення через UI Automation, довіряємо йому —
        навіть коли виділення немає. Буфер обміну (Ctrl + Insert) — лише для програм, які цього не вміють:
        редактори без виділення часто копіюють увесь рядок, і це сплутали б із виділенням.
        """
        supported, text, in_grid = False, "", False
        try:
            supported, text, in_grid = _uia_selection()
        except Exception:
            log.exception("UI Automation не змогла прочитати виділення")
        source = SOURCE_UIA
        if not supported and not in_grid:
            text = copy_selection()
            source = SOURCE_CLIPBOARD
            if "\t" in text:
                log.info("Скопійовано рядки таблиці, а не виділений текст: %r", text[:80])
                text = ""
        log.info("Виділений текст (%s): %r", source, text[:80])
        return Capture(_clean(text), source if text.strip() else "", MODE_SELECTION)

    def capture(self, x, y, screenshot, force_ocr=False):
        if pid_at(x, y) == OWN_PID:
            return Capture("", "")  # курсор над самим вікном перекладу
        # Масштаб беремо щоразу заново: під RDP він змінюється при перепідключенні з іншого пристрою,
        # а на різних моніторах сесії може бути різним.
        scale = dpi_scale(x, y)
        text, label_rect, inside_ancestors = "", None, True
        try:
            text, label_rect, inside_ancestors = self._from_uia(x, y, scale)
        except Exception:
            log.exception("UI Automation не змогла прочитати елемент")
        log.info("Під курсором (%d, %d), масштаб %d%%: UIA %r, межі напису %s", x, y, scale * 100, text[:80], label_rect)

        if text and not inside_ancestors:
            # Підозра на прихований елемент — звіряємо з тим, що справді видно на екрані.
            seen = self._from_ocr(x, y, scale, screenshot, None) if screenshot else ""
            visible = _same_text(seen, text)
            log.info("Елемент UIA поза межами предків, на екрані %r — %s", seen[:80],
                     "той самий текст" if visible else "інший текст, беремо його")
            if visible and not force_ocr:
                return Capture(_clean(text), SOURCE_UIA)
            return Capture(_clean(seen), SOURCE_OCR if seen else "")

        if text and not force_ocr:
            return Capture(_clean(text), SOURCE_UIA)

        ocr_text = ""
        if screenshot:
            if label_rect is not None:
                ocr_text = self._from_ocr(x, y, scale, screenshot, label_rect)
            # Межі від UI Automation бувають неточні (наприклад, у старих програм, які Windows розтягує
            # при високому DPI) — тоді шукаємо текст просто навколо курсора.
            if not ocr_text:
                ocr_text = self._from_ocr(x, y, scale, screenshot, None)
            log.info("OCR (%s): %r", self._ocr.description, ocr_text[:80])
        if ocr_text:
            return Capture(_clean(ocr_text), SOURCE_OCR)
        return Capture(_clean(text), SOURCE_UIA if text else "")

    def _from_uia(self, x, y, scale):
        """Повертає (текст, прямокутник напису для OCR або None, чи точка всередині всіх предків)."""
        control = auto.ControlFromPoint(x, y)
        if control is None:
            return "", None, True
        inside_ancestors = _is_visible_at(control, x, y)
        rect = control.BoundingRectangle
        label_rect = rect if inside_ancestors and _is_label_sized(rect, scale) and rect.contains(x, y) else None

        text = _text_pattern_at(control, x, y)
        if text:
            return text, label_rect, inside_ancestors

        if not _is_reliable(control, scale):
            # Над елементами буває прозорий шар (наприклад, індикатор завантаження над таблицею DevExpress),
            # і пошук за точкою знаходить його, а не комірку під ним — шукаємо текст серед сусідів шару.
            found = _descend_to_text(control.GetParentControl() or control, x, y, scale)
            if found:
                text, found_rect = found
                return text, found_rect, inside_ancestors
            return "", None, inside_ancestors

        # Іконка всередині кнопки чи пункту меню часто без назви — тоді беремо назву батька.
        node = control
        for _ in range(3):
            if node is None or not _is_reliable(node, scale):
                break
            text = _element_text(node)
            if not text and _is_label_sized(node.BoundingRectangle, scale):
                text = _children_text(node)
            if text:
                return text, label_rect, inside_ancestors
            node = node.GetParentControl()
        return "", label_rect, inside_ancestors

    def _from_ocr(self, x, y, scale, screenshot, label_rect):
        if self._ocr is None:
            from .ocr import WindowsOcr
            self._ocr = WindowsOcr(self._ocr_language)

        image = screenshot.image
        if label_rect is not None:
            pad = int(4 * scale)
            region = (label_rect.left - pad, label_rect.top - pad, label_rect.right + pad, label_rect.bottom + pad)
        else:
            region = (x - OCR_HALF_WIDTH * scale, y - OCR_HALF_HEIGHT * scale,
                      x + OCR_HALF_WIDTH * scale, y + OCR_HALF_HEIGHT * scale)
        left = int(max(region[0] - screenshot.left, 0))
        top = int(max(region[1] - screenshot.top, 0))
        right = int(min(region[2] - screenshot.left, image.width))
        bottom = int(min(region[3] - screenshot.top, image.height))
        if right - left < 4 or bottom - top < 4:
            return ""

        crop = image.crop((left, top, right, bottom))
        lines = self._ocr.recognize(crop, offset=(screenshot.left + left, screenshot.top + top), dpi_scale=scale)
        if not lines:
            return ""
        if label_rect is not None:
            # Розмір напису відомий — беремо весь текст усередині нього.
            return " ".join(" ".join(w.text for w in words) for words in lines)

        boxes = _line_boxes(lines, scale)
        best = min(boxes, key=lambda box: box.distance_to(x, y))
        if best.distance_to(x, y) > OCR_MAX_DISTANCE * scale:
            return ""
        return best.text
