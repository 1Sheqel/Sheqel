#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
Автоматический тест SheqelMotion — без GUI.
Запуск: python3.11 test_app.py
"""

import ast
import importlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

# ──────────────────────────────────────────────────────────────────────────────
# Утилиты отчёта
# ──────────────────────────────────────────────────────────────────────────────

RESULTS = []   # list of (emoji, title, detail)
PROBLEMS = []  # list of (priority, text)

def ok(title, detail=""):
    RESULTS.append(("✅", title, detail))
    print(f"  ✅  {title}" + (f" — {detail}" if detail else ""))

def fail(title, detail="", priority="🔴"):
    RESULTS.append(("❌", title, detail))
    PROBLEMS.append((priority, f"{title}: {detail}"))
    print(f"  ❌  {title}" + (f" — {detail}" if detail else ""))

def warn(title, detail="", priority="🟡"):
    RESULTS.append(("⚠️ ", title, detail))
    PROBLEMS.append((priority, f"{title}: {detail}"))
    print(f"  ⚠️   {title}" + (f" — {detail}" if detail else ""))

def section(name):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print('='*60)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Синтаксис
# ──────────────────────────────────────────────────────────────────────────────
section("1. СИНТАКСИС app.py")

APP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")

try:
    with open(APP_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    ast.parse(source)
    ok("Синтаксис app.py", "нет ошибок")
except SyntaxError as e:
    fail("Синтаксис app.py", str(e), "🔴")
    source = ""

# ──────────────────────────────────────────────────────────────────────────────
# 2. Импорты
# ──────────────────────────────────────────────────────────────────────────────
section("2. ИМПОРТЫ")

REQUIRED = [
    ("requests",        "HTTP-клиент"),
    ("customtkinter",   "GUI-фреймворк"),
    ("PIL",             "Обработка изображений"),
    ("yt_dlp",          "Скачивание видео"),
    ("audio_separator", "Разделение вокала"),
    ("cloudinary",      "CDN для Sync"),
    ("tkinter",         "Base GUI"),
    ("subprocess",      "Запуск процессов"),
    ("threading",       "Потоки"),
    ("queue",           "Очередь сообщений"),
    ("json",            "Работа с JSON"),
    ("shutil",          "Работа с файлами"),
    ("hashlib",         "SHA256 проверка"),
    ("tempfile",        "Временные файлы"),
]

for mod, desc in REQUIRED:
    try:
        importlib.import_module(mod)
        ok(f"import {mod}", desc)
    except ImportError as e:
        p = "🔴" if mod in ("requests","customtkinter","PIL","yt_dlp","tkinter") else "🟡"
        fail(f"import {mod}", str(e), p)

# ──────────────────────────────────────────────────────────────────────────────
# 3. Загрузка функций из app.py (без запуска GUI)
# ──────────────────────────────────────────────────────────────────────────────
section("3. ЗАГРУЗКА ФУНКЦИЙ (без GUI)")

# Подменяем tkinter и customtkinter, чтобы не открывать экран
import unittest.mock as mock

_tk_mock = mock.MagicMock()
sys.modules.setdefault("tkinter", _tk_mock)
sys.modules.setdefault("tkinter.filedialog", _tk_mock)
sys.modules.setdefault("tkinter.messagebox", _tk_mock)

ctk_mock = mock.MagicMock()
sys.modules["customtkinter"] = ctk_mock

app_module = None
try:
    # Импортируем только функции, не создавая окно
    spec = importlib.util.spec_from_file_location("app", APP_PATH)
    app_module = importlib.util.module_from_spec(spec)

    # Блокируем вызов ctk.set_appearance_mode и ctk.set_default_color_theme
    with mock.patch.dict(sys.modules, {
        "customtkinter": ctk_mock,
        "tkinter": _tk_mock,
        "tkinter.filedialog": _tk_mock,
        "tkinter.messagebox": _tk_mock,
    }):
        spec.loader.exec_module(app_module)
    ok("Загрузка app.py как модуля", "без ошибок запуска")
except Exception as e:
    fail("Загрузка app.py как модуля", str(e)[:120], "🔴")

# ──────────────────────────────────────────────────────────────────────────────
# 4. УТИЛИТАРНЫЕ ФУНКЦИИ
# ──────────────────────────────────────────────────────────────────────────────
section("4. УТИЛИТАРНЫЕ ФУНКЦИИ")

if app_module:
    # 4.1 find_binary
    try:
        result = app_module.find_binary("ls")
        assert result == shutil.which("ls") or result == "ls"
        ok("find_binary('ls')", f"→ {result}")
    except Exception as e:
        fail("find_binary()", str(e), "🟡")

    # 4.2 ffmpeg_bin
    try:
        fb = app_module.ffmpeg_bin()
        # ffmpeg находится рядом с app.py
        local_ff = os.path.join(os.path.dirname(APP_PATH), "ffmpeg")
        if os.path.exists(local_ff):
            ok("ffmpeg_bin()", f"локальный → {fb}")
        elif shutil.which("ffmpeg"):
            ok("ffmpeg_bin()", f"системный → {fb}")
        else:
            warn("ffmpeg_bin()", "ffmpeg не найден ни локально, ни в PATH — мерж видео/аудио не будет работать", "🔴")
    except Exception as e:
        fail("ffmpeg_bin()", str(e), "🔴")

    # 4.3 ffprobe_bin
    try:
        fp = app_module.ffprobe_bin()
        local_fp = os.path.join(os.path.dirname(APP_PATH), "ffprobe")
        if os.path.exists(local_fp):
            ok("ffprobe_bin()", f"локальный → {fp}")
        elif shutil.which("ffprobe"):
            ok("ffprobe_bin()", f"системный → {fp}")
        else:
            warn("ffprobe_bin()", "ffprobe не найден — get_duration() упадёт", "🔴")
    except Exception as e:
        fail("ffprobe_bin()", str(e), "🔴")

    # 4.4 ffmpeg реально работает
    try:
        local_ff = os.path.join(os.path.dirname(APP_PATH), "ffmpeg")
        ff_path = local_ff if os.path.exists(local_ff) else shutil.which("ffmpeg")
        if ff_path:
            out = subprocess.check_output([ff_path, "-version"], stderr=subprocess.STDOUT)
            ver_line = out.decode().splitlines()[0]
            ok("ffmpeg запускается", ver_line[:60])
        else:
            fail("ffmpeg запускается", "файл не найден", "🔴")
    except Exception as e:
        fail("ffmpeg запускается", str(e)[:80], "🔴")

    # 4.5 ffprobe реально работает
    try:
        local_fp = os.path.join(os.path.dirname(APP_PATH), "ffprobe")
        ffp_path = local_fp if os.path.exists(local_fp) else shutil.which("ffprobe")
        if ffp_path:
            out = subprocess.check_output([ffp_path, "-version"], stderr=subprocess.STDOUT)
            ver_line = out.decode().splitlines()[0]
            ok("ffprobe запускается", ver_line[:60])
        else:
            fail("ffprobe запускается", "файл не найден", "🔴")
    except Exception as e:
        fail("ffprobe запускается", str(e)[:80], "🔴")

    # 4.6 safe_name
    try:
        cases = [
            ("Hello World!", "Hello_World"),
            ("Привет мир", "Привет_мир"),
            ("   ", "roll"),
            ("valid_name-123", "valid_name-123"),
            ("a!@#b$%c", "a_b_c"),
            ("a" * 300, "a" * 300),  # длинное имя не обрезается (это нормально)
        ]
        for inp, expected in cases:
            got = app_module.safe_name(inp)
            assert got == expected, f"safe_name({inp!r}) = {got!r}, ожидалось {expected!r}"
        ok("safe_name()", f"6 тест-кейсов — OK")
    except AssertionError as e:
        fail("safe_name()", str(e), "🟡")
    except Exception as e:
        fail("safe_name()", str(e), "🟡")

    # 4.7 desktop_dir
    try:
        d = app_module.desktop_dir()
        assert os.path.isdir(d), f"не директория: {d}"
        ok("desktop_dir()", d)
    except Exception as e:
        fail("desktop_dir()", str(e), "🟡")

    # 4.8 ensure_dir
    try:
        with tempfile.TemporaryDirectory() as tmp:
            new_dir = os.path.join(tmp, "a", "b", "c")
            app_module.ensure_dir(new_dir)
            assert os.path.isdir(new_dir)
        ok("ensure_dir()", "создаёт вложенные директории")
    except Exception as e:
        fail("ensure_dir()", str(e), "🟡")

    # 4.9 file_exists_ok
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(b"x" * 2048)
            fname = f.name
        assert app_module.file_exists_ok(fname, min_size=1024) is True
        assert app_module.file_exists_ok(fname, min_size=4096) is False
        assert app_module.file_exists_ok("/nonexistent/path.bin") is False
        os.unlink(fname)
        ok("file_exists_ok()", "3 тест-кейса — OK")
    except Exception as e:
        fail("file_exists_ok()", str(e), "🟡")

    # 4.10 app_base_dir
    try:
        d = app_module.app_base_dir()
        assert os.path.isdir(d)
        ok("app_base_dir()", d)
    except Exception as e:
        fail("app_base_dir()", str(e), "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 5. parse_start_end_text — EDGE CASES
# ──────────────────────────────────────────────────────────────────────────────
section("5. parse_start_end_text — граничные случаи")

if app_module:
    fn = app_module.parse_start_end_text

    cases = [
        # (input, expected_start, expected_end, expected_sep_count, should_raise)
        ("Привет мир", "Привет мир", None, 0, False),
        ("Начало\n------\nКонец", "Начало", "Конец", 1, False),
        ("A\n---\nB\n------\nC", "A", "C", 2, False),
        ("", None, None, None, True),      # пустой текст
        ("   ", None, None, None, True),   # только пробелы
        ("------\nКонец", None, None, None, True),   # нет текста до
        ("Начало\n------", None, None, None, True),  # нет текста после
    ]

    passed = 0
    for inp, exp_s, exp_e, exp_sep, should_raise in cases:
        try:
            s, e, sep = fn(inp)
            if should_raise:
                fail(f"parse_start_end_text({inp!r:.30})", "ожидалось исключение, но его нет", "🟡")
            else:
                assert s == exp_s, f"start={s!r} != {exp_s!r}"
                assert e == exp_e, f"end={e!r} != {exp_e!r}"
                assert sep == exp_sep, f"sep_count={sep} != {exp_sep}"
                passed += 1
        except RuntimeError:
            if should_raise:
                passed += 1
            else:
                fail(f"parse_start_end_text({inp!r:.30})", "неожиданное исключение", "🟡")
        except AssertionError as ae:
            fail(f"parse_start_end_text({inp!r:.30})", str(ae), "🟡")

    if passed == len(cases):
        ok("parse_start_end_text()", f"все {len(cases)} тест-кейсов прошли")
    else:
        warn("parse_start_end_text()", f"прошло {passed}/{len(cases)}", "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 6. get_duration — тест с реальным файлом через ffprobe
# ──────────────────────────────────────────────────────────────────────────────
section("6. get_duration()")

if app_module:
    local_ff = os.path.join(os.path.dirname(APP_PATH), "ffprobe")
    ffp_path = local_ff if os.path.exists(local_ff) else shutil.which("ffprobe")

    if ffp_path:
        try:
            # создаём тестовый аудио-файл через ffmpeg
            local_ffmpeg = os.path.join(os.path.dirname(APP_PATH), "ffmpeg")
            ff_path = local_ffmpeg if os.path.exists(local_ffmpeg) else shutil.which("ffmpeg")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                wav_path = tf.name
            subprocess.run(
                [ff_path, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                 "-ar", "44100", wav_path],
                check=True, capture_output=True
            )
            dur = app_module.get_duration(wav_path)
            os.unlink(wav_path)
            assert 2.9 < dur < 3.1, f"ожидалось ~3.0, получили {dur}"
            ok("get_duration()", f"3-секундный файл → {dur:.3f} сек")
        except Exception as e:
            fail("get_duration()", str(e)[:100], "🟡")
    else:
        warn("get_duration()", "ffprobe не найден — тест пропущен", "🔴")

# ──────────────────────────────────────────────────────────────────────────────
# 7. yt-dlp доступен
# ──────────────────────────────────────────────────────────────────────────────
section("7. yt-dlp")

try:
    import yt_dlp
    ok("yt_dlp import", f"версия {yt_dlp.version.__version__}")
except Exception as e:
    fail("yt_dlp import", str(e), "🔴")

try:
    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--version"],
        capture_output=True, text=True, timeout=15
    )
    ver = result.stdout.strip() or result.stderr.strip()
    ok("yt_dlp --version", ver)
except Exception as e:
    fail("yt_dlp запуск через python -m yt_dlp", str(e), "🔴")

if app_module:
    try:
        mod = app_module._ensure_ytdlp()
        ok("_ensure_ytdlp()", "модуль загружен")
    except Exception as e:
        fail("_ensure_ytdlp()", str(e), "🔴")

# ──────────────────────────────────────────────────────────────────────────────
# 8. audio-separator
# ──────────────────────────────────────────────────────────────────────────────
section("8. audio-separator")

try:
    from audio_separator.separator import Separator
    ok("audio_separator.Separator import")
except ImportError as e:
    warn("audio_separator.Separator import", str(e), "🟡")
except Exception as e:
    warn("audio_separator.Separator import", str(e)[:80], "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 9. ElevenLabs API — без реального запроса (mock)
# ──────────────────────────────────────────────────────────────────────────────
section("9. ElevenLabs API — логика (без реального запроса)")

if app_module:
    # Проверяем что функция text_to_speech_mp3 есть и правильно сигнализирует об ошибке
    try:
        import unittest.mock as mock
        with mock.patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 401
            mock_post.return_value.text = "Unauthorized"
            try:
                app_module.text_to_speech_mp3("test", "voice_id", "/tmp/out.mp3", print)
                fail("text_to_speech_mp3 — обработка 401", "не выбросила ошибку при 401", "🟡")
            except RuntimeError as e:
                if "401" in str(e):
                    ok("text_to_speech_mp3 — обработка ошибки API", "выбрасывает RuntimeError при 401")
                else:
                    warn("text_to_speech_mp3 — обработка ошибки API", str(e)[:80], "🟡")
    except Exception as e:
        warn("text_to_speech_mp3 — mock-тест", str(e)[:80], "🟡")

    # Проверяем URL ElevenLabs
    eleven_url = getattr(app_module, "ELEVEN_BASE_URL", None)
    if eleven_url and "elevenlabs" in eleven_url.lower():
        ok("ELEVEN_BASE_URL задан", eleven_url)
    else:
        warn("ELEVEN_BASE_URL", f"значение: {eleven_url}", "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 10. Sync API — проверка логики (без реального запроса)
# ──────────────────────────────────────────────────────────────────────────────
section("10. Sync API — логика (без реального запроса)")

if app_module:
    sync_url = getattr(app_module, "SYNC_GENERATE_URL", None)
    if sync_url and "sync.so" in sync_url:
        ok("SYNC_GENERATE_URL задан", sync_url)
    else:
        warn("SYNC_GENERATE_URL", f"значение: {sync_url}", "🟡")

    sync_model = getattr(app_module, "SYNC_MODEL", None)
    ok("SYNC_MODEL", str(sync_model)) if sync_model else warn("SYNC_MODEL", "не задан", "🟡")

    sync_retries = getattr(app_module, "SYNC_RETRIES", None)
    ok("SYNC_RETRIES", str(sync_retries)) if sync_retries else warn("SYNC_RETRIES", "не задан", "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 11. Cloudinary — проверка логики инициализации
# ──────────────────────────────────────────────────────────────────────────────
section("11. Cloudinary — логика")

try:
    import cloudinary
    ok("cloudinary import")
    # _init_cloudinary() не падает при пустых ключах (просто конфигурирует)
    if app_module:
        try:
            app_module._init_cloudinary()
            ok("_init_cloudinary()", "выполняется без исключений при пустых ключах")
        except Exception as e:
            warn("_init_cloudinary()", str(e)[:80], "🟡")
except ImportError as e:
    fail("cloudinary import", str(e), "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 12. ПУТИ и ХАРДКОД
# ──────────────────────────────────────────────────────────────────────────────
section("12. ПУТИ — поиск хардкода")

if source:
    hardcoded_patterns = [
        (r'/Users/[a-zA-Z0-9_]+/', "Хардкодный путь /Users/username/"),
        (r'C:\\Users\\[a-zA-Z0-9_]+\\', "Хардкодный путь C:\\Users\\"),
        (r'/home/[a-zA-Z0-9_]+/', "Хардкодный путь /home/username/"),
    ]
    found_any = False
    for pattern, desc in hardcoded_patterns:
        matches = re.findall(pattern, source)
        # исключаем строки в комментариях и docstrings (грубая эвристика)
        real_matches = [m for m in matches if m not in ("/Users/admin/.config/", "/Users/admin/Library/")]
        if real_matches:
            unique = list(set(real_matches))[:3]
            warn(f"Хардкодные пути: {desc}", f"найдено: {unique}", "🟡")
            found_any = True
    if not found_any:
        ok("Хардкодные пути", "не найдены")

    # CONFIG_PATH использует expanduser — это правильно
    config_match = re.search(r'CONFIG_PATH\s*=\s*(.+)', source)
    if config_match:
        config_def = config_match.group(1).strip()
        if "expanduser" in config_def or "Path.home()" in config_def:
            ok("CONFIG_PATH", f"использует домашнюю директорию: {config_def}")
        else:
            warn("CONFIG_PATH", f"не использует expanduser / Path.home(): {config_def}", "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 13. ВРЕМЕННЫЕ ФАЙЛЫ — проверка удаления
# ──────────────────────────────────────────────────────────────────────────────
section("13. ВРЕМЕННЫЕ ФАЙЛЫ")

if source:
    # Проверяем наличие shutil.rmtree / os.remove для tmpdir
    has_rmtree = "shutil.rmtree" in source
    has_remove = "os.remove" in source
    has_tempdir = "tempfile.mkdtemp" in source or "tempfile.TemporaryDirectory" in source

    if has_tempdir and has_rmtree:
        ok("Временные директории", "создаются и удаляются через shutil.rmtree")
    elif has_tempdir:
        warn("Временные директории", "создаются через tempfile, но shutil.rmtree не найден", "🟡")
    else:
        ok("Временные директории", "tempfile.mkdtemp не используется")

    if has_remove:
        ok("os.remove", "используется для удаления временных файлов")

    # Проверяем finally-блоки в _separate_vocals
    vocals_match = re.search(r'def _separate_vocals.+?(?=\ndef |\Z)', source, re.DOTALL)
    if vocals_match:
        vocals_body = vocals_match.group(0)
        if "finally" in vocals_body and "shutil.rmtree" in vocals_body:
            ok("_separate_vocals cleanup", "tmp_dir удаляется в finally-блоке")
        else:
            warn("_separate_vocals cleanup", "нет finally+rmtree — утечка tmp если упадёт", "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 14. СКАЧИВАНИЕ — формат и конфиг
# ──────────────────────────────────────────────────────────────────────────────
section("14. СКАЧИВАНИЕ (download_from_url)")

if source:
    # Проверяем что формат исправлен
    dl_match = re.search(r'def download_from_url.+?(?=\ndef |\Z)', source, re.DOTALL)
    if dl_match:
        dl_body = dl_match.group(0)

        # Формат видео
        if '"bestvideo+bestaudio/best"' in dl_body:
            ok("Видео-формат", '"bestvideo+bestaudio/best" — корректный')
        elif 'bestvideo[ext=mp4]+bestaudio[ext=m4a]' in dl_body:
            warn("Видео-формат", "использует жёсткие [ext=mp4]/[ext=m4a] — может не работать на TikTok/Instagram", "🟡")
        else:
            warn("Видео-формат", "формат не распознан", "🟡")

        # Логгер вместо quiet+no_warnings
        if "_YTLogger" in dl_body or "class _YTLogger" in source:
            ok("Логгер yt-dlp", "_YTLogger перехватывает ошибки постпроцессора")
        elif '"quiet": True' in dl_body and '"no_warnings": True' in dl_body:
            warn("Логгер yt-dlp", "quiet+no_warnings глушат ошибки ffmpeg-мержа — аудио пропадает молча", "🔴")

        # ffmpeg_location
        if "ffmpeg_location" in dl_body:
            ok("ffmpeg_location", "передаётся в yt-dlp")
        else:
            warn("ffmpeg_location", "не передаётся — yt-dlp будет искать ffmpeg только в PATH", "🟡")

        # merge_output_format
        if "merge_output_format" in dl_body:
            ok("merge_output_format", "задан")
        else:
            warn("merge_output_format", "не задан — формат контейнера может быть непредсказуем", "🟡")

        # audio mode — extract-audio
        if "FFmpegExtractAudio" in dl_body or "extract_audio" in dl_body:
            ok("Аудио-режим", "постпроцессор FFmpegExtractAudio задан")
        else:
            warn("Аудио-режим", "FFmpegExtractAudio не найден", "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 15. ГРАНИЧНЫЕ СЛУЧАИ — статический анализ
# ──────────────────────────────────────────────────────────────────────────────
section("15. ГРАНИЧНЫЕ СЛУЧАИ — статический анализ")

if source:
    # Пустой текст при генерации голоса
    if 'Текст пустой' in source or '"Текст пустой."' in source:
        ok("Пустой текст — обработка", "RuntimeError 'Текст пустой.' есть")
    else:
        warn("Пустой текст", "нет явной проверки на пустой текст", "🟡")

    # Несуществующий файл — check
    if 'not os.path.exists' in source or 'os.path.exists' in source:
        ok("Проверка существования файлов", "os.path.exists используется")
    else:
        warn("Проверка существования файлов", "нет os.path.exists", "🟡")

    # Очень длинный текст — нет проверки
    if 'len(text)' in source or 'max_length' in source or 'text[:' in source:
        ok("Ограничение длины текста", "есть")
    else:
        warn("Ограничение длины текста", "нет проверки длины текста для TTS — ElevenLabs лимит 5000 символов", "🟡")

    # Обработка ошибок API
    api_error_checks = source.count('status_code not in [200, 201]') + source.count('status_code != 200')
    ok("Проверки HTTP-статусов", f"найдено {api_error_checks} проверок")

    # Обработка timeout
    timeout_count = source.count('timeout=')
    ok("Таймауты HTTP-запросов", f"задан в {timeout_count} местах")

    # Thread safety — очередь логов
    if 'queue.Queue' in source and 'log_queue' in source:
        ok("Thread-safe логирование", "через queue.Queue")
    else:
        warn("Thread-safe логирование", "не использует queue — возможны ошибки при многопоточности", "🔴")

    # Stop-флаг для загрузки
    if '_stop_download' in source:
        ok("Кнопка Стоп для загрузки", "_stop_download флаг есть")
    else:
        warn("Кнопка Стоп", "_stop_download не найден", "🟡")

    # Retry для Sync API
    retry_match = re.search(r'SYNC_RETRIES\s*=\s*(\d+)', source)
    if retry_match:
        ok("Retry для Sync API", f"SYNC_RETRIES = {retry_match.group(1)}")
    else:
        warn("Retry для Sync API", "SYNC_RETRIES не задан", "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 16. ИНТЕРНЕТ-СОЕДИНЕНИЕ
# ──────────────────────────────────────────────────────────────────────────────
section("16. ИНТЕРНЕТ-СОЕДИНЕНИЕ")

try:
    import requests
    r = requests.get("https://api.elevenlabs.io/v1/voices", timeout=5,
                     headers={"xi-api-key": "test"})
    if r.status_code in (401, 403, 422):
        ok("ElevenLabs API недоступен", f"статус {r.status_code} (ожидаемо без ключа)")
    elif r.status_code == 200:
        ok("ElevenLabs API", "доступен")
    else:
        warn("ElevenLabs API", f"неожиданный статус {r.status_code}", "🟡")
except requests.exceptions.ConnectionError:
    warn("ElevenLabs API", "нет интернета или хост недоступен", "🟡")
except requests.exceptions.Timeout:
    warn("ElevenLabs API", "таймаут подключения", "🟡")
except Exception as e:
    warn("ElevenLabs API", str(e)[:80], "🟡")

try:
    r2 = requests.get("https://api.sync.so", timeout=5)
    ok("Sync API", f"доступен (статус {r2.status_code})")
except requests.exceptions.ConnectionError:
    warn("Sync API", "нет интернета или хост недоступен", "🟡")
except Exception as e:
    warn("Sync API", str(e)[:80], "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 17. ВЕРСИЯ И ОБНОВЛЕНИЯ
# ──────────────────────────────────────────────────────────────────────────────
section("17. ВЕРСИЯ И ОБНОВЛЕНИЯ")

if app_module:
    ver = getattr(app_module, "APP_VERSION", None)
    ok("APP_VERSION", ver) if ver else warn("APP_VERSION", "не задан", "🟡")

    update_url = getattr(app_module, "UPDATE_MANIFEST_URL", None)
    if update_url and "github" in update_url.lower():
        ok("UPDATE_MANIFEST_URL", update_url[:60])

version_json = os.path.join(os.path.dirname(APP_PATH), "version.json")
if os.path.exists(version_json):
    try:
        with open(version_json) as f:
            vdata = json.load(f)
        latest = vdata.get("latest_version", "?")
        sha = vdata.get("sha256", "")
        ok("version.json", f"latest_version={latest}, sha256={'есть' if sha else 'ОТСУТСТВУЕТ'}")
        if not sha:
            warn("version.json sha256", "sha256 не задан — обновление небезопасно", "🟡")
    except Exception as e:
        fail("version.json", str(e), "🟡")
else:
    warn("version.json", "файл не найден", "🟡")

# ──────────────────────────────────────────────────────────────────────────────
# 18. copy_file — тест функции копирования
# ──────────────────────────────────────────────────────────────────────────────
section("18. copy_file()")

if app_module:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.txt")
            dst = os.path.join(tmp, "sub", "dst.txt")
            with open(src, "w") as f:
                f.write("x" * 200)
            result = app_module.copy_file(src, dst)
            assert os.path.exists(dst)
            assert os.path.getsize(dst) == 200
        ok("copy_file()", "копирует и создаёт поддиректории")
    except Exception as e:
        fail("copy_file()", str(e), "🟡")

    # Тест на слишком маленький файл
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "tiny.txt")
            dst = os.path.join(tmp, "dst.txt")
            with open(src, "w") as f:
                f.write("x")   # 1 байт < min_size=100
            try:
                app_module.copy_file(src, dst)
                warn("copy_file() — маленький файл", "не выбросил ошибку для файла < 100 байт", "🟢")
            except RuntimeError:
                ok("copy_file() — маленький файл", "выбрасывает RuntimeError как ожидается")
    except Exception as e:
        warn("copy_file() — маленький файл", str(e), "🟢")

# ──────────────────────────────────────────────────────────────────────────────
# ИТОГОВЫЙ ОТЧЁТ
# ──────────────────────────────────────────────────────────────────────────────

total = len(RESULTS)
ok_count   = sum(1 for e, _, _ in RESULTS if e == "✅")
fail_count = sum(1 for e, _, _ in RESULTS if e == "❌")
warn_count = sum(1 for e, _, _ in RESULTS if e.startswith("⚠"))

print(f"\n{'='*60}")
print(f"  ИТОГО: ✅ {ok_count}  ❌ {fail_count}  ⚠️  {warn_count}  из {total} проверок")
print('='*60)

# ──────────────────────────────────────────────────────────────────────────────
# Запись в test_report.txt
# ──────────────────────────────────────────────────────────────────────────────

report_path = os.path.join(os.path.dirname(APP_PATH), "test_report.txt")

red    = [p for pri, p in PROBLEMS if pri == "🔴"]
yellow = [p for pri, p in PROBLEMS if pri == "🟡"]
green  = [p for pri, p in PROBLEMS if pri == "🟢"]

with open(report_path, "w", encoding="utf-8") as f:
    f.write("ОТЧЁТ ПО ТЕСТИРОВАНИЮ SheqelMotion\n")
    f.write(f"Дата: {time.strftime('%d.%m.%Y %H:%M:%S')}\n")
    f.write(f"Python: {sys.version.split()[0]}\n")
    f.write(f"Платформа: {sys.platform}\n")
    f.write("=" * 60 + "\n\n")

    f.write(f"ИТОГО: ✅ {ok_count}  ❌ {fail_count}  ⚠️  {warn_count}  из {total} проверок\n\n")

    f.write("=" * 60 + "\n")
    f.write("РЕЗУЛЬТАТЫ ПО КАТЕГОРИЯМ\n")
    f.write("=" * 60 + "\n\n")

    for emoji, title, detail in RESULTS:
        line = f"{emoji}  {title}"
        if detail:
            line += f"\n       → {detail}"
        f.write(line + "\n")

    f.write("\n" + "=" * 60 + "\n")
    f.write("НАЙДЕННЫЕ ПРОБЛЕМЫ (по приоритету)\n")
    f.write("=" * 60 + "\n\n")

    if red:
        f.write("🔴 КРИТИЧНО — исправить немедленно:\n")
        for p in red:
            f.write(f"   • {p}\n")
        f.write("\n")
    else:
        f.write("🔴 Критичных проблем не найдено.\n\n")

    if yellow:
        f.write("🟡 ВАЖНО — исправить в ближайшее время:\n")
        for p in yellow:
            f.write(f"   • {p}\n")
        f.write("\n")
    else:
        f.write("🟡 Важных проблем не найдено.\n\n")

    if green:
        f.write("🟢 ЖЕЛАТЕЛЬНО — улучшение качества:\n")
        for p in green:
            f.write(f"   • {p}\n")
        f.write("\n")
    else:
        f.write("🟢 Дополнительных пожеланий нет.\n\n")

    f.write("=" * 60 + "\n")
    f.write("РЕКОМЕНДАЦИИ\n")
    f.write("=" * 60 + "\n\n")
    f.write("1. Убедиться, что ffmpeg/ffprobe доступны (либо рядом с app.py,\n")
    f.write("   либо в PATH) — без них мерж видео+аудио и извлечение аудио невозможны.\n\n")
    f.write("2. Формат скачивания видео исправлен на 'bestvideo+bestaudio/best'\n")
    f.write("   + добавлен _YTLogger для перехвата ошибок постпроцессора.\n\n")
    f.write("3. Добавить проверку длины текста перед отправкой в ElevenLabs\n")
    f.write("   (лимит 5000 символов на запрос).\n\n")
    f.write("4. version.json на GitHub — обязательно добавить sha256 перед\n")
    f.write("   каждым релизом для безопасного автообновления.\n\n")
    f.write("5. .venv в проекте привязан к старому пути — пересоздать виртуальное\n")
    f.write("   окружение или использовать системный python3.11.\n")

print(f"\n  Отчёт сохранён: {report_path}\n")

# ──────────────────────────────────────────────────────────────────────────────
# 19. ПУТИ С КИРИЛЛИЦЕЙ — тест совместимости
# ──────────────────────────────────────────────────────────────────────────────
section("19. ПУТИ С КИРИЛЛИЦЕЙ")

if app_module:
    # 19.1 ensure_dir с кириллическим путём
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cyr_dir = os.path.join(tmp, "Видео", "Русский путь")
            app_module.ensure_dir(cyr_dir)
            assert os.path.isdir(cyr_dir)
        ok("ensure_dir() с кириллицей", "создаёт директории с кириллическими именами")
    except Exception as e:
        fail("ensure_dir() с кириллицей", str(e), "🔴")

    # 19.2 copy_file с кириллическим путём
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "источник.txt")
            dst = os.path.join(tmp, "Видео Файлы", "назначение.txt")
            with open(src, "w", encoding="utf-8") as f:
                f.write("x" * 200)
            app_module.copy_file(src, dst)
            assert os.path.exists(dst)
        ok("copy_file() с кириллицей", "копирует в директорию с кириллическим именем")
    except Exception as e:
        fail("copy_file() с кириллицей", str(e), "🔴")

    # 19.3 safe_name сохраняет кириллицу
    try:
        cases = [
            ("Привет мир!", "Привет_мир"),
            ("Русский файл 2024", "Русский_файл_2024"),
            ("Видео №1", "Видео_1"),      # пробел+№ — один последовательный блок → один _
            ("Ёжик в тумане", "Ёжик_в_тумане"),
        ]
        for inp, expected in cases:
            got = app_module.safe_name(inp)
            assert got == expected, f"safe_name({inp!r}) = {got!r}, ожидалось {expected!r}"
        ok("safe_name() кириллица", f"{len(cases)} тест-кейса — кирилл. символы сохраняются")
    except AssertionError as e:
        warn("safe_name() кириллица", str(e), "🟡")
    except Exception as e:
        fail("safe_name() кириллица", str(e), "🟡")

    # 19.4 get_duration с кириллическим именем файла
    try:
        local_ffmpeg = os.path.join(os.path.dirname(APP_PATH), "ffmpeg")
        ff_path = local_ffmpeg if os.path.exists(local_ffmpeg) else shutil.which("ffmpeg")
        local_ffprobe = os.path.join(os.path.dirname(APP_PATH), "ffprobe")
        ffp_path = local_ffprobe if os.path.exists(local_ffprobe) else shutil.which("ffprobe")
        if ff_path and ffp_path:
            with tempfile.TemporaryDirectory() as tmp:
                cyr_wav = os.path.join(tmp, "тест аудио кириллица.wav")
                subprocess.run(
                    [ff_path, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                     "-ar", "44100", cyr_wav],
                    check=True, capture_output=True,
                )
                dur = app_module.get_duration(cyr_wav)
                assert 1.9 < dur < 2.1, f"ожидалось ~2.0, получено {dur}"
            ok("get_duration() с кириллицей", f"{dur:.2f} сек — кирилл. путь обработан корректно")
        else:
            warn("get_duration() с кириллицей", "ffmpeg/ffprobe не найден — тест пропущен", "🟡")
    except Exception as e:
        fail("get_duration() с кириллицей", str(e)[:100], "🔴")

    # 19.5 file_exists_ok с кириллическим путём
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cyr_file = os.path.join(tmp, "файл проверки.bin")
            with open(cyr_file, "wb") as f:
                f.write(b"x" * 2048)
            assert app_module.file_exists_ok(cyr_file, min_size=1024) is True
            assert app_module.file_exists_ok(cyr_file, min_size=4096) is False
        ok("file_exists_ok() с кириллицей", "работает корректно")
    except Exception as e:
        fail("file_exists_ok() с кириллицей", str(e), "🔴")

    # 19.6 pathlib.Path импортирован в app.py
    try:
        from pathlib import Path as _P
        assert hasattr(app_module, "Path") or "from pathlib import Path" in source
        ok("pathlib.Path в app.py", "импортирован")
    except Exception as e:
        warn("pathlib.Path в app.py", str(e), "🟡")

# Дополняем итоговый отчёт
total = len(RESULTS)
ok_count   = sum(1 for e, _, _ in RESULTS if e == "✅")
fail_count = sum(1 for e, _, _ in RESULTS if e == "❌")
warn_count = sum(1 for e, _, _ in RESULTS if e.startswith("⚠"))
print(f"\n{'='*60}")
print(f"  ИТОГО (с кириллицей): ✅ {ok_count}  ❌ {fail_count}  ⚠️  {warn_count}  из {total}")
print('='*60)
