"""Збирає все докупи: хуки клавіатури й миші, захоплення тексту, переклад, вікно та іконку в треї."""
import ctypes
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QLibraryInfo, QLocale, QObject, QPoint, QSettings, QTimer, QTranslator, Signal
from PySide6.QtGui import QAction, QActionGroup, QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .capture import MODE_CURSOR, MODE_SELECTION, TextCapturer, describe_displays, grab_screen_around, window_rect
from .exclusions import ExclusionsDialog, normalize
from .hook import HotkeyHook, MouseHook
from .hotkey_dialog import HotkeyDialog
from .keys import ALT, DEFAULT_HOTKEY, WIN, Hotkey, send_mask_key
from .popup import PopupWindow
from .processes import cursor_pos, foreground_pid, process_name
from .theme import THEMES, app_icon, apply_app_color_scheme, system_theme
from .translator import Translator, language_choices
from .tray_visibility import promote_tray_icon

log = logging.getLogger(__name__)

DEFAULT_TARGET_LANGUAGE = "uk"
DEFAULT_OCR_LANGUAGE = "auto"  # ocr.AUTO; сам модуль ocr (winrt) імпортуємо лише після створення Qt
ERROR_ALREADY_EXISTS = 183
SM_REMOTESESSION = 0x1000
TRAY_PROMOTE_ATTEMPTS = 10


class _Bridge(QObject):
    """Сигнали з робочих потоків і потоків хуків; Qt доставляє їх у головний потік."""
    hotkey = Signal()
    outside_click = Signal()
    captured = Signal(int, object)
    capture_failed = Signal(int, str)
    translated = Signal(int, str, str)
    translate_failed = Signal(int, str)


class Controller(QObject):
    def __init__(self, app):
        super().__init__()
        self._settings = QSettings("TranslateGUI", "TranslateGUI")
        self._target = self._settings.value("target_language", DEFAULT_TARGET_LANGUAGE)
        ocr_language = self._settings.value("ocr_language", DEFAULT_OCR_LANGUAGE)

        # UI Automation і OCR — в одному потоці, бо їхні COM-об'єкти прив'язані до потоку створення.
        self._capturer = None
        self._capture_pool = ThreadPoolExecutor(1, "capture", initializer=self._init_capturer, initargs=(ocr_language,))
        self._translate_pool = ThreadPoolExecutor(2, "translate")
        self._translator = Translator()

        # Кожне нове натискання гарячої клавіші чи редагування відміняє результати попередніх запитів.
        self._capture_id = 0
        self._translate_id = 0
        # Що запам'ятали в момент гарячої клавіші: точку курсора зі знімком екрана навколо неї
        # і вже прочитані результати для кожного режиму (MODE_CURSOR / MODE_SELECTION → Capture).
        self._point = None
        self._results = {}

        self._bridge = _Bridge()
        self._bridge.hotkey.connect(self._on_hotkey)
        self._bridge.captured.connect(self._on_captured)
        self._bridge.capture_failed.connect(self._on_capture_failed)
        self._bridge.translated.connect(self._on_translated)
        self._bridge.translate_failed.connect(self._on_translate_failed)

        languages = language_choices()
        self._language_names = dict(languages)
        theme_name = self._settings.value("theme", system_theme())
        if theme_name not in THEMES:
            theme_name = system_theme()
        apply_app_color_scheme(theme_name)
        self._popup = PopupWindow(languages, self._target, theme_name)
        self._popup.theme_changed.connect(self._on_theme_changed)
        self._popup.language_changed.connect(self._on_language_changed)
        self._popup.text_edited.connect(self._translate)
        self._popup.ocr_requested.connect(self._on_ocr_requested)
        self._popup.mode_requested.connect(self._on_mode_requested)
        self._popup.exclude_requested.connect(self._exclude_app)
        self._bridge.outside_click.connect(self._popup.close_if_not_pinned)

        self._keyboard = HotkeyHook(self._bridge.hotkey.emit)
        self._keyboard.hotkey = self._load_hotkey()
        self._mouse = MouseHook(on_outside_click=self._bridge.outside_click.emit)
        self._exclusions_dialog = None
        self._hotkey_dialog = None
        self._excluded = frozenset()
        self._set_excluded(self._settings.value("excluded_apps", [], type=list))
        self._tray = self._create_tray(app, ocr_language)
        self._watch_displays(app)

        self._mouse.start()
        self._keyboard.start()
        self._tray.show()
        # Лише для exe (під Python запис належав би інтерпретатору python.exe) і лише раз для кожного
        # його розташування: якщо потім користувач сховає значок, це його вибір.
        if getattr(sys, "frozen", False) and self._settings.value("tray_promoted_for", "") != sys.executable:
            self._promote_tray_icon(TRAY_PROMOTE_ATTEMPTS)
        self._tray.showMessage(
            "Translate GUI працює",
            f"Натисніть {self._hotkey_title()} — перекладу виділений текст, а якщо нічого не виділено, "
            "то напис під курсором миші.",
            QSystemTrayIcon.Information,
            4000,
        )

    def _promote_tray_icon(self, attempts):
        """Explorer заводить запис про значок не одразу після показу — пробуємо кілька разів."""
        try:
            found = promote_tray_icon(sys.executable)
        except OSError:
            log.exception("Не вдалося винести значок у треї на панель завдань")
            return
        if found:
            self._settings.setValue("tray_promoted_for", sys.executable)
        elif attempts > 1:
            QTimer.singleShot(1000, lambda: self._promote_tray_icon(attempts - 1))

    def _watch_displays(self, app):
        """Під RDP монітори й масштаб змінюються при перепідключенні з іншого пристрою."""
        remote = bool(ctypes.windll.user32.GetSystemMetrics(SM_REMOTESESSION))
        log.info("Запуск. RDP-сесія: %s. Монітори: %s", "так" if remote else "ні", describe_displays())

        # Під час зміни Qt надсилає кілька сигналів поспіль — реагуємо один раз, коли все вляжеться.
        self._displays_timer = QTimer(self, singleShot=True, interval=1000)
        self._displays_timer.timeout.connect(self._on_displays_changed)

        def changed(*_):
            self._displays_timer.start()

        def watch(screen):
            screen.geometryChanged.connect(changed)
            screen.logicalDotsPerInchChanged.connect(changed)

        for screen in app.screens():
            watch(screen)
        app.screenAdded.connect(watch)
        app.screenAdded.connect(changed)
        app.screenRemoved.connect(changed)

    def _on_displays_changed(self):
        log.info("Змінилися монітори або масштаб: %s", describe_displays())
        # Вікно могло опинитися за межами нового екрана — наступна гаряча клавіша покаже його заново.
        self._popup.hide()

    # --- тема й винятки ---

    def _on_theme_changed(self, name):
        self._settings.setValue("theme", name)
        apply_app_color_scheme(name)
        for dialog in (self._exclusions_dialog, self._hotkey_dialog):
            if dialog:
                dialog.apply_theme(name)

    def _set_excluded(self, names):
        excluded = sorted({normalize(name) for name in names if normalize(name)})
        self._settings.setValue("excluded_apps", excluded)
        self._excluded = frozenset(excluded)
        self._keyboard.excluded = self._excluded
        log.info("Програми-винятки: %s", ", ".join(excluded) or "немає")

    def _exclude_app(self, name):
        if not name:
            return
        self._set_excluded(list(self._excluded) + [name])
        self._popup.hide()
        self._tray.showMessage(
            "Translate GUI вимкнено для програми",
            f"У «{name}» {self._hotkey_title()} тепер працює як звичайно, без перекладу. "
            "Змінити: меню трею → «Виключені програми».",
            QSystemTrayIcon.Information,
            5000,
        )

    def _show_exclusions(self):
        if self._exclusions_dialog is None:
            self._exclusions_dialog = ExclusionsDialog(self._excluded, self._popup.theme_name, self._hotkey_title())
            self._exclusions_dialog.changed.connect(self._set_excluded)
            self._exclusions_dialog.finished.connect(self._on_exclusions_closed)
        self._exclusions_dialog.show()
        self._exclusions_dialog.raise_()
        self._exclusions_dialog.activateWindow()

    def _on_exclusions_closed(self):
        self._exclusions_dialog.deleteLater()
        self._exclusions_dialog = None

    def shutdown(self):
        self._mouse.stop()
        self._keyboard.stop()
        self._capture_pool.shutdown(wait=False, cancel_futures=True)
        self._translate_pool.shutdown(wait=False, cancel_futures=True)

    # --- захоплення тексту ---

    def _init_capturer(self, ocr_language):
        self._capturer = TextCapturer(ocr_language)

    def _on_hotkey(self):
        # Програми-винятки й саме вікно перекладу хук уже відсіяв: там комбінація дійшла до програми.
        if self._keyboard.hotkey.modifiers & {ALT, WIN}:
            send_mask_key()  # поки Alt / Win ще утримують — щоб їх відпускання не відкрило меню чи «Пуск»
        app_name = process_name(foreground_pid())
        x, y = cursor_pos()
        # Знімок робимо до показу вікна, а вже відкрите вікно на знімку зафарбовуємо, щоб воно не потрапило в OCR.
        # Зафарбовуємо лише саму картку: прозоре поле під тінь нічого не закриває, а сусідній рядок обрізало б.
        exclude = None
        if self._popup.isVisible():
            left, top, right, bottom = window_rect(int(self._popup.winId()))
            margin = self._popup.card_margin()
            exclude = (left + margin, top + margin, right - margin, bottom - margin)
        try:
            screenshot = grab_screen_around(x, y, exclude)
        except Exception:
            log.exception("Не вдалося зробити знімок екрана")
            screenshot = None
        self._point = (x, y, screenshot)
        self._results = {}
        self._popup.show_loading(_to_logical(x, y), app_name)

        def auto_capture():
            # Виділення читаємо одразу: щойно користувач клікне по нашому вікну, фокус перейде до нього
            # і виділення в програмі вже не прочитати. Якщо нічого не виділено — беремо напис під курсором.
            # (self._capturer з'являється лише в робочому потоці, тому звертаємося до нього тут.)
            selection = self._capturer.selected_text()
            if selection.text:
                return [selection]
            return [selection, self._capturer.capture(x, y, screenshot)]

        self._run_capture(auto_capture)

    def _on_mode_requested(self, mode):
        """Перемикач у вікні: показати інший режим (вже прочитаний або прочитати напис під курсором)."""
        if self._point is None:
            return
        if mode in self._results:
            self._show_result(self._results[mode])
        elif mode == MODE_CURSOR:
            self._popup.set_mode(MODE_CURSOR)
            self._popup.set_busy(True)
            self._popup.show_status("Шукаю текст під курсором…")
            x, y, screenshot = self._point
            self._run_capture(lambda: [self._capturer.capture(x, y, screenshot)])

    def _on_ocr_requested(self):
        if self._point is None:
            return
        self._popup.set_mode(MODE_CURSOR)
        self._popup.set_busy(True)
        self._popup.show_status("Розпізнаю текст OCR…")
        x, y, screenshot = self._point
        self._run_capture(lambda: [self._capturer.capture(x, y, screenshot, force_ocr=True)])

    def _run_capture(self, work):
        """Виконує work() у потоці захоплення; список результатів прийде сигналом captured."""
        self._capture_id += 1
        request_id = self._capture_id

        def job():
            try:
                results = work()
            except Exception as error:
                log.exception("Не вдалося отримати текст")
                self._bridge.capture_failed.emit(request_id, str(error))
            else:
                self._bridge.captured.emit(request_id, results)

        self._capture_pool.submit(job)

    def _on_captured(self, request_id, results):
        if request_id != self._capture_id:
            return
        for capture in results:
            self._results[capture.mode] = capture
        self._show_result(results[-1])

    def _show_result(self, capture):
        self._popup.set_mode(capture.mode)
        if capture.text:
            self._popup.show_capture(capture.text, capture.source)
            self._translate(capture.text)
            return
        self._translate("")
        if capture.mode == MODE_SELECTION:
            self._popup.show_capture("", "", empty_message=f"Нічого не виділено — виділіть текст і натисніть "
                                                           f"{self._hotkey_title()} або введіть текст вручну.")
        else:
            self._popup.show_capture("", "")
        # Тексту немає — вікно стає швидким перекладачем: курсор у поле, щоб одразу вводити.
        self._popup.focus_input()

    def _on_capture_failed(self, request_id, message):
        if request_id == self._capture_id:
            self._popup.set_busy(False)
            self._popup.show_status(f"Не вдалося отримати текст: {message}", error=True)

    # --- переклад ---

    def _translate(self, text):
        self._translate_id += 1
        request_id = self._translate_id
        if not text:
            self._popup.show_translation("")
            return
        target = self._target
        self._popup.show_status("Перекладаю…")

        def job():
            try:
                translation, source_language = self._translator.translate(text, target)
            except Exception as error:
                log.exception("Помилка перекладу")
                self._bridge.translate_failed.emit(request_id, str(error) or type(error).__name__)
            else:
                self._bridge.translated.emit(request_id, translation, source_language)

        self._translate_pool.submit(job)

    def _on_translated(self, request_id, text, source_language):
        if request_id == self._translate_id:
            self._popup.show_translation(text, self._language_names.get(source_language, source_language))

    def _on_translate_failed(self, request_id, message):
        if request_id == self._translate_id:
            self._popup.show_status(f"Помилка перекладу: {message}", error=True)

    def _on_language_changed(self, code):
        self._target = code
        self._settings.setValue("target_language", code)
        text = self._popup.original_text()
        if text:
            self._translate(text)

    # --- трей ---

    def _create_tray(self, app, ocr_language):
        tray = QSystemTrayIcon(app_icon(), app)
        menu = QMenu()

        enabled = QAction("Гаряча клавіша увімкнена", menu, checkable=True, checked=True)
        enabled.toggled.connect(lambda on: setattr(self._keyboard, "enabled", on))
        menu.addAction(enabled)
        self._hotkey_action = menu.addAction("", self._show_hotkey_dialog)
        menu.addAction("Виключені програми…", self._show_exclusions)

        ocr_menu = menu.addMenu("Мова OCR (запасний варіант)")
        group = QActionGroup(ocr_menu)
        from .ocr import AUTO, auto_languages, available_languages  # winrt — лише після того, як Qt вже завантажено
        cyrillic, latin = auto_languages()
        auto_title = "Авто"
        if cyrillic and latin:
            auto_title = f"Авто: кирилиця ({cyrillic}) + латиниця ({latin})"
        choices = [(AUTO, auto_title)] + [(tag, f"{name} ({tag})") for tag, name in available_languages()]
        for tag, title in choices:
            action = QAction(title, ocr_menu, checkable=True, checked=tag == ocr_language)
            action.triggered.connect(lambda _=False, t=tag: self._on_ocr_language_changed(t))
            group.addAction(action)
            ocr_menu.addAction(action)
            if tag == AUTO:
                ocr_menu.addSeparator()

        menu.addSeparator()
        menu.addAction("Вихід", app.quit)
        tray.setContextMenu(menu)
        self._tray_menu = menu
        self._tray = tray
        self._update_hotkey_texts()
        return tray

    # --- гаряча клавіша ---

    def _load_hotkey(self):
        try:
            hotkey = Hotkey.parse(self._settings.value("hotkey", DEFAULT_HOTKEY.dump()))
        except ValueError:
            hotkey = DEFAULT_HOTKEY
        return DEFAULT_HOTKEY if hotkey.problem() else hotkey

    def _hotkey_title(self):
        return self._keyboard.hotkey.title()

    def _update_hotkey_texts(self):
        title = self._hotkey_title()
        self._tray.setToolTip(f"Translate GUI — {title}: виділений текст або напис під курсором")
        self._hotkey_action.setText(f"Гаряча клавіша ({title})…")

    def _show_hotkey_dialog(self):
        if self._hotkey_dialog is None:
            self._hotkey_dialog = HotkeyDialog(self._keyboard.hotkey, self._popup.theme_name)
            self._hotkey_dialog.changed.connect(self._set_hotkey)
            self._hotkey_dialog.finished.connect(self._on_hotkey_dialog_closed)
        self._hotkey_dialog.show()
        self._hotkey_dialog.raise_()
        self._hotkey_dialog.activateWindow()

    def _on_hotkey_dialog_closed(self):
        self._hotkey_dialog.deleteLater()
        self._hotkey_dialog = None

    def _set_hotkey(self, hotkey):
        self._keyboard.hotkey = hotkey
        self._settings.setValue("hotkey", hotkey.dump())
        self._update_hotkey_texts()
        log.info("Гаряча клавіша: %s", hotkey.title())

    def _on_ocr_language_changed(self, tag):
        self._settings.setValue("ocr_language", tag)
        self._capture_pool.submit(lambda: self._capturer.set_ocr_language(tag))


def _to_logical(x, y):
    """Фізичні пікселі (хук, UI Automation) → логічні координати Qt з масштабом монітора, де точка."""
    for screen in QGuiApplication.screens():
        geometry = screen.geometry()
        ratio = screen.devicePixelRatio()
        # Qt під Windows зберігає лівий верхній кут монітора однаковим у фізичних і логічних координатах,
        # а розмір ділить на масштаб.
        left, top = geometry.x(), geometry.y()
        if left <= x < left + geometry.width() * ratio and top <= y < top + geometry.height() * ratio:
            return QPoint(round(left + (x - left) / ratio), round(top + (y - top) / ratio))
    return QCursor.pos()


def _already_running():
    kernel32 = ctypes.windll.kernel32
    _already_running.mutex = kernel32.CreateMutexW(None, False, "Local\\TranslateGUI.SingleInstance")
    return kernel32.GetLastError() == ERROR_ALREADY_EXISTS


def _setup_logging():
    log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "TranslateGUI")
    os.makedirs(log_dir, exist_ok=True)
    handlers = [RotatingFileHandler(os.path.join(log_dir, "translategui.log"), maxBytes=1_000_000,
                                    backupCount=2, encoding="utf-8")]
    if sys.stderr:  # під pythonw консолі немає
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


def run():
    _setup_logging()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Translate GUI")
    # Стандартні меню Qt (контекстне меню поля: «Копіювати», «Вставити»…) — українською, як і решта вікна.
    qt_translator = QTranslator(app)
    if qt_translator.load(QLocale("uk"), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(qt_translator)

    if _already_running():
        QMessageBox.information(None, "Translate GUI", "Програма вже запущена — її іконка в треї.")
        return 0
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Translate GUI", "Системний трей недоступний.")
        return 1

    controller = Controller(app)
    app.aboutToQuit.connect(controller.shutdown)
    return app.exec()
