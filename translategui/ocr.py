"""Розпізнавання тексту вбудованим OCR Windows (Windows.Media.Ocr)."""
import asyncio
import unicodedata
from dataclasses import dataclass, replace

from PIL import Image, ImageOps

# winrt імпортується тут, а не на рівні програми: якщо завантажити його раніше за Qt,
# процес падає з access violation. Цей модуль імпортується лише після Qt.
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

# Режим, у якому кирилицю читає кириличний рушій, а латиницю — латинський.
AUTO = "auto"

# Перед OCR картинку зводимо до масштабу 100%, а потім збільшуємо: дрібний шрифт інтерфейсу
# (9–12 px) OCR розпізнає погано. Зведення до 100% також прибирає розмиття, яке дає Windows,
# коли розтягує старі програми без підтримки DPI (типово для RDP зі 150–200%).
UPSCALE = 2
# Текст, що торкається краю картинки, OCR не бачить — додаємо поле кольору фону.
MARGIN = 16

# Однакові на вигляд латинські й кириличні літери. Вони не кажуть, якою абеткою написано слово,
# а в кириличних словах ми замінюємо ними латинські, які вставляє OCR (зокрема i → українська і).
LATIN_TO_CYRILLIC = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "y": "у", "x": "х", "i": "і", "ï": "ї",
    "A": "А", "B": "В", "E": "Е", "K": "К", "M": "М", "H": "Н", "O": "О", "P": "Р", "C": "С",
    "T": "Т", "X": "Х", "I": "І", "Y": "У",
})
LATIN_LOOKALIKES = {chr(code) for code in LATIN_TO_CYRILLIC}
CYRILLIC_LOOKALIKES = set(LATIN_TO_CYRILLIC.values())
CYRILLIC, LATIN = "cyrillic", "latin"


@dataclass
class Word:
    text: str
    left: float
    top: float
    right: float
    bottom: float


def available_languages():
    """Список (тег, назва) мов, для яких у Windows встановлено OCR."""
    return [(lang.language_tag, lang.native_name) for lang in OcrEngine.available_recognizer_languages]


def default_language():
    """Мова OCR, яку Windows вибирає за мовами профілю користувача."""
    engine = OcrEngine.try_create_from_user_profile_languages()
    return engine.recognizer_language.language_tag if engine else None


def auto_languages():
    """(кирилична, латинська) мови OCR для режиму «Авто»; будь-яка може бути None."""
    tags = [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
    cyrillic = next((tag for tag in tags if Language(tag).script == "Cyrl"), None)
    latin_tags = [tag for tag in tags if Language(tag).script == "Latn"]
    profile = default_language()
    if profile in latin_tags:
        latin = profile
    elif "en-US" in latin_tags:
        latin = "en-US"
    else:
        latin = next(iter(latin_tags), None)
    return cyrillic, latin


def _create_engine(language_tag):
    if language_tag and OcrEngine.is_language_supported(Language(language_tag)):
        return OcrEngine.try_create_from_language(Language(language_tag))
    return None


def _script_counts(text):
    """(кириличні, латинські) літери, які не мають двійника в іншій абетці."""
    cyrillic = latin = 0
    for char in text:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        if name.startswith("CYRILLIC") and char not in CYRILLIC_LOOKALIKES:
            cyrillic += 1
        elif name.startswith("LATIN") and char not in LATIN_LOOKALIKES:
            latin += 1
    return cyrillic, latin


def _as_cyrillic(word):
    return replace(word, text=word.text.translate(LATIN_TO_CYRILLIC))


def _plausible(text):
    """Чи схоже прочитання на справжнє слово: без цифр серед літер, однією абеткою,
    з нормальним регістром (усі малі, усі великі або з великої літери)."""
    letters = "".join(char for char in text if char.isalpha())
    if not letters:
        return True
    if any(char.isdigit() for char in text):
        return False
    if len({unicodedata.name(char, "").split(" ")[0] for char in letters}) > 1:
        return False
    return letters.islower() or letters.isupper() or (letters[0].isupper() and letters[1:].islower())


def _classify(word, latin_reading):
    """CYRILLIC, LATIN або None, якщо з самого слова не зрозуміло."""
    cyrillic, latin = _script_counts(word.text)
    if latin > cyrillic:
        return LATIN
    if cyrillic == 0:
        # Лише цифри, розділові знаки чи літери, однакові в обох абетках («He», «TOP»).
        return None
    if not latin_reading or not _plausible(latin_reading):
        # Латинський рушій прочитав кириличне слово як нісенітницю («3a», «BinbHe»).
        return CYRILLIC
    # Обидва прочитання схожі на слова («ги» / «zu», «тап» / «man») — вирішать сусіди.
    return None if _plausible(word.text) else LATIN


def _resolve_unsure(kinds):
    """Невизначені слова отримують абетку найближчого визначеного сусіда в рядку (за рівності — лівого)."""
    known = [(index, kind) for index, kind in enumerate(kinds) if kind]
    if not known:
        return [LATIN] * len(kinds)
    return [
        kind or min(known, key=lambda item: (abs(item[0] - index), item[0] > index))[1]
        for index, kind in enumerate(kinds)
    ]


def _merge_scripts(cyrillic_lines, latin_lines):
    """Змішаний текст: для кожного слова вирішуємо, кириличне воно чи латинське, і беремо прочитання
    відповідного рушія. Кириличний рушій інколи «бачить» у латинських словах кириличні двійники
    («zu» → «ги», «Umbau» → «ИтЬаи»), тому зважаємо й на прочитання латинського рушія, і на сусідів."""
    latin_words = [word for line in latin_lines for word in line]
    used = set()
    merged = []
    for line in cyrillic_lines:
        counterparts = []
        for word in line:
            pad = (word.bottom - word.top) / 2
            matches = [
                index for index, other in enumerate(latin_words)
                if index not in used
                and word.left - pad <= (other.left + other.right) / 2 <= word.right + pad
                and word.top - pad <= (other.top + other.bottom) / 2 <= word.bottom + pad
            ]
            used.update(matches)
            counterparts.append([latin_words[index] for index in matches])

        kinds = _resolve_unsure([
            _classify(word, " ".join(other.text for other in latin))
            for word, latin in zip(line, counterparts)
        ])
        result = []
        for word, latin, kind in zip(line, counterparts, kinds):
            if kind == CYRILLIC:
                result.append(_as_cyrillic(word))
            elif latin:
                result.extend(latin)
            else:
                result.append(word)
        merged.append(result)
    return merged


class WindowsOcr:
    def __init__(self, language=AUTO):
        self.set_language(language)

    def set_language(self, language):
        language = language or AUTO
        self._cyrillic = self._latin = self._single = None
        if language == AUTO:
            cyrillic, latin = auto_languages()
            self._cyrillic, self._latin = _create_engine(cyrillic), _create_engine(latin)
            if not (self._cyrillic and self._latin):
                # Встановлено мови лише однієї абетки — авторежиму нема з чого вибирати.
                self._single = self._cyrillic or self._latin
        else:
            self._single = _create_engine(language)
        if not (self._single or (self._cyrillic and self._latin)):
            self._single = OcrEngine.try_create_from_user_profile_languages()
        if not (self._single or (self._cyrillic and self._latin)):
            raise RuntimeError("У Windows не встановлено жодної мови для OCR")

    @property
    def description(self):
        if self._single:
            return self._single.recognizer_language.language_tag
        return (f"авто: {self._cyrillic.recognizer_language.language_tag}"
                f" + {self._latin.recognizer_language.language_tag}")

    def recognize(self, image: Image.Image, offset=(0, 0), dpi_scale=1.0):
        """Розпізнає текст і повертає рядки як списки слів у координатах екрана.

        dpi_scale — масштаб монітора, з якого знято картинку (1.0 = 100%, 2.0 = 200%).
        """
        bitmap = _prepare(image, dpi_scale)
        scale = max(dpi_scale, 1.0)

        def read(engine):
            return _read(engine, bitmap, offset, scale)

        if self._single:
            return read(self._single)

        cyrillic_lines = read(self._cyrillic)
        cyrillic, latin = _script_counts(" ".join(word.text for line in cyrillic_lines for word in line))
        if cyrillic == 0:
            return read(self._latin) or cyrillic_lines
        if latin == 0:
            return [[_as_cyrillic(word) for word in line] for line in cyrillic_lines]
        return _merge_scripts(cyrillic_lines, read(self._latin))


def _prepare(image, dpi_scale):
    gray = ImageOps.grayscale(image)
    if dpi_scale > 1:
        gray = gray.resize((max(round(gray.width / dpi_scale), 1), max(round(gray.height / dpi_scale), 1)),
                           Image.LANCZOS)
    # Світлий текст на темному фоні OCR читає гірше — інвертуємо темні області.
    if sum(gray.resize((32, 32)).getdata()) / 1024 < 110:
        gray = ImageOps.invert(gray)
    edge = [gray.getpixel((x, y)) for x in range(gray.width) for y in (0, gray.height - 1)]
    gray = ImageOps.expand(gray, border=MARGIN, fill=max(set(edge), key=edge.count))
    gray = gray.resize((gray.width * UPSCALE, gray.height * UPSCALE), Image.LANCZOS)

    writer = DataWriter()
    writer.write_bytes(gray.convert("RGBA").tobytes("raw", "BGRA"))
    return SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, gray.width, gray.height
    )


def _read(engine, bitmap, offset, scale):
    async def recognize():
        return await engine.recognize_async(bitmap)

    result = asyncio.run(recognize())

    # Назад у координати екрана: прибираємо збільшення, поле і зведення до 100%.
    def to_screen(value, origin):
        return origin + (value / UPSCALE - MARGIN) * scale

    lines = []
    for line in result.lines:
        words = []
        for word in line.words:
            r = word.bounding_rect
            words.append(Word(
                word.text,
                to_screen(r.x, offset[0]),
                to_screen(r.y, offset[1]),
                to_screen(r.x + r.width, offset[0]),
                to_screen(r.y + r.height, offset[1]),
            ))
        if words:
            lines.append(words)
    return lines
