"""Переклад через безкоштовні (неофіційні) адреси Google Translate."""
import requests
# З deep-translator беремо лише перелік мов Google; самі запити робимо напряму —
# сторінку translate.google.com/m, яку він парсить, Google часто блокує (HTTP 429).
from deep_translator.constants import GOOGLE_LANGUAGES_TO_CODES

# Google приймає до 5000 символів за запит.
MAX_CHARS = 4900
CACHE_SIZE = 500
TIMEOUT = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
}

# Найуживаніші мови — нагорі списку, з власними назвами.
POPULAR_LANGUAGES = [
    ("uk", "Українська"),
    ("en", "English"),
    ("de", "Deutsch"),
    ("pl", "Polski"),
    ("fr", "Français"),
    ("es", "Español"),
    ("it", "Italiano"),
    ("pt", "Português"),
    ("nl", "Nederlands"),
    ("cs", "Čeština"),
    ("tr", "Türkçe"),
    ("zh-CN", "中文 (简体)"),
    ("ja", "日本語"),
    ("ko", "한국어"),
]


def language_choices():
    """Список (код, назва): спершу популярні мови, далі решта мов Google за абеткою."""
    popular_codes = {code for code, _ in POPULAR_LANGUAGES}
    rest = sorted(
        ((code, name.title()) for name, code in GOOGLE_LANGUAGES_TO_CODES.items() if code not in popular_codes),
        key=lambda item: item[1],
    )
    return POPULAR_LANGUAGES + rest


def _via_chrome_extension_api(text, target):
    response = requests.post(
        "https://clients5.google.com/translate_a/t",
        params={"client": "dict-chrome-ex", "sl": "auto", "tl": target},
        data={"q": text}, headers=HEADERS, timeout=TIMEOUT,
    )
    response.raise_for_status()
    # Відповідь: [["переклад", "мова оригіналу"]]
    item = response.json()[0]
    if isinstance(item, str):
        return item, ""
    return item[0], item[1] if len(item) > 1 else ""


def _via_gtx_api(text, target):
    response = requests.post(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t"},
        data={"q": text}, headers=HEADERS, timeout=TIMEOUT,
    )
    response.raise_for_status()
    # Відповідь: [[["переклад речення", "оригінал", ...], ...], null, "мова оригіналу", ...]
    data = response.json()
    return "".join(segment[0] for segment in data[0] if segment[0]), data[2] or ""


ENDPOINTS = (_via_chrome_extension_api, _via_gtx_api)


class Translator:
    def __init__(self):
        self._cache = {}

    def translate(self, text, target):
        """Повертає (переклад, код мови оригіналу)."""
        key = (text, target)
        if key not in self._cache:
            if len(self._cache) >= CACHE_SIZE:
                self._cache.clear()
            self._cache[key] = self._request(text[:MAX_CHARS], target)
        return self._cache[key]

    @staticmethod
    def _request(text, target):
        errors = []
        for endpoint in ENDPOINTS:
            try:
                return endpoint(text, target)
            except requests.HTTPError as error:
                errors.append(f"HTTP {error.response.status_code}")
            except requests.RequestException as error:
                errors.append(type(error).__name__)
            except (ValueError, IndexError, TypeError):
                errors.append("неочікувана відповідь")
        if all(e == "HTTP 429" for e in errors):
            raise RuntimeError("Google тимчасово обмежив запити з вашої мережі (HTTP 429)")
        raise RuntimeError("Google Translate недоступний: " + ", ".join(errors))
