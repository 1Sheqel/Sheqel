#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys

def _ensure_deps():
    if "--selftest" in sys.argv:
        return    # самопроверка обновления: без сети и без установки пакетов
    packages = [
        ("requests",       "requests==2.34.2"),
        ("customtkinter",  "customtkinter==5.2.2"),
        ("PIL",            "pillow==12.2.0"),
        ("yt_dlp",         "yt-dlp"),
        ("audio_separator","audio-separator[cpu]"),
        ("cloudinary",     "cloudinary"),

    ]
    for module, pkg in packages:
        try:
            __import__(module)
        except ImportError:
            print(f"Устанавливаю {pkg}...", flush=True)
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", pkg],
                check=True,
            )

_ensure_deps()

import os
import re
import time
import queue
import shutil
import hashlib
import tempfile
import threading
import traceback
import uuid
import copy
import platform
import collections
import contextlib
import requests
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageTk

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox

SYNC_API_KEY = ""
CLOUDINARY_CLOUD_NAME = ""
CLOUDINARY_API_KEY = ""
CLOUDINARY_API_SECRET = ""
CONFIG_PATH = str(Path.home() / ".version.json")
ELEVENLABS_API_KEY = ""
APP_VERSION = "1.3.0"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/1Sheqel/Sheqel/main/version.json"

# Отчёты об ошибках разработчику. Пустой URL — отправка выключена.
# Секретов здесь нет и быть не должно: репозиторий публичный, токен бота живёт в relay-воркере.
ERROR_REPORT_URL = "https://rapid-sky-367b.creos-1.workers.dev"
ERROR_REPORT_TIMEOUT_SEC = 5
ERROR_REPORT_DEDUP_SEC = 60 * 60
ERROR_REPORT_DAILY_LIMIT = 20
ERROR_REPORT_LOG_LINES = 15
ERROR_REPORT_STATE_PATH = str(Path.home() / ".sheqelmotion_reports.json")
ERROR_REPORT_THREAD_NAME = "ErrorReport"

LOG_FILE_PATH = str(Path.home() / "SheqelMotion.log")
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
_log_file_lock = threading.Lock()
_recent_log_lines = collections.deque(maxlen=200)
_log_secured_path = None


def write_log_file(msg):
    msg = str(msg)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    global _log_secured_path
    with _log_file_lock:
        _recent_log_lines.extend(msg.splitlines() or [""])
        try:
            with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
                f.write(line)
            if _log_secured_path != LOG_FILE_PATH:      # лог — только для владельца (0600)
                secure_path(LOG_FILE_PATH)
                _log_secured_path = LOG_FILE_PATH
        except OSError:
            pass  # сбой записи лога не должен ронять приложение


def recent_log_lines(n):
    with _log_file_lock:
        return list(_recent_log_lines)[-n:]


def rotate_log_file():
    try:
        if os.path.getsize(LOG_FILE_PATH) > LOG_FILE_MAX_BYTES:
            os.replace(LOG_FILE_PATH, LOG_FILE_PATH + ".1")
            secure_path(LOG_FILE_PATH + ".1")
    except OSError:
        pass


_MEDIA_EXT = r"(?:mp4|mov|mkv|avi|webm|m4v|mp3|wav|m4a|aac|flac|ogg|opus|txt|json|srt|vtt|png|jpe?g)"
# Порядок важен: сначала URL и ключи, потом пути, имена файлов и длинные токены.
_SANITIZE_RULES = [
    (re.compile(r"(?i)\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
    (re.compile(r"(?i)\b(?:https?|ftp)://[^\s\"'<>\]})]+"), "<url>"),
    # IP-адреса и имена компьютеров (Mac: «…-MacBook-Pro.local», Windows: «DESKTOP-ABC1234»).
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"(?i)\b(?:[0-9a-f]{1,4}:){4,7}[0-9a-f]{1,4}\b"), "<ip>"),
    (re.compile(r"(?i)\b[\w-]+\.(?:local|lan|home|localdomain)\b"), "<host>"),
    (re.compile(r"\b(?:DESKTOP|LAPTOP|WIN|PC)-[A-Z0-9]{4,}\b"), "<host>"),
    # Параметры подписи вне URL (обрывки presigned-ссылок в текстах ошибок хранилищ).
    (re.compile(r"(?i)\b(X-Amz-[A-Za-z-]+|X-Goog-[A-Za-z-]+|Signature|Expires|Policy|Key-Pair-Id|"
                r"token|sig)=[^&\s\"'<>]+"), r"\1=<redacted>"),
    # Поля JSON/dict с именами и текстами: оставляем ключ, значение скрываем.
    (re.compile(r"(?i)([\"'])(\w*(?:file_?name|name|title|text|script|prompt|voice_id))\1\s*:\s*([\"'])(?:(?!\3).)*\3"),
     r"\1\2\1:\3…\3"),
    (re.compile(r"(?i)\b(?:x-api-key|xi-api-key|authorization|bearer|api[_ -]?key|api[_ -]?secret|token|secret)"
                r"(?:\"?\s*[:=]\s*\"?|\s+)[^\s\"',}]+"), "<key>"),
    (re.compile(r"(?i)\b(?:sk|gsk|pk|rk)[_-][A-Za-z0-9_\-]{6,}"), "<key>"),
    (re.compile(r"/(?:Users|home)/[^/\s\"'<>]+"), "~"),
    (re.compile(r"(?i)\b[A-Z]:\\(?:Users|Documents and Settings)\\[^\\\s\"'<>]+"), "~"),
    # Пути в кавычках скрываем целиком — внутри бывают пробелы («Рабочий стол»).
    (re.compile(r"([\"'])~[/\\][^\"'\n]*\1"), r"\1~/…\1"),
    (re.compile(r"(?i)([\"'])(?:/|[A-Z]:\\)[^\"'\n]*\1"), r"\1<path>\1"),
    (re.compile(r"(?i)~[/\\][^\n\"'<>]*?\." + _MEDIA_EXT + r"\b"), "~/…"),
    (re.compile(r"~[/\\][^\s\"'<>]*"), "~/…"),
    (re.compile(r"(?i)\b[A-Z]:\\[^\s\"'<>]*"), "<path>"),
    (re.compile(r"(?<![\w.~])/(?:[^\s/\"'<>]+/)+[^\s\"'<>]*"), "<path>"),
    # Хвосты путей после пробела: любой токен с «\» или с двумя «/».
    (re.compile(r"[^\s\"'<>]*\\[^\s\"'<>]*"), "<path>"),
    (re.compile(r"[^\s\"'<>/]*/[^\s\"'<>/]*/[^\s\"'<>]*"), "<path>"),
    (re.compile(r"(?i)([\"'])[^\"'\n]*\." + _MEDIA_EXT + r"(?:\s*\+\s*[^\"'\n]*)?\1"), r"\1<file>\1"),
    # Имя файла с пробелами («Клиент Иванов промо.mp4»): до шести слов перед медиа-расширением.
    (re.compile(r"(?i)(?:[^\s/\\\"'«»<>{}\[\],:;|→]+[ _\-]+){1,6}[^\s/\\\"'«»<>{}\[\],:;|→]*\."
                + _MEDIA_EXT + r"\b"), "<file>"),
    (re.compile(r"(?i)[^\s/\\\"'«»<>(){}\[\],:]+\." + _MEDIA_EXT + r"\b"), "<file>"),
    (re.compile(r"«[^»]*»"), "«…»"),
    (re.compile(r"\b\d{12,}\b"), "<num>"),
    (re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{16,}\b"), "<token>"),
    # Длинные токены (voice_id, public_id), кроме известных кодов ошибок хранилищ — они полезны.
    (re.compile(r"\b(?!(?:SignatureDoesNotMatch|AccessDenied|RequestTimeTooSkewed|ExpiredToken|"
                r"EntityTooLarge|InvalidAccessKeyId|AuthorizationHeaderMalformed)\b)[A-Za-z0-9]{20,}\b"),
     "<token>"),
]
# Строки, где после маркера идёт пользовательское содержимое (названия видео, файлы, голоса).
_CONTENT_MARKERS = re.compile(
    r"(?i)(Destination:|Merging formats into|Сохранено:|Скачано:|Загружаю |Переименован|"
    r"Аудио сохранено:|Lipsync готов:|Передаю в audio-separator:|voice_id:)")


_LOCAL_IDENTITY = None


def _local_identity_values():
    """Имя пользователя и компьютера этой машины — вырезаются из отчёта дословно."""
    global _LOCAL_IDENTITY
    if _LOCAL_IDENTITY is None:
        values = set()
        for getter in (lambda: os.environ.get("USER"), lambda: os.environ.get("USERNAME"),
                       lambda: Path.home().name, platform.node,
                       lambda: platform.node().split(".")[0]):
            try:
                v = (getter() or "").strip()
            except Exception:
                v = ""
            if len(v) >= 3:
                values.add(v)
        _LOCAL_IDENTITY = sorted(values, key=len, reverse=True)
    return _LOCAL_IDENTITY


def sanitize_for_report(text):
    """Вырезает из строки ключи, токены, URL, пути, имена файлов и голосов, почту, IP,
    имя пользователя и компьютера."""
    text = str(text)
    m = _CONTENT_MARKERS.search(text)
    if m:
        text = text[:m.end()] + " …"
    for pattern, repl in _SANITIZE_RULES:
        text = pattern.sub(repl, text)
    # Последним шагом: имя пользователя и компьютера, если они встретились вне путей.
    for value in _local_identity_values():
        text = re.sub(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", "<local>", text, flags=re.IGNORECASE)
    return text


# ── Отчёт об ошибке: только коды, тип и этап; хвост лога — только строки известных форматов ──
REPORT_MESSAGE_LIMIT = 200
# Ошибки внешних сервисов: тело ответа и текст запроса (сценарий, перевод, транскрипт) не уходят никогда.
_REPORT_SERVICES = (
    ("ElevenLabs", re.compile(r"(?i)elevenlabs|xi-api|text_to_speech|voices/add|create_voice")),
    ("Groq", re.compile(r"(?i)groq|transcri|translat")),
    ("Sync", re.compile(r"(?i)\bsync\b|sync\.so|lipsync|apply_lipsync|upload_to_sync|fetch_roll_from_sync")),
    ("yt-dlp", re.compile(r"(?i)yt[-_]?dlp|youtube|download_from_url|\bERROR: \[")),
    ("ffmpeg", re.compile(r"(?i)ffmpeg|ffprobe|non-zero exit status")),
    ("Cloudinary", re.compile(r"(?i)cloudinary")),
)
_REPORT_STAGES = (
    (re.compile(r"(?i)до создания задачи"), "до создания задачи"),
    (re.compile(r"(?i)после создания задачи"), "после создания задачи"),
    (re.compile(r"(?i)регистрац|register"), "регистрация файла"),
    (re.compile(r"(?i)загрузк|upload|assets"), "загрузка файла"),
    (re.compile(r"(?i)скачив|download|результат не удалось сохранить"), "скачивание"),
    (re.compile(r"(?i)status|статус|опрос"), "статус задачи"),
    (re.compile(r"(?i)\btts\b|text_to_speech|генерац\w* голос"), "генерация голоса"),
    (re.compile(r"(?i)voices/add|клонир"), "клонирование"),
    (re.compile(r"(?i)transcri|транскриб"), "транскрибация"),
    (re.compile(r"(?i)translat|перевод"), "перевод"),
    (re.compile(r"(?i)estimate|оценк"), "оценка"),
    (re.compile(r"(?i)create|создан"), "создание задачи"),
)
_REPORT_CODE_RULES = (
    # HTTP-статус рядом со словом-маркером или в стандартной фразе requests
    (re.compile(r"(?i)(?<!exit )(?:error|ошибка|status|статус|http|code|код|response|ответ)\D{0,20}?\b([1-5]\d\d)\b"),
     "HTTP {}"),
    (re.compile(r"\b([45]\d\d) (?:Client|Server) Error"), "HTTP {}"),
    # коды из JSON/«ключ=значение»: только идентификаторы латиницей, свободный текст не проходит
    (re.compile(r"(?i)[\"']?\b(status|code|error_code|errorCode|error_type|type)\b[\"']?\s*[:=]\s*[\"']?"
                r"([A-Za-z][A-Za-z0-9]*(?:[_.\-][A-Za-z0-9]+)+|[A-Z][A-Z0-9_]{2,40})(?![\w\-])"), "{}={}"),
    (re.compile(r"\b(FAILED|REJECTED|COMPLETED|PENDING|PROCESSING|CANCELL?ED)\b"), "{}"),
    (re.compile(r"(?i)exit status (-?\d+)"), "exit status {}"),
    (re.compile(r"\b([A-Z][A-Za-z]+(?:Error|Exception|Timeout|Refused|Reset))\b"), "{}"),
    (re.compile(r"(?i)\b(timed out|timeout|connection (?:reset|refused|aborted)|unauthori[sz]ed|forbidden|"
                r"not found|payment required|too many requests|rate limit\w*|quota \w+|invalid api key|"
                r"video unavailable|private video|unsupported url|members[- ]only|sign in to confirm your age|"
                r"not available in your country|no space left on device|permission denied)\b"), "{}"),
)
_QUOTED_RE = re.compile(r"«[^»]*»|\"[^\"]*\"|“[^”]*”|'[^'\n]{2,}'")
_JSON_RE = re.compile(r"\{.*\}", re.S)
_FREE_TEXT_TAIL_RE = re.compile(r"(?i)\b(text|message|detail|details|prompt|input|content|transcript|translation|"
                                r"reason|msg|текст|сообщение|причина)\s*[:=].*$")


def report_error_codes(text):
    codes = []
    for rx, fmt in _REPORT_CODE_RULES:
        for m in rx.finditer(text):
            value = fmt.format(*m.groups()) if m.groups() else m.group(0)
            if value not in codes:
                codes.append(value)
    return codes[:8]


# Для этих ошибок имена в кавычках — это имена классов, атрибутов и модулей кода, а не данные
# пользователя: оставляем их, если они похожи на идентификатор ('dict' object has no attribute 'voice_id').
REPORT_IDENT_ERRORS = ("AttributeError", "NameError", "ImportError", "ModuleNotFoundError")
_QUOTED_IDENT_RE = re.compile(r"(['\"])([A-Za-z_][A-Za-z0-9_.]*)\1")


def report_safe_message(message, where="", limit=REPORT_MESSAGE_LIMIT, exc_type=""):
    """Текст ошибки для отчёта. Внешние сервисы: «сервис: этап — коды», без тела ответа и текста
    запроса. Свои ошибки: первая строка без кавычек, JSON и хвостов «text: …», через санитайзер."""
    text = str(message or "")
    probe = f"{where}\n{text}"
    # Ошибки кода (AttributeError и т.п.) — не ответы сервисов, даже если в тексте есть имя сервиса.
    service = (None if str(exc_type) in REPORT_IDENT_ERRORS
               else next((name for name, rx in _REPORT_SERVICES if rx.search(probe)), None))
    if service:
        # Этап — по месту ошибки и первой строке; тело ответа (JSON) и «exit status» не учитываются.
        head = text.strip().splitlines()[0] if text.strip() else ""
        head = re.sub(r"(?i)exit status -?\d+", "", _JSON_RE.sub("", head))
        stage_probe = f"{where}\n{head}"
        stage = next((label for rx, label in _REPORT_STAGES if rx.search(stage_probe)), "")
        codes = report_error_codes(text)
        out = service + (f": {stage}" if stage else "") + (f" — {', '.join(codes)}" if codes else " — без кода")
        return sanitize_for_report(out)[:limit]
    first = text.strip().splitlines()[0] if text.strip() else ""
    kept = []
    if str(exc_type) in REPORT_IDENT_ERRORS:
        def keep(m):
            kept.append(m.group(0))
            return f"QIDENT{len(kept) - 1}Q"
        first = _QUOTED_IDENT_RE.sub(keep, first)
    first = _JSON_RE.sub("{…}", first)
    first = _QUOTED_RE.sub("«…»", first)
    first = _FREE_TEXT_TAIL_RE.sub(lambda m: m.group(1) + ": …", first)
    out = sanitize_for_report(first)
    for i, original in enumerate(kept):
        out = out.replace(f"QIDENT{i}Q", original)
    return out[:limit]


# Хвост лога: только строки известных форматов, и от строки остаётся лишь распознанная часть.
_LOG_TAIL_FORMATS = [re.compile(p) for p in (
    r"^Sync status: [A-Z_]+",
    r"^Sync job \S+ (?:COMPLETED|FAILED|REJECTED)",
    r"^Sync job: \S+",
    r"^Sync create response: \d{3}",
    r"^Sync status error(?: после повторов)?: \d{3}",
    r"^Повтор опроса \d+/\d+: статус-эндпоинт снова вернул \d{3}",
    r"^Отправляю в Sync\.\.\. попытка \d+/\d+",
    r"^Создаю задачу Sync",
    r"^Задача уже была создана первым запросом",
    r"^Sync ошибка на попытке \d+",
    r"^Пауза \d+ секунд перед повтором",
    r"^Verification: [^:]{0,80}",
    r"^Оценка (?:части|ролика): [\d\s.,·≈$a-zа-я()–-]{0,120}",
    r"^Стоимость job \S+: оценка \$[\d.]+, факт ≈ \$[\d.]+",
    r"^\[TIMING\] [^:]{0,40}: [\d.]+ сек",
    r"^\[TIMING\] Текст: \d+ символов, модель: [\w.-]+",
    r"^Генерирую аудио ElevenLabs",
    r"^Ролик \d+ / очередь \d+",
    r"^Режим: (?:1 видео|Начало \+ Конец)",
    r"^Lipsync (?:start|end)\.\.\.",
    r"^Роликов в очереди: \d+",
    r"^Параллельность: \d+",
    r"^Очередь завершена: успешно \d+ из \d+, ошибок \d+",
    r"^Хранилище для Sync: \w+",
    r"^(?:Загружаю (?:видео|аудио) в Sync: [\d.]+ МБ|(?:Видео|Аудио) в Sync: asset)",
    r"^Sync: удалён asset",
    r"^yt-dlp (?:устарел|обновлён)",
    r"^Сжатие: (?:запуск, файлов \d+|ошибка)",
)]
_LOG_PREFIX_RE = re.compile(r"^(\[#\d+\]\s*)")


def report_log_tail(lines):
    """Строки лога для отчёта: только известные форматы (статусы, коды, оценки, этапы).
    Строки с текстом сценария, перевода, транскрипта, именами и ответами API не берутся."""
    out, skipped = [], 0
    for raw in lines:
        line = str(raw).strip()
        m = _LOG_PREFIX_RE.match(line)
        prefix = m.group(1) if m else ""
        rest = line[len(prefix):]
        hit = next((rx.match(rest) for rx in _LOG_TAIL_FORMATS if rx.match(rest)), None)
        if hit is None:
            skipped += 1
            continue
        out.append(sanitize_for_report(prefix + hit.group(0))[:MAX_REPORT_LOG_LINE])
    if skipped:
        out.append(f"(строк лога не включено: {skipped})")
    return out


MAX_REPORT_LOG_LINE = 300


# ── Права доступа к личным файлам: 0600 для файлов, 0700 для папки ──────────
def secure_path(path, is_dir=False):
    """Mac/Linux: только владелец. Windows: файлы в профиле пользователя по умолчанию закрыты
    ACL от других учётных записей, chmod там не работает — ничего не делаем."""
    if os.name != "posix" or not path:
        return
    try:
        os.chmod(path, 0o700 if is_dir else 0o600)
    except OSError:
        pass


def open_private(path, mode="w", encoding="utf-8"):
    """Открыть файл на запись так, чтобы он сразу создавался с правами 0600 (атомарная запись)."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if "a" in mode else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    secure_path(path)     # если файл уже был с другими правами
    return os.fdopen(fd, mode, encoding=encoding)


def secure_private_files():
    """При запуске: привести права уже существующих файлов (конфиг, состояние, лог, точки лица)."""
    for p in (CONFIG_PATH, STATE_PATH, LOG_FILE_PATH, LOG_FILE_PATH + ".1", FACE_POINTS_PATH,
              ERROR_REPORT_STATE_PATH):
        if os.path.exists(p):
            secure_path(p)
    if os.path.isdir(APP_DATA_DIR):
        secure_path(APP_DATA_DIR, is_dir=True)


# ── Клонирование: voice_id из ответа ElevenLabs ─────────────────────────────
_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def voice_id_from_clone_response(obj):
    """voice_id из ответа клонирования: словарь REST API ({"voice_id": …}), объект SDK с полем
    voice_id или уже готовая строка. Иначе ValueError."""
    if isinstance(obj, str):
        value = obj
    elif isinstance(obj, dict):
        value = obj.get("voice_id") or obj.get("voiceId")
    else:
        value = getattr(obj, "voice_id", None) or getattr(obj, "voiceId", None)
    value = str(value).strip() if value else ""
    if not _VOICE_ID_RE.match(value):
        raise ValueError("нет корректного voice_id")
    return value


# ── Защищённое обновление: sha256 обязателен, самопроверка, замок одного экземпляра ──────────
UPDATE_APP_URL = "https://raw.githubusercontent.com/1Sheqel/Sheqel/main/app.py"
UPDATE_SHA_RETRIES = 3            # повторов при несовпадении sha256 (кэш GitHub ещё отдаёт старый файл)
UPDATE_RETRY_PAUSE_SEC = 60
UPDATE_DOWNLOAD_TIMEOUT_SEC = 60
UPDATE_SELFTEST_TIMEOUT_SEC = 60
SELFTEST_MARKER = "SHEQEL_SELFTEST_OK"
AFTER_UPDATE_WAIT_SEC = 15
UPDATE_TMP_NAME = "app.py.new"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UpdateError(RuntimeError):
    """kind: no_sha | download | sha | selftest | version | replace."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def platform_is_windows():
    return sys.platform == "win32"


def _no_window_kwargs():
    return {"creationflags": 0x08000000} if platform_is_windows() else {}   # CREATE_NO_WINDOW


def download_update_file(url, dest, timeout=UPDATE_DOWNLOAD_TIMEOUT_SEC):
    """Скачивает файл целиком в dest и возвращает его sha256. Любой сбой — UpdateError('download')."""
    h = hashlib.sha256()
    try:
        with requests.get(url, timeout=timeout, stream=True,
                          headers={"Cache-Control": "no-cache", "Pragma": "no-cache"}) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    if chunk:
                        f.write(chunk)
                        h.update(chunk)
    except Exception as e:
        raise UpdateError("download", f"Не удалось скачать обновление: {_strip_urls(e)}")
    return h.hexdigest()


def run_update_selftest(path, expected_version, timeout=UPDATE_SELFTEST_TIMEOUT_SEC, python=None):
    """Запускает скачанный файл отдельным процессом с --selftest: без окна, сети, отчётов и записи файлов.
    Нужна метка SELFTEST_MARKER с версией, равной версии из манифеста."""
    try:
        res = subprocess.run([python or sys.executable, path, "--selftest"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout, cwd=os.path.dirname(path),
                             env=dict(os.environ, SHEQEL_SELFTEST="1"), **_no_window_kwargs())
    except subprocess.TimeoutExpired:
        raise UpdateError("selftest", f"Новая версия не прошла самопроверку: не ответила за {timeout} с.")
    m = re.search(SELFTEST_MARKER + r" version=(\S+)", res.stdout or "")
    if res.returncode != 0 or not m:
        missing = re.search(r"No module named '([^']+)'", res.stderr or "")
        if missing:
            raise UpdateError("selftest", f"Новой версии нужен пакет «{missing.group(1)}», а он не установлен. "
                                          "Обновление не применено.")
        last = (res.stderr or res.stdout or "").strip().splitlines()[-1:] or ["нет вывода"]
        raise UpdateError("selftest", "Новая версия не прошла самопроверку — обновление не применено "
                                      f"(код {res.returncode}: {sanitize_for_report(last[0])[:160]}).")
    if m.group(1) != expected_version:
        raise UpdateError("version", f"Версия в скачанном файле ({m.group(1)}) не совпадает с объявленной "
                                     f"({expected_version}). Обновление не применено.")
    return m.group(1)


def install_verified_update(app_dir, expected_sha256, latest_version, url=None, on_status=None,
                            retries=None, pause=None, sleep=time.sleep):
    """Скачать → sha256 (с повторами против кэша) → самопроверка → замена app.py.
    Временный файл удаляется при любом исходе (finally). Возвращает путь к app.py."""
    on_status = on_status or (lambda text: None)
    url = url or UPDATE_APP_URL
    retries = UPDATE_SHA_RETRIES if retries is None else retries
    pause = UPDATE_RETRY_PAUSE_SEC if pause is None else pause
    if not isinstance(expected_sha256, str) or not _SHA256_RE.match(expected_sha256.strip().lower()):
        raise UpdateError("no_sha", "В манифесте обновления нет контрольной суммы sha256 — обновление отклонено "
                                    "ради безопасности. Сообщи разработчику.")
    expected_sha256 = expected_sha256.strip().lower()
    app_path = os.path.join(app_dir, "app.py")
    tmp = os.path.join(app_dir, UPDATE_TMP_NAME)
    try:
        for attempt in range(retries + 1):
            on_status("Скачиваю обновление..." if attempt == 0 else f"Повтор {attempt} из {retries}: скачиваю...")
            got = download_update_file(f"{url}?t={time.time_ns()}", tmp)   # ?t= — мимо кэша
            if got == expected_sha256:
                break
            if attempt < retries:
                on_status(f"Обновление ещё публикуется, повтори через несколько минут. "
                          f"Пробую снова через {pause:g} с (повтор {attempt + 1} из {retries})...")
                sleep(pause)
        else:
            raise UpdateError("sha", "Скачанный файл не совпадает с контрольной суммой даже после повторов — "
                                     "он повреждён или подменён. Обновление не применено.")
        on_status("Проверяю новую версию...")
        run_update_selftest(tmp, latest_version)
        on_status("Применяю обновление...")
        try:
            if os.path.exists(app_path):
                shutil.copy2(app_path, app_path + ".bak")
            os.replace(tmp, app_path)
            with open(os.path.join(app_dir, ".just_updated"), "w", encoding="utf-8") as f:
                f.write(latest_version)
        except OSError as e:
            raise UpdateError("replace", f"Не удалось заменить файл программы: {e}")
        return app_path
    finally:
        for leftover in (tmp, tmp + ".part"):
            try:
                if os.path.exists(leftover):
                    os.remove(leftover)
            except OSError:
                pass


def run_selftest():
    """python app.py --selftest: модуль загрузился, main и APP_VERSION на месте. Без окна, сети и записи."""
    problems = []
    if not callable(globals().get("main")):
        problems.append("нет main()")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(globals().get("APP_VERSION", ""))):
        problems.append("нет APP_VERSION")
    if "LipsyncTwoModeApp" not in globals():
        problems.append("нет окна приложения")
    if problems:
        print("SHEQEL_SELFTEST_FAIL " + "; ".join(problems), flush=True)
        return 1
    print(f"{SELFTEST_MARKER} version={APP_VERSION}", flush=True)
    return 0


# Замок «один экземпляр»: две копии не пишут конфиг и state.json одновременно.
def instance_lock_path():
    return os.path.join(APP_DATA_DIR, "instance.lock")


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if platform_is_windows():
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return code.value == 259                                         # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def read_instance_lock():
    try:
        with open(instance_lock_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def acquire_instance_lock(wait_sec=0.0, sleep=time.sleep):
    """(True, None) — замок наш. (False, данные замка) — работает другая копия.
    Устаревший замок (процесса нет) или наш собственный (тот же PID после os.execv) забирается."""
    path = instance_lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    secure_path(os.path.dirname(path), is_dir=True)
    deadline = time.time() + max(0.0, wait_sec)
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "time": time.time(), "version": APP_VERSION}, f)
            return True, None
        except FileExistsError:
            info = read_instance_lock()
            if not info or info.get("pid") == os.getpid() or not pid_alive(info.get("pid")):
                try:
                    os.remove(path)          # устаревший или наш — забираем
                except OSError:
                    pass
                continue
            if time.time() < deadline:
                sleep(0.3)
                continue
            return False, info


def release_instance_lock():
    info = read_instance_lock()
    if info and info.get("pid") == os.getpid():
        try:
            os.remove(instance_lock_path())
        except OSError:
            pass


def notify_already_running(info, after_update=False):
    text = ("SheqelMotion уже открыт" + (f" (процесс {info.get('pid')})" if info else "") + ".\n\n"
            + ("Предыдущая копия ещё не закрылась после обновления. Закрой её и запусти программу снова."
               if after_update else "Переключись на открытое окно — вторая копия не запускается, чтобы "
                                    "не испортить настройки и очередь."))
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning("SheqelMotion", text, parent=root)
        root.destroy()
    except Exception:
        print(text, file=sys.stderr)


_EXPECTED_ERROR_RE = re.compile(
    r"(?i)(?:\b(?:error|ошибка|status|статус|http)\b[^\n]{0,25}?\b(?:401|402|429)\b"
    r"|\b(?:401|402|429)\b\s*(?:client error|unauthorized|payment required|too many requests)"
    r"|api key неверный|unauthorized|invalid[_ ]api[_ ]key|quota[_ ]exceeded|rate[_ -]?limit"
    r"|сначала укажи|текст пустой|нет текста (?:до|после)"
    r"|слишком большой для groq"
    r"|video unavailable|private video|this video (?:is|has been) (?:not available|unavailable|private|removed)"
    r"|unsupported url|http error 404|members[- ]only|not available in your country|sign in to confirm your age"
    r"|не удалось прочитать куки)")


def is_expected_error(text):
    """Ожидаемые ситуации (неверный ключ, лимиты, недоступное видео) — не баг, отчёт не нужен."""
    return bool(_EXPECTED_ERROR_RE.search(str(text)))


def _where_from_tb(tb, default="app"):
    """«модуль.функция:строка» — по последнему кадру app.py в traceback, без путей."""
    frames = traceback.extract_tb(tb) if tb else []
    if not frames:
        return default
    own = [f for f in frames if f.filename.endswith("app.py")]
    f = (own or frames)[-1]
    return f"{Path(f.filename).stem}.{f.name}:{f.lineno}"


def _os_description():
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} {platform.machine()}"
    if sys.platform == "win32":
        return f"Windows {platform.release()} ({platform.version()}) {platform.machine()}"
    return f"{platform.system()} {platform.release()} {platform.machine()}"


class ErrorReporter:
    """Отправляет разработчику обезличенный отчёт. Никогда не бросает исключений и не блокирует UI."""

    def __init__(self):
        self._lock = threading.Lock()
        self._mem_state = {}
        self.install_id = ""
        self.enabled = False

    def configure(self, install_id, enabled):
        with self._lock:
            self.install_id = install_id
            self.enabled = bool(enabled)

    def build_payload(self, where, exc_type, message, job_id=None):
        safe_job = str(job_id) if job_id and re.fullmatch(r"[A-Za-z0-9\-]{6,64}", str(job_id)) else None
        # Имя класса и место ошибки формирует код, а не пользователь: проверяем формат,
        # иначе длинные имена вроде SyncResultDownloadError санитайзер принял бы за токены.
        where = str(where)
        if not re.fullmatch(r"[A-Za-z0-9_.<>]{1,100}(?::\d{1,6})?", where):
            where = sanitize_for_report(where)[:120]
        exc_type = str(exc_type)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,79}", exc_type):
            exc_type = "Error"
        return {
            "install_id": self.install_id,
            "app_version": APP_VERSION,
            "os": _os_description(),
            "python": platform.python_version(),
            "where": where,
            "exc_type": exc_type,
            "message": report_safe_message(message, where, exc_type=exc_type),
            "log_tail": report_log_tail(recent_log_lines(ERROR_REPORT_LOG_LINES * 4))[-ERROR_REPORT_LOG_LINES:],
            "job_id": safe_job,
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def report(self, where, exc=None, exc_type=None, message=None, job_id=None):
        try:
            if not ERROR_REPORT_URL or not self.enabled or not self.install_id:
                return
            exc_type = exc_type or (type(exc).__name__ if exc is not None else "Error")
            message = message if message is not None else str(exc)
            if is_expected_error(message):
                return
            payload = self.build_payload(where, exc_type, message, job_id)
            signature = f"{payload['exc_type']}@{payload['where'].split(':')[0]}"
            threading.Thread(target=self._send, args=(payload, signature),
                             name=ERROR_REPORT_THREAD_NAME, daemon=True).start()
        except Exception as e:
            write_log_file(f"Отчёт об ошибке не сформирован: {type(e).__name__}: {e}")

    def _send(self, payload, signature):
        try:
            if not self._reserve_slot(signature):
                write_log_file(f"Отчёт об ошибке пропущен (повтор за час или суточный лимит): {signature}")
                return
            res = requests.post(ERROR_REPORT_URL, json=payload, timeout=ERROR_REPORT_TIMEOUT_SEC)
            write_log_file(f"Отчёт об ошибке отправлен (HTTP {res.status_code}): {signature}")
        except Exception as e:
            write_log_file(f"Отчёт об ошибке не отправлен: {type(e).__name__}: {e}")

    def _reserve_slot(self, signature):
        """Не чаще раза в час на одинаковую ошибку и не больше ERROR_REPORT_DAILY_LIMIT в сутки."""
        now = time.time()
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            state = self._load_state()
            if state.get("day") != today:
                state = {"day": today, "count": 0, "last": state.get("last", {})}
            last = {k: v for k, v in state.get("last", {}).items() if now - v < ERROR_REPORT_DEDUP_SEC}
            if signature in last or state.get("count", 0) >= ERROR_REPORT_DAILY_LIMIT:
                return False
            last[signature] = now
            state["last"] = last
            state["count"] = state.get("count", 0) + 1
            self._save_state(state)
            return True

    def _load_state(self):
        try:
            with open(ERROR_REPORT_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return dict(self._mem_state)

    def _save_state(self, state):
        self._mem_state = dict(state)
        try:
            tmp = ERROR_REPORT_STATE_PATH + ".tmp"
            with open_private(tmp) as f:
                json.dump(state, f)
            os.replace(tmp, ERROR_REPORT_STATE_PATH)
        except OSError:
            pass


error_reporter = ErrorReporter()


# ── Состояние между сессиями и фоновые задачи ───────────────────────────────
APP_DATA_DIR = str(Path.home() / ".sheqelmotion")
STATE_PATH = os.path.join(APP_DATA_DIR, "state.json")
VOICE_DATA_DIR = os.path.join(APP_DATA_DIR, "voices")
STATE_SAVE_DELAY_MS = 800
MAX_SAVED_TASKS = 300
MAX_SYNC_JOURNAL = 500
MAX_VOICE_FILES = 60
TASK_FINISHED = ("done", "error", "cancelled", "interrupted")


class BackgroundTaskManager:
    """Фоновые задачи всех вкладок (голос, скачивание, транскрибация, перевод, клонирование,
    в будущем — сжатие видео). Потоки пишут только сюда; виджеты читают отсюда в главном потоке.
    Статусы: queued, running, done, error, cancelled, interrupted (приложение закрыли во время работы)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks = {}
        self._changes = queue.Queue()

    def create(self, kind, panel, title="", meta=None, status="queued"):
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock:
            self._tasks[task_id] = {
                "id": task_id, "kind": kind, "panel": panel, "title": title, "status": status,
                "progress": None, "message": "", "result": None, "error": None,
                "meta": dict(meta or {}), "created": now, "updated": now, "cancel": False,
            }
        self._changes.put(task_id)
        return task_id

    def update(self, task_id, **fields):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.update(fields)
            task["updated"] = time.time()
        self._changes.put(task_id)

    def get(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            return copy.deepcopy(task) if task else None

    def list(self, panel=None, kind=None, **meta):
        with self._lock:
            items = [copy.deepcopy(t) for t in self._tasks.values()
                     if (panel is None or t["panel"] == panel) and (kind is None or t["kind"] == kind)
                     and all(t["meta"].get(k) == v for k, v in meta.items())]
        return sorted(items, key=lambda t: t["created"])

    def latest(self, kind, **meta):
        items = self.list(kind=kind, **meta)
        return items[-1] if items else None

    def active(self, panel=None, kind=None):
        return [t for t in self.list(panel=panel, kind=kind) if t["status"] not in TASK_FINISHED]

    def request_cancel(self, task_id):
        self.update(task_id, cancel=True)

    def cancel_requested(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task["cancel"])

    def clear_finished(self):
        """Удаляет завершённые задачи (для «Очистить сохранённые данные»)."""
        with self._lock:
            gone = [i for i, t in self._tasks.items() if t["status"] in TASK_FINISHED]
            for task_id in gone:
                del self._tasks[task_id]
        for task_id in gone:
            self._changes.put(task_id)
        return len(gone)

    def remove(self, task_id):
        with self._lock:
            removed = self._tasks.pop(task_id, None)
        if removed:
            self._changes.put(task_id)

    def drain_changes(self):
        changed = set()
        while True:
            try:
                changed.add(self._changes.get_nowait())
            except queue.Empty:
                return changed

    def snapshot(self, limit=MAX_SAVED_TASKS):
        items = self.list()
        return [{k: v for k, v in t.items() if k != "cancel"} for t in items[-limit:]]

    def restore(self, tasks):
        """Задачи, которые шли в момент закрытия, восстанавливаются как «прерванные»."""
        with self._lock:
            for t in tasks or []:
                if not isinstance(t, dict) or "id" not in t:
                    continue
                t = dict(t, cancel=False)
                if t.get("status") not in TASK_FINISHED:
                    t["status"] = "interrupted"
                    t["message"] = "прервано: приложение было закрыто"
                self._tasks[t["id"]] = t


class AppStateStore:
    """state.json: очередь, поля вкладок, задачи, журнал Sync. Запись атомарная."""

    def __init__(self, path=None):
        self.path = path or STATE_PATH
        self._lock = threading.Lock()
        self.load_error = None

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            backup = f"{self.path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                shutil.copy2(self.path, backup)
            except OSError:
                backup = None
            self.load_error = f"{e} (копия: {backup})"
            return {}

    def save(self, data):
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            secure_path(os.path.dirname(self.path), is_dir=True)   # ~/.sheqelmotion — 0700
            tmp = self.path + ".tmp"
            with open_private(tmp) as f:                            # state.json — 0600
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)

    def clear(self):
        """Удаляет state.json, его временный файл и копии битых файлов. Возвращает число файлов."""
        removed = 0
        with self._lock:
            folder, base = os.path.dirname(self.path), os.path.basename(self.path)
            try:
                names = os.listdir(folder)
            except OSError:
                return 0
            for name in names:
                if name == base or name == base + ".tmp" or name.startswith(base + ".corrupt-"):
                    try:
                        os.remove(os.path.join(folder, name))
                        removed += 1
                    except OSError:
                        pass
        return removed


# ── Секреты не попадают в state.json и журнал Sync ─────────────────────────
# Подписанная ссылка — URL с параметрами подписи (S3, GCS, Azure, CloudFront, токены).
_SIGNED_URL_RE = re.compile(
    r"(?i)(https?://[^\s\"'<>?#]+)\?([^\s\"'<>#]*(?:X-Amz-|X-Goog-|Signature=|Key-Pair-Id=|Policy=|"
    r"[?&]?sig=|[?&]?se=|[?&]?token=|access_token=|api_key=|apikey=)[^\s\"'<>#]*)")
_KEY_LIKE_RE = re.compile(r"\b(?:sk|gsk|xi)_[A-Za-z0-9]{20,}\b")
# Поля, где ссылка остаётся как есть: outputUrl нужен для ручного забора результата.
STATE_KEEP_URL_FIELDS = ("output_url",)


def scrub_secrets(value, secrets=(), keep_fields=STATE_KEEP_URL_FIELDS, _field=None):
    """Копия value без API-ключей и подписанных ссылок (кроме полей keep_fields)."""
    if isinstance(value, dict):
        return {k: scrub_secrets(v, secrets, keep_fields, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_secrets(v, secrets, keep_fields, _field) for v in value]
    if not isinstance(value, str) or _field in keep_fields:
        return value
    for secret in secrets:
        if secret and len(secret) >= 8 and secret in value:
            value = value.replace(secret, "<ключ>")
    value = _KEY_LIKE_RE.sub("<ключ>", value)
    return _SIGNED_URL_RE.sub(r"\1?<подпись удалена>", value)


def known_secret_values(*extra):
    """Значения ключей из глобальных настроек (ключи пользователя — только в памяти и config)."""
    return [s for s in (SYNC_API_KEY, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET, *extra)
            if isinstance(s, str) and s]


def _lock_of(obj, name):
    """Блокировка объекта или пустой контекст (подставные объекты в тестах её не имеют)."""
    return getattr(obj, name, None) or contextlib.nullcontext()


def _locked_copy(obj, lock_name, attr):
    with _lock_of(obj, lock_name):
        value = getattr(obj, attr)
        return [dict(v) for v in value] if isinstance(value, list) else value


# Журнал задач Sync (job_id, outputUrl, путь): приложение подставляет сюда свой обработчик.
sync_job_hook = None


def journal_sync_job(event, **fields):
    hook = sync_job_hook
    if hook is None:
        return
    try:
        fields = scrub_secrets(fields, known_secret_values())
        hook(dict(fields, event=event, time=time.strftime("%Y-%m-%d %H:%M:%S")))
    except Exception as e:
        write_log_file(f"Журнал Sync: не удалось записать событие {event}: {e}")


def prune_voice_files(keep_paths=(), limit=MAX_VOICE_FILES):
    """Оставляет в папке голосов не больше limit файлов (кроме keep_paths — текущих A/B)."""
    try:
        files = sorted((os.path.join(VOICE_DATA_DIR, n) for n in os.listdir(VOICE_DATA_DIR)),
                       key=os.path.getmtime)
    except OSError:
        return
    keep = {os.path.abspath(p) for p in keep_paths if p}
    extra = [p for p in files if os.path.abspath(p) not in keep][:max(0, len(files) - limit)]
    for p in extra:
        try:
            os.remove(p)
        except OSError:
            pass

_orig_thread_excepthook = threading.excepthook


def _thread_excepthook(args):
    name = args.thread.name if args.thread else "?"
    write_log_file(f"НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ В ПОТОКЕ {name}:\n" + "".join(
        traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    if name != ERROR_REPORT_THREAD_NAME:
        error_reporter.report(_where_from_tb(args.exc_traceback), exc=args.exc_value,
                              exc_type=args.exc_type.__name__)
    _orig_thread_excepthook(args)


ELEVEN_BASE_URL = "https://api.elevenlabs.io"
ELEVEN_TTS_MODEL_ID = "eleven_v4"      # модель по умолчанию
ELEVEN_TTS_MODEL_ID_V3 = "eleven_v3"   # для обратной совместимости — выбирается в UI
ELEVEN_OUTPUT_FORMAT = "mp3_44100_192"

# Языки для TTS-панели (language_code в ElevenLabs API). "Авто" — поле не отправляется.
ELEVEN_TTS_LANGUAGES = {
    "Авто": None,
    "Русский": "ru", "Английский": "en", "Украинский": "uk",
    "Немецкий": "de", "Французский": "fr", "Испанский": "es",
    "Итальянский": "it", "Польский": "pl", "Португальский": "pt",
    "Китайский": "zh", "Японский": "ja", "Корейский": "ko",
    "Арабский": "ar", "Турецкий": "tr", "Хинди": "hi",
    "Нидерландский": "nl", "Шведский": "sv", "Чешский": "cs",
    "Румынский": "ro", "Венгерский": "hu", "Литовский": "lt",
    "Эстонский": "et", "Латышский": "lv",
}

GROQ_TRANSLATE_MODEL = "openai/gpt-oss-120b"  # llama-3.3-70b-versatile депрекейтнута Groq
GROQ_TRANSLATE_RETRIES = 3

SYNC_GENERATE_URL = "https://api.sync.so/v2/generate"
SYNC_LIST_URL = "https://api.sync.so/v2/generations"
SYNC_MODEL = "sync-3"
# Передаём явно: совпадает с текущим значением по умолчанию у Sync, но не зависит от его смены.
SYNC_SYNC_MODE = "bounce"
# Частота кадров входа Sync: оплата идёт за кадры, поэтому 25 fps дешевле для 30/60 fps.
SYNC_TARGET_FPS = 25
SYNC_FPS_MODES = {"Как в исходнике": "source", "25 fps (дешевле для 30/60 fps)": "25"}
SYNC_FPS_LABELS = {v: k for k, v in SYNC_FPS_MODES.items()}


def sync_target_fps(mode):
    return SYNC_TARGET_FPS if mode == "25" else None
SYNC_POLL_INTERVAL_SEC = 5
SYNC_TIMEOUT_SEC = 30 * 60
SYNC_CREATE_TIMEOUT_SEC = 90

VIDEO_CRF = "13"
VIDEO_PRESET = "slow"
AUDIO_BITRATE = "256k"
SHARPEN_FILTER = "unsharp=3:3:0.6:3:3:0.0"

END_PADDING = 0.8
SYNC_INPUT_SCALE = "720:-2"
SYNC_INPUT_FPS = "25"
CONCURRENT_ROLLS = 2
PAUSE_BETWEEN_ROLLS_SEC = 3
SYNC_RETRIES = 3
SILENCE_NOISE = "-25dB"
SILENCE_DURATION = "1.8"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Arc Browser — тёмный стальной
BG = "#12131a"
PANEL = "#1a1b26"
CARD = "#20222f"
BTN = "#252736"
BTN_HOVER = "#32354a"
BTN_OK = "#0e7a60"
BTN_OK_HOVER = "#129970"
BTN_PRIMARY = "#1c3f6e"
BTN_PRIMARY_HOVER = "#234d87"
BTN_DANGER = "#b84800"
BTN_DANGER_HOVER = "#d45500"
TEXT = "#dde2f0"
MUTED = "#606880"
CARD_HOVER_DELETE = "#2a1210"


def app_base_dir():
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent)
    return str(Path(__file__).resolve().parent)


def find_binary(name):
    return shutil.which(name) or name


def ffmpeg_bin():
    for p in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if Path(p).exists():
            return p
    return find_binary("ffmpeg")


def ffprobe_bin():
    for p in ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"]:
        if Path(p).exists():
            return p
    return find_binary("ffprobe")


def run(cmd):
    subprocess.run(cmd, check=True)


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def file_exists_ok(path, min_size=1024):
    return os.path.exists(path) and os.path.getsize(path) >= min_size


def safe_name(name):
    name = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", name)
    name = name.strip("_").strip(".")  # убираем точки тоже
    # Убираем точки в начале и конце
    while name.startswith("."):
        name = name[1:]
    while name.endswith("."):
        name = name[:-1:]
    return name or "roll"


def desktop_dir():
    d = Path.home() / "Desktop"
    return str(d) if d.is_dir() else str(Path.home())

def open_folder(path):
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])

def set_adaptive_window(win, width_ratio=0.72, height_ratio=0.82, min_width=900, min_height=720):
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()

    width = max(min_width, int(screen_width * width_ratio))
    height = max(min_height, int(screen_height * height_ratio))

    x = int((screen_width - width) / 2)
    y = int((screen_height - height) / 2)

    win.geometry(f"{width}x{height}+{x}+{y}")
    win.minsize(min_width, min_height)


# ── Боковое меню: иконки, подсказки, подгонка подписей ─────────────────────
# Все иконки меню — здесь. Если на какой-то системе символ не отрисуется, меняется одна строка.
NAV_ICONS = {
    "voice_gen": "🎤",
    "voice_clone": "👥",
    "downloader": "⬇",
    "compress": "📦",
    "transcribe": "📝",
    "groq_translate": "🌐",
    "new_task": "➕",
    "clear_all": "🗑",
    "update": "🔄",
    "settings": "⚙",
}
NAV_ARROW_OPEN = "▼"
NAV_ARROW_CLOSED = "►"
SIDEBAR_WIDTH = 210
MAIN_MIN_HEIGHT = 640
KEY_DOT_ON = "#4ade80"
KEY_DOT_OFF = "#5b6178"
SETTINGS_PANELS = {"settings", "api", "sync", "cloudinary", "groq"}


def fit_text(font, text, max_px):
    """Подпись, которая помещается в max_px пикселей; иначе обрезка с «…». (текст, обрезан_ли)."""
    if font.measure(text) <= max_px:
        return text, False
    cut = text
    while cut and font.measure(cut + "…") > max_px:
        cut = cut[:-1]
    return cut.rstrip() + "…", True


def balance_style(balance):
    """Текст и цвет баланса ElevenLabs (пороги прежнего бейджа на кнопке API key)."""
    if not balance:
        return "", MUTED
    remaining = balance["remaining"]
    label = f"{remaining // 1000}k" if remaining >= 1000 else str(remaining)
    pct = remaining / max(balance["limit"], 1)
    color = "#86efac" if pct > 0.3 else "#fcd34d" if pct > 0.1 else "#fca5a5"
    return f"{label} симв.", color


def reveal_in_file_manager(path, platform_name=None, popen=None):
    """Показывает файл в Finder / Проводнике. Если файла нет — открывает папку."""
    platform_name = platform_name or sys.platform
    popen = popen or subprocess.Popen
    folder = os.path.dirname(path) or "."
    if not os.path.exists(path):
        open_folder(folder)
        return "folder"
    if platform_name == "darwin":
        popen(["open", "-R", path])
    elif platform_name == "win32":
        # Проводник разбирает /select сам: путь целиком в кавычках, строкой.
        popen(f'explorer /select,"{os.path.normpath(path)}"')
    else:
        open_folder(folder)
        return "folder"
    return "file"


class HoverTip:
    """Подсказка при наведении. text — строка или функция (для меняющегося текста)."""

    def __init__(self, widget, text, delay=500):
        self.widget, self.text, self.delay = widget, text, delay
        self._job = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def current_text(self):
        return self.text() if callable(self.text) else self.text

    def _schedule(self, _event=None):
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self):
        self._job = None
        text = self.current_text()
        if not text or self._tip is not None:
            return
        try:
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(tip, text=text, justify="left", bg="#2a2d3e", fg=TEXT, padx=8, pady=5,
                     wraplength=320, font=("Helvetica", 11)).pack()
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
            tip.wm_geometry(f"+{x}+{y}")
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def get_duration(path):
    result = subprocess.check_output([
        ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], encoding="utf-8", stderr=subprocess.DEVNULL)
    return float(result.strip())


def copy_file(src, dst):
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    import shutil as _sh
    _sh.copy2(src, dst)
    if not file_exists_ok(dst, min_size=100):
        raise RuntimeError(f"Не удалось скопировать файл: {dst}")
    return dst


def parse_start_end_text(full_text):
    """Возвращает (start_text, end_text, separator_count)."""
    separators = re.findall(r"-{3,}", full_text)
    if not separators:
        one = full_text.strip()
        if not one:
            raise RuntimeError("Текст пустой.")
        return one, None, 0
    parts = re.split(r"-{3,}", full_text)
    start_text = parts[0].strip()
    end_text = parts[-1].strip()
    if not start_text:
        raise RuntimeError("Нет текста до первого ------")
    if not end_text:
        raise RuntimeError("Нет текста после последнего ------")
    return start_text, end_text, len(separators)


def eleven_voice_settings(model_id):
    """Eleven v4 — только stability и similarity_boost (style, speed и speaker_boost не отправляем).
    v3 — как раньше, со style и speaker_boost."""
    settings = {"stability": 0.55, "similarity_boost": 1.0}
    if model_id != ELEVEN_TTS_MODEL_ID:
        settings.update({"style": 0.45, "use_speaker_boost": True})
    return settings


def text_to_speech_mp3(text, voice_id, output_mp3, log, model_id=None, language_code=None):
    """
    model_id: str | None — ELEVEN_TTS_MODEL_ID (v4) по умолчанию, либо ELEVEN_TTS_MODEL_ID_V3
    language_code: str | None — ISO 639-1 код (напр. "ru"); None/не передан — автоопределение
    """
    model_id = model_id or ELEVEN_TTS_MODEL_ID
    url = f"{ELEVEN_BASE_URL}/v1/text-to-speech/{voice_id}?output_format={ELEVEN_OUTPUT_FORMAT}"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    data = {
        "text": text,
        "model_id": model_id,
        "voice_settings": eleven_voice_settings(model_id),
    }
    if language_code:
        data["language_code"] = language_code
    chars = len(text)
    log(f"[TIMING] Текст: {chars} символов, модель: {model_id}" +
        (f", язык: {language_code}" if language_code else ""))
    log("Генерирую аудио ElevenLabs...")

    t_api_start = time.time()
    res = requests.post(url, headers=headers, json=data, timeout=300)
    t_api_end = time.time()
    t_api = t_api_end - t_api_start
    log(f"[TIMING] ElevenLabs API (ожидание ответа): {t_api:.2f} сек  (статус {res.status_code})")

    if res.status_code != 200:
        raise RuntimeError(f"ElevenLabs TTS error: {res.status_code}\n{res.text}")

    t_save_start = time.time()
    with open(output_mp3, "wb") as f:
        f.write(res.content)
    t_save_end = time.time()
    log(f"[TIMING] Сохранение файла ({len(res.content) // 1024} KB): {t_save_end - t_save_start:.2f} сек")

    if not file_exists_ok(output_mp3):
        raise RuntimeError(f"Пустой mp3: {output_mp3}")

    t_dur_start = time.time()
    dur = get_duration(output_mp3)
    t_dur_end = time.time()
    log(f"[TIMING] get_duration (ffprobe): {t_dur_end - t_dur_start:.2f} сек")
    log(f"[TIMING] ИТОГО: {t_dur_end - t_api_start:.2f} сек | Аудио готово: {dur:.2f} сек")
    log(f"Аудио сохранено: {output_mp3}")
    return output_mp3


def generate_full_voice_to_desktop(api_key, voice_id, full_text, voice_name, log):
    global ELEVENLABS_API_KEY
    ELEVENLABS_API_KEY = api_key
    eleven_text = full_text.replace("------", '[пауза 3 сек]')
    save_dir = Path(desktop_dir()) / "ElevenLabs_Generated_Voices"
    ensure_dir(save_dir)
    safe_voice = safe_name(voice_name or voice_id)
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    output_mp3 = str(save_dir / f"{safe_voice}_{timestamp}_full_voice.mp3")
    text_to_speech_mp3(eleven_text, voice_id, output_mp3, log)
    return output_mp3


def convert_audio_to_wav(input_audio, output_wav):
    cmd = [ffmpeg_bin(), "-y", "-i", input_audio, "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", output_wav]
    run(cmd)
    if not file_exists_ok(output_wav):
        raise RuntimeError(f"Не удалось создать wav: {output_wav}")
    return output_wav

def convert_audio_to_wav_trimmed(input_audio, output_wav, start_sec="", end_sec=""):
    cmd = [ffmpeg_bin(), "-y", "-i", input_audio]
    if start_sec:
        cmd += ["-ss", str(float(start_sec))]
    if end_sec:
        cmd += ["-to", str(float(end_sec))]
    cmd += ["-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", output_wav]
    run(cmd)
    if not file_exists_ok(output_wav):
        raise RuntimeError(f"Не удалось создать wav: {output_wav}")
    return output_wav


def detect_silences(audio_path, log):
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-i", audio_path,
        "-af", f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_DURATION}",
        "-f", "null", "-"
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8", errors="replace")
    output = proc.stderr or ""
    starts, ends = [], []
    for line in output.splitlines():
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            starts.append(float(m_start.group(1)))
        m_end = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m_end:
            ends.append(float(m_end.group(1)))
    silences = []
    for i in range(min(len(starts), len(ends))):
        if ends[i] > starts[i]:
            silences.append({"start": starts[i], "end": ends[i]})
    log(f"Найдено пауз: {len(silences)}")
    for s in silences[:5]:
        log(f"Пауза: {s['start']:.2f} -> {s['end']:.2f}")
    return silences


def split_start_end_by_silence(full_voice_mp3, output_dir, log, expected_separators=None):
    silences = detect_silences(full_voice_mp3, log)
    if not silences:
        raise RuntimeError(
            "Не нашёл пауз в full_voice.mp3. "
            "Сгенерируй голос с разделителями ------."
        )

    # Отбрасываем хвостовую тишину (если она у самого конца файла)
    total_duration = get_duration(full_voice_mp3)
    silences = [
        s for s in silences
        if s["start"] > 0.5                       # не в начале файла
        and (total_duration - s["end"]) > 0.5     # не в конце файла
    ]
    log(f"После фильтра хвостовой/начальной тишины: {len(silences)} пауз")

    if not silences:
        raise RuntimeError(
            "Не нашёл внутренних пауз в full_voice.mp3. "
            "Возможно ты сгенерил голос без разделителей ------."
        )

    # Сверка с текстом
    if expected_separators is not None and expected_separators > 0:
        if len(silences) < expected_separators:
            raise RuntimeError(
                f"В тексте {expected_separators} разделителей ------, "
                f"а в аудио найдено только {len(silences)} пауз. "
                f"Похоже full_voice.mp3 сгенерирован не из этого текста."
            )
        log(f"Сверка с текстом: ожидалось {expected_separators} пауз, "
            f"в аудио найдено {len(silences)} — ок.")

    first = silences[0]
    last = silences[-1]
    start_cut = max(0.0, first["start"])
    end_cut = last["end"]

    part_start = str(Path(output_dir) / "part_start.wav")
    part_end = str(Path(output_dir) / "part_end.wav")
    run([ffmpeg_bin(), "-y", "-i", full_voice_mp3,
         "-ss", "0", "-to", str(round(start_cut, 3)),
         "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", part_start])
    run([ffmpeg_bin(), "-y", "-i", full_voice_mp3,
         "-ss", str(round(end_cut, 3)),
         "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", part_end])
    if not file_exists_ok(part_start):
        raise RuntimeError("Не удалось создать part_start.wav")
    if not file_exists_ok(part_end):
        raise RuntimeError("Не удалось создать part_end.wav")
    log(f"part_start.wav: {get_duration(part_start):.2f} сек")
    log(f"part_end.wav: {get_duration(part_end):.2f} сек")
    return part_start, part_end


def prepare_video_for_sync(input_video, audio_wav, output_video, target_fps=None):
    """Готовит видео для отправки в Sync без потерь качества.
    target_fps — перекодировать в эту частоту кадров (crf 18, разрешение то же);
    None — как в исходнике, только обрезка без перекодировки."""
    audio_duration = get_duration(audio_wav)
    target_duration = round(audio_duration + END_PADDING, 3)

    if target_fps:
        source_fps = probe_video_frames(input_video)["fps"]
        if abs(source_fps - target_fps) > 0.01:
            run([
                ffmpeg_bin(), "-y",
                "-i", input_video,
                "-t", str(target_duration),
                "-vf", f"fps={target_fps}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                output_video,
            ])
            if not file_exists_ok(output_video):
                raise RuntimeError(f"Не удалось подготовить видео для Sync: {output_video}")
            return output_video

    # Просто обрезаем видео по длине аудио, без перекодировки
    cmd = [
        ffmpeg_bin(), "-y",
        "-i", input_video,
        "-t", str(target_duration),
        "-c", "copy",                  # ← без перекодировки, оригинальное качество
        "-movflags", "+faststart",
        output_video,
    ]
    
    try:
        run(cmd)
    except subprocess.CalledProcessError:
        # Если -c copy не сработал (редкий кодек) — fallback на перекодировку
        cmd = [
            ffmpeg_bin(), "-y",
            "-i", input_video,
            "-t", str(target_duration),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_video,
        ]
        run(cmd)
    
    if not file_exists_ok(output_video):
        raise RuntimeError(f"Не удалось подготовить видео для Sync: {output_video}")
    return output_video

def _init_cloudinary():
    import cloudinary
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
    )

def upload_to_cloudinary(file_path, log):
    import cloudinary.uploader
    import tempfile as _tmpfile
    _init_cloudinary()

    # Исправляем имя файла если начинается с . или _
    file_name = Path(file_path).name
    clean_name = file_name.lstrip("._")
    if not clean_name:
        clean_name = "file" + Path(file_path).suffix

    tmp_path = None
    if file_name != clean_name:
        tmp_path = str(Path(_tmpfile.mkdtemp()) / clean_name)
        import shutil as _shutil
        _shutil.copy2(file_path, tmp_path)
        file_path = tmp_path
        log(f"Переименован для загрузки: {clean_name}")

    try:
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        log(f"Загружаю {Path(file_path).name} ({size_mb:.1f} MB) на Cloudinary...")
        opts = dict(resource_type="video", invalidate=True)
        if size_mb > 90:
            result = cloudinary.uploader.upload_large(
                file_path, **opts, chunk_size=50 * 1024 * 1024)
        else:
            result = cloudinary.uploader.upload(file_path, **opts)
        url = result["secure_url"]
        public_id = result["public_id"]
        log(f"  → {url}")
        return url, public_id
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

def delete_from_cloudinary(public_id, log):
    """Удаляет файл с Cloudinary по public_id."""
    import cloudinary.uploader
    _init_cloudinary()
    try:
        cloudinary.uploader.destroy(public_id, resource_type="video", invalidate=True)
        log(f"Cloudinary: удалён {public_id}")
    except Exception as e:
        log(f"Cloudinary delete error ({public_id}): {e}")

# ── Хранилище Sync (Asset Uploads): presign → PUT → register → assetId ─────
SYNC_ASSETS_UPLOAD_URL = "https://api.sync.so/v2/assets/upload"
SYNC_ASSETS_URL = "https://api.sync.so/v2/assets"
SYNC_API_TIMEOUT_SEC = 30
SYNC_ASSET_PUT_TIMEOUT = (10, 600)          # (соединение, ожидание ответа) для больших файлов
SYNC_ASSET_EXPIRY_MARGIN_SEC = 60
CLOUDINARY_FALLBACK_MAX_BYTES = 100 * 1024 * 1024
SYNC_STORAGE_MODES = {"Sync (рекомендуется)": "sync", "Cloudinary": "cloudinary"}
SYNC_STORAGE_LABELS = {v: k for k, v in SYNC_STORAGE_MODES.items()}
_SYNC_CONTENT_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".aac": "audio/aac",
}


class SyncAssetError(RuntimeError):
    """Файл не дошёл до Sync (запрос ссылки, загрузка или регистрация) — задача ещё не создавалась."""

    def __init__(self, message, stage, limit_exceeded=False, size=None, limit=None):
        super().__init__(message)
        self.stage = stage
        self.limit_exceeded = limit_exceeded
        self.size = size
        self.limit = limit


def _strip_urls(text):
    """Для локального лога в пути загрузки: подписанные ссылки не пишем вообще."""
    return re.sub(r"https?://\S+", "<ссылка>", str(text))


def cloudinary_configured():
    return bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)


def sync_asset_content_type(path):
    return _SYNC_CONTENT_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


class _ProgressFile:
    """Тело PUT, читаемое с диска кусками. __len__ даёт Content-Length (без chunked-передачи,
    её не принимают подписанные ссылки), файл целиком в память не загружается."""

    def __init__(self, path, on_progress):
        self._f = open(path, "rb")
        self._size = os.path.getsize(path)
        self._sent = 0
        self._step = -1
        self._on_progress = on_progress

    def __len__(self):
        return self._size

    def read(self, n=-1):
        chunk = self._f.read(n if n and n > 0 else 1024 * 1024)
        self._sent += len(chunk)
        step = (self._sent * 10 // self._size) if self._size else 10
        if step != self._step:
            self._step = step
            self._on_progress(step * 10)
        return chunk

    def close(self):
        self._f.close()


def _sync_error_detail(res):
    """Код и текст ошибки из ответа Sync или хранилища — без ссылок."""
    code = ""
    m = re.search(r"<Code>([^<]+)</Code>", res.text or "")
    if m:
        code = m.group(1)
    else:
        try:
            data = res.json()
            code = data.get("code") or data.get("error") or ""
            msg = data.get("message") or ""
            return _strip_urls(f"{code} {msg}".strip())[:300]
        except ValueError:
            pass
    return _strip_urls(code or (res.text or "")[:300])


def _is_plan_limit(res):
    return res.status_code == 422 and "file_size_exceeds_plan_limit" in (res.text or "")


def _plan_limit_value(res):
    """Лимит тарифа, если Sync его вернул (поле или размер в тексте сообщения)."""
    try:
        data = res.json()
        for key in ("limit", "maxSize", "max_size", "planLimit", "limitBytes"):
            if data.get(key):
                return str(data[key])
    except ValueError:
        pass
    m = re.search(r"(\d+(?:[.,]\d+)?\s*(?:GiB|GB|MiB|MB|KiB|KB|bytes))", res.text or "", re.I)
    return m.group(1) if m else None


def upload_to_sync_assets(path, kind, api_key, log):
    """Загружает файл в хранилище Sync и регистрирует ассет. kind: 'video' | 'audio'.
    Возвращает asset id. Ключ и подписанные ссылки в лог не пишутся."""
    label = "видео" if kind == "video" else "аудио"
    name = Path(path).name
    size = os.path.getsize(path)
    content_type = sync_asset_content_type(path)
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    log(f"Загружаю {label} в Sync: {size / 1048576:.1f} МБ ({content_type})")

    canonical_url = None
    for presign_try in (1, 2):
        try:
            res = requests.post(SYNC_ASSETS_UPLOAD_URL, headers=headers, timeout=SYNC_API_TIMEOUT_SEC,
                                json={"fileName": name, "contentType": content_type, "size": size})
        except requests.exceptions.RequestException as e:
            raise SyncAssetError(f"запрос ссылки для загрузки {label}: {_strip_urls(e)}", "presign")
        if res.status_code not in (200, 201):
            raise SyncAssetError(f"запрос ссылки для загрузки {label}: HTTP {res.status_code} "
                                 f"{_sync_error_detail(res)}", "presign", limit_exceeded=_is_plan_limit(res),
                                 size=size, limit=_plan_limit_value(res))
        data = res.json()
        upload_url, canonical_url = data["uploadUrl"], data["url"]
        expires_at = time.time() + float(data.get("expiresIn") or 900)

        body = _ProgressFile(path, lambda pct: log(f"Загружаю {label} в Sync: {pct}%"))
        try:
            put = requests.put(upload_url, data=body, headers={"Content-Type": content_type},
                               timeout=SYNC_ASSET_PUT_TIMEOUT)
        except requests.exceptions.RequestException as e:
            raise SyncAssetError(f"загрузка {label}: {_strip_urls(e)}", "upload")
        finally:
            body.close()
        if put.status_code in (200, 201, 204):
            break
        detail = _sync_error_detail(put)
        expired = (time.time() >= expires_at - SYNC_ASSET_EXPIRY_MARGIN_SEC
                   or "expired" in (put.text or "").lower())
        if presign_try == 1 and expired:
            log(f"Ссылка для загрузки {label} истекла — запрашиваю новую")
            continue
        hint = (" — Content-Type загрузки не совпал с заявленным" if "SignatureDoesNotMatch" in detail else "")
        raise SyncAssetError(f"загрузка {label}: HTTP {put.status_code} {detail}{hint}", "upload")

    try:
        reg = requests.post(SYNC_ASSETS_URL, headers=headers, timeout=SYNC_API_TIMEOUT_SEC,
                            json={"url": canonical_url, "type": kind.upper(), "name": name})
    except requests.exceptions.RequestException as e:
        raise SyncAssetError(f"регистрация {label}: {_strip_urls(e)}", "register")
    if reg.status_code not in (200, 201):
        raise SyncAssetError(f"регистрация {label}: HTTP {reg.status_code} {_sync_error_detail(reg)}",
                             "register", limit_exceeded=_is_plan_limit(reg), size=size,
                             limit=_plan_limit_value(reg))
    asset_id = reg.json()["id"]
    log(f"{label.capitalize()} в Sync: asset {asset_id}")
    return asset_id


def delete_sync_asset(asset_id, api_key, log):
    """Удаляет ассет в Sync. Ошибка удаления только пишется в лог."""
    try:
        res = requests.delete(f"{SYNC_ASSETS_URL}/{asset_id}", headers={"x-api-key": api_key},
                              timeout=SYNC_API_TIMEOUT_SEC)
        if res.status_code in (200, 202, 204):
            log(f"Sync: удалён asset {asset_id}")
        else:
            log(f"Sync: не удалось удалить asset {asset_id}: HTTP {res.status_code} {_sync_error_detail(res)}")
    except Exception as e:
        log(f"Sync: не удалось удалить asset {asset_id}: {_strip_urls(e)}")


def sync_asset_limit_message(err, label):
    size_mb = (err.size or 0) / 1048576
    text = f"Файл {label} ({size_mb:.0f} МБ) больше лимита тарифа Sync"
    if err.limit:
        text += f" (лимит: {err.limit})"
    return (text + ". Что делать: обрезать видео или включить «25 fps» в настройках (файл станет меньше), "
            "либо повысить тариф Sync. Через Cloudinary можно отправить файл не больше 100 МБ.")


def upload_inputs_to_sync(video_in, audio_wav, api_key, log):
    """Видео и аудио в хранилище Sync. При ошибке уже загруженный ассет удаляется:
    задачи ещё нет, он никому не нужен."""
    done = []
    try:
        for path, kind in ((video_in, "video"), (audio_wav, "audio")):
            done.append(upload_to_sync_assets(path, kind, api_key, log))
        return {"video": done[0], "audio": done[1]}
    except SyncAssetError:
        for asset_id in done:
            delete_sync_asset(asset_id, api_key, log)
        raise


def log_actual_sync_cost(job_id, output_duration, estimate, log):
    """«оценка $X, факт ≈ $Y» по outputDuration из ответа Sync. Пустое поле — ничего не пишем."""
    try:
        if not output_duration or not estimate:
            return
        actual = estimate_sync_cost(float(output_duration), estimate["fps"], SYNC_API_KEY)
        log(f"Стоимость job {job_id}: оценка ${estimate['cost']:.2f}, факт ≈ ${actual['cost']:.2f} "
            f"(outputDuration {float(output_duration):.2f} с, {format_estimate_source(actual['source'])})")
    except Exception as e:
        log(f"Не удалось посчитать фактическую стоимость job {job_id}: {e}")


class SyncResultDownloadError(RuntimeError):
    """Sync-задача COMPLETED (оплачена), но результат не сохранён локально — пересоздавать её нельзя."""


def apply_lipsync_sync(video_in, audio_wav, final_out, log, options=None, estimate=None,
                       storage="cloudinary", journal_ctx=None):
    """Отправляет видео и аудио в Sync: через хранилище Sync (assetId) или Cloudinary (URL).
    options — поле options запроса (например active_speaker_detection), одинаковое на каждой попытке.
    estimate — оценка стоимости этой части (cost, fps) для сверки с фактом после COMPLETED.
    storage — "sync" | "cloudinary"; при ошибке загрузки в Sync возможен откат на Cloudinary.
    journal_ctx — что добавить в каждую запись журнала Sync (ролик, часть, итоговый файл, партия)."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    # Все варианты сетевых обрывов — включая RemoteDisconnected, BrokenPipe,
    # ConnectionReset и timeout (requests.exceptions.ConnectionError покрывает их всех).
    _NET_ERRORS = (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        ConnectionResetError,
        BrokenPipeError,
    )

    def _make_session():
        """Новая изолированная сессия с retry-адаптером на транспортном уровне."""
        s = requests.Session()
        retry = Retry(
            total=3, backoff_factor=1, connect=3, read=3,
            status_forcelist=[502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        return s

    headers = {"x-api-key": SYNC_API_KEY, "Content-Type": "application/json"}

    def journal(event, **fields):
        journal_sync_job(event, **dict(journal_ctx or {}, **fields))

    # Пары Cloudinary-файлов по каждой попытке. "safe" ставится True только когда
    # исход job'а на стороне Sync подтверждён (COMPLETED/FAILED/REJECTED, либо
    # точно подтверждённое "job не создан") — удаляем в самом конце функции
    # только такие пары, чтобы не выбить файл из-под ещё живой задачи.
    uploaded_pairs = []

    def _find_existing_job(video_ref, audio_ref):
        """Ambiguous-ошибка на create (timeout/connection error) — не значит, что
        job не создался на стороне Sync. Проверяем список активных generations
        по /v2/generations, прежде чем считать попытку проваленной.
        video_ref/audio_ref — URL (Cloudinary) или assetId (хранилище Sync).
        Возвращает (job_id_или_None, confirmed) — confirmed=True значит исход
        точно установлен (job найден, либо точно не найден)."""
        vs = _make_session()
        try:
            resp = vs.get(
                SYNC_LIST_URL,
                headers=headers,
                params={"status": "PENDING,PROCESSING"},
                timeout=30,
            )
            if resp.status_code != 200:
                log(f"Verification: /v2/generations вернул {resp.status_code} — не удалось подтвердить исход")
                return None, False
            for gen in resp.json():
                refs = set()
                for i in gen.get("input", []):
                    if isinstance(i, dict):
                        refs.update(r for r in (i.get("url"), i.get("assetId")) if r)
                if video_ref in refs and audio_ref in refs:
                    log(f"Verification: найден уже созданный job {gen.get('id')} для этой загрузки")
                    return gen.get("id"), True
            log("Verification: подходящего job'а не найдено — create действительно не удался")
            return None, True
        except Exception as e:
            log(f"Verification: ошибка при проверке /v2/generations: {e} — не удалось подтвердить исход")
            return None, False
        finally:
            vs.close()

    def _cleanup_confirmed():
        orphaned = []
        for p in uploaded_pairs:
            if p["safe"]:
                for pub_id in (p["video"], p["audio"]):
                    if pub_id:
                        delete_from_cloudinary(pub_id, log)
            else:
                orphaned.extend(pid for pid in (p["video"], p["audio"]) if pid)
        if orphaned:
            log(
                "⚠️ Не удалены с Cloudinary (исход job'а не подтверждён, оставлены "
                f"во избежание обрыва живой задачи): {', '.join(orphaned)}"
            )
        # Ассеты Sync общие для всех попыток: удаляем, только если исход каждой подтверждён.
        if sync_assets and not sync_assets.get("deleted"):
            if all(p["safe"] for p in uploaded_pairs):
                for kind in ("video", "audio"):
                    delete_sync_asset(sync_assets[kind], SYNC_API_KEY, log)
                sync_assets["deleted"] = True
            else:
                log("⚠️ Ассеты Sync не удалены (исход задачи не подтверждён, оставлены во избежание "
                    f"обрыва живой задачи): {sync_assets['video']}, {sync_assets['audio']}")

    def _replay_create(payload, create_headers):
        """Тот же запрос с тем же Idempotency-Key: Sync вернёт уже созданную задачу (200)
        или создаст её один раз. None — так и не удалось получить ответ."""
        for i in range(1, 6):
            rs = _make_session()
            try:
                r = rs.post(SYNC_GENERATE_URL, headers=create_headers, json=payload,
                            timeout=SYNC_CREATE_TIMEOUT_SEC)
            except _NET_ERRORS as e:
                log(f"Повтор {i}/5 с тем же Idempotency-Key не дошёл: {e}")
                time.sleep(5)
                continue
            finally:
                rs.close()
            if r.status_code == 409 and "IDEMPOTENCY_IN_PROGRESS" in (r.text or ""):
                wait = float(r.headers.get("Retry-After") or 2)
                log(f"Sync ещё обрабатывает первый запрос — жду {wait:g} с")
                time.sleep(wait)
                continue
            return r
        return None

    # ── Доставка файлов в Sync ──────────────────────────────────────────
    sync_assets = None
    if storage == "sync":
        try:
            sync_assets = upload_inputs_to_sync(video_in, audio_wav, SYNC_API_KEY, log)
        except SyncAssetError as e:
            label = "видео" if "видео" in str(e) else "аудио"
            small_enough = (e.size or 0) <= CLOUDINARY_FALLBACK_MAX_BYTES
            can_fallback = cloudinary_configured() and (not e.limit_exceeded or small_enough)
            if e.limit_exceeded and not can_fallback:
                message = f"До создания задачи (загрузка в Sync): {sync_asset_limit_message(e, label)}"
                log(message)
                raise SyncAssetError(message, e.stage, True, e.size, e.limit) from None
            if not can_fallback:
                message = (f"До создания задачи (загрузка в Sync): {e}. Cloudinary не настроен — "
                           "откатиться некуда. Проверь Sync-ключ и интернет или настрой Cloudinary.")
                log(message)
                error_reporter.report("app.upload_to_sync_assets", exc_type="SyncAssetError", message=str(e))
                raise SyncAssetError(message, e.stage) from None
            log(f"До создания задачи (загрузка в Sync): {e} — откатываюсь на Cloudinary")
            storage = "cloudinary"

    last_error = None
    last_job_id = None
    last_error_code = None
    # Одни и те же options на каждой попытке: sync_mode всегда, плюс выбор лица, если задан.
    request_options = {"sync_mode": SYNC_SYNC_MODE, **(options or {})}

    for attempt in range(1, SYNC_RETRIES + 1):
        session = _make_session()
        pair = {"video": None, "audio": None, "safe": False}
        uploaded_pairs.append(pair)
        try:
            log(f"Отправляю в Sync... попытка {attempt}/{SYNC_RETRIES}")

            if sync_assets:
                # Ассеты загружены один раз до цикла — повторы их переиспользуют.
                video_ref, audio_ref = sync_assets["video"], sync_assets["audio"]
                inputs = [{"type": "video", "assetId": video_ref}, {"type": "audio", "assetId": audio_ref}]
            else:
                # Cloudinary → URL + public_id для удаления
                video_ref, pair["video"] = upload_to_cloudinary(video_in, log)
                audio_ref, pair["audio"] = upload_to_cloudinary(audio_wav, log)
                inputs = [{"type": "video", "url": video_ref}, {"type": "audio", "url": audio_ref}]

            payload = {"model": SYNC_MODEL, "input": inputs, "options": request_options}

            # Ключ = одно намерение «создать задачу в этой попытке». При обрыве повторяем
            # тот же запрос с тем же ключом — Sync не создаст платный дубль. Новая попытка
            # после подтверждённого провала получает новый ключ.
            idempotency_key = f"sheqel-{uuid.uuid4()}"
            create_headers = dict(headers, **{"Idempotency-Key": idempotency_key})
            log(f"Создаю задачу Sync (Idempotency-Key {idempotency_key})")
            job_id = None
            try:
                res = session.post(
                    SYNC_GENERATE_URL,
                    headers=create_headers,
                    json=payload,
                    timeout=SYNC_CREATE_TIMEOUT_SEC,
                )
            except _NET_ERRORS as e:
                # Неопределённая ошибка: неизвестно, создался ли job на стороне Sync.
                log(f"Ambiguous-ошибка при создании job'а: {e} — повторяю тот же запрос "
                    "с тем же Idempotency-Key")
                res = _replay_create(payload, create_headers)
                if res is None:
                    log("Повтор с ключом не дал ответа — проверяю через /v2/generations")
                    found_id, confirmed = _find_existing_job(video_ref, audio_ref)
                    if found_id:
                        job_id = found_id
                        log(f"Job подтверждён (id={job_id}) несмотря на ошибку create-запроса — "
                            "продолжаю без повторной загрузки")
                    else:
                        if confirmed:
                            pair["safe"] = True
                        raise
            if job_id is None:
                log(f"Sync create response: {res.status_code}")
                log(res.text[:1500])
                if res.status_code not in [200, 201]:
                    pair["safe"] = True
                    raise RuntimeError(f"Sync create error: {res.status_code}\n{res.text}")
                job_id = res.json().get("id")
                if not job_id:
                    pair["safe"] = True
                    raise RuntimeError(f"Sync не вернул job id: {res.text}")
                if str(res.headers.get("Idempotency-Replayed", "")).lower() == "true":
                    log(f"Задача уже была создана первым запросом — продолжаю с ней (без дубля)")
                log(f"Sync job: {job_id}")

            last_job_id = job_id
            journal("created", job_id=job_id, idempotency_key=idempotency_key,
                    video=Path(video_in).name, output_path=final_out,
                    storage="sync" if sync_assets else "cloudinary",
                    assets=({"video": sync_assets["video"], "audio": sync_assets["audio"]}
                            if sync_assets else None),
                    cloudinary_ids=[i for i in (pair["video"], pair["audio"]) if i])
            status_url = f"{SYNC_GENERATE_URL}/{job_id}"
            started = time.time()
            last_state = None
            while True:
                if time.time() - started > SYNC_TIMEOUT_SEC:
                    raise RuntimeError("Sync timeout.")
                try:
                    status_res = session.get(status_url, headers=headers, timeout=30)
                except _NET_ERRORS as e:
                    log(f"Сетевая ошибка при polling статуса: {e} — пересоздаю сессию")
                    session = _make_session()
                    time.sleep(5)
                    continue
                if status_res.status_code != 200:
                    # Транзитная ошибка статус-эндпоинта (502/429 и т.п.) не значит,
                    # что сама задача умерла — толерантно повторяем опрос, как с _NET_ERRORS.
                    log(f"Sync status error: {status_res.status_code} — повторяю опрос")
                    recovered = False
                    for retry_i in range(1, 4):
                        time.sleep(5)
                        try:
                            status_res = session.get(status_url, headers=headers, timeout=30)
                        except _NET_ERRORS as e:
                            log(f"Сетевая ошибка при повторном опросе статуса: {e} — пересоздаю сессию")
                            session = _make_session()
                            continue
                        if status_res.status_code == 200:
                            recovered = True
                            break
                        log(f"Повтор опроса {retry_i}/3: статус-эндпоинт снова вернул {status_res.status_code}")
                    if not recovered:
                        # Исход job'а так и не подтверждён — не помечаем pair как safe.
                        raise RuntimeError(f"Sync status error после повторов: {status_res.status_code}\n{status_res.text}")
                status = status_res.json()
                state = status.get("status")
                if state != last_state:
                    log(f"Sync status: {state}")
                    last_state = state
                if state == "COMPLETED":
                    # Задача оплачена и готова: любая ошибка дальше — только повтор
                    # скачивания этого же job_id, без новой загрузки и новой задачи.
                    pair["safe"] = True
                    video_url_out = status.get("outputUrl") or status.get("output_url")
                    log(f"Sync job {job_id} COMPLETED, outputUrl: {video_url_out}")
                    log_actual_sync_cost(job_id, status.get("outputDuration"), estimate, log)
                    journal("completed", job_id=job_id, output_url=video_url_out,
                            output_duration=status.get("outputDuration"))
                    if not video_url_out:
                        _cleanup_confirmed()
                        raise SyncResultDownloadError(
                            f"Sync job {job_id} COMPLETED, но outputUrl пустой: {status}")
                    last_dl_error = None
                    for dl_attempt in range(1, 4):
                        try:
                            dl_session = _make_session()
                            try:
                                dl_res = dl_session.get(video_url_out, timeout=300)
                                dl_res.raise_for_status()
                                video_data = dl_res.content
                            finally:
                                dl_session.close()
                            # Папку пересоздаём на случай, если её удалили во время обработки.
                            ensure_dir(Path(final_out).parent)
                            with open(final_out, "wb") as f:
                                f.write(video_data)
                            if not file_exists_ok(final_out):
                                raise RuntimeError(f"Sync скачал пустое видео: {final_out}")
                            log(f"Lipsync готов: {final_out}")
                            journal("saved", job_id=job_id, path=final_out)
                            for p in uploaded_pairs:
                                p["safe"] = True
                            _cleanup_confirmed()
                            return final_out
                        except Exception as e:
                            last_dl_error = e
                            log(f"Ошибка скачивания/записи результата job {job_id} "
                                f"(попытка {dl_attempt}/3): {e}")
                            if dl_attempt < 3:
                                time.sleep(5)
                    _cleanup_confirmed()
                    journal("download_failed", job_id=job_id, output_url=video_url_out,
                            error=str(last_dl_error))
                    raise SyncResultDownloadError(
                        f"Sync job {job_id} COMPLETED, но результат не удалось сохранить: "
                        f"{last_dl_error}\nЗабрать вручную: {video_url_out}")
                if state in ["FAILED", "REJECTED"]:
                    # Терминальный статус от Sync — job точно больше не тронет входные файлы.
                    pair["safe"] = True
                    last_error_code = status.get("errorCode") or status.get("error_code")
                    journal("failed", job_id=job_id, error_code=last_error_code,
                            error=str(status.get("error") or "")[:300])
                    raise RuntimeError(f"Sync failed: {status}")
                time.sleep(SYNC_POLL_INTERVAL_SEC)
        except SyncResultDownloadError as e:
            log(str(e))
            error_reporter.report("app.apply_lipsync_sync", exc=e, job_id=last_job_id)
            raise
        except Exception as e:
            last_error = e
            log(f"Sync ошибка на попытке {attempt}: {e}")
            if attempt < SYNC_RETRIES:
                log("Пауза 60 секунд перед повтором...")
                time.sleep(60)
        finally:
            session.close()

    # Удаляем с Cloudinary только те пары, чей исход подтверждён.
    # Файлы с неопределённым исходом (ambiguous timeout/connection error без
    # подтверждения, исчерпанные повторы статус-эндпоинта, общий таймаут) —
    # намеренно НЕ удаляются, чтобы не оборвать возможно ещё живую задачу.
    _cleanup_confirmed()
    stage = (f"после создания задачи {last_job_id} (генерация Sync)" if last_job_id
             else "до создания задачи (загрузка, регистрация или параметры)")
    error_reporter.report(
        "app.apply_lipsync_sync",
        exc_type=type(last_error).__name__ if last_error else "RuntimeError",
        message=f"Sync не сработал после {SYNC_RETRIES} попыток, {stage}: {last_error}"
                + (f" [errorCode={last_error_code}]" if last_error_code else ""),
        job_id=last_job_id,
    )
    raise RuntimeError(f"Sync не сработал после {SYNC_RETRIES} попыток, {stage}: {last_error}")


def enhance_video(input_video, output_video):
    vf = f"{SHARPEN_FILTER},format=yuv420p,setpts=PTS-STARTPTS"
    cmd = [
        ffmpeg_bin(), "-y", "-i", input_video,
        "-vf", vf, "-af", "aresample=44100,asetpts=PTS-STARTPTS",
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-movflags", "+faststart", output_video,
    ]
    run(cmd)
    if not file_exists_ok(output_video):
        raise RuntimeError(f"Не создан финал: {output_video}")
    return output_video


# ── Сжатие видео (локально через ffmpeg) ───────────────────────────────────
COMPRESS_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".3gp",
                       ".wmv", ".flv", ".mpg", ".mpeg"}
COMPRESS_SUFFIX = "_compressed"
COMPRESS_DIR_NAME = "compressed"
COMPRESS_TMP_SUFFIX = ".compressing.mp4"
COMPRESS_PRESETS = {
    "Под лимит (для Cloudinary)": ("target", None),
    "Высокое качество (CRF 20)": ("crf", 20),
    "Баланс (CRF 23)": ("crf", 23),
    "Маленький (CRF 27)": ("crf", 27),
    "Свой размер, МБ": ("target", None),
}
COMPRESS_LIMIT_PRESET = "Под лимит (для Cloudinary)"
COMPRESS_DEFAULT_PRESET = "Высокое качество (CRF 20)"
COMPRESS_PRESET_ALIASES = {"Под лимит": COMPRESS_LIMIT_PRESET}   # имена из прошлых версий
COMPRESS_PRESET_HINT = ("Sync принимает большие файлы (до 5 ГБ с учётом тарифа; проверено 521 МБ), сжимать для "
                        "него обычно не нужно. «Под лимит» нужен для Cloudinary — он берёт файлы до 100 МБ.")
COMPRESS_CFR_VALUES = ("30", "25")
COMPRESS_DEFAULT_LIMIT_MB = 95
COMPRESS_AAC_KBPS = 128
COMPRESS_CONTAINER_OVERHEAD = 0.015   # доля размера на контейнер mp4
COMPRESS_LIMIT_MARGIN = 0.04          # запас «под лимит»: целимся ниже лимита
COMPRESS_MIN_VIDEO_KBPS = 150
COMPRESS_DURATION_TOLERANCE = 0.5
COMPRESS_X264_PRESET = "medium"
COMPRESS_X265_CRF_OFFSET = 5          # CRF 28 у x265 ≈ CRF 23 у x264
COMPRESS_MP4_AUDIO_COPY = {"aac", "mp3", "alac", "ac3", "eac3"}
MB = 1024 * 1024

# Аппаратные кодировщики по платформам. Ветка Windows изолирована здесь и в _compress_popen_kwargs.
HW_ENCODERS = {
    "darwin": {"h264": ["h264_videotoolbox"], "hevc": ["hevc_videotoolbox"]},
    "win32": {"h264": ["h264_nvenc", "h264_qsv", "h264_amf"],
              "hevc": ["hevc_nvenc", "hevc_qsv", "hevc_amf"]},
}
SOFTWARE_ENCODERS = {"h264": "libx264", "hevc": "libx265"}
COMPRESS_FAST_HINT = ("Аппаратный кодировщик в 3–10 раз быстрее, но при том же размере качество ниже, "
                      "а попадание в лимит менее точное. Для лучшего качества оставь галочку выключенной.")
TONEMAP_FILTER_NAMES = ("zscale", "libplacebo", "tonemap", "tonemapx", "tonemap_videotoolbox",
                        "tonemap_opencl", "tonemap_vaapi", "scale_vt", "colorspace")


class CompressCancelled(Exception):
    """Пользователь нажал «Отмена»."""


def _compress_popen_kwargs():
    """На Windows ffmpeg из GUI-приложения не должен открывать окно консоли."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def _parse_rate(text):
    try:
        num, _, den = str(text).partition("/")
        value = float(num) / float(den or 1)
        return value if value > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def probe_video_info(path):
    """Параметры файла через ffprobe. Ширина и высота — как видит зритель (с учётом поворота)."""
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        **_compress_popen_kwargs())
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe не прочитал файл: {out.stderr.strip()[-300:]}")
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise RuntimeError("в файле нет видеодорожки")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    rotation = 0
    for sd in video.get("side_data_list") or []:
        if "rotation" in sd:
            rotation = int(round(float(sd["rotation"])))
    if not rotation and (video.get("tags") or {}).get("rotate"):
        rotation = -int(float(video["tags"]["rotate"]))
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if abs(rotation) % 180 == 90:
        width, height = height, width
    pix_fmt = video.get("pix_fmt") or ""
    bits = int(video.get("bits_per_raw_sample") or 0) or (
        12 if "12" in pix_fmt else 10 if ("10" in pix_fmt or pix_fmt in ("p010le", "p010be")) else 8)
    transfer = video.get("color_transfer") or ""
    hdr = {"smpte2084": "PQ", "arib-std-b67": "HLG"}.get(transfer)
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    avg, real = _parse_rate(video.get("avg_frame_rate")), _parse_rate(video.get("r_frame_rate"))
    fps = avg or real or 0.0
    audio_kbps = None
    if audio is not None:
        try:
            audio_kbps = int(audio.get("bit_rate") or 0) / 1000 or None
        except ValueError:
            audio_kbps = None
    return {
        "path": str(path), "size": os.path.getsize(path), "duration": duration,
        "width": width, "height": height, "rotation": rotation,
        "fps": round(fps, 3), "vfr": bool(avg and real and abs(avg - real) > 0.5),
        "codec": video.get("codec_name") or "", "pix_fmt": pix_fmt, "bits": bits,
        "hdr": hdr, "color_transfer": transfer, "color_primaries": video.get("color_primaries") or "",
        "color_space": video.get("color_space") or "",
        "has_audio": audio is not None, "audio_codec": (audio or {}).get("codec_name") or "",
        "audio_kbps": audio_kbps, "bitrate_kbps": int(fmt.get("bit_rate") or 0) / 1000 or None,
    }


def describe_video_info(info):
    minutes, seconds = divmod(int(round(info["duration"])), 60)
    parts = [f"{info['size'] / MB:.0f} МБ", f"{minutes}:{seconds:02d}", f"{info['width']}×{info['height']}",
             f"{info['fps']:g} fps" + (" (переменный)" if info.get("vfr") else ""),
             {"h264": "H.264", "hevc": "HEVC"}.get(info["codec"], info["codec"].upper())
             + (f" {info['bits']}-bit" if info["bits"] > 8 else "")]
    if info.get("hdr"):
        parts.append(f"HDR ({info['hdr']})")
    if info.get("rotation"):
        parts.append(f"поворот {info['rotation']}°")
    parts.append("без звука" if not info["has_audio"] else f"звук {info['audio_codec']}")
    return " · ".join(parts)


_encoder_cache = {}
_encoder_cache_lock = threading.Lock()


def parse_ffmpeg_encoders(text):
    """Имена видеокодировщиков из вывода `ffmpeg -encoders`."""
    return {m.group(1) for m in re.finditer(r"(?m)^\s*V[.A-Z]{5}\s+(\S+)", text or "")}


def available_video_encoders():
    with _encoder_cache_lock:
        if "list" not in _encoder_cache:
            try:
                out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True,
                                     text=True, timeout=30, **_compress_popen_kwargs()).stdout
            except Exception:
                out = ""
            _encoder_cache["list"] = parse_ffmpeg_encoders(out)
        return set(_encoder_cache["list"])


def encoder_works(name):
    """Кодировщик может быть в списке, но без нужного железа (NVENC без NVIDIA). Пробное кодирование."""
    with _encoder_cache_lock:
        if name in _encoder_cache:
            return _encoder_cache[name]
    try:
        res = subprocess.run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-f", "lavfi",
                              "-i", "color=black:s=256x256:r=25:d=0.4", "-c:v", name, "-f", "null", "-"],
                             capture_output=True, timeout=30, **_compress_popen_kwargs())
        ok = res.returncode == 0
    except Exception:
        ok = False
    with _encoder_cache_lock:
        _encoder_cache[name] = ok
    return ok


def pick_compress_encoder(codec, fast, platform_name=None, encoders=None, works=None):
    """По умолчанию программный (x264 / x265). Аппаратный — только по галочке «Быстрее»
    и только если он реально работает; иначе тоже программный."""
    platform_name = platform_name or sys.platform
    encoders = available_video_encoders() if encoders is None else encoders
    works = works or encoder_works
    if fast:
        for name in HW_ENCODERS.get(platform_name, {}).get(codec, []):
            if name in encoders and works(name):
                return name
    return SOFTWARE_ENCODERS[codec]


def is_hw_encoder(name):
    return name not in SOFTWARE_ENCODERS.values()


def ffmpeg_filter_names():
    with _encoder_cache_lock:
        if "filters" not in _encoder_cache:
            try:
                out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True,
                                     text=True, timeout=30, **_compress_popen_kwargs()).stdout
            except Exception:
                out = ""
            _encoder_cache["filters"] = {m.group(1) for m in re.finditer(r"(?m)^\s*[.A-Z|]{2,3}\s+(\S+)\s", out)}
        return set(_encoder_cache["filters"])


def tonemap_filters_present():
    names = ffmpeg_filter_names()
    return [n for n in TONEMAP_FILTER_NAMES if n in names]


SWSCALE_SDR_FILTER = ("scale=out_transfer=bt709:out_primaries=bt709:out_color_matrix=bt709:out_range=tv")
ZSCALE_SDR_FILTER = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                     "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")


def _swscale_converts_transfer():
    """Белый SDR после перевода в PQ должен стать ~203 нит (10-бит код ≈ 573), а не остаться 940.
    Так отличаем настоящий перевод (ffmpeg 8) от простой смены меток."""
    try:
        res = subprocess.run(
            [ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=white:s=64x64:d=0.04,format=yuv444p",
             "-vf", "scale=in_transfer=bt709:in_primaries=bt709:in_color_matrix=bt709:in_range=tv:"
                    "out_transfer=smpte2084:out_primaries=bt2020:out_color_matrix=bt2020nc:out_range=tv,"
                    "format=yuv444p16le",
             "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, timeout=30, **_compress_popen_kwargs())
        data = res.stdout
        if res.returncode != 0 or len(data) < 64 * 64 * 2:
            return False
        code = int.from_bytes(data[64 * 32 * 2:64 * 32 * 2 + 2], "little") >> 6
        return 500 <= code <= 650
    except Exception:
        return False


def ffmpeg_version_info():
    """(строка версии, (major, minor) или None). Сборки из git (gyan.dev «2025-…-git») без номера — None."""
    with _encoder_cache_lock:
        if "version" in _encoder_cache:
            return _encoder_cache["version"]
    try:
        first = subprocess.run([ffmpeg_bin(), "-hide_banner", "-version"], capture_output=True, text=True,
                               timeout=30, **_compress_popen_kwargs()).stdout.splitlines()[0]
    except Exception:
        first = ""
    m = re.search(r"version\s+(\S+)", first)
    text = m.group(1) if m else "не определена"
    num = re.match(r"n?(\d+)\.(\d+)", text)
    value = (text, (int(num.group(1)), int(num.group(2))) if num else None)
    with _encoder_cache_lock:
        _encoder_cache["version"] = value
    return value


def swscale_tonemap_available():
    with _encoder_cache_lock:
        if "swscale_hdr" in _encoder_cache:
            return _encoder_cache["swscale_hdr"]
    ok = _swscale_converts_transfer()
    with _encoder_cache_lock:
        _encoder_cache["swscale_hdr"] = ok
    return ok


def ffmpeg_upgrade_hint(version, swscale_ok, method):
    """Подсказка, если ffmpeg ниже 8 или нет swscale-тонирования. None — всё хорошо."""
    text, num = version
    if swscale_ok and (num is None or num >= (8, 0)):
        return None
    hint = (f"ffmpeg {text}: для перевода HDR в SDR нужен ffmpeg 8 или новее. Обновить: на Mac — "
            "brew upgrade ffmpeg; на Windows — winget upgrade ffmpeg или сборка «release full» с gyan.dev. ")
    if method:
        hint += f"Сейчас HDR переводится через {method[0]}."
    else:
        hint += "Без обновления HDR-видео будут сохранены как 10-bit HEVC."
    return hint


def hdr_to_sdr_method():
    """(название, цепочка фильтров) рабочего перевода HDR → SDR или None."""
    with _encoder_cache_lock:
        if "sdr" in _encoder_cache:
            return _encoder_cache["sdr"]
    names = ffmpeg_filter_names()
    method = None
    if "zscale" in names and "tonemap" in names:
        method = ("zscale + tonemap (hable)", ZSCALE_SDR_FILTER)
    elif swscale_tonemap_available():
        method = ("swscale ffmpeg", SWSCALE_SDR_FILTER)
    with _encoder_cache_lock:
        _encoder_cache["sdr"] = method
    return method


def default_compress_settings():
    return {"preset": COMPRESS_DEFAULT_PRESET, "target_mb": COMPRESS_DEFAULT_LIMIT_MB, "resolution": "source",
            "fps": "source", "audio": "copy", "codec": "h264", "fast": False, "hdr_mode": "sdr",
            "out_dir": "", "cfr": None, "cfr_fps": "30"}


def normalize_compress_settings(settings):
    """Настройки из state.json прошлых версий: старые имена пресетов, недостающие поля."""
    s = dict(default_compress_settings(), **(settings or {}))
    s["preset"] = COMPRESS_PRESET_ALIASES.get(s["preset"], s["preset"])
    if s["preset"] not in COMPRESS_PRESETS:
        s["preset"] = COMPRESS_DEFAULT_PRESET
    if str(s.get("cfr_fps")) not in COMPRESS_CFR_VALUES:
        s["cfr_fps"] = "30"
    return s


def compress_target_kbps(info, target_mb, audio_kbps, strict):
    """Битрейт видео под размер: из объёма вычитаем аудио, контейнер и (для лимита) запас."""
    duration = max(info["duration"], 0.1)
    share = 1 - COMPRESS_CONTAINER_OVERHEAD - (COMPRESS_LIMIT_MARGIN if strict else 0)
    total_bits = target_mb * MB * 8 * share
    audio_bits = (audio_kbps or 0) * 1000 * duration
    return (total_bits - audio_bits) / duration / 1000


def compress_plan(info, settings, encoders=None, works=None, platform_name=None, sdr_method="auto"):
    """Что и как кодировать для файла: кодировщик, фильтры, звук, битрейт или качество, предупреждения."""
    settings = normalize_compress_settings(settings)
    kind, crf = COMPRESS_PRESETS[settings["preset"]]
    codec = settings.get("codec", "h264")
    warnings = []
    ten_bit = False
    filters = []
    if info.get("hdr"):
        method = hdr_to_sdr_method() if sdr_method == "auto" else sdr_method
        if settings.get("hdr_mode") == "sdr" and method:
            filters.append(method[1])
            warnings.append(f"HDR ({info['hdr']}) будет переведён в SDR ({method[0]}): "
                            "цвета и яркие участки немного изменятся.")
        else:
            codec, ten_bit = "hevc", True
            if settings.get("hdr_mode") == "sdr" and not method:
                warnings.append("В этом ffmpeg нет перевода HDR в SDR без потери цвета: "
                                "сохраняю HDR в 10-bit HEVC. В 8 бит без перевода цвета стали бы блёклыми.")
            else:
                warnings.append("HDR сохраняется: 10-bit HEVC с цветовыми метками.")
    encoder = pick_compress_encoder(codec, settings.get("fast"), platform_name, encoders, works)
    if info["codec"] == "hevc" and codec == "h264":
        warnings.append("Исходник уже HEVC: в H.264 файл может не уменьшиться.")

    res = settings.get("resolution", "source")
    out_w, out_h = info["width"], info["height"]
    if res != "source":
        short = int(res)
        if min(out_w, out_h) > short:
            if out_w >= out_h:
                filters.append(f"scale=-2:{short}")
                out_w, out_h = int(round(out_w * short / out_h / 2) * 2), short
            else:
                filters.append(f"scale={short}:-2")
                out_w, out_h = short, int(round(out_h * short / out_w / 2) * 2)
    out_fps = info["fps"]
    fps_opt = settings.get("fps", "source")
    target_fps = None
    if fps_opt != "source" and info["fps"] and info["fps"] > float(fps_opt) + 0.5:
        target_fps = fps_opt
    # Переменный fps → постоянный: фильтр fps дублирует или пропускает кадры по меткам времени,
    # поэтому длительность и синхронизация со звуком сохраняются. cfr=None — «включено для VFR».
    to_cfr = bool(info.get("vfr")) and settings.get("cfr") is not False
    if to_cfr and target_fps is None:
        target_fps = fps_opt if fps_opt != "source" else str(settings.get("cfr_fps") or "30")
    if to_cfr:
        warnings.append(f"Переменный fps ({info['fps']:g} в среднем) будет приведён к постоянному {target_fps}.")
    if target_fps:
        filters.append(f"fps={target_fps}")
        out_fps = float(target_fps)
    hw = is_hw_encoder(encoder)
    if ten_bit:
        filters.append("format=p010le" if hw else "format=yuv420p10le")
    else:
        filters.append("format=yuv420p")

    audio_mode = settings.get("audio", "copy")
    audio_kbps = 0
    audio_args = []
    if info["has_audio"]:
        if audio_mode == "copy" and info["audio_codec"] in COMPRESS_MP4_AUDIO_COPY:
            audio_args = ["-c:a", "copy"]
            audio_kbps = info.get("audio_kbps") or COMPRESS_AAC_KBPS
        else:
            if audio_mode == "copy":
                warnings.append(f"Звук {info['audio_codec']} нельзя положить в MP4 как есть — будет AAC 128k.")
            audio_args = ["-c:a", "aac", "-b:a", f"{COMPRESS_AAC_KBPS}k"]
            audio_kbps = COMPRESS_AAC_KBPS

    plan = {"kind": kind, "crf": crf, "codec": codec, "encoder": encoder, "hw": hw, "ten_bit": ten_bit,
            "filters": filters, "audio_args": audio_args, "audio_kbps": audio_kbps,
            "out_w": out_w, "out_h": out_h, "out_fps": out_fps, "hdr_out": info.get("hdr") if ten_bit else None,
            "color_transfer": info.get("color_transfer"), "warnings": warnings, "video_kbps": None,
            "cfr": bool(target_fps),
            "target_mb": None, "strict": False}
    if kind == "target":
        target_mb = float(settings.get("target_mb") or COMPRESS_DEFAULT_LIMIT_MB)
        strict = settings["preset"] == COMPRESS_LIMIT_PRESET
        kbps = compress_target_kbps(info, target_mb, audio_kbps, strict)
        # Битрейт выше исходного не нужен: файл только вырос бы. Тогда кодируем с битрейтом исходника.
        source_kbps = (info.get("bitrate_kbps") or info["size"] * 8 / max(info["duration"], 0.1) / 1000)
        source_video_kbps = max(source_kbps - (info.get("audio_kbps") or 0), COMPRESS_MIN_VIDEO_KBPS)
        capped = kbps > source_video_kbps
        if capped:
            kbps = source_video_kbps * 0.9
        plan.update(video_kbps=kbps, target_mb=target_mb, strict=strict, capped=capped)
        if kbps < COMPRESS_MIN_VIDEO_KBPS:
            plan["error"] = (f"{target_mb:g} МБ слишком мало для {info['duration'] / 60:.1f} мин: "
                             f"на видео остаётся {max(kbps, 0):.0f} кбит/с. Увеличь размер или обрежь видео.")
        else:
            bpp = kbps * 1000 / max(out_w * out_h * (out_fps or 25), 1)
            if bpp < 0.03:
                warnings.append(f"На видео {kbps:.0f} кбит/с — качество будет низким. "
                                "Попробуй 720p или больший размер.")
        if info["size"] <= target_mb * MB:
            warnings.append(f"Файл уже меньше {target_mb:g} МБ — сжатие может быть не нужно.")
    return plan


def sync_compat_warnings(info, plan):
    """Что из результата Sync не принимает или не гарантирует (docs: media formats support)."""
    out = []
    if plan["codec"] == "hevc":
        out.append("Sync: H.265 даёт до 15% потерь качества, лучший результат — H.264.")
    if plan["ten_bit"] or plan.get("hdr_out"):
        out.append("Sync: HDR поддерживается не полностью — Sync сам сведёт его к SDR; "
                   "рекомендуется 8-bit yuv420p.")
    if max(plan["out_w"], plan["out_h"]) > 4096 or min(plan["out_w"], plan["out_h"]) > 2160:
        out.append("Sync: видео больше 4096×2160 будет отклонено — выбери 1080p.")
    fps = plan.get("out_fps") or 0
    if info.get("vfr") and not plan.get("cfr"):
        out.append("Sync рекомендует постоянный fps, а у исходника он переменный — "
                   "включи «Постоянный fps».")
    elif fps and not any(abs(fps - f) < 0.05 for f in (24, 25, 30)):
        out.append(f"Sync рекомендует 24, 25 или 30 fps (постоянный), сейчас {fps:g}.")
    return out


def _encoder_quality_args(plan):
    enc, crf = plan["encoder"], plan["crf"]
    if enc == "libx264":
        return ["-crf", str(crf)]
    if enc == "libx265":
        return ["-crf", str(crf + COMPRESS_X265_CRF_OFFSET)]
    if enc.endswith("_videotoolbox"):
        return ["-q:v", str({20: 65, 23: 55, 27: 45}.get(crf, 55))]
    if enc.endswith("_nvenc"):
        return ["-rc", "vbr", "-cq", str(crf + (4 if plan["codec"] == "hevc" else 0)), "-b:v", "0"]
    if enc.endswith("_qsv"):
        return ["-global_quality", str(crf)]
    if enc.endswith("_amf"):
        return ["-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf + 2)]
    return ["-crf", str(crf)]


def build_compress_cmd(src, dst, plan, video_kbps=None, pass_no=None, passlog="sheqel_pass",
                       seek=None, length=None):
    """Команда ffmpeg списком аргументов (без shell): пути с пробелами, кириллицей и # безопасны."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y"]
    if seek is not None:
        cmd += ["-ss", f"{seek:.3f}"]
    cmd += ["-i", str(src)]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-map", "0:v:0"]
    if pass_no != 1:
        cmd += ["-map", "0:a?"]
    cmd += ["-vf", ",".join(plan["filters"]), "-c:v", plan["encoder"]]
    enc = plan["encoder"]
    if enc == "libx264":
        cmd += ["-preset", COMPRESS_X264_PRESET]
    if enc == "libx265":
        cmd += ["-preset", COMPRESS_X264_PRESET]
    if video_kbps:
        kb = int(video_kbps)
        cmd += ["-b:v", f"{kb}k", "-maxrate", f"{int(kb * 1.5)}k", "-bufsize", f"{kb * 2}k"]
    else:
        cmd += _encoder_quality_args(plan)
    x265_params = ["log-level=error"]
    if plan["ten_bit"]:
        cmd += ["-color_primaries", "bt2020", "-color_trc", plan.get("color_transfer") or "smpte2084",
                "-colorspace", "bt2020nc"]
        if enc == "libx265" and plan.get("hdr_out") == "PQ":
            x265_params += ["hdr10=1", "repeat-headers=1"]
        if enc.endswith("_videotoolbox"):
            cmd += ["-profile:v", "main10"]
    elif plan["filters"] and any(f.startswith(("scale=out_transfer", "zscale")) for f in plan["filters"]):
        cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    if enc == "libx265" and pass_no:
        x265_params += [f"pass={pass_no}", f"stats={passlog}.x265.log"]
    elif pass_no:
        cmd += ["-pass", str(pass_no), "-passlogfile", passlog]
    if enc == "libx265":
        cmd += ["-x265-params", ":".join(x265_params)]
    if plan["codec"] == "hevc":
        cmd += ["-tag:v", "hvc1"]   # иначе QuickTime и iPhone не откроют HEVC в mp4
    if pass_no == 1:
        cmd += ["-an", "-progress", "pipe:1", "-nostats", "-f", "null", "-"]
        return cmd
    cmd += plan["audio_args"] or ["-an"]
    cmd += ["-max_muxing_queue_size", "1024", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", "-f", "mp4", str(dst)]
    return cmd


def run_ffmpeg_progress(cmd, duration, on_progress=None, cancel=None, cwd=None, register=None):
    """Запускает ffmpeg, разбирает -progress. on_progress(доля 0..1, скорость). Отмена — CompressCancelled.
    Ошибка — RuntimeError с хвостом вывода ffmpeg."""
    err_file = tempfile.TemporaryFile(mode="w+b")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_file, cwd=cwd,
                            **_compress_popen_kwargs())
    if register:
        register(proc)
    cancelled = False
    try:
        for raw in proc.stdout:
            if cancel and cancel():
                cancelled = True
                break
            line = raw.decode("utf-8", "replace").strip()
            key, _, value = line.partition("=")
            if key in ("out_time_us", "out_time_ms") and value.lstrip("-").isdigit() and duration:
                frac = min(max(int(value) / 1_000_000 / duration, 0.0), 1.0)
                if on_progress:
                    on_progress(frac, None)
            elif key == "speed" and on_progress:
                try:
                    on_progress(None, float(value.rstrip("x")))
                except ValueError:
                    pass
    finally:
        if cancelled or (cancel and cancel()):
            cancelled = True
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        else:
            proc.wait()
        if register:
            register(None)
    if cancelled:
        err_file.close()
        raise CompressCancelled()
    if proc.returncode != 0:
        err_file.seek(0)
        tail = err_file.read().decode("utf-8", "replace").strip().splitlines()[-30:]
        err_file.close()
        raise RuntimeError("ffmpeg завершился с ошибкой:\n" + "\n".join(tail))
    err_file.close()
    return proc.returncode


def compress_output_path(src, out_dir=None):
    """Папка compressed рядом с оригиналом (или выбранная), имя_compressed.mp4, при совпадении — _2, _3…
    Путь оригинала не возвращается никогда."""
    src = Path(src)
    folder = Path(out_dir) if out_dir else src.parent / COMPRESS_DIR_NAME
    stem = src.stem + COMPRESS_SUFFIX
    for n in range(1, 1000):
        cand = folder / (f"{stem}.mp4" if n == 1 else f"{stem}_{n}.mp4")
        if cand.exists() or os.path.abspath(cand) == os.path.abspath(src):
            continue
        tmp = cand.with_name(cand.stem + COMPRESS_TMP_SUFFIX)
        if not tmp.exists():
            return str(cand)
    raise RuntimeError("не удалось подобрать имя для результата")


def compress_tmp_path(final_path):
    p = Path(final_path)
    return str(p.with_name(p.stem + COMPRESS_TMP_SUFFIX))


def collect_compress_inputs(paths=(), folder=None, recursive=False):
    """Видео из выбранных файлов и папки. Папка compressed и файлы *_compressed пропускаются всегда."""
    found = []

    def ok(p):
        p = Path(p)
        return (p.suffix.lower() in COMPRESS_VIDEO_EXTS and not p.stem.endswith(COMPRESS_SUFFIX)
                and COMPRESS_SUFFIX + "_" not in p.stem and not p.name.endswith(COMPRESS_TMP_SUFFIX)
                and COMPRESS_DIR_NAME not in p.parent.parts[-1:] and not p.name.startswith("."))

    for p in paths:
        if os.path.isfile(p) and ok(p):
            found.append(os.path.abspath(p))
    if folder:
        if recursive:
            for root, dirs, files in os.walk(folder):
                dirs[:] = sorted(d for d in dirs if d != COMPRESS_DIR_NAME and not d.startswith("."))
                found += [os.path.join(root, f) for f in sorted(files) if ok(os.path.join(root, f))]
        else:
            found += [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                      if os.path.isfile(os.path.join(folder, f)) and ok(os.path.join(folder, f))]
    seen, result = set(), []
    for p in found:
        key = os.path.abspath(p)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def verify_compressed(src_info, out_path):
    """После кодирования: длительность ±0,5 с, есть видео, есть звук (если был в исходнике)."""
    info = probe_video_info(out_path)
    problems = []
    if abs(info["duration"] - src_info["duration"]) > COMPRESS_DURATION_TOLERANCE:
        problems.append(f"длительность {info['duration']:.2f} с вместо {src_info['duration']:.2f} с")
    if src_info["has_audio"] and not info["has_audio"]:
        problems.append("пропал звук")
    if problems:
        raise RuntimeError("проверка результата не прошла: " + ", ".join(problems))
    return info


def compress_free_space_check(out_dir, need_bytes):
    os.makedirs(out_dir, exist_ok=True)
    free = shutil.disk_usage(out_dir).free
    if free < need_bytes:
        raise RuntimeError(f"мало места на диске: нужно ≈ {need_bytes / MB:.0f} МБ, свободно {free / MB:.0f} МБ")


def compress_video_file(src, info, plan, final_path, log, progress=None, cancel=None, register=None,
                        on_tmp=None):
    """Сжимает один файл. Пишет во временный файл рядом с результатом, переименовывает только после
    проверки ffprobe. Оригинал не открывается на запись. Возвращает словарь результата."""
    if os.path.abspath(final_path) == os.path.abspath(src):
        raise RuntimeError("путь результата совпал с оригиналом")
    if plan.get("error"):
        raise RuntimeError(plan["error"])
    out_dir = os.path.dirname(final_path)
    need = (plan["target_mb"] * MB if plan["kind"] == "target" else info["size"]) * 1.2
    compress_free_space_check(out_dir, need)
    tmp = compress_tmp_path(final_path)
    if on_tmp:
        on_tmp(tmp)
    duration = info["duration"]
    two_pass = plan["kind"] == "target" and not plan["hw"]
    work_dir = tempfile.mkdtemp(prefix="sheqel_compress_")   # журналы 2 проходов: ASCII-путь, без «:»
    state = {"base": 0.0, "span": 1.0, "speed": None}

    def report(frac, speed):
        if speed is not None:
            state["speed"] = speed
        if frac is not None and progress:
            progress(state["base"] + state["span"] * frac, state["speed"])

    def encode(kbps):
        if two_pass:
            state.update(base=0.0, span=0.4)
            run_ffmpeg_progress(build_compress_cmd(src, tmp, plan, kbps, pass_no=1), duration, report,
                                cancel, cwd=work_dir, register=register)
            state.update(base=0.4, span=0.6)
            run_ffmpeg_progress(build_compress_cmd(src, tmp, plan, kbps, pass_no=2), duration, report,
                                cancel, cwd=work_dir, register=register)
        else:
            state.update(base=0.0, span=1.0)
            run_ffmpeg_progress(build_compress_cmd(src, tmp, plan, kbps), duration, report, cancel,
                                cwd=work_dir, register=register)

    started = time.time()
    try:
        kbps = plan["video_kbps"]
        log(f"Сжатие: {Path(src).name} → {Path(final_path).name} · {plan['encoder']} · "
            + (f"{kbps:.0f} кбит/с{' (2 прохода)' if two_pass else ''}" if kbps else f"CRF {plan['crf']}")
            + f" · {plan['out_w']}×{plan['out_h']}")
        try:
            encode(kbps)
        except RuntimeError as e:
            if not plan["hw"]:
                raise
            log(f"Аппаратный кодировщик {plan['encoder']} не справился — перехожу на программный. {e}")
            plan = dict(plan, encoder=SOFTWARE_ENCODERS[plan["codec"]], hw=False,
                        filters=[f.replace("format=p010le", "format=yuv420p10le") for f in plan["filters"]])
            two_pass = plan["kind"] == "target"
            encode(kbps)
        size = os.path.getsize(tmp)
        if plan["kind"] == "target":
            # Кодировщик может промахнуться (шумные кадры, аппаратный rate control). Поправка — только
            # видеочасти: звук и контейнер от битрейта видео не зависят. Не больше двух повторов.
            target = plan["target_mb"] * MB
            fixed = (plan["audio_kbps"] or 0) * 1000 / 8 * duration + target * COMPRESS_CONTAINER_OVERHEAD
            for retry in range(2):
                if plan.get("capped") and size <= target:
                    break   # файл и так меньше цели: раздувать до неё нет смысла
                if not (size > target if plan["strict"] else abs(size - target) > target * 0.1):
                    break
                want = (target * (1 - COMPRESS_LIMIT_MARGIN) if plan["strict"] else target) - fixed
                got = max(size - fixed, 1)
                kbps = kbps * max(want, 1) / got
                log(f"Размер {size / MB:.1f} МБ мимо цели {plan['target_mb']:g} МБ — "
                    f"повтор {retry + 1}/2 с {kbps:.0f} кбит/с")
                encode(kbps)
                size = os.path.getsize(tmp)
            if plan["strict"] and size > target:
                raise RuntimeError(f"не удалось уложиться в {plan['target_mb']:g} МБ "
                                   f"(вышло {size / MB:.1f} МБ). Попробуй 720p или меньший fps.")
        out_info = verify_compressed(info, tmp)
        final_path = compress_output_path(src, out_dir) if os.path.exists(final_path) else final_path
        os.replace(tmp, final_path)
        if on_tmp:
            on_tmp(None)
        elapsed = time.time() - started
        log(f"Сжато: {Path(final_path).name} · {info['size'] / MB:.1f} → {size / MB:.1f} МБ "
            f"за {elapsed:.0f} с")
        return {"output": final_path, "size": size, "src_size": info["size"], "bigger": size >= info["size"],
                "duration": out_info["duration"], "width": out_info["width"], "height": out_info["height"],
                "elapsed": elapsed, "encoder": plan["encoder"]}
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        if on_tmp:
            on_tmp(None)
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def estimate_crf_size(src, info, plan, cancel=None, register=None):
    """Примерный размер для CRF: кодируем три отрезка по 4 с (начало, середина, конец)."""
    duration = info["duration"]
    seg = 4.0
    if duration <= seg * 3:
        points, lengths = [0.0], [duration]
    else:
        points = [duration * 0.1, duration * 0.5, max(duration * 0.85, 0) - seg / 2]
        lengths = [seg] * 3
    work_dir = tempfile.mkdtemp(prefix="sheqel_estimate_")
    try:
        bytes_total = 0
        sampled = 0.0
        sample_plan = dict(plan, audio_args=[])
        for i, (start, length) in enumerate(zip(points, lengths)):
            if cancel and cancel():
                raise CompressCancelled()
            out = os.path.join(work_dir, f"s{i}.mp4")
            run_ffmpeg_progress(build_compress_cmd(src, out, sample_plan, seek=start, length=length),
                                length, None, cancel, cwd=work_dir, register=register)
            bytes_total += os.path.getsize(out)
            sampled += length
        video_bytes = bytes_total / max(sampled, 0.1) * duration
        audio_bytes = (plan["audio_kbps"] or 0) * 1000 / 8 * duration
        return int((video_bytes + audio_bytes) * (1 + COMPRESS_CONTAINER_OVERHEAD))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def compress_estimate_key(info, settings):
    keys = ("preset", "resolution", "fps", "audio", "codec", "fast", "hdr_mode")
    return json.dumps([info["path"], info["size"], round(os.path.getmtime(info["path"]))]
                      + [settings.get(k) for k in keys], ensure_ascii=False)


def cleanup_compress_tmp(path):
    """Удаляет недописанный файл сжатия (только с нашим суффиксом)."""
    if path and str(path).endswith(COMPRESS_TMP_SUFFIX):
        try:
            os.remove(path)
            return True
        except OSError:
            return False
    return False


# ── Лицо говорящего: options.active_speaker_detection для Sync ─────────────
FACE_MODES = {
    "По умолчанию": "default",
    "Автоопределение говорящего": "auto",
    "Выбрать вручную": "manual",
}
FACE_MODE_LABELS = {v: k for k, v in FACE_MODES.items()}
ROLL_VIDEO_FIELDS = {"single": "video_single", "start": "video_start", "end": "video_end"}
FACE_POINTS_PATH = str(Path.home() / ".sheqelmotion_faces.json")
FACE_PICKER_MAX_W = 860
FACE_PICKER_MAX_H = 500
_face_points_lock = threading.Lock()


def face_video_key(video_path):
    """Путь + размер + время изменения: точка не «переедет» на другой файл с тем же именем."""
    st = os.stat(video_path)
    return f"{os.path.abspath(video_path)}|{st.st_size}|{int(st.st_mtime)}"


def load_face_points():
    try:
        with open(FACE_POINTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_face_point(point):
    with _face_points_lock:
        data = load_face_points()
        data[point["video_key"]] = point
        tmp = FACE_POINTS_PATH + ".tmp"
        with open_private(tmp) as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, FACE_POINTS_PATH)


def face_point_for_roll(roll, kind):
    """Точка лица для видео ролика: из сцены, а если её нет — из сохранённых на диске."""
    video = roll.get(ROLL_VIDEO_FIELDS[kind]) or ""
    if not video or not os.path.exists(video):
        return None
    try:
        key = face_video_key(video)
    except OSError:
        return None
    point = (roll.get("face_points") or {}).get(kind)
    if point and point.get("video_key") == key:
        return point
    return load_face_points().get(key)


def probe_video_frames(path):
    """Размер кадра с учётом поворота из метаданных, число кадров, fps и поворот."""
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=width,height,nb_read_packets,avg_frame_rate"
         ":stream_side_data=rotation:stream_tags=rotate",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    st = json.loads(out)["streams"][0]
    width, height = int(st["width"]), int(st["height"])
    rotation = 0
    for side in st.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if not rotation and (st.get("tags") or {}).get("rotate"):
        rotation = int(st["tags"]["rotate"])
    if rotation % 180:
        width, height = height, width
    num, _, den = str(st.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    return {"width": width, "height": height, "frames": int(st.get("nb_read_packets") or 0),
            "fps": fps, "rotation": rotation}


def extract_video_frame(video_path, frame_number, out_png):
    """N-й декодированный кадр в PNG — та же нумерация, что у входа Sync (кадры не пересчитываются)."""
    if os.path.exists(out_png):
        os.remove(out_png)
    subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-y", "-i", str(video_path),
         "-vf", f"select=eq(n\\,{int(frame_number)})", "-frames:v", "1", str(out_png)],
        capture_output=True, check=True,
    )
    if not os.path.exists(out_png):
        raise RuntimeError(f"кадра {frame_number} нет в видео")
    return out_png


def speaker_options(mode, point=None):
    """options для POST /v2/generate. Режимы взаимоисключающие; «по умолчанию» — без options."""
    if mode == "auto":
        return {"active_speaker_detection": {"auto_detect": True}}
    if mode == "manual" and point:
        return {"active_speaker_detection": {
            "frame_number": int(point["frame_number"]),
            "coordinates": [int(point["coordinates"][0]), int(point["coordinates"][1])],
        }}
    return None


def resolve_speaker_for_sync_input(mode, point, sync_video, log, source_video=None):
    """Сверяет ручную точку с реальным входом Sync: пересчитывает координаты под его размер,
    номер кадра — под его fps (N × fps входа / fps исходника), а если кадр не попал
    в обрезанное видео — откатывается на автоопределение."""
    if mode == "auto":
        log("Лицо говорящего: автоопределение")
        return speaker_options("auto")
    if mode != "manual":
        return None
    if not point:
        log("Лицо говорящего: ручной режим, но точка не выбрана — использую автоопределение")
        return speaker_options("auto")
    x, y = point["coordinates"]
    try:
        info = probe_video_frames(sync_video)
    except Exception as e:
        log(f"Лицо говорящего: не удалось прочитать вход Sync ({e}) — отправляю точку как есть")
        return speaker_options("manual", point)
    if (info["width"], info["height"]) != (point["width"], point["height"]):
        x = round(x * info["width"] / point["width"])
        y = round(y * info["height"] / point["height"])
        log(f"Лицо говорящего: кадр Sync {info['width']}x{info['height']}, выбирали на "
            f"{point['width']}x{point['height']} — координаты пересчитаны")
    x = min(max(0, x), info["width"] - 1)
    y = min(max(0, y), info["height"] - 1)
    frame = int(point["frame_number"])
    source_fps = point.get("fps")
    if not source_fps and source_video:
        try:
            source_fps = probe_video_frames(source_video)["fps"]
        except Exception:
            source_fps = None
    if source_fps and info["fps"] and abs(source_fps - info["fps"]) > 0.01:
        mapped = int(round(frame * info["fps"] / source_fps))
        log(f"Лицо говорящего: кадр {frame} при {source_fps:g} fps → кадр {mapped} "
            f"при {info['fps']:g} fps входа Sync")
        frame = mapped
    if info["frames"] and frame >= info["frames"]:
        log(f"Лицо говорящего: кадр {frame} не попал в обрезанное видео "
            f"({info['frames']} кадров) — использую автоопределение")
        return speaker_options("auto")
    log(f"Лицо говорящего: кадр {frame}, точка ({x}, {y})")
    return speaker_options("manual", {"frame_number": frame, "coordinates": [x, y]})


# ── Оценка стоимости и времени липсинка ─────────────────────────────────────
SYNC_ESTIMATE_URL = "https://api.sync.so/v2/generations/estimate"
# В документации Sync не сказано, тарифицируется ли сам вызов estimate («No media is fetched
# and no generation is created»). Считаем бесплатным, но это не подтверждено — уточнить у Sync.
SYNC_ESTIMATE_TIMEOUT_SEC = 10
SYNC_PRICE_PER_FRAME = 0.00427        # по ответу estimate для sync-3; запасной вариант локальной оценки
SYNC_SEC_PER_VIDEO_SEC = (20, 30)     # время обработки sync-3 на секунду видео
SYNC_QUEUE_OVERHEAD_SEC = (60, 120)   # ожидание в очереди Sync на каждую задачу
COST_CONFIRM_DEFAULT_THRESHOLD = 20.0
# Панель хода очереди внизу главного окна
LOG_PANEL_MAX_LINES = 500
QUEUE_PANEL_MODES = {"Показывать во время работы": "running", "Только итог": "summary", "Не показывать": "off"}
QUEUE_PANEL_LABELS = {v: k for k, v in QUEUE_PANEL_MODES.items()}


# Последняя цена за кадр из успешного ответа estimate (сохраняется в конфиге между сессиями).
sync_price_state = {"per_frame": None}


def local_price_per_frame():
    return sync_price_state["per_frame"] or SYNC_PRICE_PER_FRAME


def billable_seconds(video_sec, audio_sec, sync_mode=SYNC_SYNC_MODE):
    """Длина выхода Sync по таблице sync_mode из документации — она и оплачивается."""
    if sync_mode == "cut_off":
        return min(video_sec, audio_sec)
    if sync_mode == "silence":
        return max(video_sec, audio_sec)
    return audio_sec  # loop, bounce, remap: длина выхода = длина аудио


def estimate_sync_cost(duration, fps, api_key, model=SYNC_MODEL):
    """Цена по данным Sync (POST /v2/generations/estimate); если запрос не удался — локальная оценка.
    Ключ уходит только в заголовке запроса и нигде не логируется."""
    error = "нет ключа Sync"
    if api_key and duration > 0 and fps > 0:
        try:
            res = requests.post(
                SYNC_ESTIMATE_URL,
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json={"model": model, "duration": round(duration, 3), "fps": round(fps, 3)},
                timeout=SYNC_ESTIMATE_TIMEOUT_SEC,
            )
            if res.status_code == 200:
                data = res.json()
                cost = float(data["estimatedGenerationCost"])
                frames = int(round(float(data["estimatedFrameCount"])))
                if frames > 0 and cost > 0:
                    sync_price_state["per_frame"] = cost / frames
                return {"cost": cost, "frames": frames, "source": "sync"}
            error = f"HTTP {res.status_code}"
        except Exception as e:
            error = type(e).__name__
    frames = int(round(max(duration, 0) * fps))
    return {"cost": round(frames * local_price_per_frame(), 2), "frames": frames,
            "source": "local", "error": error}


def estimate_sync_time(billable_sec):
    """Диапазон времени одной задачи Sync в секундах (оценка, не гарантия)."""
    return (billable_sec * SYNC_SEC_PER_VIDEO_SEC[0] + SYNC_QUEUE_OVERHEAD_SEC[0],
            billable_sec * SYNC_SEC_PER_VIDEO_SEC[1] + SYNC_QUEUE_OVERHEAD_SEC[1])


def estimate_queue_time(roll_times, workers=None, stagger=None):
    """Время всей очереди при параллельности workers: раскладка роликов по потокам в порядке очереди."""
    workers = max(1, workers or CONCURRENT_ROLLS)
    stagger = PAUSE_BETWEEN_ROLLS_SEC if stagger is None else stagger

    def makespan(durations):
        free = [0.0] * workers
        for i, d in enumerate(durations):
            w = min(range(workers), key=free.__getitem__)
            free[w] = max(free[w], i * stagger) + d
        return max(free) if durations else 0.0

    return makespan([t[0] for t in roll_times]), makespan([t[1] for t in roll_times])


def estimate_split_durations(voice_path):
    """Длины аудио начала и конца для «Начало + Конец» — по тем же паузам, что split_start_end_by_silence."""
    total = get_duration(voice_path)
    silences = [s for s in detect_silences(voice_path, lambda m: None)
                if s["start"] > 0.5 and (total - s["end"]) > 0.5]
    if not silences:
        raise RuntimeError("паузы не найдены")
    return max(0.0, silences[0]["start"]), max(0.0, total - silences[-1]["end"])


def estimate_cache_key(roll, target_fps=None):
    """Ключ оценки: меняется при смене видео, аудио, обрезки, режима ролика или fps для Sync.
    None — данных мало."""
    voice = roll.get("voice_file") or ""
    kinds = ["single"] if roll.get("mode") == "1 видео" else ["start", "end"]
    videos = [roll.get(ROLL_VIDEO_FIELDS[k]) or "" for k in kinds]
    if not voice or not all(videos):
        return None
    try:
        files = tuple(face_video_key(p) for p in [voice] + videos)
    except OSError:
        return None
    return (roll.get("mode"), files, roll.get("audio_start", ""), roll.get("audio_end", ""),
            SYNC_MODEL, SYNC_SYNC_MODE, target_fps)


def estimate_roll(roll, api_key, target_fps=None):
    """Оценка ролика по частям: длительности, fps, кадры, цена и время.
    target_fps — частота кадров, в которую вход будет перекодирован для Sync (None — как в исходнике)."""
    voice = roll["voice_file"]
    total_audio = get_duration(voice)
    if roll["mode"] == "1 видео":
        start = float(roll.get("audio_start") or 0)
        end = float(roll.get("audio_end") or total_audio)
        audio_parts = [("single", max(0.0, min(end, total_audio) - start), "")]
    else:
        try:
            a, b = estimate_split_durations(voice)
            audio_parts = [("start", a, ""), ("end", b, "")]
        except Exception:
            note = "по полной длине аудио — паузы не найдены"
            audio_parts = [("start", total_audio, note), ("end", total_audio, note)]

    parts = []
    for kind, audio_sec, note in audio_parts:
        video = roll[ROLL_VIDEO_FIELDS[kind]]
        info = probe_video_frames(video)
        video_sec = get_duration(video)
        sent_video = min(video_sec, audio_sec + END_PADDING)  # так обрезает prepare_video_for_sync
        bill = billable_seconds(sent_video, audio_sec)
        source_fps = info["fps"] or 25.0
        fps = target_fps or source_fps
        cost = estimate_sync_cost(bill, fps, api_key)
        parts.append({
            "kind": kind, "video_sec": video_sec, "audio_sec": audio_sec, "billable": bill,
            "fps": fps, "source_fps": source_fps, "width": info["width"], "height": info["height"],
            "frames": cost["frames"], "cost": cost["cost"], "source": cost["source"],
            "time": estimate_sync_time(bill), "note": note,
            "video_shorter": video_sec + 0.05 < audio_sec,
        })
    sources = {p["source"] for p in parts}
    return {
        "parts": parts,
        "cost": round(sum(p["cost"] for p in parts), 2),
        "frames": sum(p["frames"] for p in parts),
        "billable": sum(p["billable"] for p in parts),
        "time": (sum(p["time"][0] for p in parts), sum(p["time"][1] for p in parts)),
        "source": sources.pop() if len(sources) == 1 else "mixed",
    }


def format_minutes(seconds_range):
    lo, hi = (max(1, round(s / 60)) for s in seconds_range)
    return f"{lo} мин" if lo == hi else f"{lo}–{hi} мин"


def format_estimate_source(source):
    return {"sync": "по данным Sync", "local": "локальная оценка"}.get(source, "частично локальная оценка")


def process_lipsync(video_path, audio_wav, output_mp4, temp_dir, log,
                    face_mode="default", face_point=None, sync_fps=None, storage="cloudinary",
                    journal_ctx=None):
    stem = safe_name(Path(output_mp4).name)
    sync_video = str(Path(temp_dir) / (stem + "_sync_input.mp4"))
    raw_video = str(Path(temp_dir) / (stem + "_raw.mp4"))
    prepare_video_for_sync(video_path, audio_wav, sync_video, target_fps=sync_fps)
    options = resolve_speaker_for_sync_input(face_mode, face_point, sync_video, log,
                                             source_video=video_path)
    estimate = None
    try:
        audio_sec = get_duration(audio_wav)
        bill = billable_seconds(get_duration(sync_video), audio_sec)
        fps = probe_video_frames(sync_video)["fps"] or 25.0
        estimate = dict(estimate_sync_cost(bill, fps, SYNC_API_KEY), fps=fps, billable=bill)
        log(f"Оценка части: {bill:.1f} с · {fps:g} fps · {estimate['frames']} кадров · "
            f"≈ ${estimate['cost']:.2f} ({format_estimate_source(estimate['source'])}) · "
            f"≈ {format_minutes(estimate_sync_time(bill))}")
    except Exception as e:
        log(f"Оценка части недоступна: {e}")
    apply_lipsync_sync(sync_video, audio_wav, raw_video, log, options=options, estimate=estimate,
                       storage=storage, journal_ctx=dict(journal_ctx or {}, final_path=output_mp4))
    enhance_video(raw_video, output_mp4)
    return output_mp4


# ── Забор результата прерванного ролика из Sync ────────────────────────────
SYNC_PART_LABELS = {"single": "ролик", "start": "начало", "end": "конец"}


def sync_jobs_for_roll(journal, roll_id):
    """Задачи Sync последнего запуска ролика: {часть: сводка}. Сводка — последняя созданная
    задача части (повторы после подтверждённого провала создают новую) и её события."""
    entries = [e for e in journal or [] if isinstance(e, dict) and e.get("roll_id") == roll_id]
    created = [e for e in entries if e.get("event") == "created" and e.get("job_id")]
    if not created:
        return {}
    batch = created[-1].get("batch")
    parts = {}
    for e in created:
        if e.get("batch") == batch:
            parts[e.get("part") or "single"] = {
                "job_id": e["job_id"], "roll_id": roll_id, "final_path": e.get("final_path") or "", "batch": batch,
                "assets": e.get("assets") or None, "cloudinary_ids": e.get("cloudinary_ids") or [],
                "saved_path": None, "output_url": None, "state": None, "error_code": None,
            }
    for e in entries:
        info = next((v for v in parts.values() if v["job_id"] == e.get("job_id")), None)
        if info is None:
            continue
        if e.get("event") == "completed":
            info["output_url"] = e.get("output_url")
        elif e.get("event") == "fetched":
            info["saved_path"] = e.get("path")
        elif e.get("event") == "failed":
            info["state"], info["error_code"] = "FAILED", e.get("error_code")
    return parts


def sync_result_path(final_path):
    """Понятное имя в папке партии: имя из очереди, а если такой файл уже есть — «_из_Sync»."""
    path = Path(final_path)
    if not path.exists():
        return str(path)
    for n in range(1, 100):
        cand = path.with_name(f"{path.stem}_из_Sync{'' if n == 1 else f'_{n}'}{path.suffix}")
        if not cand.exists():
            return str(cand)
    return str(path.with_name(f"{path.stem}_из_Sync_{int(time.time())}{path.suffix}"))


def _download_sync_output(url, dest, log, progress=None):
    """Скачивает результат во временный файл рядом с dest. raise_for_status, сверка размера
    с Content-Length, минимум 1 КБ. Возвращает путь к временному файлу."""
    tmp = dest + ".part"
    try:
        with requests.get(url, stream=True, timeout=(30, 300)) as res:
            res.raise_for_status()
            expected = int(res.headers.get("Content-Length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                for chunk in res.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        got += len(chunk)
                        if progress and expected:
                            progress(got / expected)
        if expected and got != expected:
            raise RuntimeError(f"скачано {got} байт из {expected} — файл неполный")
        if not file_exists_ok(tmp):
            raise RuntimeError(f"скачанный файл пустой ({got} байт)")
        return tmp
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def cleanup_sync_inputs(info, api_key, log):
    """Удаление входных файлов задачи после подтверждённого исхода (те же правила, что в очереди)."""
    for asset_id in ((info.get("assets") or {}).get("video"), (info.get("assets") or {}).get("audio")):
        if asset_id:
            delete_sync_asset(asset_id, api_key, log)
    for public_id in info.get("cloudinary_ids") or []:
        delete_from_cloudinary(public_id, log)


def fetch_sync_result(info, api_key, log, progress=None):
    """Один запрос статуса задачи Sync (без новой генерации). Возвращает словарь:
    state — saved | pending | failed | error; path, status, error_code, message."""
    job_id = info["job_id"]
    try:
        res = requests.get(f"{SYNC_GENERATE_URL}/{job_id}", headers={"x-api-key": api_key}, timeout=30)
    except requests.exceptions.RequestException as e:
        return {"state": "error", "message": f"Sync недоступен: {_strip_urls(e)}. Попробуй позже."}
    if res.status_code == 404:
        return {"state": "error", "message": f"Sync не нашёл задачу {job_id}."}
    if res.status_code != 200:
        return {"state": "error", "message": f"Sync ответил HTTP {res.status_code}. Попробуй позже."}
    status = res.json()
    state = status.get("status")
    log(f"Sync job {job_id}: {state}")
    if state in ("FAILED", "REJECTED"):
        code = status.get("errorCode") or status.get("error_code") or "без кода"
        journal_sync_job("failed", job_id=job_id, roll_id=info.get("roll_id"), error_code=code,
                         error=str(status.get("error") or "")[:300])
        cleanup_sync_inputs(info, api_key, log)
        return {"state": "failed", "status": state, "error_code": code,
                "message": f"Sync не сделал ролик (статус {state}, код {code}). "
                           "Повторная отправка — новая задача, её придётся оплатить заново."}
    if state != "COMPLETED":
        return {"state": "pending", "status": state,
                "message": f"Задача ещё не готова (статус {state}). Повтори «Забрать» позже."}
    url = status.get("outputUrl") or status.get("output_url")
    if not url:
        return {"state": "error", "status": state, "message": "Sync вернул COMPLETED без ссылки на результат."}
    log(f"Sync job {job_id} COMPLETED, outputUrl: {url}")
    journal_sync_job("completed", job_id=job_id, roll_id=info.get("roll_id"), output_url=url,
                     output_duration=status.get("outputDuration"))
    dest = sync_result_path(info["final_path"])
    ensure_dir(Path(dest).parent)
    try:
        raw = _download_sync_output(url, dest, log, progress)
    except Exception as e:
        return {"state": "error", "status": state,
                "message": f"Задача готова, но скачать не удалось: {_strip_urls(e)}. "
                           "Повтори позже или забери по outputUrl из лога."}
    try:
        enhance_video(raw, dest)  # как в очереди: та же обработка финала
        os.remove(raw)
    except Exception as e:
        try:
            playable = get_duration(raw) > 0
        except Exception:
            playable = False
        if not playable:
            for leftover in (raw, dest):  # dest — новое имя, недописанный финал удаляем
                try:
                    os.remove(leftover)
                except OSError:
                    pass
            log(f"Обработка финала не удалась: {e}")
            return {"state": "error", "status": state,
                    "message": "Скачанный файл не читается как видео. Повтори позже или забери по outputUrl из лога."}
        log(f"Обработка финала не удалась ({e}) — сохраняю файл Sync как есть")
        os.replace(raw, dest)
    log(f"Результат из Sync сохранён: {dest}")
    journal_sync_job("fetched", job_id=job_id, roll_id=info.get("roll_id"), path=dest)
    cleanup_sync_inputs(info, api_key, log)
    return {"state": "saved", "status": state, "path": dest, "message": f"Сохранено: {dest}"}


def _ensure_ytdlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        import subprocess
        import sys
        subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"], check=True)
        import yt_dlp
        return yt_dlp


def _ytdlp_ffmpeg_opts():
    def _test_ffmpeg(path):
        try:
            result = subprocess.run(
                [path, "-version"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    candidates = []

    local = Path(app_base_dir()) / exe_name
    if local.exists():
        candidates.append(str(local.parent))

    candidates.append("/opt/homebrew/bin")
    candidates.append("/usr/local/bin")

    found = shutil.which("ffmpeg")
    if found:
        candidates.append(str(Path(found).parent))

    for directory in candidates:
        ffmpeg_path = str(Path(directory) / exe_name)
        if Path(ffmpeg_path).exists() and _test_ffmpeg(ffmpeg_path):
            return directory

    return None


# ── Куки браузеров для yt-dlp: какие браузеры есть, порядок, понятные ошибки ──────────────
COOKIE_BROWSER_ORDER = ["firefox", "chrome", "brave", "edge", "safari", "chromium"]   # Safari — последним из основных
COOKIE_BROWSER_LABELS = {"firefox": "Firefox", "chrome": "Chrome", "brave": "Brave", "edge": "Edge",
                         "safari": "Safari", "chromium": "Chromium"}
# Где лежит профиль или база куки. Пути относительно домашней папки (Mac, Linux) или переменных
# окружения Windows. Отсутствующий браузер в режиме «Авто» пропускается молча.
COOKIE_BROWSER_PATHS = {
    "darwin": {
        "firefox": ["~/Library/Application Support/Firefox/Profiles"],
        "chrome": ["~/Library/Application Support/Google/Chrome"],
        "brave": ["~/Library/Application Support/BraveSoftware/Brave-Browser"],
        "edge": ["~/Library/Application Support/Microsoft Edge"],
        "safari": ["~/Library/Containers/com.apple.Safari", "~/Library/Safari", "~/Library/Cookies"],
        "chromium": ["~/Library/Application Support/Chromium"],
    },
    "win32": {
        "firefox": ["%APPDATA%/Mozilla/Firefox/Profiles"],
        "chrome": ["%LOCALAPPDATA%/Google/Chrome/User Data"],
        "brave": ["%LOCALAPPDATA%/BraveSoftware/Brave-Browser/User Data"],
        "edge": ["%LOCALAPPDATA%/Microsoft/Edge/User Data"],
        "chromium": ["%LOCALAPPDATA%/Chromium/User Data"],
    },
    "linux": {
        "firefox": ["~/.mozilla/firefox", "~/snap/firefox/common/.mozilla/firefox"],
        "chrome": ["~/.config/google-chrome"],
        "brave": ["~/.config/BraveSoftware/Brave-Browser"],
        "edge": ["~/.config/microsoft-edge"],
        "chromium": ["~/.config/chromium", "~/snap/chromium/common/chromium"],
    },
}


def _platform_key(platform_name=None):
    p = platform_name or sys.platform
    return "darwin" if p == "darwin" else "win32" if p == "win32" else "linux"


def browser_profile_paths(browser, platform_name=None, home=None, env=None):
    home = home or str(Path.home())
    env = env if env is not None else os.environ
    out = []
    for raw in COOKIE_BROWSER_PATHS[_platform_key(platform_name)].get(browser, []):
        path = raw.replace("~", home, 1) if raw.startswith("~") else raw
        missing = False
        for var in re.findall(r"%([A-Z_]+)%", path):
            if not env.get(var):
                missing = True
            path = path.replace(f"%{var}%", env.get(var, ""))
        if not missing:
            out.append(os.path.normpath(path))
    return out


# Признак, что в профиле реально есть база куки (папка браузера бывает и без неё).
_COOKIE_DB_GLOBS = {
    "firefox": ["*/cookies.sqlite"],
    "chrome": ["*/Cookies", "*/Network/Cookies"], "brave": ["*/Cookies", "*/Network/Cookies"],
    "edge": ["*/Cookies", "*/Network/Cookies"], "chromium": ["*/Cookies", "*/Network/Cookies"],
}


def browser_profile_exists(browser, platform_name=None, home=None, env=None):
    """Есть ли у браузера профиль с базой куки. Safari — по папке: сама база закрыта системой
    без Полного доступа к диску, это проверит yt-dlp и даст понятную ошибку."""
    import glob
    for root in browser_profile_paths(browser, platform_name, home, env):
        if not os.path.isdir(root):
            continue
        patterns = _COOKIE_DB_GLOBS.get(browser)
        if not patterns:
            return True
        if any(glob.glob(os.path.join(glob.escape(root), pat)) for pat in patterns):
            return True
    return False


def cookie_browser_candidates(platform_name=None, home=None, env=None):
    """Браузеры для режима «Авто» в нужном порядке — только те, чей профиль есть на этом компьютере."""
    return [b for b in COOKIE_BROWSER_ORDER if browser_profile_exists(b, platform_name, home, env)]


def _browser_priority_order():
    """Совместимость: порядок перебора, только установленные браузеры."""
    return cookie_browser_candidates()


_BOT_CHECK_RE = re.compile(r"(?i)sign in to confirm you(?:'|’)?re not a bot")
_NO_COOKIE_DB_RE = re.compile(r"(?i)could not find (\w+) cookies database")
_SAFARI_PERM_RE = re.compile(r"(?i)(operation not permitted|permission denied|errno 1\b).{0,200}"
                             r"(safari|binarycookies)|(safari|binarycookies).{0,200}(operation not permitted|permission denied)")
_FATAL_VIDEO_RE = re.compile(r"(?i)video unavailable|private video|this video (?:is|has been) (?:not available|"
                             r"unavailable|private|removed)|unsupported url|http error 404|members[- ]only|"
                             r"not available in your country|sign in to confirm your age")


def classify_download_error(text, dpapi=False):
    """bot_check | no_cookie_db | safari_permission | dpapi | video | other."""
    text = str(text)
    if dpapi or _DPAPI_ERROR_HINT in text.lower():
        return "dpapi"
    if _BOT_CHECK_RE.search(text):
        return "bot_check"
    if _NO_COOKIE_DB_RE.search(text):
        return "no_cookie_db"
    if _SAFARI_PERM_RE.search(text):
        return "safari_permission"
    if _FATAL_VIDEO_RE.search(text):
        return "video"
    return "other"


SAFARI_DISK_ACCESS_HINT = ("Нужен Полный доступ к диску для приложения, из которого запущена программа "
                           "(Системные настройки → Конфиденциальность и безопасность → Полный доступ к диску), "
                           "потом перезапустите программу. Или выберите другой браузер.")


def friendly_download_error(kind, browser=None, raw=""):
    name = COOKIE_BROWSER_LABELS.get(browser or "", browser or "браузер")
    if kind == "bot_check":
        return ("YouTube просит войти. Откройте youtube.com в выбранном браузере и войдите в аккаунт, "
                "потом повторите. Если включён VPN, отключите его.")
    if kind == "no_cookie_db":
        return f"В браузере {name} не найден профиль. Выберите другой браузер."
    if kind == "safari_permission":
        return SAFARI_DISK_ACCESS_HINT
    if kind == "dpapi":
        return ("Не удалось прочитать куки из браузера (известный баг Chrome на Windows). "
                "Выберите Firefox или файл cookies.txt.")
    text = re.sub(r"^ERROR:\s*", "", str(raw).strip().splitlines()[0] if str(raw).strip() else "")
    return text[:300] or "Не удалось скачать."


def summarize_cookie_failures(failures):
    """Итог «Авто», когда не сработал ни один браузер. failures: [(браузер, вид, сырой текст)]."""
    kinds = [k for _b, k, _r in failures]
    if "bot_check" in kinds:
        return friendly_download_error("bot_check")
    if "video" in kinds:
        b, k, raw = next(f for f in failures if f[1] == "video")
        return friendly_download_error(k, b, raw)
    parts = []
    for b, k, raw in failures:
        name = COOKIE_BROWSER_LABELS.get(b, b)
        reason = {"no_cookie_db": "профиль не найден", "safari_permission": "нужен Полный доступ к диску",
                  "dpapi": "куки не расшифровать (баг Chrome на Windows)"}.get(k) or friendly_download_error(k, b, raw)[:80]
        parts.append(f"{name} — {reason}")
    text = "Не удалось взять куки ни из одного браузера: " + "; ".join(parts) + "."
    if "safari_permission" in kinds:
        text += " " + SAFARI_DISK_ACCESS_HINT
    else:
        text += " Выберите браузер, в котором вы вошли в YouTube, или файл cookies.txt."
    return text


def find_best_browser_with_google():
    """
    Ищет браузер где выполнен вход в Google аккаунт.
    Возвращает название браузера для yt-dlp или None.
    """
    for browser in cookie_browser_candidates():
        try:
            import yt_dlp
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "cookiesfrombrowser": (browser,),
                "simulate": True,
                "skip_download": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                jar = ydl.cookiejar
                for cookie in jar:
                    if "google.com" in cookie.domain or "youtube.com" in cookie.domain:
                        if cookie.name in ("SID", "HSID", "SSID", "LOGIN_INFO", "__Secure-1PSID"):
                            return browser
        except Exception:
            continue
    return None


AUTO_BROWSER = "__AUTO__"


def open_full_disk_access_settings():
    """macOS: раздел «Полный доступ к диску» в Системных настройках (нужен для куки Safari)."""
    try:
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"])
    except Exception:
        pass
_DPAPI_ERROR_HINT = "decrypt with dpapi"


def download_from_url(url, output_dir, mode, denoise, log, browser=None, cookies_file=None, on_cookies=None):
    """
    Скачивает видео или аудио через yt-dlp в максимальном качестве.
    mode: 'video' | 'audio'
    denoise: bool — применить голосовой денойз к аудио
    browser: str | None — браузер для cookiesfrombrowser, либо AUTO_BROWSER —
             перебор браузеров по приоритету (см. _browser_priority_order),
             с переходом к следующему при ошибке (напр. DPAPI на Windows)
    cookies_file: str | None — путь к cookies.txt; приоритет над browser
    on_cookies: callable(str) | None — сообщает, откуда реально взяты куки («Firefox», «cookies.txt», «без куки»)
    """
    on_cookies = on_cookies or (lambda label: None)
    homebrew_dirs = ["/opt/homebrew/bin", "/usr/local/bin"]
    for d in homebrew_dirs:
        if os.path.exists(d) and d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    yt_dlp = _ensure_ytdlp()
    ensure_dir(output_dir)

    # Добавляем директорию с бандленным ffmpeg в PATH для yt-dlp
    base = str(Path(app_base_dir()))
    env_path = os.environ.get("PATH", "")
    if base not in env_path.split(os.pathsep):
        os.environ["PATH"] = base + os.pathsep + env_path

    timestamp = time.strftime("%d-%m-%Y_%H-%M-%S")
    ffmpeg_dir = _ytdlp_ffmpeg_opts()

    last_pct = {"v": ""}

    def progress_hook(d):
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "").strip()
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            line = f"  {pct}  {speed}  ETA {eta}"
            if pct != last_pct["v"]:
                last_pct["v"] = pct
                log(line)
        elif d["status"] == "finished":
            log(f"  Скачано: {Path(d.get('filename', '')).name}")

    # dpapi_seen фиксирует, всплыло ли "Failed to decrypt with DPAPI" в логах
    # yt-dlp за текущую попытку — используется для показа понятного сообщения
    # об известном баге Chrome/Chromium/Edge 127+ на Windows вместо сырой ошибки.
    dpapi_seen = {"v": False}

    class _YTLogger:
        def debug(self, msg):
            if msg.startswith("[debug]"):
                return
            log(msg)
        def warning(self, msg):
            if _DPAPI_ERROR_HINT in msg.lower():
                dpapi_seen["v"] = True
            log(f"[предупреждение] {msg}")
        def error(self, msg):
            if _DPAPI_ERROR_HINT in msg.lower():
                dpapi_seen["v"] = True
            log(f"[ошибка] {msg}")

    base_common = {
        "quiet": True,
        "no_warnings": False,
        "logger": _YTLogger(),
        "progress_hooks": [progress_hook],
    }
    if ffmpeg_dir:
        base_common["ffmpeg_location"] = ffmpeg_dir

    def _attempt(common):
        if mode == "video":
            outtmpl = str(Path(output_dir) / f"{timestamp}_%(title).80s.%(ext)s")
            opts = {
                **common,
                "format": (
                    "bestvideo[ext=mp4][vcodec^=avc]+bestaudio[ext=m4a]/"
                    "bestvideo[ext=mp4][vcodec^=avc]+bestaudio/"
                    "bestvideo[vcodec^=avc]+bestaudio/"
                    "bestvideo+bestaudio/"
                    "best"
                ),
                "merge_output_format": "mp4",
                "format_sort": ["res", "ext:mp4:m4a"],
                "outtmpl": outtmpl,
                "restrictfilenames": True,
                "windowsfilenames": True,
                "postprocessors": [{
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                }],
                "postprocessor_args": {
                    "videoconvertor": ["-vcodec", "libx264", "-acodec", "aac"],
                    "merger": ["-c:v", "copy", "-c:a", "aac"],
                },
            }
            log("Получаю информацию о видео...")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if not os.path.exists(filename):
                    filename = str(Path(filename).with_suffix(".mp4"))
            log(f"Сохранено: {filename}")
            return filename, None

        elif mode == "audio":
            outtmpl = str(Path(output_dir) / f"{timestamp}_%(title).80s.%(ext)s")
            opts = {
                **common,
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "restrictfilenames": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                }],
            }
            log("Получаю информацию об аудио...")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                base_name = ydl.prepare_filename(info)
            filename = str(Path(base_name).with_suffix(".mp3"))

            if denoise and os.path.exists(filename):
                log(f"Передаю в audio-separator: {Path(filename).name}")
                vocals_out = str(Path(filename).with_suffix("")) + "_vocals.mp3"
                _separate_vocals(filename, vocals_out, log)
                os.remove(filename)
                filename = vocals_out

            log(f"Сохранено: {filename}")
            return filename, None

        else:  # split — видео как есть + аудио отдельно
            outtmpl = str(Path(output_dir) / f"{timestamp}_%(title).80s.%(ext)s")
            opts = {
                **common,
                "format": (
                    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                    "bestvideo[ext=mp4]+bestaudio/"
                    "bestvideo+bestaudio/best"
                ),
                "merge_output_format": "mp4",
                "outtmpl": outtmpl,
                "restrictfilenames": True,
            }
            log("Скачиваю видео в максимальном качестве...")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if not os.path.exists(filename):
                    filename = str(Path(filename).with_suffix(".mp4"))

            video_out, audio_out = _split_video_audio(filename, output_dir, denoise, log)
            try:
                os.remove(filename)
            except Exception:
                pass
            return video_out, audio_out

    def _fail(exc, browser_name):
        """Сырой текст — в лог, наружу — понятное сообщение."""
        raw = str(exc)
        kind = classify_download_error(raw, dpapi_seen["v"])
        write_log_file(f"yt-dlp: ошибка ({browser_name or 'без куки'}): {raw}")
        return kind, raw

    # ── cookies.txt — приоритет над cookies-from-browser ────────────────────
    if cookies_file:
        common = {**base_common, "cookiefile": cookies_file}
        try:
            result = _attempt(common)
            on_cookies("cookies.txt")
            return result
        except Exception as e:
            kind, raw = _fail(e, "cookies.txt")
            raise RuntimeError(friendly_download_error(kind, None, raw)) from e

    # ── Авто: только установленные браузеры, следующий при ошибке куки или «войдите» ──
    if browser == AUTO_BROWSER:
        candidates = cookie_browser_candidates()
        if not candidates:
            log("Браузеров с профилем не найдено — скачиваю без куки")
            try:
                result = _attempt(base_common)
                on_cookies("без куки")
                return result
            except Exception as e:
                kind, raw = _fail(e, None)
                raise RuntimeError(friendly_download_error(kind, None, raw)) from e
        failures = []
        for i, b in enumerate(candidates, start=1):
            dpapi_seen["v"] = False
            common = {**base_common, "cookiesfrombrowser": (b,)}
            name = COOKIE_BROWSER_LABELS.get(b, b)
            try:
                log(f"Пробую куки из браузера: {name} ({i}/{len(candidates)})...")
                result = _attempt(common)
                on_cookies(name)
                return result
            except Exception as e:
                kind, raw = _fail(e, name)
                failures.append((b, kind, raw))
                if kind == "video":          # видео недоступно — другой браузер не поможет
                    raise RuntimeError(friendly_download_error(kind, b, raw)) from e
                log(f"⚠ {name}: {friendly_download_error(kind, b, raw)[:120]} — пробую следующий браузер")
        raise RuntimeError(summarize_cookie_failures(failures))

    # ── Конкретный браузер, выбранный вручную ────────────────────────────────
    if browser:
        common = {**base_common, "cookiesfrombrowser": (browser.lower(),)}
        name = COOKIE_BROWSER_LABELS.get(browser.lower(), browser)
        try:
            result = _attempt(common)
            on_cookies(name)
            return result
        except Exception as e:
            kind, raw = _fail(e, name)
            raise RuntimeError(friendly_download_error(kind, browser.lower(), raw)) from e

    # ── Без cookies ───────────────────────────────────────────────────────
    try:
        result = _attempt(base_common)
        on_cookies("без куки")
        return result
    except Exception as e:
        kind, raw = _fail(e, None)
        raise RuntimeError(friendly_download_error(kind, None, raw)) from e


def _separate_vocals(input_path, output_path, log):
    """Выделяет голос через audio-separator. Сохраняет mp3 в output_path."""
    import sys as _sys
    try:
        from audio_separator.separator import Separator
    except ImportError:
        log("Устанавливаю audio-separator...")
        subprocess.run(
            [_sys.executable, "-m", "pip", "install", "audio-separator[cpu]"],
            check=True,
        )
        from audio_separator.separator import Separator

    in_p = Path(input_path)
    log(f"[audio-separator] Входной файл: {in_p.name}"
        f" ({in_p.suffix.upper() or 'нет расширения'})")

    tmp_dir = tempfile.mkdtemp()
    try:
        # ШАГ 1: конвертируем в WAV — audio-separator работает лучше с WAV
        wav_path = str(Path(tmp_dir) / "input.wav")
        log("[audio-separator] ШАГ 1: конвертирую в WAV 44100 Hz...")
        run([ffmpeg_bin(), "-y", "-i", input_path, "-ar", "44100", "-ac", "2", wav_path])
        if not os.path.exists(wav_path):
            raise RuntimeError(f"ffmpeg не создал WAV: {wav_path}")
        log(f"[audio-separator] WAV готов: {Path(wav_path).name}")

        # ШАГ 2: разделяем голос и музыку
        log("[audio-separator] ШАГ 2: запускаю разделение...")
        sep = Separator(output_dir=tmp_dir)
        sep.load_model()
        files = sep.separate(wav_path)

        log(f"[audio-separator] Сгенерированные файлы: {[Path(f).name for f in files]}")

        # ШАГ 3: ищем файл с голосом (Vocals в имени)
        vocals = None
        for f in files:
            full = f if Path(f).is_absolute() else str(Path(tmp_dir) / Path(f).name)
            if "vocal" in Path(full).name.lower() and os.path.exists(full):
                vocals = full
                break

        if not vocals:
            raise RuntimeError(
                f"audio-separator: файл с голосом не найден. "
                f"Файлы: {[Path(f).name for f in files]}"
            )
        log(f"[audio-separator] ШАГ 3: голос найден: {Path(vocals).name}")

        # ШАГ 4: конвертируем результат в mp3
        run([ffmpeg_bin(), "-y", "-i", vocals, "-q:a", "0", output_path])
        log(f"Голос готов: {Path(output_path).name}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _split_video_audio(video_path, output_dir, denoise, log):
    """
    Разделяет видео на:
      - VIDEO.mp4  — оригинальное видео со всем оригинальным звуком
      - AUDIO.mp3  — только аудиодорожка, с денойзом если выбрано
    """
    ensure_dir(output_dir)
    base = safe_name(Path(video_path).stem)[:60]
    video_out = str(Path(output_dir) / (base + "_VIDEO.mp4"))
    audio_out = str(Path(output_dir) / (base + "_AUDIO.mp3"))

    safe_video = str(Path(tempfile.mkdtemp()) / "input.mp4")
    shutil.copy2(video_path, safe_video)

    log("Копирую оригинальное видео (с голосом)...")
    shutil.copy2(video_path, video_out)

    log("Извлекаю аудио в максимальном качестве...")
    try:
        subprocess.run([
            ffmpeg_bin(), "-y", "-i", safe_video,
            "-vn", "-acodec", "libmp3lame", "-q:a", "0", audio_out
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        try:
            tmp_wav = audio_out + ".wav"
            subprocess.run([
                ffmpeg_bin(), "-y", "-i", safe_video,
                "-vn", "-acodec", "pcm_s16le", "-ar", "44100", tmp_wav
            ], check=True, capture_output=True)
            subprocess.run([
                ffmpeg_bin(), "-y", "-i", tmp_wav,
                "-acodec", "libmp3lame", "-q:a", "0", audio_out
            ], check=True, capture_output=True)
            try:
                os.remove(tmp_wav)
            except Exception:
                pass
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Не удалось извлечь аудио: {e}")

    if denoise:
        log(f"Передаю в audio-separator: {Path(audio_out).name}")
        vocals_out = str(Path(output_dir) / (base + "_vocals.mp3"))
        _separate_vocals(audio_out, vocals_out, log)
        os.remove(audio_out)
        audio_out = vocals_out

    try:
        os.remove(safe_video)
    except Exception:
        pass

    if not file_exists_ok(video_out):
        raise RuntimeError(f"Не удалось сохранить видео: {video_out}")
    if not file_exists_ok(audio_out):
        raise RuntimeError(f"Не удалось создать аудиодорожку: {audio_out}")
    return video_out, audio_out


def make_neon_logo(text="SheqelMotion", width=720, height=140):

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    font = None
    font_size = 70
    candidates = []
    if sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
    ]

    elif sys.platform == "win32":
        
        candidates = [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
    ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    for path in candidates:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, font_size)
                break
            except Exception:
                continue

    if font is None:
        font = ImageFont.load_default()

    temp = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(temp)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    x = (width - text_w) // 2
    y = (height - text_h) // 2

    # Electric neon: deep blue outer → cyan mid → white-blue core
    for blur, alpha in [(32, 70), (22, 110), (14, 150)]:
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.text((x, y), text, font=font, fill=(0, 60, 200, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(blur))
        img.alpha_composite(glow)

    for blur, alpha in [(9, 180), (5, 210)]:
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.text((x, y), text, font=font, fill=(0, 160, 255, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(blur))
        img.alpha_composite(glow)

    for blur, alpha in [(3, 235), (1, 255)]:
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.text((x, y), text, font=font, fill=(80, 210, 255, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(blur))
        img.alpha_composite(glow)

    draw = ImageDraw.Draw(img)
    draw.text((x, y), text, font=font, fill=(200, 235, 255, 255))

    return img


try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

_BaseApp = TkinterDnD.Tk if HAS_DND else ctk.CTk


def delete_elevenlabs_voice(voice_id, api_key):
    url = f"{ELEVEN_BASE_URL}/v1/voices/{voice_id}"
    headers = {"xi-api-key": api_key}
    res = requests.delete(url, headers=headers, timeout=30)
    return res.status_code in [200, 204, 404]   # 404 — голоса уже нет, запись можно убрать


def fetch_elevenlabs_balance(api_key) -> dict | None:
    try:
        headers = {"xi-api-key": api_key}
        print("[balance] запрос баланса ElevenLabs")
        res = requests.get(
            f"{ELEVEN_BASE_URL}/v1/user/subscription",
            headers=headers, timeout=10,
        )
        print(f"[balance] статус: {res.status_code}")
        print(f"[balance] ответ: {res.text[:300]}")
        if res.status_code != 200:
            return None
        data = res.json()
        character_count = data.get("character_count", 0)
        character_limit = data.get("character_limit", 0)
        tier = data.get("tier", "")
        next_reset = data.get("next_character_count_reset_unix", 0)
        remaining = max(0, character_limit - character_count)
        return {
            "used": character_count,
            "limit": character_limit,
            "remaining": remaining,
            "tier": tier,
            "next_reset": next_reset,
        }
    except Exception:
        return None


def translate_with_groq(text, target_lang, api_key, log) -> str:
    import requests

    lang_names = {
        "RU": "русский", "EN": "английский", "UK": "украинский",
        "DE": "немецкий", "FR": "французский", "ES": "испанский",
        "IT": "итальянский", "PL": "польский", "PT": "португальский",
        "ZH": "китайский", "JA": "японский", "KO": "корейский",
        "AR": "арабский", "TR": "турецкий", "HI": "хинди",
        "NL": "нидерландский", "SV": "шведский", "CS": "чешский",
        "RO": "румынский", "HU": "венгерский", "LT": "литовский",
        "ET": "эстонский", "LV": "латышский",
    }
    lang_name = lang_names.get(target_lang, target_lang)

    log(f"Перевожу на {lang_name} через Groq LLaMA...")

    system_prompt = (
        f"Ты профессиональный переводчик. Переведи текст на {lang_name} язык. "
        f"Сохраняй стиль, тон и смысл оригинала. "
        f"Если текст рекламный — адаптируй под культуру языка. "
        f"Верни ТОЛЬКО переведённый текст без пояснений и комментариев. "
        f"Если в тексте есть разделители ------ — сохраняй их на тех же местах."
    )

    payload = {
        "model": GROQ_TRANSLATE_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }

    last_err = None
    for attempt in range(1, GROQ_TRANSLATE_RETRIES + 1):
        res = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )

        if res.status_code == 401:
            raise RuntimeError("Groq API key неверный")

        if res.status_code == 429:
            wait = 5.0
            try:
                wait = max(wait, float(res.headers.get("retry-after", wait)))
            except ValueError:
                pass
            last_err = RuntimeError(f"Groq error 429: {res.text[:300]}")
            if attempt < GROQ_TRANSLATE_RETRIES:
                log(f"Groq: превышен лимит запросов, пауза {wait:.0f} сек "
                    f"(попытка {attempt}/{GROQ_TRANSLATE_RETRIES})...")
                time.sleep(wait)
                continue
            raise last_err

        if res.status_code != 200:
            raise RuntimeError(f"Groq error {res.status_code}: {res.text[:300]}")

        result = res.json()["choices"][0]["message"]["content"].strip()
        log(f"Переведено: {len(result)} символов")
        return result

    raise last_err


def fetch_groq_usage(api_key) -> dict | None:
    try:
        import requests
        res = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if res.status_code != 200:
            return None

        headers_lower = {k.lower(): v for k, v in res.headers.items()}

        for key_remaining in [
            "x-ratelimit-remaining-audio-seconds",
            "x-ratelimit-remaining-tokens",
            "ratelimit-remaining",
        ]:
            for key_limit in [
                "x-ratelimit-limit-audio-seconds",
                "x-ratelimit-limit-tokens",
                "ratelimit-limit",
            ]:
                remaining = headers_lower.get(key_remaining)
                limit = headers_lower.get(key_limit)
                if remaining and limit:
                    try:
                        r = float(remaining)
                        l = float(limit)
                        return {
                            "remaining_hours": r / 3600,
                            "limit_hours": l / 3600,
                            "remaining_sec": r,
                            "raw_remaining": remaining,
                            "raw_limit": limit,
                        }
                    except Exception:
                        pass

        return {
            "remaining_hours": None,
            "limit_hours": 8.0,
            "remaining_sec": None,
            "plan": "free",
        }
    except Exception:
        return None


def transcribe_with_groq(audio_path, api_key, log) -> tuple:
    import requests

    size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    log(f"Транскрибирую {Path(audio_path).name} ({size_mb:.1f} MB)...")

    working_path = audio_path
    tmp_path = None

    if size_mb > 24:
        log("Файл большой — сжимаю аудио...")
        tmp_path = str(Path(tempfile.mkdtemp()) / "audio_compressed.mp3")
        subprocess.run([
            ffmpeg_bin(), "-y", "-i", audio_path,
            "-vn", "-ar", "16000", "-ac", "1",
            "-b:a", "64k", tmp_path
        ], check=True, capture_output=True)
        working_path = tmp_path
    elif Path(audio_path).suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        log("Извлекаю аудио из видео...")
        tmp_path = str(Path(tempfile.mkdtemp()) / "audio_extracted.mp3")
        subprocess.run([
            ffmpeg_bin(), "-y", "-i", audio_path,
            "-vn", "-ar", "16000", "-ac", "1", "-q:a", "0", tmp_path
        ], check=True, capture_output=True)
        working_path = tmp_path

    try:
        try:
            dur_result = subprocess.run(
                [ffprobe_bin(), "-v", "error", "-show_entries",
                 "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                 working_path],
                capture_output=True, encoding="utf-8"
            )
            duration_sec = float(dur_result.stdout.strip())
        except Exception:
            duration_sec = 0.0

        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}

        with open(working_path, "rb") as f:
            files = {"file": (Path(working_path).name, f, "audio/mpeg")}
            data = {
                "model": "whisper-large-v3",
                "response_format": "text",
            }
            res = requests.post(url, headers=headers,
                                files=files, data=data, timeout=120)

        if res.status_code == 401:
            raise RuntimeError("Groq API key неверный")
        if res.status_code == 413:
            raise RuntimeError("Файл слишком большой для Groq (макс 25MB)")
        if res.status_code != 200:
            raise RuntimeError(f"Groq error {res.status_code}: {res.text[:300]}")

        text = res.text.strip()
        log(f"Транскрибировано: {len(text)} символов")
        return text, duration_sec
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


class LipsyncTwoModeApp(_BaseApp):
    def __init__(self):
        super().__init__()
        self.title("SheqelMotion Studio")
        set_adaptive_window(self, min_height=MAIN_MIN_HEIGHT)
        self.resizable(True, True)
        self.configure(fg_color=BG)
        # Принудительно поднимаем окно на передний план (фикс для macOS)
        self.update_idletasks()
        self.lift()
        self.attributes('-topmost', True)
        self.after(200, lambda: self.attributes('-topmost', False))
        self.focus_force()
        # Фикс: клик по иконке в Dock восстанавливает свёрнутое/скрытое окно
        try:
            self.createcommand('tk::mac::ReopenApplication', self._reopen_window)
        except Exception:
            pass
        self.api_key = ""
        self._el_balance = None
        self.main_voice_id = ""
        self.main_voice_name = ""
        self.rolls = []
        self.next_roll_id = 1
        self.selected_roll_id = None
        self._active_panel = None
        self._stop_download = False
        self._detected_browser = None
        self._browser_checked = False
        self.log_queue = queue.Queue()
        self.is_processing = False
        self.sync_key = SYNC_API_KEY
        self.cloudinary_cloud_name = ""
        self.cloudinary_api_key = ""
        self.cloudinary_api_secret = ""
        self.groq_api_key = ""
        self.groq_used_seconds = 0.0
        self._groq_lock = threading.Lock()          # счётчик пишут потоки транскрибации
        self.preview_process = None
        self.preview_voice_id = None
        self.voice_expiry_list = []
        self._expiry_lock = threading.RLock()       # список правят поток автоудаления и клонирование
        self._config_lock = threading.Lock()
        self._config_load_failed = False
        self.install_id = ""
        self.error_reports_enabled = True
        self.error_reports_notice_shown = False
        self.error_reports_var = ctk.BooleanVar(value=True)
        self.clear_text_after_clone = True
        self.clear_text_after_clone_var = ctk.BooleanVar(value=True)
        self.spellcheck_online = True            # LanguageTool: текст генератора уходит на languagetool.org
        self.spellcheck_online_var = ctk.BooleanVar(value=True)
        self.cost_confirm_enabled = True
        self.cost_confirm_threshold = COST_CONFIRM_DEFAULT_THRESHOLD
        self.queue_panel_mode = "running"
        self.cost_confirm_var = ctk.BooleanVar(value=True)
        self._estimates = {}
        self._estimate_lock = threading.Lock()
        self._saved_sync_price = None
        self.sync_fps_mode = "source"
        # Явный выбор хранилища (None — не выбирали). Без выбора: Cloudinary, если он уже
        # настроен (поведение существующих установок не меняется), иначе Sync.
        self._sync_storage_choice = None
        self.sync_storage = "sync"
        self._scene_estimate_label = None
        self._queue_progress = {}
        self._progress_lock = threading.Lock()
        self._log_lines = collections.deque(maxlen=LOG_PANEL_MAX_LINES)   # [(номер, строка)]
        self._log_seq = 0
        self._queue_summary = None
        self._queue_batch_dir = None
        self._qp = None
        self._qp_w = {}
        self._qp_forced = False
        self._qp_details_open = False
        # Этап 2: панели живут всё время работы, состояние — в менеджере задач и на диске.
        self.background_tasks = BackgroundTaskManager()
        self._panels = {}
        self._panel_renderers = {}
        self._panel_state = {}
        self._state_store = AppStateStore()
        self._state_ready = False        # до восстановления состояние не сохраняем
        self._state_save_job = None
        self._sync_journal = []
        self._journal_lock = threading.Lock()
        self._journal_dirty = False
        self._sync_fetch_widgets = None
        self._sync_fetch_pending = set()
        self._vg = None                  # виджеты вкладки генерации; None, пока вкладка не построена
        self._clone_pending = set()      # задачи клонирования, итог которых ещё не применён
        self._clone_cleanup = None       # очистка извлечённого аудио (из вкладки клонирования)
        self._notice_w = {}
        # Сжатие видео: параметры файлов, оценки размера (кэш), запущенные ffmpeg
        self._compress_lock = threading.Lock()
        self._compress_info = {}
        self._compress_probing = set()
        self._compress_est = {}
        self._compress_est_queue = []
        self._compress_est_gen = 0
        self._compress_est_running = False
        self._compress_est_current = None
        self._compress_est_proc = None
        self._compress_procs = {}
        self._compress_dirty = False
        self._compress_caps_ready = False
        self._closing = False
        self._panel_renderers["scene"] = self._render_sync_fetch
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == "darwin":
            try:
                self.createcommand("::tk::mac::Quit", self._on_close)
            except Exception:
                pass
        self._nav_buttons = {}
        self._nav_sections = {}
        self._key_dots = {}
        self._balance_lbl = None
        self._settings_widgets = None
        self.build_ui()
        self.add_roll()
        self.poll_logs()
        self.load_config()
        self._init_error_reporting()
        self.cost_confirm_var.set(self.cost_confirm_enabled)
        self.clear_text_after_clone_var.set(self.clear_text_after_clone)
        self.spellcheck_online_var.set(self.spellcheck_online)
        self._saved_sync_price = sync_price_state["per_frame"]
        self._restore_state()
        self._apply_nav_collapsed()
        global sync_job_hook
        sync_job_hook = self._on_sync_journal_event
        self.update_status()
        self.after(3000, lambda: self.check_for_updates(silent=True))
        self.after(500, self._check_just_updated)
        self.after(2000, self._cleanup_expired_voices_on_start)
        self.after(2000, self._check_elevenlabs)
        self.after(3000, self._refresh_balance_badge)
        self.after(4000, self._check_ytdlp_update)
        threading.Thread(target=self._voice_expiry_watcher, daemon=True).start()


    def _reopen_window(self):
        """Вызывается при клике на иконку Dock — восстанавливает скрытое/свёрнутое окно."""
        self.deiconify()
        self.lift()
        for child in self.winfo_children():
            if isinstance(child, ctk.CTkToplevel) and child.winfo_exists():
                child.deiconify()
                child.lift()
                child.focus_force()
                return
        self.focus_force()

    def _init_error_reporting(self):
        if not self.install_id:
            self.install_id = str(uuid.uuid4())
            self.save_config()
        self.error_reports_var.set(self.error_reports_enabled)
        error_reporter.configure(self.install_id, self.error_reports_enabled)
        # Пока URL пуст, отправка выключена — уведомление покажем, когда она реально заработает.
        if ERROR_REPORT_URL and not self.error_reports_notice_shown:
            self.after(2500, self._show_error_report_notice)

    def _on_error_reports_toggle(self):
        self.error_reports_enabled = bool(self.error_reports_var.get())
        error_reporter.configure(self.install_id, self.error_reports_enabled)
        self.save_config()
        self.log(f"Отчёты об ошибках: {'включены' if self.error_reports_enabled else 'выключены'}")

    # ── Оценка стоимости ────────────────────────────────────────────────
    def _estimate_for(self, roll, wait=False):
        """Оценка из кэша; если её нет — считаем в фоне (wait=False) или прямо сейчас (wait=True).
        Возвращает None, если у ролика не хватает файлов для оценки."""
        target_fps = sync_target_fps(self.sync_fps_mode)
        key = estimate_cache_key(roll, target_fps)
        if key is None:
            return None
        with self._estimate_lock:
            cached = self._estimates.get(key)
            if cached and (not cached.get("pending") or not wait):
                return cached
            if not cached:
                self._estimates[key] = {"pending": True}
        snapshot = dict(roll)

        def compute():
            try:
                result = estimate_roll(snapshot, self.sync_key, target_fps)
            except Exception as e:
                result = {"error": str(e)}
            with self._estimate_lock:
                self._estimates[key] = result
            return result

        if wait:
            return compute()

        def work():
            compute()
            self.after(0, self._on_estimates_updated)

        threading.Thread(target=work, daemon=True).start()
        return {"pending": True}

    def _estimate_short_text(self, est):
        if est is None:
            return ""
        if est.get("pending"):
            return "оценка…"
        if est.get("error"):
            return "оценка недоступна"
        return f"≈ ${est['cost']:.2f} · ≈ {format_minutes(est['time'])}"

    def _estimate_detail_text(self, est):
        if est is None:
            return "Оценка: выбери голос и видео"
        if est.get("pending"):
            return "Оценка: считаю…"
        if est.get("error"):
            return f"Оценка недоступна: {est['error'][:120]}"
        names = {"single": "Видео", "start": "Начало", "end": "Конец"}
        lines = []
        for p in est["parts"]:
            fps_text = (f"{p['source_fps']:g}→{p['fps']:g} fps" if abs(p["source_fps"] - p["fps"]) > 0.01
                        else f"{p['fps']:g} fps")
            line = (f"{names[p['kind']]}: {p['billable']:.1f} с · {fps_text} · "
                    f"{p['width']}x{p['height']} · {p['frames']} кадров · ≈ ${p['cost']:.2f}")
            if p["note"]:
                line += f" ({p['note']})"
            if p["video_shorter"]:
                line += " · ⚠ видео короче аудио — Sync проиграет его туда-обратно"
            lines.append(line)
        lines.append(f"Итого: ≈ ${est['cost']:.2f} ({format_estimate_source(est['source'])}) · "
                     f"время ≈ {format_minutes(est['time'])} (оценка)")
        return "\n".join(lines)

    def _queue_estimate_text(self):
        valid, _ = self.validate_rolls()
        if not valid:
            return ""
        ests = [self._estimate_for(r) for _, r in valid]
        if any(e and e.get("pending") for e in ests):
            return "очередь: оценка…"
        known = [e for e in ests if e and not e.get("error")]
        if not known:
            return "очередь: оценка недоступна"
        total = sum(e["cost"] for e in known)
        times = estimate_queue_time([e["time"] for e in known])
        text = f"очередь ≈ ${total:.2f} · ≈ {format_minutes(times)}"
        if len(known) < len(ests):
            text += " (без части роликов)"
        return text

    def _persist_sync_price(self):
        """Последнюю цену за кадр из ответа Sync сохраняем в конфиг — для локальной оценки."""
        price = sync_price_state["per_frame"]
        if price and price != self._saved_sync_price:
            self._saved_sync_price = price
            self.save_config()

    def _on_sync_storage_change(self, label):
        self._sync_storage_choice = SYNC_STORAGE_MODES[label]
        self.sync_storage = self._sync_storage_choice
        self.save_config()
        self.log(f"Хранилище для Sync: {label}")
        if self._settings_widgets:
            self._render_settings()   # подсказка «Выбрано вами»

    def _on_sync_fps_change(self, label):
        self.sync_fps_mode = SYNC_FPS_MODES[label]
        self.save_config()
        self._on_estimates_updated()

    def _on_estimates_updated(self):
        self._persist_sync_price()
        self.render_scene_list()
        self.update_status()
        lbl = self._scene_estimate_label
        if lbl is not None and lbl.winfo_exists() and self._active_panel == "scene":
            roll = next((r for r in self.rolls if r["id"] == self.selected_roll_id), None)
            if roll is not None:
                lbl.configure(text=self._estimate_detail_text(self._estimate_for(roll)))

    def _on_cost_confirm_toggle(self):
        self.cost_confirm_enabled = bool(self.cost_confirm_var.get())
        self.save_config()

    def _on_cost_threshold_change(self, entry):
        try:
            value = float(entry.get().replace(",", ".").strip())
            if value < 0:
                raise ValueError
        except ValueError:
            entry.delete(0, "end")
            entry.insert(0, f"{self.cost_confirm_threshold:g}")
            return
        self.cost_confirm_threshold = value
        self.save_config()

    def _show_error_report_notice(self):
        messagebox.showinfo(
            "Отчёты об ошибках",
            "При сбое программа отправит разработчику короткий обезличенный отчёт.\n\n"
            "Отправляется: версия программы, ОС, тип ошибки и место в коде, код ответа сервиса, "
            "очищенные строки технического лога (статусы и коды) и случайный номер установки.\n\n"
            "Не отправляется: API-ключи, тексты сценариев, переводов и транскриптов, имена голосов "
            "и роликов, пути и файлы.\n\n"
            "Отключить: Настройки → Приложение → «Отправлять разработчику обезличенные отчёты». "
            "Там же — «Показать пример отчёта».\n\n"
            "Проверка орфографии в генераторе голоса отправляет текст на languagetool.org; "
            "её можно выключить там же.",
            parent=self,
        )
        self.error_reports_notice_shown = True
        self.save_config()

    def _on_close(self):
        if self.is_processing:
            if not messagebox.askyesno(
                "Очередь ещё идёт",
                "Идёт обработка очереди.\n\n"
                "Если закрыть приложение сейчас, задачи Sync доработают на сервере "
                "(и будут оплачены), но результаты не скачаются.\n\n"
                "Всё равно закрыть?",
                icon="warning", parent=self,
            ):
                return
            self.log("Приложение закрыто пользователем во время обработки очереди")
        if self.background_tasks.active(kind="compress"):
            if not messagebox.askyesno(
                "Идёт сжатие видео",
                "Сжатие остановится, недописанный файл будет удалён. Уже готовые файлы останутся.\n\n"
                "Закрыть?", icon="warning", parent=self,
            ):
                return
            self.log("Приложение закрыто во время сжатия видео — ffmpeg остановлен")
        self._compress_stop_all_on_close()
        if self._state_save_job is not None:
            self.after_cancel(self._state_save_job)
        self._save_state_now()
        release_instance_lock()
        self.destroy()

    def _parse_version(self, v):
        #Превращает '1.2.10' в (1, 2, 10) для корректного сравнения.
        try:
            return tuple(int(x) for x in str(v).split(".") if x.isdigit())
        except Exception:
            return (0, 0, 0)

    def check_for_updates(self, silent=False):
        """Только проверяет манифест. Сам файл не качает."""
        try:
            url = f"{UPDATE_MANIFEST_URL}?t={int(time.time())}"
            res = requests.get(url, timeout=10, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            res.raise_for_status()
            manifest = res.json()
        except Exception as e:
            if not silent:
                messagebox.showerror("Ошибка проверки",
                                     f"Не удалось проверить обновления:\n{e}")
            return

        latest = manifest.get("latest_version", "0.0.0")
        notes = manifest.get("release_notes", "")
        force = manifest.get("force_update", False)
        min_required = manifest.get("min_required_version", "0.0.0")
        expected_sha256 = manifest.get("sha256", "")

        current_v = self._parse_version(APP_VERSION)
        latest_v = self._parse_version(latest)
        min_v = self._parse_version(min_required)

        if latest_v <= current_v:
            if not silent:
                messagebox.showinfo("Обновления",
                                    f"У тебя последняя версия: {APP_VERSION}")
            return

        self._show_update_dialog(latest, notes, force or (current_v < min_v), expected_sha256)

    def _show_update_dialog(self, latest_version, notes, is_forced, expected_sha256=""):
        win = ctk.CTkToplevel(self)
        win.title("Доступно обновление")
        win.geometry("560x520")
        win.configure(fg_color=BG)
        win.transient(self)
        win.after(100, win.grab_set)
        win.protocol("WM_DELETE_WINDOW", win.destroy)

        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self.label(frame, "🎉 Доступно обновление", size=22, weight="bold").pack(anchor="w", padx=20, pady=(20, 8))
        self.label(frame, f"Текущая версия:  {APP_VERSION}",
                   size=12, color=MUTED).pack(anchor="w", padx=20)
        self.label(frame, f"Новая версия:  {latest_version}",
                   size=15, weight="bold", color="#86efac").pack(anchor="w", padx=20, pady=(0, 14))

        self.label(frame, "Что нового:", size=13, weight="bold").pack(anchor="w", padx=20)
        notes_box = ctk.CTkTextbox(frame, fg_color="#1a1a1a", text_color="#e5e5e5",
                                   border_width=0, height=130,
                                   font=ctk.CTkFont(size=12), wrap="word")
        notes_box.pack(fill="both", expand=True, padx=20, pady=(6, 10))
        notes_box.insert("1.0", notes or "—")
        notes_box.configure(state="disabled")

        progress_label = self.label(frame, "", size=12, color=MUTED)
        progress_label.configure(wraplength=480)
        progress_label.pack(anchor="w", fill="x", padx=20, pady=(0, 6))
        error_label = self.label(frame, "", size=11, color="#fca5a5")
        error_label.configure(wraplength=480)

        btn_row = ctk.CTkFrame(frame, fg_color=PANEL)
        btn_row.pack(fill="x", padx=20, pady=(0, 16))
        failures = {"n": 0}

        def set_status(text):
            # Вызывается из фонового потока — виджеты трогает только главный поток.
            self.after(0, lambda: progress_label.winfo_exists() and progress_label.configure(text=text))

        def on_failed(err):
            failures["n"] += 1
            update_btn.configure(state="normal")
            progress_label.configure(text="")
            messagebox.showerror("Ошибка обновления", str(err), parent=win)
            if is_forced and failures["n"] >= 2 and not later_btn_ref:
                # Обязательное обновление дважды не удалось — даём выйти, чтобы не заблокировать программу.
                error_label.configure(text=f"Не удалось обновиться дважды подряд: {err}")
                error_label.pack(anchor="w", fill="x", padx=20, pady=(0, 6), before=btn_row)
                later = self.button(btn_row, "Закрыть, обновлю позже", win.destroy,
                                    color=BTN, hover=BTN_HOVER, width=220)
                later.pack(side="right", padx=(8, 0))
                later_btn_ref.append(later)
                win.protocol("WM_DELETE_WINDOW", win.destroy)

        def do_update():
            if getattr(sys, "frozen", False):
                messagebox.showinfo("Обновление", f"Доступна версия {latest_version}.\n\n"
                                    "Автообновление недоступно в .exe/.app сборке.\n"
                                    "Скачай новый файл вручную с GitHub и замени текущий.", parent=win)
                win.destroy()
                return
            if self.is_processing:
                messagebox.showwarning("Обновление", "Идёт очередь роликов. Дождись её окончания и обнови.",
                                       parent=win)
                return
            update_btn.configure(state="disabled")
            app_dir = app_base_dir()

            def run():
                try:
                    app_path = install_verified_update(app_dir, expected_sha256, latest_version,
                                                       on_status=set_status)
                except UpdateError as e:
                    self.log(f"Обновление не применено ({e.kind}): {e}")
                    self.after(0, lambda err=e: on_failed(err))
                    return
                except Exception as e:
                    self.after(0, lambda err=e: on_failed(err))
                    return
                set_status("Перезапускаюсь...")
                self.after(300, lambda: self._restart_after_update(app_path))   # только главный поток

            threading.Thread(target=run, daemon=True).start()

        later_btn_ref = []
        if not is_forced:
            self.button(btn_row, "Позже", win.destroy,
                        color=BTN, hover=BTN_HOVER, width=120).pack(side="right", padx=(8, 0))

        update_btn = self.button(btn_row, "Скачать обновление", do_update,
                                 color=BTN_OK, hover=BTN_OK_HOVER, width=220)
        update_btn.pack(side="right")

        if is_forced:
            self.label(frame, "⚠ Это обязательное обновление.",
                       size=11, color="#fca5a5").pack(anchor="w", padx=20, pady=(0, 6))
            win.protocol("WM_DELETE_WINDOW", lambda: None)
        self._update_dialog = win
        return win

    def _restart_after_update(self, app_path):
        """Главный поток. Сохранить состояние, отпустить замок, запустить новую версию с --after-update."""
        try:
            self.stop_preview()
        except Exception:
            pass
        try:
            self._compress_stop_all_on_close()
            if self._state_save_job is not None:
                self.after_cancel(self._state_save_job)
            self._save_state_now()
        except Exception as e:
            write_log_file(f"Перед перезапуском не удалось сохранить состояние: {e}")
        release_instance_lock()
        write_log_file("Перезапуск после обновления")
        python_exe = sys.executable
        args = [app_path, "--after-update"]
        if platform_is_windows():
            # pythonw — без окна консоли; новый процесс ждёт, пока этот завершится (замок).
            pyw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
            exe = pyw if os.path.exists(pyw) else python_exe
            subprocess.Popen([exe] + args, cwd=os.path.dirname(app_path),
                             creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | NEW_PROCESS_GROUP
            self.destroy()
            os._exit(0)
            return
        try:
            os.execv(python_exe, [python_exe] + args)
        except Exception as e:
            write_log_file(f"os.execv не удался ({e}), запускаю новый процесс")
            subprocess.Popen([python_exe] + args, cwd=os.path.dirname(app_path))
            self.destroy()
            os._exit(0)

    def _check_just_updated(self):
        """Если на прошлом старте было обновление — показываем toast."""
        flag_path = Path(app_base_dir()) / ".just_updated"
        if flag_path.exists():
            try:
                with open(flag_path, "r", encoding="utf-8") as f:
                    updated_to = f.read().strip()
                os.remove(flag_path)
                # Удаляем бэкап если всё ок
                backup = Path(app_base_dir()) / "app.py.bak"
                if backup.exists():
                    try:
                        os.remove(backup)
                    except Exception:
                        pass
                self.after(800, lambda: messagebox.showinfo(
                    "✓ Обновлено",
                    f"Приложение успешно обновлено до версии {updated_to}"
                ))
            except Exception:
                pass

    def _check_elevenlabs(self):
        if not self.api_key:
            return

        def run():
            headers = {"xi-api-key": self.api_key}
            try:
                res = requests.get(
                    f"{ELEVEN_BASE_URL}/v1/user",
                    headers=headers, timeout=10,
                )
            except Exception:
                return

            if res.status_code != 200:
                return

            try:
                models_res = requests.get(
                    f"{ELEVEN_BASE_URL}/v1/models",
                    headers=headers, timeout=10,
                )
                if models_res.status_code == 200:
                    ids = [m.get("model_id") for m in models_res.json()]
                    if ELEVEN_TTS_MODEL_ID not in ids:
                        self.after(0, lambda: messagebox.showwarning(
                            "ElevenLabs",
                            f"Твой план ElevenLabs не поддерживает {ELEVEN_TTS_MODEL_ID}.\n"
                            "Нужен план Creator или выше. Можно выбрать Eleven v3 "
                            "в панели генерации голоса.",
                        ))
            except Exception:
                pass
            self.after(0, self._refresh_balance_badge)

        threading.Thread(target=run, daemon=True).start()

    def _check_ytdlp_update(self):
        def run():
            try:
                import yt_dlp
                current = yt_dlp.version.__version__

                res = requests.get(
                    "https://pypi.org/pypi/yt-dlp/json",
                    timeout=10
                )
                if res.status_code != 200:
                    return

                latest = res.json()["info"]["version"]

                if current == latest:
                    self.log(f"yt-dlp актуален: {current}")
                    return

                self.log(f"yt-dlp устарел: {current} → {latest}. Обновляю...")

                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install",
                     "-U", "yt-dlp", "--quiet"],
                    capture_output=True,
                    encoding="utf-8",
                    timeout=120,
                )

                if result.returncode == 0:
                    self.log(f"yt-dlp обновлён до {latest} ✓")
                    self.after(0, lambda v=latest: self.status_label.configure(
                        text=f"yt-dlp обновлён до {v}"
                    ))
                    self.after(5000, self.update_status)
                else:
                    self.log(f"Ошибка обновления yt-dlp: {result.stderr[:200]}")

            except Exception as e:
                self.log(f"yt-dlp check error: {e}")

        threading.Thread(target=run, daemon=True).start()

    def button(self, parent, text, command, color=BTN_PRIMARY, hover=BTN_PRIMARY_HOVER, width=150):
        return ctk.CTkButton(parent, text=text, command=command, fg_color=color, hover_color=hover, text_color="white", corner_radius=20, height=38, width=width, font=ctk.CTkFont(size=14, weight="bold"))

    def label(self, parent, text, size=14, weight="normal", color=TEXT):
        return ctk.CTkLabel(parent, text=text, text_color=color, font=ctk.CTkFont(size=size, weight=weight), anchor="w", justify="left")

    def _fix_paste(self, widget):
        def do_paste(event=None):
            try:
                text = widget.clipboard_get()
                widget.insert("insert", text)
            except Exception:
                pass
            return "break"
        widget.bind("<Command-v>", do_paste)
        widget.bind("<Command-V>", do_paste)
        widget.bind("<Control-v>", do_paste)
        widget.bind("<Control-V>", do_paste)

    def _add_copy_menu(self, widget, get_text_fn):
        import tkinter as tk
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Копировать", command=lambda: (
            self.clipboard_clear(),
            self.clipboard_append(get_text_fn()),
        ))
        def show_menu(event):
            menu.tk_popup(event.x_root, event.y_root)
        widget.bind("<Button-2>", show_menu)
        widget.bind("<Button-3>", show_menu)

    def _show_dnd_tooltip(self, widget, msg):
        try:
            tip = ctk.CTkToplevel(self)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tip.geometry(f"+{x}+{y}")
            tip.configure(fg_color="#2d5a27")
            ctk.CTkLabel(tip, text=f"✓ {msg}", text_color="white",
                         font=ctk.CTkFont(size=12)).pack(padx=12, pady=8)
            self.after(2000, tip.destroy)
        except Exception:
            pass

    def _roll_is_locked(self, roll, parent=None, notify=True):
        """Ролик в обработке нельзя править: поток очереди уже читает его данные."""
        # Очередь пишет статус вида «Обработка 1/3», поэтому сравниваем по началу строки.
        if not str(roll.get("status", "")).startswith("Обработка"):
            return False
        if notify:
            messagebox.showwarning(
                "Ролик в обработке",
                "Этот ролик сейчас обрабатывается — изменить его можно после завершения.",
                parent=parent or self,
            )
        return True

    def _apply_dnd(self, widget, roll_fn, also_select=False):
        if not HAS_DND:
            return
        def on_drop(event):
            roll = roll_fn()
            if roll is None:
                return
            if self._roll_is_locked(roll):
                return
            if also_select:
                self.selected_roll_id = roll["id"]
            files = re.findall(r'\{[^}]+\}|\S+', event.data)
            if not files:
                return
            filepath = files[0].strip('{}')
            ext = Path(filepath).suffix.lower()
            if ext in ('.mp3', '.wav', '.m4a', '.aac'):
                roll["voice_file"] = filepath
                msg = f"Голос: {Path(filepath).name}"
            elif ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm'):
                if roll["mode"] == "1 видео":
                    roll["video_single"] = filepath
                    msg = f"Видео: {Path(filepath).name}"
                else:
                    if not roll["video_start"]:
                        roll["video_start"] = filepath
                        msg = f"Видео начало: {Path(filepath).name}"
                    else:
                        roll["video_end"] = filepath
                        msg = f"Видео конец: {Path(filepath).name}"
            else:
                return
            roll["status"] = "Ожидает"
            self.refresh_rolls()
            self._show_dnd_tooltip(widget, msg)
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind('<<Drop>>', on_drop)

    def build_ui(self):
        # Top accent strip
        strip = ctk.CTkFrame(self, height=4, fg_color="#0095ff", corner_radius=0)
        strip.pack(fill="x")
        strip.pack_propagate(False)

        # Main body: sidebar (left) + content (right)
        body = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        body.pack(fill="both", expand=True)

        # ── Sidebar ──────────────────────────────────────────────────────────
        sidebar = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=0, width=SIDEBAR_WIDTH)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Logo — render full-size, scale down via CTkImage to fit sidebar
        logo_img = make_neon_logo("SheqelMotion", 720, 120)
        logo = ctk.CTkImage(light_image=logo_img, dark_image=logo_img, size=(176, 34))
        ctk.CTkLabel(sidebar, image=logo, text="").pack(anchor="center", padx=10, pady=(20, 8))

        # Separator
        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2d3e", corner_radius=0).pack(
            fill="x", padx=12, pady=(0, 4))

        # Нижний блок закреплён (упакован первым), меню над ним прокручивается.
        sidebar_bottom = ctk.CTkFrame(sidebar, fg_color=PANEL, corner_radius=0)
        sidebar_bottom.pack(side="bottom", fill="x")
        nav_scroll = ctk.CTkScrollableFrame(sidebar, fg_color=PANEL, corner_radius=0,
                                            scrollbar_button_color=PANEL,
                                            scrollbar_button_hover_color="#3d3d6a")
        nav_scroll.pack(fill="both", expand=True, padx=(4, 0))
        self._build_nav(nav_scroll)
        self._build_sidebar_bottom(sidebar_bottom)
        self._enable_mousewheel(nav_scroll)

        # Thin vertical divider
        ctk.CTkFrame(body, width=1, fg_color="#2a2d3e", corner_radius=0).pack(
            side="left", fill="y")

        # ── Right section: scene list + detail + bottom bar ───────────────────
        right_section = ctk.CTkFrame(body, fg_color=BG, corner_radius=0)
        right_section.pack(side="left", fill="both", expand=True)

        # Columns row (scene list + detail)
        columns_row = ctk.CTkFrame(right_section, fg_color=BG, corner_radius=0)
        columns_row.pack(fill="both", expand=True)

        # ── Scene list (middle panel, 300px) ─────────────────────────────────
        scene_list_frame = ctk.CTkFrame(columns_row, fg_color=PANEL, corner_radius=0, width=300)
        scene_list_frame.pack(side="left", fill="y")
        scene_list_frame.pack_propagate(False)

        self.label(scene_list_frame, "Сцены", size=13, weight="bold", color=MUTED).pack(
            anchor="w", padx=14, pady=(14, 6))

        self.scene_list_scroll = ctk.CTkScrollableFrame(
            scene_list_frame, fg_color=PANEL,
            scrollbar_button_color="#3d3d6a",
            scrollbar_button_hover_color="#ff7eb6",
        )
        self.scene_list_scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # "Новая задача" button pinned at bottom of scene list
        add_scene_btn = ctk.CTkButton(
            scene_list_frame, text="＋  Новая задача", command=self.add_roll,
            fg_color=BTN_PRIMARY, hover_color=BTN_PRIMARY_HOVER,
            text_color=TEXT, corner_radius=10, height=38,
            font=ctk.CTkFont(size=13),
        )
        add_scene_btn.pack(fill="x", padx=10, pady=10)

        # Divider between scene list and detail
        ctk.CTkFrame(columns_row, width=1, fg_color="#2a2d3e", corner_radius=0).pack(
            side="left", fill="y")

        # ── Detail panel (right panel, fills rest) ────────────────────────────
        self.detail_panel = ctk.CTkFrame(columns_row, fg_color=BG, corner_radius=0)
        self.detail_panel.pack(side="left", fill="both", expand=True)

        # ── Bottom bar (spans scene list + detail, NOT sidebar) ───────────────
        bottom = ctk.CTkFrame(right_section, fg_color=BG)
        bottom.pack(fill="x", padx=20, pady=(6, 20))
        self.status_label = self.label(bottom, "", size=13, color=MUTED)
        self.status_label.pack(anchor="w", pady=(0, 8))
        self.start_btn = ctk.CTkButton(
            bottom, text="НАЧАТЬ ОЧЕРЕДЬ", command=self.start_queue,
            fg_color=BTN_DANGER, hover_color=BTN_DANGER_HOVER,
            text_color="white", corner_radius=24, height=50,
            font=ctk.CTkFont(size=17, weight="bold"),
        )
        self.start_btn.pack(fill="x")
        # Ход очереди — над нижней строкой; появляется только во время очереди и с её итогом.
        self._build_queue_panel(right_section, before=bottom)
        self._build_notice_bar(right_section, before=bottom)

    def _show_container(self, name, factory):
        """Скрывает все панели, показывает панель name. Возвращает (контейнер, создан_ли_сейчас)."""
        self._active_panel = name
        for other, frame in self._panels.items():
            if other != name:
                frame.pack_forget()
        container = self._panels.get(name)
        created = container is None
        if created:
            container = factory()
            self._panels[name] = container
        container.pack(fill="both", expand=True)
        if not created and hasattr(container, "_parent_canvas"):
            self.after(30, lambda c=container._parent_canvas: self._repaint_scroll(c))
        return container, created

    @staticmethod
    def _repaint_scroll(canvas):
        """macOS: после смены размера окна скрытая прокручиваемая панель при повторном показе
        иногда остаётся недорисованной, пока её не прокрутят. Сдвиг на шаг и возврат на место
        её перерисовывает; прокрутка «на ту же позицию» не помогает."""
        try:
            if not canvas.winfo_exists():
                return
            top = canvas.yview()[0]
            if top > 0:
                canvas.yview_scroll(-1, "units")
            else:
                canvas.yview_scroll(1, "units")
            canvas.after(40, lambda: canvas.winfo_exists() and canvas.yview_moveto(top))
        except Exception:
            pass

    def _start_panel(self, name):
        """Панели не уничтожаются: создаются один раз, дальше только скрываются и показываются.
        Возвращает контейнер для построения, если панель создаётся впервые, иначе None —
        тогда панель уже есть, а её содержимое подтягивается из менеджера задач."""
        scroll, created = self._show_container(name, lambda: ctk.CTkScrollableFrame(
            self.detail_panel, fg_color=BG,
            scrollbar_button_color="#3d3d6a",
            scrollbar_button_hover_color="#ff7eb6",
        ))
        self.update_status()
        if created:
            return scroll  # нарисует _register_renderer в конце построения панели
        self._render_panel(name)
        return None

    def _register_renderer(self, name, renderer):
        """Регистрирует рендерер панели и сразу рисует её по состоянию менеджера задач."""
        self._panel_renderers[name] = renderer
        self._render_panel(name)

    def _render_panel(self, name):
        renderer = self._panel_renderers.get(name)
        if renderer is not None and self._active_panel == name:
            try:
                renderer()
            except Exception:
                self.report_callback_exception(*sys.exc_info())

    # ── Менеджер задач → виджеты (только главный поток) ───────────────────
    def _process_task_changes(self):
        changed = self.background_tasks.drain_changes()
        if not changed:
            return
        panels = set()
        finished = False
        for task_id in changed:
            task = self.background_tasks.get(task_id)
            if task:
                panels.add(task["panel"])
                finished = finished or task["status"] in TASK_FINISHED
                if task_id in self._sync_fetch_pending and task["status"] in TASK_FINISHED:
                    self._sync_fetch_pending.discard(task_id)
                    self._finish_sync_fetch(task)
                if task_id in self._clone_pending and task["status"] in TASK_FINISHED:
                    self._clone_pending.discard(task_id)
                    self._on_clone_finished(task)
        for name in panels:
            self._render_panel(name)  # скрытая панель обновится при показе
        self._schedule_state_save()

    # ── Состояние на диск ─────────────────────────────────────────────────
    def _schedule_state_save(self):
        if not self._state_ready:
            return
        if self._state_save_job is not None:
            self.after_cancel(self._state_save_job)
        self._state_save_job = self.after(STATE_SAVE_DELAY_MS, self._save_state_now)

    def _collect_state(self):
        with self._journal_lock:
            journal = list(self._sync_journal[-MAX_SYNC_JOURNAL:])
        state = {
            "version": 1,
            "rolls": self.rolls,
            "next_roll_id": self.next_roll_id,
            "selected_roll_id": self.selected_roll_id,
            "panels": self._panel_state,
            "tasks": self.background_tasks.snapshot(),
            "sync_journal": journal,
        }
        # Ключи и подписанные ссылки на диск не попадают (outputUrl остаётся для ручного забора).
        return scrub_secrets(state, known_secret_values(self.api_key, self.sync_key, self.groq_api_key))

    def _save_state_now(self):
        self._state_save_job = None
        if not self._state_ready:
            return
        try:
            self._state_store.save(self._collect_state())
        except Exception as e:
            self.log(f"Не удалось сохранить состояние: {e}")

    def _restore_state(self):
        data = self._state_store.load()
        if self._state_store.load_error:
            self.log(f"Состояние прошлой сессии не прочитано: {self._state_store.load_error}")
        rolls = data.get("rolls")
        interrupted = []
        if isinstance(rolls, list) and rolls:
            for roll in rolls:
                roll.setdefault("face_mode", "default")
                roll.setdefault("face_points", {})
                status = str(roll.get("status", ""))
                if status.startswith("Обработка") or status == "В очереди":
                    roll["status"] = "Прервано"
                    interrupted.append(roll)
            self.rolls = rolls
            self.next_roll_id = max(int(data.get("next_roll_id") or 1),
                                    max(int(r.get("id", 0)) for r in rolls) + 1)
            ids = {r.get("id") for r in rolls}
            self.selected_roll_id = (data.get("selected_roll_id")
                                     if data.get("selected_roll_id") in ids else rolls[0]["id"])
            self.render_scene_list()
            self.render_scene_detail()
        self._panel_state = data.get("panels") if isinstance(data.get("panels"), dict) else {}
        self.background_tasks.restore(data.get("tasks"))
        self.background_tasks.drain_changes()
        for t in self.background_tasks.list(kind="compress"):
            if t["status"] == "interrupted" and cleanup_compress_tmp((t.get("meta") or {}).get("tmp")):
                self.log(f"Сжатие {t['title']} было прервано закрытием — недописанный файл удалён")
        journal = data.get("sync_journal")
        self._sync_journal = journal if isinstance(journal, list) else []
        self._state_ready = True
        if rolls or data.get("tasks"):
            self.log(f"Восстановлено из прошлой сессии: роликов {len(rolls or [])}, "
                     f"задач {len(data.get('tasks') or [])}, записей журнала Sync {len(self._sync_journal)}")
        if interrupted:
            titles = ", ".join(self.get_roll_title(self.rolls.index(r), r) for r in interrupted)
            self.log(f"Ролики, которые обрабатывались при закрытии, помечены «Прервано»: {titles}. "
                     "Повторно они не запускаются; job_id и outputUrl — в журнале Sync.")

    def _on_sync_journal_event(self, event):
        """Вызывается из потока очереди — только запись в список; сохранение — в главном потоке."""
        with self._journal_lock:
            self._sync_journal.append(event)
            del self._sync_journal[:-MAX_SYNC_JOURNAL]
            self._journal_dirty = True

    # ── Боковое меню (только навигация) ───────────────────────────────────
    def _nav_spec(self):
        """Разделы меню. Пункт: (панель или None, иконка, подпись, подсказка, команда).
        Новый пункт — одна строка здесь."""
        return [
            ("voice", "ГОЛОС", [
                ("voice_gen", "voice_gen", "Сгенерировать", "Сгенерировать голос (ElevenLabs)",
                 self.show_voice_gen_panel),
                ("voice_clone", "voice_clone", "Клонировать", "Клонировать голос (ElevenLabs)",
                 self.show_voice_clone_panel),
            ]),
            ("media", "МЕДИА", [
                ("downloader", "downloader", "Скачать видео", "Скачать видео или аудио по ссылке",
                 self.show_downloader_panel),
                ("compress", "compress", "Сжать видео", "Сжать видео локально через ffmpeg",
                 self.show_compress_panel),
            ]),
            ("text", "ТЕКСТ", [
                ("transcribe", "transcribe", "Транскрибация", "Распознать речь из аудио или видео (Groq)",
                 self.show_transcribe_panel),
                ("groq_translate", "groq_translate", "Переводчик", "Перевести текст (Groq)",
                 self.show_groq_translate_panel),
            ]),
            ("tasks", "ЗАДАЧИ", [
                (None, "new_task", "Новая задача", "Добавить ролик в очередь", self.add_roll),
                (None, "clear_all", "Очистить всё", "Очистить очередь роликов", self.clear_rolls),
                (None, "update", "Обновить", "Проверить обновления приложения",
                 lambda: self.check_for_updates(silent=False)),
            ]),
        ]

    def _key_spec(self):
        """Ключи: (id, название, задан ли, панель изменения, назначение, необязательный)."""
        return [
            ("elevenlabs", "ElevenLabs", lambda: bool(self.api_key), self.show_api_key_panel,
             "Озвучка и клонирование голоса", False),
            ("sync", "Sync", lambda: bool(self.sync_key) and "ВСТАВЬ" not in self.sync_key,
             self.show_sync_key_panel, "Липсинк", False),
            ("cloudinary", "Cloudinary", cloudinary_configured, self.show_cloudinary_panel,
             "Запасное хранилище для Sync", True),
            ("groq", "Groq", lambda: bool(self.groq_api_key), self.show_groq_key_panel,
             "Транскрибация и переводчик", False),
        ]

    def _nav_button(self, parent, panel, icon_key, label, tip, command):
        font = ctk.CTkFont(size=13)
        icon = NAV_ICONS.get(icon_key, "•")
        text, cut = fit_text(font, f"{icon}  {label}", SIDEBAR_WIDTH - 52)
        btn = ctk.CTkButton(parent, text=text, command=command, fg_color="transparent",
                            hover_color=BTN_HOVER, text_color=TEXT, anchor="w", corner_radius=8,
                            height=34, font=font)
        btn.pack(fill="x", padx=4, pady=1)
        HoverTip(btn, f"{label}\n{tip}" if cut else tip)
        if panel:
            self._nav_buttons[panel] = btn
        return btn

    def _build_nav(self, parent):
        self._nav_sections = {}
        for key, title, items in self._nav_spec():
            header = ctk.CTkButton(parent, text=f"{NAV_ARROW_OPEN}  {title}", anchor="w",
                                   fg_color="transparent", hover_color=BTN_HOVER, text_color=MUTED, height=24,
                                   corner_radius=6, font=ctk.CTkFont(size=11, weight="bold"),
                                   command=lambda k=key: self._toggle_nav_section(k))
            header.pack(fill="x", padx=4, pady=(8, 0))
            body = ctk.CTkFrame(parent, fg_color="transparent")
            body.pack(fill="x")
            for item in items:
                self._nav_button(body, *item)
            self._nav_sections[key] = {"header": header, "body": body, "title": title}

    def _nav_collapsed(self):
        return set(self._panel_state_get("sidebar", "collapsed") or [])

    def _apply_nav_collapsed(self):
        collapsed = self._nav_collapsed()
        for key, sec in self._nav_sections.items():
            hidden = key in collapsed
            sec["header"].configure(text=f"{NAV_ARROW_CLOSED if hidden else NAV_ARROW_OPEN}  {sec['title']}")
            if hidden and sec["body"].winfo_manager():
                sec["body"].pack_forget()
            elif not hidden and not sec["body"].winfo_manager():
                sec["body"].pack(fill="x", after=sec["header"])

    def _toggle_nav_section(self, key):
        collapsed = self._nav_collapsed()
        collapsed ^= {key}
        self._panel_state_set("sidebar", collapsed=sorted(collapsed))
        self._apply_nav_collapsed()

    def _build_sidebar_bottom(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color="#2a2d3e", corner_radius=0).pack(fill="x", padx=12, pady=(4, 6))
        keys_row = ctk.CTkFrame(parent, fg_color="transparent")
        keys_row.pack(fill="x", padx=14, pady=(0, 2))
        self.label(keys_row, "Ключи", size=11, color=MUTED).pack(side="left", padx=(0, 6))
        self._key_dots = {}
        for key_id, name, is_set, _panel, purpose, optional in self._key_spec():
            dot = ctk.CTkLabel(keys_row, text="●", text_color=KEY_DOT_OFF, font=ctk.CTkFont(size=15),
                               cursor="hand2", width=18)
            dot.pack(side="left", padx=1)
            dot.bind("<Button-1>", lambda e: self.show_settings_panel())
            HoverTip(dot, lambda n=name, s=is_set, p=purpose, o=optional:
                     f"{n}: {'задан' if s() else 'не задан'}" + (" (необязателен)" if o else "") + f"\n{p}")
            self._key_dots[key_id] = dot
        self._balance_lbl = self.label(parent, "", size=11, color=MUTED)
        self._balance_lbl.pack(anchor="w", fill="x", padx=14, pady=(0, 4))
        HoverTip(self._balance_lbl, "Остаток символов ElevenLabs в этом месяце")
        self._nav_button(parent, "settings", "settings", "Настройки", "Ключи, Sync, отчёты об ошибках, лог",
                         self.show_settings_panel)
        self.label(parent, f"SheqelMotion Studio {APP_VERSION}", size=10, color=MUTED).pack(
            anchor="w", padx=14, pady=(2, 10))

    def _render_key_status(self):
        for key_id, _name, is_set, *_rest in self._key_spec():
            dot = self._key_dots.get(key_id)
            if dot is not None:
                dot.configure(text_color=KEY_DOT_ON if is_set() else KEY_DOT_OFF)
        self._render_balance()
        if self._settings_widgets:
            self._render_settings()

    def _render_balance(self):
        text, color = balance_style(self._el_balance)
        if self._balance_lbl is not None:
            self._balance_lbl.configure(text=f"ElevenLabs: {text}" if text else
                                        ("ElevenLabs: баланс…" if self.api_key else ""), text_color=color)
        w = (self._settings_widgets or {}).get("keys", {}).get("elevenlabs")
        if w is not None:
            w["extra"].configure(text=f"баланс {text}" if text else "", text_color=color)

    def _render_nav_active(self):
        active = self._active_panel
        for panel, btn in self._nav_buttons.items():
            on = active == panel or (panel == "settings" and active in SETTINGS_PANELS)
            btn.configure(fg_color=BTN_PRIMARY if on else "transparent",
                          hover_color=BTN_PRIMARY_HOVER if on else BTN_HOVER)

    # ── Вкладка «Настройки» ───────────────────────────────────────────────
    def _settings_back_link(self, parent):
        """Строка «← Настройки» над панелями ключей."""
        ctk.CTkButton(parent, text="←  Настройки", command=self.show_settings_panel, anchor="w",
                      fg_color="transparent", hover_color=BTN_HOVER, text_color=MUTED, width=120, height=28,
                      font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20, pady=(14, 0))

    def _settings_card(self, parent, title, subtitle=""):
        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=20)
        card.pack(fill="x", padx=20, pady=(0, 14))
        self.label(card, title, size=18, weight="bold").pack(anchor="w", padx=22, pady=(18, 2))
        if subtitle:
            lbl = self.label(card, subtitle, size=12, color=MUTED)
            lbl.configure(wraplength=480)
            lbl.pack(anchor="w", fill="x", padx=22, pady=(0, 6))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=22, pady=(4, 18))
        body.columnconfigure(1, weight=1)
        return body

    def _sync_settings_spec(self):
        """Карточка «Липсинк Sync»: (вид, подпись, …). Те же обработчики и ключи конфига, что раньше."""
        return [
            ("menu", "Хранилище файлов для Sync", list(SYNC_STORAGE_MODES),
             lambda: SYNC_STORAGE_LABELS[self.sync_storage], self._on_sync_storage_change,
             lambda: "По умолчанию: Sync. " + ("Выбрано вами." if self._sync_storage_choice
                                               else "Сейчас действует умолчание.")),
            ("menu", "Частота кадров для Sync", list(SYNC_FPS_MODES),
             lambda: SYNC_FPS_LABELS[self.sync_fps_mode], self._on_sync_fps_change),
            ("switch", "Показывать окно стоимости перед запуском", self.cost_confirm_var,
             self._on_cost_confirm_toggle),
            ("entry", "Всегда спрашивать, если очередь дороже, $",
             lambda: f"{self.cost_confirm_threshold:g}", self._on_cost_threshold_change,
             lambda: f"По умолчанию: {COST_CONFIRM_DEFAULT_THRESHOLD:g} $"),
        ]

    def _app_settings_spec(self):
        return [
            ("switch", "Отправлять разработчику обезличенные отчёты об ошибках", self.error_reports_var,
             self._on_error_reports_toggle),
            ("switch", "После клонирования очищать текст генерации", self.clear_text_after_clone_var,
             self._on_clear_after_clone_toggle),
            ("switch", "Проверка орфографии (текст отправляется на languagetool.org)", self.spellcheck_online_var,
             self._on_spellcheck_toggle),
            ("buttons", "Отчёты об ошибках", [("Показать пример отчёта", self.show_report_example, BTN),
                                              ("Отправить тестовый отчёт", self.send_test_report, BTN)]),
            ("menu", "Ход очереди", list(QUEUE_PANEL_MODES), lambda: QUEUE_PANEL_LABELS[self.queue_panel_mode],
             self._on_queue_panel_mode_change, lambda: "По умолчанию: показывать во время работы."),
            ("buttons", "Лог", [("Открыть лог", self.open_log_window, BTN),
                                ("Показать файл лога в папке", self.reveal_log_file, BTN)]),
            ("buttons", "Сохранённые данные", [("Очистить сохранённые данные", self.clear_saved_data,
                                               BTN_DANGER)]),
            ("buttons", f"Версия {APP_VERSION}", [("Проверить обновления",
                                                   lambda: self.check_for_updates(silent=False), BTN)]),
        ]

    def _settings_rows(self, body, spec):
        """Строки карточки из списка. Подпись слева переносится, кнопки — отдельной строкой:
        вкладка помещается в узкую область (~560 px при окне 1100 px)."""
        widgets = []
        row = 0
        for item in spec:
            kind, title = item[0], item[1]
            title_lbl = self.label(body, title, size=13)
            title_lbl.configure(wraplength=230)
            if kind == "buttons":
                title_lbl.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 2))
                row += 1
                cell = ctk.CTkFrame(body, fg_color="transparent")
                cell.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))
                for text, command, color in item[2]:
                    hover = BTN_DANGER_HOVER if color == BTN_DANGER else BTN_HOVER
                    self.button(cell, text, command, color=color, hover=hover,
                                width=max(140, 9 * len(text))).pack(side="left", padx=(0, 8))
                row += 1
                continue
            hint_fn = item[5] if kind == "menu" and len(item) > 5 else item[4] if kind == "entry" and len(item) > 4 else None
            if hint_fn:
                cell = ctk.CTkFrame(body, fg_color="transparent")
                cell.grid(row=row, column=0, sticky="w", pady=6, padx=(0, 16))
                title_lbl.destroy()
                title_lbl = self.label(cell, title, size=13)
                title_lbl.configure(wraplength=230)
                title_lbl.pack(anchor="w")
                hint = self.label(cell, hint_fn(), size=11, color=MUTED)
                hint.configure(wraplength=230)
                hint.pack(anchor="w")
                widgets.append(("hint", hint, hint_fn))
            else:
                title_lbl.grid(row=row, column=0, sticky="w", pady=6, padx=(0, 16))
            if kind == "menu":
                values, current, command = item[2], item[3], item[4]
                m = ctk.CTkOptionMenu(body, values=values, command=command, width=220, height=30,
                                      fg_color=BTN, button_color=BTN, button_hover_color=BTN_HOVER,
                                      dropdown_fg_color=PANEL, dropdown_hover_color=BTN_HOVER,
                                      font=ctk.CTkFont(size=12), dropdown_font=ctk.CTkFont(size=12))
                m.grid(row=row, column=1, sticky="w", pady=6)
                widgets.append(("menu", m, current))
            elif kind == "switch":
                var, command = item[2], item[3]
                ctk.CTkSwitch(body, text="", variable=var, command=command, progress_color=BTN_OK,
                              width=50).grid(row=row, column=1, sticky="w", pady=6)
            elif kind == "entry":
                current, command = item[2], item[3]
                e = ctk.CTkEntry(body, width=90, height=30, font=ctk.CTkFont(size=12))
                e.grid(row=row, column=1, sticky="w", pady=6)
                for seq in ("<Return>", "<FocusOut>"):
                    e.bind(seq, lambda ev, e=e, c=command: c(e))
                widgets.append(("entry", e, current))
            row += 1
        return widgets

    def show_settings_panel(self):
        scroll = self._start_panel("settings")
        if scroll is None:
            return
        self._enable_mousewheel(scroll)
        self.label(scroll, "Настройки", size=24, weight="bold").pack(anchor="w", padx=24, pady=(20, 12))

        body = self._settings_card(scroll, "Ключи", "Ключи хранятся только на этом компьютере в файле настроек.")
        keys = {}
        for row, (key_id, name, _is_set, panel, purpose, optional) in enumerate(self._key_spec()):
            dot = ctk.CTkLabel(body, text="●", text_color=KEY_DOT_OFF, font=ctk.CTkFont(size=16), width=18)
            dot.grid(row=row, column=0, sticky="nw", pady=(8, 6))
            info = ctk.CTkFrame(body, fg_color="transparent")
            info.grid(row=row, column=1, sticky="ew", pady=6, padx=(8, 8))
            line = ctk.CTkFrame(info, fg_color="transparent")
            line.pack(anchor="w", fill="x")
            self.label(line, name, size=14, weight="bold").pack(side="left")
            status = self.label(line, "", size=12)
            status.pack(side="left", padx=(10, 0))
            extra = self.label(line, "", size=12, color=MUTED)
            extra.pack(side="left", padx=(10, 0))
            note = purpose + (" · необязательно" if optional else "")
            desc = self.label(info, note, size=11, color=MUTED)
            desc.configure(wraplength=360)
            desc.pack(anchor="w", fill="x")
            self.button(body, "Изменить", panel, color=BTN, hover=BTN_HOVER, width=100).grid(
                row=row, column=2, sticky="e", pady=6)
            keys[key_id] = {"dot": dot, "status": status, "extra": extra}

        sync_body = self._settings_card(
            scroll, "Липсинк Sync",
            "Хранилище — куда загружать видео и аудио перед отправкой в Sync. Окно стоимости показывает "
            "оценку перед запуском очереди.")
        sync_widgets = self._settings_rows(sync_body, self._sync_settings_spec())
        app_body = self._settings_card(scroll, "Приложение")
        self._settings_rows(app_body, self._app_settings_spec())
        self._settings_widgets = {"keys": keys, "rows": sync_widgets}
        self._register_renderer("settings", self._render_settings)

    def _render_settings(self):
        w = self._settings_widgets
        if not w:
            return
        for key_id, _name, is_set, *_rest in self._key_spec():
            row = w["keys"].get(key_id)
            if row is None or not row["dot"].winfo_exists():
                continue
            on = is_set()
            text = "✓ задан" if on else "не задан"
            if row["status"].cget("text") != text:
                row["dot"].configure(text_color=KEY_DOT_ON if on else KEY_DOT_OFF)
                row["status"].configure(text=text, text_color="#86efac" if on else MUTED)
        for kind, widget, current in w["rows"]:   # пишем в виджет только при реальном изменении
            value = current()
            if kind == "hint" and widget.cget("text") != value:
                widget.configure(text=value)
            elif kind == "menu" and widget.get() != value:
                widget.set(value)
            elif kind == "entry" and widget.get() != value and widget.focus_get() is not widget._entry:
                widget.delete(0, "end")
                widget.insert(0, value)
        text, color = balance_style(self._el_balance)
        el = w["keys"].get("elevenlabs")
        if el is not None:
            el["extra"].configure(text=f"баланс {text}" if text else "", text_color=color)

    def reveal_log_file(self):
        try:
            reveal_in_file_manager(LOG_FILE_PATH)
        except Exception as e:
            messagebox.showerror("Лог", f"Не удалось открыть: {e}", parent=self)

    # ── Сжать видео ───────────────────────────────────────────────────────
    COMPRESS_RES_LABELS = {"Как в исходнике": "source", "1080p": "1080", "720p": "720"}
    COMPRESS_FPS_LABELS = {"Как в исходнике": "source", "25": "25", "30": "30"}
    COMPRESS_AUDIO_LABELS = {"Копия": "copy", "AAC 128k": "aac"}
    COMPRESS_CODEC_LABELS = {"H.264": "h264", "H.265 (HEVC)": "hevc"}
    COMPRESS_HDR_LABELS = {"Перевести в SDR (H.264 8-bit, для Sync)": "sdr",
                           "Сохранить HDR (10-bit HEVC)": "keep"}

    def _compress_settings(self):
        return normalize_compress_settings(self._panel_state_get("compress", "settings"))

    def _compress_files(self):
        return list(self._panel_state_get("compress", "files") or [])

    def _compress_mark_dirty(self):
        self._compress_dirty = True

    def _compress_warmup(self):
        """Список кодировщиков, фильтры и проверка перевода HDR — в фоне, интерфейс не ждёт."""
        def work():
            try:
                available_video_encoders()
                ffmpeg_version_info()
                swscale_tonemap_available()
                tonemap_filters_present()
                hdr_to_sdr_method()
                for name in HW_ENCODERS.get(sys.platform, {}).get("h264", []) + \
                        HW_ENCODERS.get(sys.platform, {}).get("hevc", []):
                    if name in available_video_encoders():
                        encoder_works(name)
            finally:
                self._compress_caps_ready = True
                self._compress_mark_dirty()
        threading.Thread(target=work, daemon=True).start()

    def _compress_probe_missing(self, paths):
        with self._compress_lock:
            todo = [p for p in paths if p not in self._compress_info and p not in self._compress_probing]
            self._compress_probing.update(todo)
        if not todo:
            return

        def work():
            for p in todo:
                try:
                    info = probe_video_info(p)
                except Exception as e:
                    info = {"error": str(e)[:300], "path": p}
                with self._compress_lock:
                    self._compress_info[p] = info
                    self._compress_probing.discard(p)
                self._compress_mark_dirty()
        threading.Thread(target=work, daemon=True).start()

    # Оценка размера для CRF: лениво, в фоне, с отменой и кэшем.
    def _compress_cancel_estimates(self):
        with self._compress_lock:
            self._compress_est_gen += 1
            self._compress_est_queue = []
            proc = self._compress_est_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _compress_request_estimates(self, items):
        """items: [(ключ, info, plan)] для CRF-пресета. Уже посчитанные и стоящие в очереди пропускаются."""
        with self._compress_lock:
            queued = {k for k, _, _ in self._compress_est_queue}
            new = [it for it in items if it[0] not in self._compress_est and it[0] not in queued
                   and it[0] != self._compress_est_current]
            self._compress_est_queue += new
            start = new and not self._compress_est_running
            if start:
                self._compress_est_running = True
        if start:
            threading.Thread(target=self._compress_estimate_worker, daemon=True).start()

    def _compress_estimate_worker(self):
        try:
            while True:
                with self._compress_lock:
                    if not self._compress_est_queue:
                        return
                    key, info, plan = self._compress_est_queue.pop(0)
                    gen = self._compress_est_gen
                    self._compress_est_current = key
                self._compress_mark_dirty()

                def register(proc):
                    with self._compress_lock:
                        self._compress_est_proc = proc

                try:
                    size = estimate_crf_size(info["path"], info, plan,
                                             cancel=lambda: self._compress_est_gen != gen or self._closing,
                                             register=register)
                    value = size
                except CompressCancelled:
                    value = None
                except Exception as e:
                    write_log_file(f"Оценка размера не удалась ({Path(info['path']).name}): {e}")
                    value = "error"
                with self._compress_lock:
                    self._compress_est_current = None
                    if value is not None and gen == self._compress_est_gen:
                        self._compress_est[key] = value
                self._compress_mark_dirty()
        finally:
            with self._compress_lock:
                self._compress_est_running = False
                self._compress_est_current = None

    def show_compress_panel(self):
        outer = self._start_panel("compress")
        if outer is not None:
            self._build_compress_panel(outer)

    def _build_compress_panel(self, outer):
        tasks = self.background_tasks
        self._compress_warmup()
        self._compress_probe_missing(self._compress_files())

        head = ctk.CTkFrame(outer, fg_color=PANEL, corner_radius=20)
        head.pack(fill="x", padx=20, pady=(20, 10))
        self.label(head, "Сжать видео", size=22, weight="bold").pack(anchor="w", padx=24, pady=(20, 4))
        self.label(head, "Локально через ffmpeg, бесплатно. Оригинал никогда не перезаписывается: результат "
                         "кладётся в папку «compressed» рядом с ним (или в выбранную) с суффиксом _compressed.",
                   size=12, color=MUTED).pack(anchor="w", fill="x", padx=24, pady=(0, 10))
        pick_row = ctk.CTkFrame(head, fg_color="transparent")
        pick_row.pack(fill="x", padx=20, pady=(0, 18))
        recursive_var = ctk.BooleanVar(value=bool(self._panel_state_get("compress", "recursive", False)))

        def add_paths(paths=(), folder=None):
            found = collect_compress_inputs(paths, folder, recursive_var.get())
            files = self._compress_files()
            added = [p for p in found if p not in files]
            if not found:
                messagebox.showinfo("Сжать видео", "Видео не найдено (папка «compressed» и файлы "
                                                   "*_compressed пропускаются).", parent=self)
                return
            self._panel_state_set("compress", files=files + added)
            self._compress_probe_missing(added)
            render_compress()

        def pick_files():
            chosen = filedialog.askopenfilenames(
                parent=self, title="Видео для сжатия",
                filetypes=[("Видео", " ".join(f"*{e}" for e in sorted(COMPRESS_VIDEO_EXTS))), ("Все файлы", "*")])
            if chosen:
                add_paths(paths=list(chosen))

        def pick_folder():
            folder = filedialog.askdirectory(parent=self, title="Папка с видео")
            if folder:
                add_paths(folder=folder)

        def clear_list():
            if busy():
                return
            self._compress_cancel_estimates()
            self._panel_state_set("compress", files=[], batch=None)
            render_compress()

        self.button(pick_row, "Файлы…", pick_files, color=BTN, hover=BTN_HOVER, width=110).pack(side="left")
        self.button(pick_row, "Папка…", pick_folder, color=BTN, hover=BTN_HOVER, width=110).pack(side="left", padx=8)
        ctk.CTkCheckBox(pick_row, text="включая подпапки", variable=recursive_var, text_color=TEXT,
                        command=lambda: self._panel_state_set("compress", recursive=recursive_var.get()),
                        font=ctk.CTkFont(size=12)).pack(side="left", padx=8)
        clear_btn = self.button(pick_row, "Очистить список", clear_list, color=BTN, hover=BTN_HOVER, width=140)
        clear_btn.pack(side="right")

        list_frame = ctk.CTkFrame(outer, fg_color="transparent")
        list_frame.pack(fill="x", padx=16, pady=(0, 6))
        empty_lbl = self.label(list_frame, "Добавь видео кнопками «Файлы…» или «Папка…».", size=12, color=MUTED)

        # ── Настройки ────────────────────────────────────────────────────
        card = ctk.CTkFrame(outer, fg_color=PANEL, corner_radius=20)
        card.pack(fill="x", padx=20, pady=(4, 10))
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=20, pady=(16, 6))
        settings = self._compress_settings()
        menus = {}

        def menu(row, title, values, current, key, mapping=None):
            self.label(grid, title, size=12, color=MUTED).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
            m = ctk.CTkOptionMenu(grid, values=list(values), width=300, height=28,
                                  fg_color=BTN, button_color=BTN, button_hover_color=BTN_HOVER,
                                  dropdown_fg_color=PANEL, dropdown_hover_color=BTN_HOVER,
                                  font=ctk.CTkFont(size=12), dropdown_font=ctk.CTkFont(size=12),
                                  command=lambda v: on_setting(key, mapping[v] if mapping else v))
            reverse = {v: k for k, v in (mapping or {}).items()}
            m.set(reverse.get(current, current) if mapping else current)
            m.grid(row=row, column=1, sticky="w", pady=4)
            menus[key] = m
            return m

        menu(0, "Пресет", COMPRESS_PRESETS, settings["preset"], "preset")
        preset_hint = self.label(grid, COMPRESS_PRESET_HINT, size=11, color=MUTED)
        preset_hint.configure(wraplength=520)
        preset_hint.grid(row=1, column=1, sticky="w", pady=(0, 4))
        self.label(grid, "Размер, МБ", size=12, color=MUTED).grid(row=2, column=0, sticky="w", pady=4)
        size_entry = ctk.CTkEntry(grid, width=90, height=28, font=ctk.CTkFont(size=12))
        size_entry.insert(0, f"{settings['target_mb']:g}")
        size_entry.grid(row=2, column=1, sticky="w", pady=4)
        menu(3, "Разрешение", self.COMPRESS_RES_LABELS, settings["resolution"], "resolution", self.COMPRESS_RES_LABELS)
        menu(4, "Частота кадров", self.COMPRESS_FPS_LABELS, settings["fps"], "fps", self.COMPRESS_FPS_LABELS)
        cfr_row = ctk.CTkFrame(grid, fg_color="transparent")
        cfr_row.grid(row=5, column=1, sticky="w", pady=4)
        cfr_var = ctk.BooleanVar(value=settings["cfr"] is not False)
        ctk.CTkCheckBox(cfr_row, text="Постоянный fps (рекомендуется для Sync)", variable=cfr_var,
                        text_color=TEXT, font=ctk.CTkFont(size=12),
                        command=lambda: on_setting("cfr", bool(cfr_var.get()))).pack(side="left")
        cfr_menu = ctk.CTkOptionMenu(cfr_row, values=list(COMPRESS_CFR_VALUES), width=70, height=26,
                                     fg_color=BTN, button_color=BTN, button_hover_color=BTN_HOVER,
                                     dropdown_fg_color=PANEL, dropdown_hover_color=BTN_HOVER,
                                     font=ctk.CTkFont(size=12), dropdown_font=ctk.CTkFont(size=12),
                                     command=lambda v: on_setting("cfr_fps", v))
        cfr_menu.set(settings["cfr_fps"])
        cfr_menu.pack(side="left", padx=8)
        cfr_note = self.label(cfr_row, "", size=11, color=MUTED)
        cfr_note.pack(side="left")
        menu(6, "Звук", self.COMPRESS_AUDIO_LABELS, settings["audio"], "audio", self.COMPRESS_AUDIO_LABELS)
        menu(7, "Кодек", self.COMPRESS_CODEC_LABELS, settings["codec"], "codec", self.COMPRESS_CODEC_LABELS)
        hdr_menu = menu(8, "HDR-файлы", self.COMPRESS_HDR_LABELS, settings["hdr_mode"], "hdr_mode",
                        self.COMPRESS_HDR_LABELS)
        fast_var = ctk.BooleanVar(value=bool(settings["fast"]))
        ctk.CTkCheckBox(card, text="Быстрее (хуже качество) — аппаратный кодировщик", variable=fast_var,
                        text_color=TEXT, font=ctk.CTkFont(size=12),
                        command=lambda: on_setting("fast", bool(fast_var.get()))).pack(anchor="w", padx=20, pady=(4, 2))
        fast_hint = self.label(card, COMPRESS_FAST_HINT, size=11, color=MUTED)
        fast_hint.configure(wraplength=640)
        fast_hint.pack(anchor="w", fill="x", padx=20, pady=(0, 4))
        enc_lbl = self.label(card, "", size=11, color=MUTED)
        enc_lbl.configure(wraplength=680)
        enc_lbl.pack(anchor="w", fill="x", padx=20, pady=(0, 2))
        ffmpeg_lbl = self.label(card, "", size=11, color=MUTED)
        ffmpeg_lbl.pack(anchor="w", fill="x", padx=20, pady=(0, 2))
        upgrade_lbl = self.label(card, "", size=11, color="#fdba74")
        upgrade_lbl.configure(wraplength=680)
        out_row = ctk.CTkFrame(card, fg_color="transparent")
        out_row.pack(fill="x", padx=20, pady=(2, 16))
        out_lbl = self.label(out_row, "", size=12)
        out_lbl.pack(side="left")

        def choose_out():
            folder = filedialog.askdirectory(parent=self, title="Папка для сжатых видео")
            if folder:
                on_setting("out_dir", folder)

        self.button(out_row, "Сбросить", lambda: on_setting("out_dir", ""), color=BTN, hover=BTN_HOVER,
                    width=100).pack(side="right")
        self.button(out_row, "Выбрать папку…", choose_out, color=BTN, hover=BTN_HOVER,
                    width=150).pack(side="right", padx=8)

        def on_setting(key, value):
            current = self._compress_settings()
            if current.get(key) == value:
                return
            current[key] = value
            self._panel_state_set("compress", settings=current)
            self._compress_cancel_estimates()   # оценки под старые настройки больше не нужны
            render_compress()

        def on_size(_e=None):
            try:
                value = float(size_entry.get().replace(",", "."))
                if value <= 0:
                    raise ValueError
            except ValueError:
                size_entry.delete(0, "end")
                size_entry.insert(0, f"{self._compress_settings()['target_mb']:g}")
                return
            on_setting("target_mb", value)

        size_entry.bind("<Return>", on_size)
        size_entry.bind("<FocusOut>", on_size)

        # ── Запуск ───────────────────────────────────────────────────────
        run_card = ctk.CTkFrame(outer, fg_color=PANEL, corner_radius=20)
        run_card.pack(fill="x", padx=20, pady=(0, 20))
        run_row = ctk.CTkFrame(run_card, fg_color="transparent")
        run_row.pack(fill="x", padx=20, pady=(16, 6))
        start_btn = self.button(run_row, "🗜 Сжать", lambda: start(), color=BTN_OK, hover=BTN_OK_HOVER, width=140)
        start_btn.pack(side="left")
        cancel_btn = self.button(run_row, "Отмена", lambda: cancel(), color=BTN_DANGER, hover=BTN_DANGER_HOVER,
                                 width=110)
        cancel_btn.pack(side="left", padx=8)
        open_out_btn = self.button(run_row, "Открыть папку", lambda: None, color=BTN, hover=BTN_HOVER, width=140)
        open_out_btn.pack(side="right")
        total_bar = ctk.CTkProgressBar(run_card, height=8)
        total_bar.set(0)
        total_bar.pack(fill="x", padx=20, pady=(4, 4))
        total_lbl = self.label(run_card, "", size=12, color=MUTED)
        total_lbl.pack(anchor="w", fill="x", padx=20, pady=(0, 16))

        rows = {}
        state = {"signature": None, "announced": {t["id"] for t in tasks.list(kind="compress")
                                                   if t["status"] in TASK_FINISHED}}

        def batch_tasks():
            batch = self._panel_state_get("compress", "batch")
            return [t for t in tasks.list(kind="compress") if t["meta"].get("batch") == batch] if batch else []

        def busy():
            return any(t["status"] not in TASK_FINISHED for t in tasks.list(kind="compress"))

        def remove_file(path):
            if busy():
                return
            self._panel_state_set("compress", files=[p for p in self._compress_files() if p != path])
            render_compress()

        def keep_original(task_id):
            task = tasks.get(task_id)
            res = (task or {}).get("result") or {}
            out = res.get("output")
            if not out:
                return
            if not messagebox.askyesno(
                    "Оставить оригинал",
                    f"Сжатая копия получилась больше оригинала.\n\nУдалить сжатую копию?\n{out}\n\n"
                    f"Оригинал не трогается:\n{task['meta'].get('src')}", icon="warning", parent=self):
                return
            src = task["meta"].get("src")
            if os.path.abspath(out) == os.path.abspath(src or "") or COMPRESS_SUFFIX not in Path(out).stem:
                return
            try:
                os.remove(out)
            except OSError as e:
                messagebox.showerror("Оставить оригинал", f"Не удалось удалить: {e}", parent=self)
                return
            self.log(f"Сжатие: удалена копия больше оригинала, оставлен оригинал: {out}")
            tasks.update(task_id, result=dict(res, output=None, kept_original=True))

        def make_row(path):
            row = ctk.CTkFrame(list_frame, fg_color=CARD, corner_radius=12)
            row.pack(fill="x", padx=4, pady=4)
            top = ctk.CTkFrame(row, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(10, 0))
            name = self.label(top, Path(path).name, size=13, weight="bold")
            name.pack(side="left")
            remove = ctk.CTkButton(top, text="✕", width=28, height=24, fg_color="transparent",
                                   hover_color=BTN_HOVER, text_color=MUTED, command=lambda: remove_file(path))
            remove.pack(side="right")
            keep = self.button(top, "Оставить оригинал", lambda: None, color=BTN_DANGER,
                               hover=BTN_DANGER_HOVER, width=170)
            open_btn = self.button(top, "Открыть папку", lambda: None, color=BTN, hover=BTN_HOVER, width=130)
            body = ctk.CTkFrame(row, fg_color="transparent")
            body.pack(fill="x", padx=12, pady=(2, 10))
            labels = {}
            for key, color in (("info", MUTED), ("warn", "#fcd34d"), ("sync", "#fdba74"), ("status", TEXT)):
                lbl = self.label(body, "", size=11, color=color)
                lbl.configure(wraplength=680)
                labels[key] = lbl
            bar = ctk.CTkProgressBar(body, height=6)
            bar.set(0)
            return {"row": row, "remove": remove, "keep": keep, "open": open_btn, "bar": bar, **labels}

        ROW_ORDER = ("info", "warn", "sync", "status", "bar")

        def show_line(w, key, visible):
            """Строки карточки в постоянном порядке: появившаяся встаёт на своё место, а не в конец."""
            widget = w[key]
            if visible and not widget.winfo_manager():
                after = [w[k] for k in ROW_ORDER[ROW_ORDER.index(key) + 1:] if w[k].winfo_manager()]
                opts = {"anchor": "w", "fill": "x", "pady": (2, 0) if key != "bar" else (6, 0)}
                if after:
                    opts["before"] = after[0]
                widget.pack(**opts)
            elif not visible and widget.winfo_manager():
                widget.pack_forget()

        def show(widget, visible, **pack):
            if visible and not widget.winfo_manager():
                widget.pack(**pack)
            elif not visible and widget.winfo_manager():
                widget.pack_forget()

        def fmt_eta(sec):
            if sec is None or sec != sec or sec < 0:
                return ""
            return f"осталось ~{int(sec // 60)} мин {int(sec % 60):02d} с" if sec >= 60 else f"осталось ~{int(sec)} с"

        def render_compress():
            files = self._compress_files()
            settings = self._compress_settings()
            kind = COMPRESS_PRESETS.get(settings["preset"], ("crf", 23))[0]
            caps = self._compress_caps_ready
            running = busy()
            btasks = batch_tasks()
            by_src = {t["meta"].get("src"): t for t in btasks}

            self._compress_probe_missing(files)   # файлы из прошлой сессии и добавленные извне
            signature = tuple(files)
            if signature != state["signature"]:
                for w in rows.values():
                    w["row"].destroy()
                rows.clear()
                for p in files:
                    rows[p] = make_row(p)
                state["signature"] = signature
            show(empty_lbl, not files, anchor="w", padx=8, pady=8)

            # Настройки
            size_entry.configure(state="normal" if kind == "target" else "disabled")
            any_hdr = any((self._compress_info.get(p) or {}).get("hdr") for p in files)
            sdr = hdr_to_sdr_method() if caps else None
            hdr_menu.configure(state="normal" if any_hdr else "disabled")
            if caps:
                encs = available_video_encoders()
                soft = SOFTWARE_ENCODERS[settings["codec"]]
                chosen = pick_compress_encoder(settings["codec"], settings["fast"])
                text = f"Кодировщик: {chosen}"
                if settings["fast"] and chosen == soft:
                    text += " — аппаратного нет или он не работает, будет программный"
                if any_hdr:
                    text += (f" · HDR → SDR: {sdr[0]}" if sdr else
                             " · перевода HDR → SDR в этом ffmpeg нет: HDR сохранится в 10-bit HEVC")
                    text += f" · фильтры тонирования: {', '.join(tonemap_filters_present()) or 'нет'}"
                enc_lbl.configure(text=text)
            else:
                enc_lbl.configure(text="Проверяю ffmpeg…")
            out_lbl.configure(text="Куда: " + (settings["out_dir"] or "папка «compressed» рядом с каждым видео"))
            vfr_files = [p for p in files if (self._compress_info.get(p) or {}).get("vfr")]
            cfr_var.set(settings["cfr"] is not False)
            cfr_menu.configure(state="normal" if settings["cfr"] is not False else "disabled")
            cfr_note.configure(text=(f"переменный fps у файлов: {len(vfr_files)}" if vfr_files
                                     else "у выбранных файлов fps постоянный"))
            if caps:
                version = ffmpeg_version_info()
                method = hdr_to_sdr_method()
                ffmpeg_lbl.configure(text=f"ffmpeg {version[0]} · перевод HDR → SDR: "
                                          + (method[0] if method else "нет (HDR сохранится в 10-bit HEVC)"))
                hint = ffmpeg_upgrade_hint(version, swscale_tonemap_available(), method)
                upgrade_lbl.configure(text=hint or "")
                if hint and not upgrade_lbl.winfo_manager():
                    upgrade_lbl.pack(anchor="w", fill="x", padx=20, pady=(0, 4), after=ffmpeg_lbl)
                elif not hint and upgrade_lbl.winfo_manager():
                    upgrade_lbl.pack_forget()

            # Строки файлов
            est_items = []
            for p, w in rows.items():
                info = self._compress_info.get(p)
                task = by_src.get(p)
                plan = None
                if info is None:
                    w["info"].configure(text="Читаю параметры…")
                elif info.get("error"):
                    w["info"].configure(text=f"Не удалось прочитать: {info['error']}", text_color="#fca5a5")
                else:
                    w["info"].configure(text=describe_video_info(info), text_color=MUTED)
                    if caps:
                        plan = compress_plan(info, settings)
                show_line(w, "info", True)
                warn = (plan or {}).get("warnings") or []
                if plan and plan.get("error"):
                    warn = [plan["error"]] + warn
                w["warn"].configure(text="\n".join("⚠ " + x for x in warn))
                show_line(w, "warn", bool(warn))
                sync = sync_compat_warnings(info, plan) if plan else []
                w["sync"].configure(text="\n".join(sync))
                show_line(w, "sync", bool(sync))

                status, color, bar, open_dir, keep_id = "", MUTED, None, None, None
                st = task["status"] if task else None
                if st in ("queued",):
                    status = "В очереди"
                elif st == "running":
                    status, color = task.get("message") or "Сжимаю…", "#fcd34d"
                    bar = task.get("progress") or 0
                elif st == "done":
                    res = task.get("result") or {}
                    if res.get("kept_original"):
                        status, color = "Оставлен оригинал (сжатая копия была больше и удалена)", MUTED
                    else:
                        saved = 1 - res["size"] / max(res["src_size"], 1)
                        status = (f"✓ {res['src_size'] / MB:.1f} → {res['size'] / MB:.1f} МБ "
                                  f"({'−' if saved >= 0 else '+'}{abs(saved):.0%}) · {Path(res['output']).name}")
                        color = "#86efac"
                        open_dir = os.path.dirname(res["output"])
                        if res.get("bigger"):
                            status = "⚠ Получилось больше оригинала. " + status
                            color = "#fcd34d"
                            keep_id = task["id"]
                    bar = 1.0
                elif st == "error":
                    status, color = f"Ошибка: {task.get('error')}", "#fca5a5"
                elif st == "cancelled":
                    status = "Отменено, недописанный файл удалён"
                elif st == "interrupted":
                    status = "Прервано: приложение закрыли, недописанный файл удалён"
                elif plan and not plan.get("error") and info and not info.get("error"):
                    if kind == "target":
                        status = (f"Итог: до {plan['target_mb']:g} МБ" if plan["strict"]
                                  else f"Итог: ≈ {plan['target_mb']:g} МБ (±10%)")
                        status += f" · видео {plan['video_kbps']:.0f} кбит/с"
                    else:
                        key = compress_estimate_key(info, settings)
                        value = self._compress_est.get(key)
                        if value == "error":
                            status = "Оценка размера не удалась"
                        elif value:
                            change = 1 - value / max(info["size"], 1)
                            status = (f"Итог: ≈ {value / MB:.0f} МБ "
                                      f"({'−' if change >= 0 else '+'}{abs(change):.0%}, примерно)")
                        elif not running:
                            status = ("Оцениваю размер…" if self._compress_est_current == key
                                      else "Оценка размера в очереди…")
                            est_items.append((key, info, plan))
                w["status"].configure(text=status, text_color=color)
                show_line(w, "status", bool(status))
                if bar is not None:
                    w["bar"].set(bar)
                show_line(w, "bar", bar is not None)
                w["open"].configure(command=(lambda d=open_dir: open_folder(d)) if open_dir else (lambda: None))
                show(w["open"], bool(open_dir), side="right", padx=4)
                w["keep"].configure(command=(lambda i=keep_id: keep_original(i)))
                show(w["keep"], bool(keep_id), side="right", padx=4)
                w["remove"].configure(state="disabled" if running else "normal")
            if kind == "crf" and est_items and not running:
                self._compress_request_estimates(est_items)

            # Общий прогресс
            start_btn.configure(state="disabled" if running or not files else "normal",
                                text="Сжимаю…" if running else "🗜 Сжать")
            cancel_btn.configure(state="normal" if running else "disabled")
            clear_btn.configure(state="disabled" if running else "normal")
            total = sum(t["meta"].get("duration") or 1 for t in btasks) or 1
            done_media = 0.0
            eta = 0.0
            known = True
            cur = None
            for t in btasks:
                dur = t["meta"].get("duration") or 1
                if t["status"] in TASK_FINISHED:
                    done_media += dur
                elif t["status"] == "running":
                    cur = t
                    done_media += dur * (t.get("progress") or 0)
            rate = (cur or {}).get("wall_per_media")
            if cur is not None:
                if rate:
                    eta = (cur["meta"].get("duration") or 0) * (1 - (cur.get("progress") or 0)) * rate
                    eta += sum((t["meta"].get("duration") or 0) for t in btasks if t["status"] == "queued") * rate
                else:
                    known = False
            total_bar.set(done_media / total if btasks else 0)
            if running:
                pos = sum(1 for t in btasks if t["status"] in TASK_FINISHED) + 1
                total_lbl.configure(text=f"Файл {min(pos, len(btasks))} из {len(btasks)} · "
                                         f"{done_media / total:.0%}" + (f" · {fmt_eta(eta)}" if known and rate else ""))
            elif btasks:
                done = [t for t in btasks if t["status"] == "done"]
                saved = sum((t["result"] or {}).get("src_size", 0) - (t["result"] or {}).get("size", 0)
                            for t in done if not (t["result"] or {}).get("kept_original"))
                failed = sum(1 for t in btasks if t["status"] == "error")
                stopped = sum(1 for t in btasks if t["status"] in ("cancelled", "interrupted"))
                text = f"Готово: {len(done)} из {len(btasks)}"
                if saved > 0:
                    text += f" · сэкономлено {saved / MB:.0f} МБ"
                if failed:
                    text += f" · ошибок {failed} (подробности в ~/SheqelMotion.log)"
                if stopped:
                    text += f" · не сжато {stopped}"
                total_lbl.configure(text=text)
            else:
                total_lbl.configure(text="")
            last_out = next((os.path.dirname((t["result"] or {}).get("output") or "")
                             for t in reversed(btasks) if t["status"] == "done"
                             and (t["result"] or {}).get("output")), None)
            if not last_out and files:
                last_out = settings["out_dir"] or os.path.join(os.path.dirname(files[0]), COMPRESS_DIR_NAME)
            open_out_btn.configure(state="normal" if last_out and os.path.isdir(last_out) else "disabled",
                                   command=lambda d=last_out: open_folder(d))

            for t in btasks:   # ошибка, случившаяся на другой вкладке, — показать один раз
                if t["status"] == "error" and t["id"] not in state["announced"]:
                    state["announced"].add(t["id"])
                    self.after(50, lambda t=t: messagebox.showerror(
                        "Сжать видео", f"{Path(t['meta'].get('src', '')).name}:\n{t.get('error')}", parent=self))
                elif t["status"] in TASK_FINISHED:
                    state["announced"].add(t["id"])

        def start():
            if busy():
                return
            files = [p for p in self._compress_files() if os.path.exists(p)]
            missing = [p for p in self._compress_files() if not os.path.exists(p)]
            if missing:
                messagebox.showwarning("Сжать видео", "Не найдены (убраны из списка):\n"
                                       + "\n".join(Path(p).name for p in missing), parent=self)
                self._panel_state_set("compress", files=files)
            ready = [p for p in files if (self._compress_info.get(p) or {}).get("duration")]
            if not ready:
                messagebox.showwarning("Сжать видео", "Нет файлов с прочитанными параметрами.", parent=self)
                return
            settings = self._compress_settings()
            errors = []
            for p in ready:
                plan = compress_plan(self._compress_info[p], settings)
                if plan.get("error"):
                    errors.append(f"{Path(p).name}: {plan['error']}")
            if errors:
                messagebox.showerror("Сжать видео", "\n\n".join(errors), parent=self)
                return
            self._compress_cancel_estimates()
            batch = uuid.uuid4().hex[:8]
            self._panel_state_set("compress", batch=batch)
            ids = [tasks.create("compress", "compress", title=Path(p).name,
                                meta={"batch": batch, "src": p, "duration": self._compress_info[p]["duration"],
                                      "settings": settings, "tmp": None})
                   for p in ready]
            self.log(f"Сжатие: запуск, файлов {len(ids)}, пресет «{settings['preset']}»")
            threading.Thread(target=self._compress_queue_worker, args=(ids,), daemon=True).start()
            render_compress()

        def cancel():
            for t in batch_tasks():
                if t["status"] not in TASK_FINISHED:
                    tasks.request_cancel(t["id"])
            self._compress_kill_running()

        self._register_renderer("compress", render_compress)

    def _compress_queue_worker(self, task_ids):
        tasks = self.background_tasks
        for n, task_id in enumerate(task_ids):
            task = tasks.get(task_id)
            if task is None:
                continue
            if tasks.cancel_requested(task_id) or self._closing:
                # Закрытие приложения — «Прервано», кнопка «Отмена» — «Отменено».
                status = "interrupted" if self._closing else "cancelled"
                for rest in task_ids[n:]:
                    t = tasks.get(rest)
                    if t and t["status"] not in TASK_FINISHED:
                        tasks.update(rest, status=status, message="Отменено" if status == "cancelled"
                                     else "прервано: приложение было закрыто")
                break
            meta = task["meta"]
            src, settings = meta["src"], meta["settings"]
            tasks.update(task_id, status="running", progress=0.0, message="Готовлю…")
            clock = {"start": time.time(), "last": 0.0}

            def progress(frac, speed, task_id=task_id, clock=clock, meta=meta):
                now = time.time()
                if now - clock["last"] < 0.3 and frac < 1:
                    return
                clock["last"] = now
                elapsed = now - clock["start"]
                rate = elapsed / max(frac * (meta.get("duration") or 1), 0.01) if frac > 0.02 else None
                eta = elapsed / frac * (1 - frac) if frac > 0.02 else None
                msg = f"{frac:.0%}"
                if speed:
                    msg += f" · {speed:g}x"
                if eta is not None:
                    msg += f" · осталось ~{int(eta // 60)} мин {int(eta % 60):02d} с" if eta >= 60 \
                        else f" · осталось ~{int(eta)} с"
                tasks.update(task_id, progress=frac, message=msg, wall_per_media=rate)

            def on_tmp(path, task_id=task_id):
                t = tasks.get(task_id)
                if t:
                    tasks.update(task_id, meta=dict(t["meta"], tmp=path))

            def register(proc, task_id=task_id):
                with self._compress_lock:
                    if proc is None:
                        self._compress_procs.pop(task_id, None)
                    else:
                        self._compress_procs[task_id] = proc

            try:
                info = probe_video_info(src)   # свежие параметры: файл мог измениться
                plan = compress_plan(info, settings)
                final = compress_output_path(src, settings.get("out_dir") or None)
                result = compress_video_file(src, info, plan, final, self.log, progress=progress,
                                             cancel=lambda task_id=task_id: tasks.cancel_requested(task_id)
                                             or self._closing,
                                             register=register, on_tmp=on_tmp)
                tasks.update(task_id, status="done", progress=1.0, result=result,
                             message="больше оригинала" if result["bigger"] else "готово")
                if result["bigger"]:
                    self.log(f"Сжатие: {Path(src).name} — результат больше оригинала "
                             f"({result['src_size'] / MB:.1f} → {result['size'] / MB:.1f} МБ)")
            except CompressCancelled:
                if self._closing:
                    tasks.update(task_id, status="interrupted", message="прервано: приложение было закрыто")
                else:
                    tasks.update(task_id, status="cancelled", message="Отменено")
                    self.log(f"Сжатие отменено: {Path(src).name}, недописанный файл удалён")
            except Exception as e:
                text = str(e)
                self.log(f"Сжатие: ошибка {Path(src).name}: {text}")   # хвост вывода ffmpeg — в лог
                short = text.splitlines()[0] if text else type(e).__name__
                if "ffmpeg завершился" in short:
                    short = "ffmpeg не смог сжать файл, подробности в ~/SheqelMotion.log"
                tasks.update(task_id, status="error", error=short)

    def _compress_kill_running(self):
        with self._compress_lock:
            procs = list(self._compress_procs.values())
        for proc in procs:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass

    def _compress_stop_all_on_close(self):
        """При закрытии: остановить ffmpeg и удалить недописанные файлы."""
        self._closing = True
        for t in self.background_tasks.active(kind="compress"):
            self.background_tasks.request_cancel(t["id"])
        with self._compress_lock:
            procs = list(self._compress_procs.values())
            if self._compress_est_proc is not None:
                procs.append(self._compress_est_proc)
        self._compress_cancel_estimates()
        self._compress_kill_running()
        # Ждём именно остановленные процессы: при завершении ffmpeg дописывает mp4 (+faststart)
        # и мог бы заново создать файл, удалённый раньше времени.
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
        for t in self.background_tasks.list(kind="compress"):
            cleanup_compress_tmp((t.get("meta") or {}).get("tmp"))

    # ── Очистка сохранённых данных ────────────────────────────────────────
    def clear_saved_data(self):
        if self.is_processing or self.background_tasks.active():
            messagebox.showwarning(
                "Сохранённые данные",
                "Сначала дождись окончания очереди и фоновых задач (генерация, загрузки, перевод).",
                parent=self)
            return
        if not messagebox.askyesno(
                "Очистить сохранённые данные",
                "Будут удалены:\n"
                f"• {STATE_PATH} — очередь роликов, поля вкладок, история задач и журнал Sync\n"
                f"• {VOICE_DATA_DIR} — сгенерированные голоса A/B\n\n"
                "Ключи API и настройки не трогаются. Готовые ролики в папках партий остаются.\n"
                "После очистки забрать результат прерванного ролика из Sync будет нельзя.\n\n"
                "Продолжить?", icon="warning", parent=self):
            return
        if self._state_save_job is not None:
            self.after_cancel(self._state_save_job)
            self._state_save_job = None
        self._state_ready = False
        removed = self._state_store.clear()
        try:
            voices = len(os.listdir(VOICE_DATA_DIR))
        except OSError:
            voices = 0
        shutil.rmtree(VOICE_DATA_DIR, ignore_errors=True)
        self.background_tasks.clear_finished()
        self.background_tasks.drain_changes()
        with self._journal_lock:
            self._sync_journal = []
            self._journal_dirty = False
        self._panel_state = {}
        self._compress_cancel_estimates()
        with self._compress_lock:
            self._compress_info = {}
            self._compress_est = {}
        # Панели вкладок пересоздаются пустыми при следующем открытии.
        for name in [n for n in self._panels if n != "scene"]:
            self._panels.pop(name).destroy()
            self._panel_renderers.pop(name, None)
        self._vg = None
        self._settings_widgets = None
        self.rolls = []
        self.next_roll_id = 1
        self._state_ready = True
        self._apply_nav_collapsed()
        self.add_roll()
        self.log(f"Сохранённые данные очищены: файлов состояния {removed}, голосовых файлов {voices}")
        messagebox.showinfo("Сохранённые данные", "Готово: сохранённые данные очищены.", parent=self)

    # ── Забор результата прерванного ролика из Sync ───────────────────────
    def _build_sync_fetch_row(self, parent, roll):
        with self._journal_lock:
            journal = list(self._sync_journal)
        parts = sync_jobs_for_roll(journal, roll["id"])
        box = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=10)
        box.pack(fill="x", padx=16, pady=(0, 10))
        if not parts:
            self.label(box, "Ролик прерван до создания задачи в Sync — оплаты не было. "
                            "Его можно просто запустить заново.",
                       size=12, color=MUTED).pack(
                anchor="w", fill="x", padx=12, pady=10)
            return
        jobs = " · ".join(f"{SYNC_PART_LABELS.get(k, k)}: {v['job_id']}" for k, v in parts.items())
        self.label(box, f"Задача уже создана в Sync ({jobs}). Результат можно забрать без новой оплаты.",
                   size=12, color=MUTED).pack(anchor="w", padx=12, pady=(10, 4))
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 10))
        btn = self.button(row, "Забрать результат из Sync",
                          lambda rid=roll["id"]: self.fetch_roll_from_sync(rid),
                          color=BTN_OK, hover=BTN_OK_HOVER, width=220)
        btn.pack(side="left")
        lbl = self.label(row, "", size=12, color=MUTED)
        lbl.pack(side="left", padx=12)
        self._sync_fetch_widgets = {"roll_id": roll["id"], "button": btn, "label": lbl}
        self._render_sync_fetch()

    def _render_sync_fetch(self):
        w = self._sync_fetch_widgets
        if not w or not w["button"].winfo_exists():
            return
        task = self.background_tasks.latest("sync_fetch", roll_id=w["roll_id"])
        busy = bool(task and task["status"] not in TASK_FINISHED)
        w["button"].configure(state="disabled" if busy else "normal",
                              text="Проверяю…" if busy else "Забрать результат из Sync")
        if task is None:
            w["label"].configure(text="")
            return
        color = {"done": "#86efac", "error": "#fca5a5"}.get(task["status"], "#fcd34d")
        if task["status"] == "interrupted":
            text = "Прервано: приложение закрыли — нажми ещё раз"
        else:
            text = task.get("message") or ""
        w["label"].configure(text=text, text_color=color)

    def fetch_roll_from_sync(self, roll_id):
        roll = next((r for r in self.rolls if r["id"] == roll_id), None)
        if roll is None:
            return
        if self.is_processing:
            messagebox.showwarning("Sync", "Дождись окончания очереди.", parent=self)
            return
        if not SYNC_API_KEY:
            messagebox.showerror("Sync", "Сначала укажи Sync API key.", parent=self)
            return
        if self.background_tasks.active(kind="sync_fetch") and \
                self.background_tasks.latest("sync_fetch", roll_id=roll_id)["status"] not in TASK_FINISHED:
            return
        with self._journal_lock:
            parts = sync_jobs_for_roll(list(self._sync_journal), roll_id)
        expected = ["single"] if roll["mode"] == "1 видео" else ["start", "end"]
        tasks = self.background_tasks
        task_id = tasks.create("sync_fetch", "scene", title=f"Забрать из Sync: ролик {roll_id}",
                               meta={"roll_id": roll_id}, status="running")
        self._sync_fetch_pending.add(task_id)
        tasks.update(task_id, message="Запрашиваю статус задачи в Sync…")
        api_key = SYNC_API_KEY

        def worker():
            results = {}
            try:
                for part in expected:
                    label = SYNC_PART_LABELS[part]
                    info = parts.get(part)
                    if info is None:
                        results[part] = {"state": "missing",
                                         "message": "в Sync не отправлялась (очередь прервали раньше). "
                                                    "Чтобы её сделать, ролик нужно запустить заново — "
                                                    "это новые задачи и новая оплата."}
                        continue
                    if info.get("saved_path") and os.path.exists(info["saved_path"]):
                        results[part] = {"state": "saved", "path": info["saved_path"],
                                         "message": f"уже забран: {info['saved_path']}"}
                        continue
                    tasks.update(task_id, message=f"{label.capitalize()}: запрашиваю статус…", progress=None)

                    def progress(f, label=label):
                        tasks.update(task_id, progress=f, message=f"{label.capitalize()}: скачиваю {f:.0%}")

                    results[part] = fetch_sync_result(info, api_key, self.log, progress)
                    self.log(f"Забрать из Sync, ролик {roll_id}, {label}: {results[part]['message']}")
            except Exception as e:
                self.log(f"Забрать из Sync, ролик {roll_id}: ошибка {e}")
                error_reporter.report("app.fetch_roll_from_sync", exc=e)
                results["_error"] = {"state": "error", "message": str(e)}
            states = {r["state"] for r in results.values()}
            all_saved = states == {"saved"}
            if all_saved:
                short = "✓ Результат сохранён в папку партии"
            elif "pending" in states:
                short = "Sync ещё обрабатывает задачу — повтори позже"
            elif "failed" in states:
                short = "Sync не сделал ролик — нужна новая оплата"
            else:
                short = "Не удалось забрать — подробности в окне и в логе"
            # Итог покажет главный поток, когда менеджер задач сообщит о завершении.
            tasks.update(task_id, status="done" if all_saved else "error", progress=None, message=short,
                         result={"parts": results, "all_saved": all_saved})

        threading.Thread(target=worker, daemon=True).start()

    def _finish_sync_fetch(self, task):
        roll_id = task["meta"].get("roll_id")
        results = (task.get("result") or {}).get("parts") or {}
        all_saved = bool((task.get("result") or {}).get("all_saved"))
        idx = next((i for i, r in enumerate(self.rolls) if r["id"] == roll_id), None)
        lines = [f"{SYNC_PART_LABELS.get(k, 'ошибка').capitalize()}: {r['message']}" for k, r in results.items()]
        text = "\n\n".join(lines)
        if all_saved:
            if idx is not None:
                self.set_roll_status(idx, "Готово")
            messagebox.showinfo("Результат из Sync", text, parent=self)
        elif any(r["state"] == "failed" for r in results.values()):
            messagebox.showerror("Результат из Sync", text, parent=self)
        else:
            messagebox.showwarning("Результат из Sync", text, parent=self)

    # ── Итог клонирования: голос в генератор, очистка текста ─────────────
    def _on_spellcheck_toggle(self):
        self.spellcheck_online = bool(self.spellcheck_online_var.get())
        self.save_config()
        self.log(f"Проверка орфографии онлайн: {'включена' if self.spellcheck_online else 'выключена'}")

    # ── Прозрачность отчётов: пример и тестовая отправка ─────────────────
    REPORT_EXAMPLE_ERROR = ('ElevenLabs TTS error: 400\n{"detail":{"status":"invalid_text",'
                            '"message":"text: здесь был бы текст вашего сценария"}}')

    def report_example_payload(self):
        """Ровно то, что ушло бы при такой ошибке сейчас: с вашим текущим логом, после санитайзера."""
        return error_reporter.build_payload("app.text_to_speech_mp3:0", "RuntimeError", self.REPORT_EXAMPLE_ERROR)

    def show_report_example(self):
        payload = self.report_example_payload()
        win = ctk.CTkToplevel(self)
        win.title("Пример отчёта об ошибке")
        win.configure(fg_color=BG)
        win.transient(self)
        win.geometry("640x560")
        state = "включена" if self.error_reports_enabled else "выключена"
        info = self.label(win, "Так выглядит отчёт, который уйдёт разработчику при сбое генерации голоса — "
                               f"с вашим текущим логом, после очистки. Отправка сейчас {state}. "
                               "Текст сценария из ответа сервиса в отчёт не попадает.", size=12, color=MUTED)
        info.configure(wraplength=600)
        info.pack(anchor="w", fill="x", padx=16, pady=(14, 6))
        box = ctk.CTkTextbox(win, fg_color="#111111", text_color="#e5e5e5",
                             font=ctk.CTkFont(family="Courier New", size=12))
        box.pack(fill="both", expand=True, padx=16)
        box.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        box.configure(state="disabled")
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=12)
        self.button(row, "Закрыть", win.destroy, color=BTN, hover=BTN_HOVER, width=110).pack(side="right")
        self.button(row, "Копировать", lambda: (self.clipboard_clear(),
                                                self.clipboard_append(box.get("1.0", "end").strip())),
                    color=BTN, hover=BTN_HOVER, width=120).pack(side="right", padx=8)
        self._report_example_win = win
        return win

    def test_report_payload(self):
        payload = error_reporter.build_payload("app.settings_test", "TestReport",
                                               "Тестовый отчёт из «Настроек»: проверка связи")
        payload["message"] = "[ТЕСТ] " + payload["message"]
        payload["log_tail"] = []          # тест — без хвоста лога
        return payload

    def send_test_report(self):
        if not ERROR_REPORT_URL:
            messagebox.showerror("Тестовый отчёт", "Адрес воркера не задан — отправлять некуда.", parent=self)
            return
        payload = self.test_report_payload()

        def work():
            try:
                res = requests.post(ERROR_REPORT_URL, json=payload, timeout=ERROR_REPORT_TIMEOUT_SEC)
                ok = 200 <= res.status_code < 300
                text = (f"Отправлено (HTTP {res.status_code})." if ok
                        else f"Ошибка: воркер ответил HTTP {res.status_code}.")
            except Exception as e:
                ok, text = False, f"Ошибка: {type(e).__name__} — нет связи с воркером."
            write_log_file(f"Тестовый отчёт: {text}")
            self.after(0, lambda: (messagebox.showinfo if ok else messagebox.showerror)(
                "Тестовый отчёт", text, parent=self))

        threading.Thread(target=work, daemon=True).start()

    def _on_clear_after_clone_toggle(self):
        self.clear_text_after_clone = bool(self.clear_text_after_clone_var.get())
        self.save_config()

    def _on_clone_finished(self, task):
        """Главный поток. Успех: голос в генератор (в данных — даже если вкладки ещё нет), текст
        очищается (по настройке) — только если голос подставился. Ошибка или прерывание: ничего не меняется."""
        if task["status"] != "done":
            return
        result = task.get("result") or {}
        name = result.get("name") or task.get("meta", {}).get("name") or ""
        try:
            voice_id = voice_id_from_clone_response(result.get("voice_id") or result)
            applied = self._apply_cloned_voice(name, voice_id)
        except Exception as e:
            voice_id = str(result.get("voice_id") or "")
            applied = False
            self.log(f"Клон готов, но подставить его в генератор не удалось: {type(e).__name__}")
            error_reporter.report("app._on_clone_finished", exc=e)
            messagebox.showwarning("Клонирование", f"Голос «{name}» создан, но подставить его в генератор "
                                   "не удалось. Текст генерации не тронут — выбери голос вручную: "
                                   "«Выбрать из списка моих голосов».", parent=self)
        if self._clone_cleanup is not None:
            try:
                self._clone_cleanup()
            except Exception:
                pass
        if self._active_panel == "voice_clone":
            if applied:
                messagebox.showinfo("Готово", f"Голос «{name}» клонирован.\n\nvoice_id: {voice_id}", parent=self)
            self.after(300, self.show_voice_gen_panel)
        elif applied:
            self._show_notice(f"Голос «{name}» готов", "Открыть генерацию", self.show_voice_gen_panel)
        else:
            self._show_notice(f"Голос «{name}» создан — выбери его в генерации вручную", "Открыть генерацию",
                              self.show_voice_gen_panel)

    def _apply_cloned_voice(self, name, voice_id):
        """Шаг 1 — голос (данные и поле, если вкладка уже есть). Шаг 2 — только если шаг 1 удался:
        очистка текста с резервом «Вернуть». Ошибка на любом шаге не роняет приложение и не теряет текст.
        Возвращает True, если голос подставлен."""
        state = self._panel_state.setdefault("voice_gen", {})
        prev_state = {k: state.get(k) for k in ("voice", "voice_id")}
        prev_main = (self.main_voice_name, self.main_voice_id)
        vg = getattr(self, "_vg", None)
        try:
            if not name:
                raise ValueError("нет имени голоса")
            voice_id = voice_id_from_clone_response(voice_id)
            if vg and vg["voice_box"].winfo_exists():
                vg["voice_box"].delete("1.0", "end")
                vg["voice_box"].insert("1.0", name)
            state["voice"], state["voice_id"] = name, voice_id
            self.main_voice_name, self.main_voice_id = name, voice_id
        except Exception as e:
            for k, v in prev_state.items():          # голос — как был; текст не трогаем
                if v is None:
                    state.pop(k, None)
                else:
                    state[k] = v
            self.main_voice_name, self.main_voice_id = prev_main
            if vg and vg["voice_box"].winfo_exists() and prev_state.get("voice") is not None:
                try:
                    vg["voice_box"].delete("1.0", "end")
                    vg["voice_box"].insert("1.0", prev_state["voice"])
                except Exception:
                    pass
            self.log(f"Подставить клон в генератор не удалось ({type(e).__name__}) — голос и текст не тронуты")
            error_reporter.report("app._apply_cloned_voice", exc=e)
            messagebox.showwarning("Клонирование", f"Голос «{name}» создан, но подставить его в генератор не "
                                   "удалось. Текст генерации не тронут — выбери голос вручную: "
                                   "«Выбрать из списка моих голосов».", parent=self)
            self._schedule_state_save()
            return False

        cleared = False
        widget = vg["text_box"] if vg and vg["text_box"].winfo_exists() else None
        current = widget.get("1.0", "end").rstrip("\n") if widget is not None else (state.get("text") or "")
        if self.clear_text_after_clone and current.strip():
            try:
                state["text_backup"] = current       # «Вернуть» — и после перезапуска
                state["text"] = ""
                if widget is not None:
                    widget.delete("1.0", "end")
                    vg["update_status"]()
                cleared = True
            except Exception as e:
                state["text"] = current               # текст — как был
                state.pop("text_backup", None)
                if widget is not None:
                    try:
                        if not widget.get("1.0", "end").strip():
                            widget.insert("1.0", current)
                    except Exception:
                        pass
                self.log(f"Очистить текст после клонирования не удалось ({type(e).__name__}) — текст оставлен")
        try:
            if vg and vg["voice_box"].winfo_exists():
                vg["render_restore_notice"]()
        except Exception:
            pass
        self._panel_state_set("voice_gen")
        self.save_config()
        self.log(f"Клон «{name}» подставлен в генератор" + (", текст очищен (можно вернуть)" if cleared else ""))
        return True

    def _build_notice_bar(self, parent, before):
        bar = ctk.CTkFrame(parent, fg_color="#1f3a2e", corner_radius=12)
        lbl = self.label(bar, "", size=13, color="#86efac")
        lbl.pack(side="left", padx=14, pady=8)
        close = ctk.CTkButton(bar, text="✕", width=28, height=24, fg_color="transparent", hover_color=BTN_HOVER,
                              text_color=TEXT, command=self._hide_notice)
        close.pack(side="right", padx=(4, 10))
        action = ctk.CTkButton(bar, text="", width=150, height=26, fg_color=BTN_OK, hover_color=BTN_OK_HOVER,
                               text_color="white", corner_radius=10)
        action.pack(side="right")
        self._notice_w = {"bar": bar, "label": lbl, "action": action, "before": before}

    def _show_notice(self, text, action_text, action):
        """Ненавязчивое уведомление внизу окна: не модальное, фокус не забирает."""
        w = self._notice_w
        if not w:
            return
        w["label"].configure(text=text)

        def run():
            self._hide_notice()
            action()
        w["action"].configure(text=action_text, command=run)
        if not w["bar"].winfo_manager():
            w["bar"].pack(fill="x", padx=20, pady=(4, 0), before=w["before"])

    def _hide_notice(self):
        if self._notice_w and self._notice_w["bar"].winfo_manager():
            self._notice_w["bar"].pack_forget()

    def _panel_state_set(self, panel, **fields):
        self._panel_state.setdefault(panel, {}).update(fields)
        self._schedule_state_save()

    def _panel_state_get(self, panel, key, default=None):
        return self._panel_state.get(panel, {}).get(key, default)

    def show_api_key_panel(self):
        scroll = self._start_panel("api")
        if scroll is None:
            return
        self._settings_back_link(scroll)
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        self.label(frame, "ElevenLabs API key", size=22, weight="bold").pack(
            anchor="w", padx=24, pady=(22, 6))
        self.label(
            frame,
            "Нужен только для «Сгенерировать голос». Для готового full_voice.mp3 — не используется.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=24, pady=(0, 18))
        self.label(frame, "API key", size=14, weight="bold").pack(anchor="w", padx=24)
        api_box = ctk.CTkTextbox(
            frame, height=80, fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=13),
        )
        api_box.pack(fill="x", padx=24, pady=(6, 14))
        if self.api_key:
            api_box.insert("1.0", self.api_key)
        self._fix_paste(api_box)
        result_label = self.label(frame, "", size=13, color=MUTED)
        result_label.pack(anchor="w", padx=24, pady=(0, 6))

        def save():
            api = api_box.get("1.0", "end").strip()
            if not api:
                messagebox.showerror("Ошибка", "Введите ElevenLabs API key.", parent=self)
                return
            self.api_key = api
            self.save_config()
            self.update_status()
            self._refresh_balance_badge()
            result_label.configure(text="✓ Ключ сохранён", text_color="#86efac")

        self.button(frame, "Сохранить ключ", save,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=190).pack(
            anchor="e", padx=24, pady=(0, 20))

    def show_sync_key_panel(self):
        scroll = self._start_panel("sync")
        if scroll is None:
            return
        self._settings_back_link(scroll)
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        self.label(frame, "Sync.so API key", size=22, weight="bold").pack(
            anchor="w", padx=24, pady=(22, 6))
        self.label(
            frame,
            "Нужен для lipsync. Берётся в личном кабинете sync.so.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=24, pady=(0, 18))
        self.label(frame, "API key", size=14, weight="bold").pack(anchor="w", padx=24)
        sync_box = ctk.CTkTextbox(
            frame, height=80, fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=13),
        )
        sync_box.pack(fill="x", padx=24, pady=(6, 14))
        if self.sync_key and "ВСТАВЬ" not in self.sync_key:
            sync_box.insert("1.0", self.sync_key)
        self._fix_paste(sync_box)
        result_label = self.label(frame, "", size=13, color=MUTED)
        result_label.pack(anchor="w", padx=24, pady=(0, 6))

        def save():
            key = sync_box.get("1.0", "end").strip()
            if not key:
                messagebox.showerror("Ошибка", "Введите Sync API key.", parent=self)
                return
            self.sync_key = key
            global SYNC_API_KEY
            SYNC_API_KEY = key
            self.save_config()
            with self._estimate_lock:
                self._estimates.clear()  # пересчитать по данным Sync с новым ключом
            self.update_status()
            result_label.configure(text="✓ Ключ сохранён", text_color="#86efac")

        self.button(frame, "Сохранить ключ", save,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=190).pack(
            anchor="e", padx=24, pady=(0, 20))

    def show_cloudinary_panel(self):
        scroll = self._start_panel("cloudinary")
        if scroll is None:
            return
        self._settings_back_link(scroll)
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        self.label(frame, "Cloudinary", size=22, weight="bold").pack(
            anchor="w", padx=24, pady=(22, 6))
        self.label(
            frame,
            "Нужен для загрузки файлов в Sync. Берётся в личном кабинете cloudinary.com.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=24, pady=(0, 18))

        def _field(label_text, value):
            self.label(frame, label_text, size=13, weight="bold").pack(anchor="w", padx=24)
            box = ctk.CTkTextbox(
                frame, height=44, fg_color="#ffffff", text_color="#111111",
                border_width=1, border_color="#737373",
                corner_radius=10, font=ctk.CTkFont(size=13),
            )
            box.pack(fill="x", padx=24, pady=(4, 14))
            if value:
                box.insert("1.0", value)
            return box

        cloud_box  = _field("Cloud Name",  self.cloudinary_cloud_name)
        key_box    = _field("API Key",      self.cloudinary_api_key)
        secret_box = _field("API Secret",   self.cloudinary_api_secret)
        self._fix_paste(cloud_box)
        self._fix_paste(key_box)
        self._fix_paste(secret_box)

        result_label = self.label(frame, "", size=13, color=MUTED)
        result_label.pack(anchor="w", padx=24, pady=(0, 6))

        def save():
            cloud  = cloud_box.get("1.0", "end").strip()
            key    = key_box.get("1.0", "end").strip()
            secret = secret_box.get("1.0", "end").strip()
            if not cloud or not key or not secret:
                messagebox.showerror("Ошибка", "Заполните все три поля.", parent=self)
                return
            self.cloudinary_cloud_name = cloud
            self.cloudinary_api_key    = key
            self.cloudinary_api_secret = secret
            global CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
            CLOUDINARY_CLOUD_NAME  = cloud
            CLOUDINARY_API_KEY     = key
            CLOUDINARY_API_SECRET  = secret
            self.save_config()
            self.update_status()
            result_label.configure(text="✓ Ключи сохранены", text_color="#86efac")

        self.button(frame, "Сохранить ключи", save,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=200).pack(
            anchor="e", padx=24, pady=(0, 20))

    def show_groq_key_panel(self):
        scroll = self._start_panel("groq")
        if scroll is None:
            return
        self._settings_back_link(scroll)
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        self.label(frame, "Groq API key", size=22, weight="bold").pack(
            anchor="w", padx=24, pady=(22, 6))
        self.label(
            frame,
            "Бесплатно. Регистрация на groq.com → API Keys",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=24, pady=(0, 18))
        self.label(frame, "API key (начинается с gsk_...)", size=14, weight="bold").pack(
            anchor="w", padx=24)
        groq_box = ctk.CTkTextbox(
            frame, height=80, fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=13),
        )
        groq_box.pack(fill="x", padx=24, pady=(6, 14))
        if self.groq_api_key:
            groq_box.insert("1.0", self.groq_api_key)
        self._fix_paste(groq_box)
        result_label = self.label(frame, "", size=13, color=MUTED)
        result_label.pack(anchor="w", padx=24, pady=(0, 6))

        def save():
            key = groq_box.get("1.0", "end").strip()
            if not key:
                messagebox.showerror("Ошибка", "Введите Groq API key.", parent=self)
                return
            self.groq_api_key = key
            self.save_config()
            self.update_status()
            result_label.configure(text="✓ Ключ сохранён", text_color="#86efac")

        self.button(frame, "Сохранить ключ", save,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=190).pack(
            anchor="e", padx=24, pady=(0, 20))

    def fetch_voice_id_by_name(self, name):
        for v in self.fetch_all_voices():
            if v["name"].lower() == name.lower():
                return v["voice_id"]
        return None
    
    def fetch_all_voices(self):
        """Возвращает список словарей {name, voice_id, category}."""
        if not self.api_key:
            raise RuntimeError("Сначала укажи ElevenLabs API key.")
        url = f"{ELEVEN_BASE_URL}/v1/voices"
        headers = {"xi-api-key": self.api_key}
        res = requests.get(url, headers=headers, timeout=60)
        if res.status_code != 200:
            raise RuntimeError(f"{res.status_code}: {res.text[:300]}")
        voices = res.json().get("voices", [])
        result = []
        for v in voices:
            name = v.get("name", "").strip()
            vid = v.get("voice_id", "")
            category = v.get("category", "")
            result.append({
                "name": name,
                "voice_id": vid,
                "category": category,
                "preview_url": v.get("preview_url", ""),
                "created_at": v.get("created_at_unix", 0),
            })
        return result
    
    def create_voice_from_sample(self, audio_path, voice_name, description=""):
        """Загружает аудио в ElevenLabs и создаёт клон. Возвращает voice_id."""
        if not self.api_key:
            raise RuntimeError("Сначала укажи ElevenLabs API key.")
        if not os.path.exists(audio_path):
            raise RuntimeError(f"Файл не найден: {audio_path}")

        url = f"{ELEVEN_BASE_URL}/v1/voices/add"
        headers = {"xi-api-key": self.api_key}
        data = {
            "name": voice_name,
            "description": description or "Клонирован через SheqelMotion Studio",
            "remove_background_noise": "true",
        }

        with open(audio_path, "rb") as f:
            files = [("files", (Path(audio_path).name, f, "audio/mpeg"))]
            res = requests.post(url, headers=headers, data=data, files=files, timeout=300)

        if res.status_code not in [200, 201]:
            try:
                err = res.json()
                detail = err.get("detail", {})
                status = detail.get("status", "")
                message = detail.get("message", res.text)
                if status == "missing_permissions":
                    raise RuntimeError(
                        "Нет прав на клонирование. В API ключе поставь Voices: Write. "
                        "Если стоит — проверь что план Creator или выше."
                    )
                raise RuntimeError(f"{res.status_code}: {message}")
            except ValueError:
                raise RuntimeError(f"{res.status_code}: {res.text[:300]}")

        try:
            return voice_id_from_clone_response(res.json())
        except ValueError:
            raise RuntimeError("ElevenLabs не вернул voice_id в ответе на клонирование")
    
    def show_voice_clone_panel(self):
        if not self.api_key:
            messagebox.showerror("Ошибка", "Сначала добавь ElevenLabs API key: «Настройки» → «Ключи» → ElevenLabs.", parent=self)
            self.show_api_key_panel()
            return
        scroll = self._start_panel("voice_clone")
        if scroll is None:
            return
        tasks = self.background_tasks
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        self.label(frame, "🎙 Клонировать голос", size=22, weight="bold").pack(
            anchor="w", padx=22, pady=(20, 6))
        self.label(
            frame,
            "Загрузи 1-3 минуты чистой речи без шума и музыки. "
            "Поддерживаются mp3, wav, m4a, flac.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=22, pady=(0, 16))

        # ── Переключатель источника ────────────────────────────────────────
        source_var = ctk.StringVar(value="audio")
        source_row = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=12)
        source_row.pack(fill="x", padx=22, pady=(0, 16))

        audio_btn = ctk.CTkButton(
            source_row, text="🎵 Аудио файл",
            fg_color=BTN_OK, hover_color=BTN_OK_HOVER,
            font=ctk.CTkFont(size=13), height=36, corner_radius=10,
        )
        audio_btn.pack(side="left", padx=(8, 4), pady=8, expand=True, fill="x")

        video_btn = ctk.CTkButton(
            source_row, text="🎬 Видео файл",
            fg_color=BTN, hover_color=BTN_HOVER,
            font=ctk.CTkFont(size=13), height=36, corner_radius=10,
        )
        video_btn.pack(side="left", padx=(4, 8), pady=8, expand=True, fill="x")

        # ── Имя и описание ─────────────────────────────────────────────────
        self.label(frame, "Имя голоса", size=14, weight="bold").pack(anchor="w", padx=22)
        name_entry = ctk.CTkEntry(
            frame, placeholder_text="например: Иван — диктор",
            fg_color="white", text_color="#111", border_color="#737373",
            font=ctk.CTkFont(size=13),
        )
        name_entry.pack(fill="x", padx=22, pady=(6, 14))
        self._fix_paste(name_entry)

        self.label(frame, "Описание (опционально)", size=14, weight="bold").pack(anchor="w", padx=22)
        desc_entry = ctk.CTkEntry(
            frame, placeholder_text="например: тёплый низкий мужской голос",
            fg_color="white", text_color="#111", border_color="#737373",
            font=ctk.CTkFont(size=13),
        )
        desc_entry.pack(fill="x", padx=22, pady=(6, 14))
        self._fix_paste(desc_entry)

        selected = {"path": ""}
        extracted = {"tmp_dir": None}
        extracting = {"active": False}

        # ── Аудио блок ─────────────────────────────────────────────────────
        audio_block = ctk.CTkFrame(frame, fg_color="transparent")

        self.label(audio_block, "Аудио-сэмпл", size=14, weight="bold").pack(anchor="w")
        audio_file_row = ctk.CTkFrame(audio_block, fg_color=PANEL)
        audio_file_row.pack(fill="x", pady=(6, 0))

        audio_file_label = ctk.CTkLabel(
            audio_file_row, text="  не выбран",
            fg_color="#f5f5f5", text_color="#888",
            anchor="w", corner_radius=6,
            font=ctk.CTkFont(size=12), height=32,
        )
        audio_file_label.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)

        def choose_audio():
            f = filedialog.askopenfilename(
                parent=self, title="Выбери аудио-сэмпл",
                filetypes=[("Аудио", "*.mp3 *.wav *.m4a *.flac *.ogg"), ("Все файлы", "*.*")],
            )
            if f:
                selected["path"] = f
                try:
                    size_mb = os.path.getsize(f) / (1024 * 1024)
                    audio_file_label.configure(
                        text=f"  {Path(f).name}  ({size_mb:.1f} МБ)", text_color="#111")
                except Exception:
                    audio_file_label.configure(text=f"  {Path(f).name}", text_color="#111")

        self.button(audio_file_row, "Выбрать файл", choose_audio,
                    color=BTN, hover=BTN_HOVER, width=130).pack(side="right")

        # ── Видео блок ─────────────────────────────────────────────────────
        video_block = ctk.CTkFrame(frame, fg_color="transparent")

        self.label(video_block, "Видео файл", size=14, weight="bold").pack(anchor="w")
        video_file_row = ctk.CTkFrame(video_block, fg_color=PANEL)
        video_file_row.pack(fill="x", pady=(6, 0))

        video_file_label = ctk.CTkLabel(
            video_file_row, text="  не выбран",
            fg_color="#f5f5f5", text_color="#888",
            anchor="w", corner_radius=6,
            font=ctk.CTkFont(size=12), height=32,
        )
        video_file_label.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)

        extract_status_label = self.label(
            video_block,
            "Выбери видео — аудио будет извлечено автоматически",
            size=12, color=MUTED,
        )
        extract_status_label.pack(anchor="w", pady=(8, 0))

        def _cleanup_extracted():
            if extracting["active"]:
                # ffmpeg ещё пишет в папку — удалим, когда извлечение завершится.
                extracted["cleanup_pending"] = True
                return
            extracted["cleanup_pending"] = False
            if extracted["tmp_dir"]:
                shutil.rmtree(extracted["tmp_dir"], ignore_errors=True)
                extracted["tmp_dir"] = None

        def extract_audio_from_video(video_path):
            tmp_dir = tempfile.mkdtemp()
            extracted["tmp_dir"] = tmp_dir
            raw_audio = str(Path(tmp_dir) / "extracted.mp3")
            run([ffmpeg_bin(), "-y", "-i", video_path,
                 "-vn", "-ar", "44100", "-ac", "1", "-q:a", "0", raw_audio])
            return raw_audio

        def choose_video():
            f = filedialog.askopenfilename(
                parent=self, title="Выбери видео файл",
                filetypes=[("Видео", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm"), ("Все файлы", "*.*")],
            )
            if not f or extracting["active"]:
                return
            _cleanup_extracted()  # прошлое извлечённое аудио больше не нужно
            selected["path"] = ""
            video_file_label.configure(text=f"  {Path(f).name}", text_color="#111")
            extracting["active"] = True
            task_id = tasks.create("clone_extract", "voice_clone", title=f"Аудио из {Path(f).name}",
                                   meta={"video": f}, status="running")
            render_clone()

            def worker():
                try:
                    out_path = extract_audio_from_video(f)
                    dur = get_duration(out_path)
                    size_mb = os.path.getsize(out_path) / (1024 * 1024)
                    tasks.update(task_id, status="done", progress=1.0,
                                 result={"path": out_path, "duration": dur, "size_mb": size_mb})
                except Exception as e:
                    tasks.update(task_id, status="error", error=str(e))
                finally:
                    extracting["active"] = False
                    if extracted.get("cleanup_pending"):
                        _cleanup_extracted()

            threading.Thread(target=worker, daemon=True).start()

        applied = {"task": None}

        def render_clone():
            render_upload()
            task = tasks.latest("clone_extract")
            busy = bool(task and task["status"] in ("queued", "running"))
            uploading = bool(tasks.active(kind="clone_upload"))
            clone_btn_ref[0].configure(state="disabled" if busy or uploading else "normal",
                                       text="Загружаю…" if uploading else "🎙 Клонировать")
            if task is None:
                return
            if busy:
                extract_status_label.configure(text="Извлекаю аудио... (можно переключаться на другие вкладки)",
                                               text_color=MUTED)
            elif task["status"] == "done":
                res = task.get("result") or {}
                if not res.get("path") or not os.path.exists(res["path"]):
                    extract_status_label.configure(text="Извлечённое аудио не найдено — выбери видео заново",
                                                   text_color="#fcd34d")
                    return
                extract_status_label.configure(
                    text=f"✓ Аудио извлечено: {res.get('duration') or 0:.1f} сек · {res.get('size_mb') or 0:.1f} МБ",
                    text_color="#86efac")
                if applied["task"] != task["id"] and source_var.get() == "video":
                    selected["path"] = res["path"]
                    applied["task"] = task["id"]
            elif task["status"] == "interrupted":
                extract_status_label.configure(text="Прервано: приложение закрыли — выбери видео заново",
                                               text_color="#fcd34d")
            else:
                extract_status_label.configure(text=f"Ошибка извлечения: {task.get('error')}",
                                               text_color="#fca5a5")


        self.button(video_file_row, "Выбрать файл", choose_video,
                    color=BTN, hover=BTN_HOVER, width=130).pack(side="right")

        # ── Переключение блоков ────────────────────────────────────────────
        def switch_source(mode):
            source_var.set(mode)
            if mode == "audio":
                video_block.pack_forget()
                audio_block.pack(fill="x", padx=22, pady=(0, 14))
                audio_btn.configure(fg_color=BTN_OK, hover_color=BTN_OK_HOVER)
                video_btn.configure(fg_color=BTN, hover_color=BTN_HOVER)
            else:
                audio_block.pack_forget()
                video_block.pack(fill="x", padx=22, pady=(0, 14))
                video_btn.configure(fg_color=BTN_OK, hover_color=BTN_OK_HOVER)
                audio_btn.configure(fg_color=BTN, hover_color=BTN_HOVER)
            selected["path"] = ""

        audio_btn.configure(command=lambda: switch_source("audio"))
        video_btn.configure(command=lambda: switch_source("video"))

        # Показываем аудио блок по умолчанию
        audio_block.pack(fill="x", padx=22, pady=(0, 14))

        result_label = self.label(frame, "", size=12, color=MUTED)
        result_label.pack(anchor="w", padx=22, pady=(0, 8))
        self._add_copy_menu(result_label, lambda lbl=result_label: lbl.cget("text"))

        def clone():
            name = name_entry.get().strip()
            description = desc_entry.get().strip()
            path = selected["path"]
            if not name:
                messagebox.showerror("Ошибка", "Введи имя голоса.", parent=self)
                return
            if not path:
                messagebox.showerror("Ошибка", "Выбери аудио-сэмпл.", parent=self)
                return
            if tasks.active(kind="clone_upload"):
                return
            # Загрузка в ElevenLabs — в фоне: окно не зависает, вкладки можно переключать.
            task_id = tasks.create("clone_upload", "voice_clone", title=f"Клон «{name}»",
                                   meta={"name": name}, status="running")
            clone_state["announce"] = task_id
            self._clone_pending.add(task_id)
            render_clone()

            def worker():
                try:
                    voice_id = self.create_voice_from_sample(path, name, description)
                    self._expiry_add({"voice_id": voice_id, "name": name,
                                      "expires_at": time.time() + 10 * 60 * 60})
                    self.save_config()
                    # Голос и текст генератора меняет главный поток (_on_clone_finished) — при любой вкладке.
                    tasks.update(task_id, status="done", result={"voice_id": voice_id, "name": name})
                except Exception as e:
                    tasks.update(task_id, status="error", error=str(e))

            threading.Thread(target=worker, daemon=True).start()

        clone_state = {"announce": None}

        def render_upload():
            up = tasks.latest("clone_upload")
            if up is None:
                return
            st = up["status"]
            name = (up.get("result") or {}).get("name") or up["meta"].get("name", "")
            if st in ("queued", "running"):
                result_label.configure(text="Загружаю в ElevenLabs... (можно переключаться на другие вкладки)",
                                       text_color=MUTED)
            elif st == "done":
                result_label.configure(text=f"✓ Голос «{name}» будет автоудалён через 10 часов",
                                       text_color="#fcd34d")
            elif st == "interrupted":
                result_label.configure(text="Прервано: приложение закрыли во время загрузки. Проверь список "
                                            "голосов — клон мог успеть создаться.", text_color="#fcd34d")
            else:
                result_label.configure(text=f"Ошибка: {up.get('error')}", text_color="#fca5a5")
            if clone_state["announce"] == up["id"] and st in TASK_FINISHED:
                clone_state["announce"] = None      # итог — один раз
                # Успех применяет _on_clone_finished (при любой вкладке); здесь — только ошибка.
                if st == "error":
                    messagebox.showerror("Ошибка", str(up.get("error")), parent=self)

        clone_btn = self.button(frame, "🎙 Клонировать", clone,
                                color=BTN_OK, hover=BTN_OK_HOVER, width=200)
        clone_btn.pack(anchor="e", padx=22, pady=(0, 20))
        clone_btn_ref = [clone_btn]

        frame.bind("<Destroy>", lambda e: _cleanup_extracted())
        self._clone_cleanup = _cleanup_extracted
        self._register_renderer("voice_clone", render_clone)

    def show_downloader_panel(self):
        try:
            _ensure_ytdlp()
        except RuntimeError as e:
            messagebox.showerror("yt-dlp не найден", str(e), parent=self)
            return

        outer = self._start_panel("downloader")
        if outer is None:
            return
        tasks = self.background_tasks

        # ── Заголовок ──────────────────────────────────────────────────────
        self.label(outer, "⬇ Скачать видео / аудио", size=20, weight="bold").pack(
            anchor="w", padx=20, pady=(16, 2))
        self.label(
            outer,
            "TikTok · YouTube · Instagram · Twitter · почем и тысячи других сайтов.",
            size=12, color=MUTED,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        # ── Поле для ссылок ────────────────────────────────────────────────
        self.label(outer, "Ссылки (по одной на строке):", size=13, weight="bold").pack(
            anchor="w", padx=20)
        url_box = ctk.CTkTextbox(
            outer, height=80,
            fg_color="white", text_color="#111",
            border_color="#737373", border_width=1,
            font=ctk.CTkFont(size=12),
        )
        url_box.pack(fill="x", padx=20, pady=(4, 10))

        # ── Cookies браузера ───────────────────────────────────────────────
        browser_status_label = self.label(outer, "🔍 Определяю браузер...", size=12, color=MUTED)
        browser_status_label.pack(anchor="w", padx=20, pady=(0, 4))
        cookies_used_lbl = self.label(outer, "", size=12, color=MUTED)   # какой браузер реально дал куки

        cookies_row = ctk.CTkFrame(outer, fg_color="transparent")
        cookies_row.pack(fill="x", padx=20, pady=(0, 4))

        use_cookies_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            cookies_row,
            text="Использовать cookies браузера (для видео с авторизацией)",
            variable=use_cookies_var,
            text_color=TEXT, fg_color=BTN_PRIMARY,
            font=ctk.CTkFont(size=12),
        ).pack(side="left")

        browser_override_var = ctk.StringVar(value="Авто")
        ctk.CTkOptionMenu(
            cookies_row,
            values=["Авто", "Chrome", "Firefox", "Safari", "Edge", "Brave", "Chromium", "Файл cookies.txt"],
            variable=browser_override_var,
            fg_color=BTN, button_color=BTN,
            button_hover_color=BTN_HOVER,
            width=150,
        ).pack(side="right")
        self.label(cookies_row, "Браузер:", size=12, color=MUTED).pack(side="right", padx=(0, 6))

        # ── Путь к cookies.txt (показывается только при выборе "Файл cookies.txt") ──
        cookies_file_var = ctk.StringVar(value="")
        cookies_file_row = ctk.CTkFrame(outer, fg_color="transparent")

        ctk.CTkEntry(
            cookies_file_row, textvariable=cookies_file_var,
            fg_color="white", text_color="#111",
            border_color="#737373", font=ctk.CTkFont(size=11),
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        def choose_cookies_file():
            f = filedialog.askopenfilename(
                parent=self, title="Выбери cookies.txt",
                filetypes=[("Cookies", "*.txt"), ("Все файлы", "*.*")],
            )
            if f:
                cookies_file_var.set(f)

        self.button(cookies_file_row, "Обзор...", choose_cookies_file,
                    color=BTN, hover=BTN_HOVER, width=90).pack(side="left")

        def _update_cookies_file_vis(*_):
            if browser_override_var.get() == "Файл cookies.txt":
                cookies_file_row.pack(fill="x", padx=20, pady=(0, 8))
            else:
                cookies_file_row.pack_forget()

        browser_override_var.trace_add("write", _update_cookies_file_vis)
        _update_cookies_file_vis()

        # Результат пишется в текущую метку: панель могла быть пересоздана (очистка данных),
        # пока поток искал браузер.
        self._browser_status_label = browser_status_label

        def _show_browser_status():
            lbl = self._browser_status_label
            if lbl is None or not lbl.winfo_exists():
                return
            browser = self._detected_browser
            if browser:
                lbl.configure(text=f"✓ Найден браузер с Google аккаунтом: {browser.capitalize()}",
                              text_color="#86efac")
            else:
                lbl.configure(text="⚠ Браузер с Google аккаунтом не найден — некоторые видео могут не скачаться",
                              text_color="#fcd34d")

        def _auto_detect_browser():
            self._browser_detecting = True
            try:
                self._detected_browser = find_best_browser_with_google()
            finally:
                self._browser_detecting = False
            self.after(0, _show_browser_status)

        if not self._browser_checked:
            self._browser_checked = True
            threading.Thread(target=_auto_detect_browser, daemon=True).start()
        elif not getattr(self, "_browser_detecting", False):
            _show_browser_status()

        def recheck():
            if getattr(self, "_browser_detecting", False):
                return
            self._browser_checked = True
            self._detected_browser = None
            browser_status_label.configure(text="🔍 Определяю браузер...", text_color=MUTED)
            threading.Thread(target=_auto_detect_browser, daemon=True).start()

        self.button(outer, "🔄 Перепроверить", recheck,
                    color=BTN, hover=BTN_HOVER, width=140).pack(anchor="w", padx=20, pady=(0, 8))

        # ── Режим ─────────────────────────────────────────────────────────
        mode_var = ctk.StringVar(value="video")
        mode_row = ctk.CTkFrame(outer, fg_color="transparent")
        mode_row.pack(fill="x", padx=20, pady=(0, 6))
        self.label(mode_row, "Режим:", size=13, weight="bold").pack(side="left", padx=(0, 10))
        for txt, val in [
            ("Видео (mp4)", "video"),
            ("Аудио (mp3)", "audio"),
            ("Видео + Аудио отдельно", "split"),
        ]:
            ctk.CTkRadioButton(
                mode_row, text=txt, variable=mode_var, value=val,
                text_color=TEXT, fg_color=BTN_PRIMARY,
                font=ctk.CTkFont(size=12),
            ).pack(side="left", padx=8)

        # ── Опции ─────────────────────────────────────────────────────────
        opts_row = ctk.CTkFrame(outer, fg_color="transparent")
        opts_row.pack(fill="x", padx=20, pady=(0, 8))
        denoise_var = ctk.BooleanVar(value=False)
        denoise_cb = ctk.CTkCheckBox(
            opts_row, text="Убрать музыку (audio-separator)",
            variable=denoise_var, text_color=TEXT, fg_color=BTN_PRIMARY,
            font=ctk.CTkFont(size=12),
        )

        def _update_denoise_vis(*_):
            if mode_var.get() == "video":
                denoise_cb.pack_forget()
            else:
                denoise_cb.pack(side="left", padx=(0, 24))

        mode_var.trace_add("write", _update_denoise_vis)
        _update_denoise_vis()

        # ── Папка ─────────────────────────────────────────────────────────
        folder_row = ctk.CTkFrame(outer, fg_color="transparent")
        folder_row.pack(fill="x", padx=20, pady=(0, 10))
        self.label(folder_row, "Папка:", size=12, weight="bold").pack(side="left", padx=(0, 6))
        folder_var = ctk.StringVar(value=str(Path(desktop_dir()) / "SheqelMotion_Downloads"))
        ctk.CTkEntry(
            folder_row, textvariable=folder_var,
            fg_color="white", text_color="#111",
            border_color="#737373", font=ctk.CTkFont(size=11),
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        def choose_folder():
            d = filedialog.askdirectory(title="Выбери папку для сохранения", parent=self)
            if d:
                folder_var.set(d)

        self.button(folder_row, "Выбрать...", choose_folder,
                    color=BTN, hover=BTN_HOVER, width=90).pack(side="left")

        # ── Кнопки ────────────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(0, 8))

        def do_download():
            raw = url_box.get("1.0", "end")
            urls = [u.strip() for u in raw.splitlines() if u.strip().startswith("http")]
            if not urls:
                messagebox.showerror(
                    "Ошибка", "Вставь хотя бы одну ссылку (начинается с http).", parent=self)
                return

            self._stop_download = False
            mode = mode_var.get()
            denoise = denoise_var.get()
            output_dir = folder_var.get()

            def get_browser_for_download():
                """Возвращает (browser, cookies_file) для download_from_url."""
                if not use_cookies_var.get():
                    return None, None
                override = browser_override_var.get()
                if override == "Файл cookies.txt":
                    path = cookies_file_var.get().strip()
                    return (None, path) if path else (None, None)
                if override != "Авто":
                    return override.lower(), None
                return AUTO_BROWSER, None

            batch = uuid.uuid4().hex[:8]
            self._panel_state_set("downloader", batch=batch)
            task_ids = [tasks.create("download", "downloader", title=url, meta={"url": url, "batch": batch})
                        for url in urls]
            render_downloads()

            def process_one(url, task_id):
                def log(msg):
                    update = {}
                    m = re.search(r'(\d+(?:\.\d+)?)\s*%', msg)
                    if m:
                        update["progress"] = min(float(m.group(1)) / 100.0, 1.0)
                    short = msg.strip()[:70]
                    if short:
                        update["message"] = short
                    if update:
                        tasks.update(task_id, **update)

                tasks.update(task_id, status="running", progress=0.0)
                used = {"cookies": None}
                try:
                    browser_choice, cookies_file_choice = get_browser_for_download()
                    main_file, extra_file = download_from_url(
                        url, output_dir, mode, denoise, log,
                        browser=browser_choice, cookies_file=cookies_file_choice,
                        on_cookies=lambda label: used.update(cookies=label))
                    show_name = Path(main_file).name
                    if extra_file:
                        show_name += " + " + Path(extra_file).name
                    self.log(f"Скачано: {url} → {main_file}" +
                             (f" + {extra_file}" if extra_file else ""))
                    tasks.update(task_id, status="done", progress=1.0,
                                 result={"files": [f for f in (main_file, extra_file) if f], "name": show_name,
                                         "cookies": used["cookies"]})
                except Exception as e:
                    self.log(f"Ошибка скачивания {url}: {e}")
                    error_reporter.report("app.download_from_url", exc=e)
                    tasks.update(task_id, status="error", error=str(e))

            def run_queue():
                for i, task_id in enumerate(task_ids):
                    if self._stop_download:
                        for rest in task_ids[i:]:
                            tasks.update(rest, status="cancelled", message="Остановлено")
                        break
                    process_one(urls[i], task_id)
                self._stop_download = False

            threading.Thread(target=run_queue, daemon=True).start()

        dl_btn = self.button(btn_row, "⬇ Скачать", do_download,
                             color=BTN_OK, hover=BTN_OK_HOVER, width=140)
        dl_btn.pack(side="left")

        stop_btn = self.button(btn_row, "✕ Стоп",
                               lambda: setattr(self, "_stop_download", True),
                               color=BTN_DANGER, hover=BTN_DANGER_HOVER, width=100)
        stop_btn.pack(side="left", padx=8)
        stop_btn.configure(state="disabled")

        status_lbl = self.label(btn_row, "", size=12, color=MUTED)
        status_lbl.pack(side="left", padx=12)

        self.button(btn_row, "Открыть папку",
                    lambda: open_folder(folder_var.get()),
                    color=BTN, hover=BTN_HOVER, width=130).pack(side="right")

        # ── История загрузок (из менеджера задач, переживает перезапуск) ────
        self.label(outer, "История загрузок", size=13, weight="bold", color=MUTED).pack(
            anchor="w", padx=20, pady=(4, 4))

        history_scroll = ctk.CTkScrollableFrame(outer, fg_color=BG, corner_radius=10)
        history_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        rows = {}

        def _add_history_row(url_text):
            row = ctk.CTkFrame(history_scroll, fg_color=PANEL, corner_radius=8)
            row.pack(fill="x", padx=4, pady=3)
            row.columnconfigure(1, weight=1)

            status_lbl = self.label(row, "⏳", size=14)
            status_lbl.grid(row=0, column=0, padx=(10, 6), pady=8, sticky="w")

            short = (url_text[:66] + "…") if len(url_text) > 66 else url_text
            name_lbl = self.label(row, short, size=11, color=MUTED)
            name_lbl.grid(row=0, column=1, padx=4, pady=8, sticky="w")

            pbar = ctk.CTkProgressBar(row, width=130, height=6)
            pbar.set(0)
            pbar.grid(row=0, column=2, padx=8, pady=8, sticky="ew")

            open_btn_h = self.button(row, "Открыть", lambda: None,
                                     color=BTN, hover=BTN_HOVER, width=80)
            open_btn_h.grid(row=0, column=3, padx=(4, 10), pady=6)
            open_btn_h.configure(state="disabled")

            return {"row": row, "status": status_lbl, "name": name_lbl, "pbar": pbar, "open": open_btn_h}

        def render_downloads():
            items = tasks.list(kind="download")
            for task in items:
                w = rows.get(task["id"])
                if w is None:
                    w = rows[task["id"]] = _add_history_row(task["title"])
                status = task["status"]
                if status == "done":
                    name = (task.get("result") or {}).get("name") or task["title"]
                    files = (task.get("result") or {}).get("files") or []
                    used = (task.get("result") or {}).get("cookies")
                    shown = (name[:60] + "…") if len(name) > 60 else name
                    w["status"].configure(text="✓", text_color="#86efac")
                    w["name"].configure(text=shown + (f" · куки: {used}" if used else ""), text_color=TEXT)
                    w["pbar"].set(1.0)
                    w["pbar"].configure(progress_color="#86efac")
                    if files:
                        w["open"].configure(state="normal",
                                            command=lambda f=files[0]: open_folder(str(Path(f).parent)))
                elif status == "error":
                    err = str(task.get("error"))
                    w["status"].configure(text="✗", text_color="#fca5a5")
                    w["name"].configure(text=f"Ошибка: {err[:160]}", text_color="#fca5a5", wraplength=420)
                    w["pbar"].configure(progress_color="#fca5a5")
                    if "Полный доступ к диску" in err and sys.platform == "darwin":
                        w["open"].configure(state="normal", text="Доступ к диску", command=open_full_disk_access_settings)
                elif status in ("cancelled", "interrupted"):
                    note = "Остановлено" if status == "cancelled" else "Прервано: приложение закрыли"
                    w["status"].configure(text="✕", text_color="#fca5a5")
                    w["name"].configure(text=f"{note} — {task['title'][:50]}", text_color=MUTED)
                elif status == "running":
                    if task.get("message"):
                        w["name"].configure(text=task["message"])
                    w["pbar"].set(task.get("progress") or 0)
            last_used = next(((x.get("result") or {}).get("cookies") for x in reversed(items)
                              if x["status"] == "done" and (x.get("result") or {}).get("cookies")), None)
            cookies_used_lbl.configure(text=f"Куки последней загрузки: {last_used}" if last_used else "")
            if last_used and not cookies_used_lbl.winfo_manager():
                cookies_used_lbl.pack(anchor="w", padx=20, pady=(0, 4), after=browser_status_label)
            batch = self._panel_state_get("downloader", "batch")
            current = [x for x in items if x["meta"].get("batch") == batch] if batch else []
            active = [x for x in current if x["status"] not in TASK_FINISHED]
            done = sum(1 for x in current if x["status"] == "done")
            dl_btn.configure(state="disabled" if active else "normal",
                             text="Скачиваю..." if active else "⬇ Скачать")
            stop_btn.configure(state="normal" if active else "disabled")
            if active:
                position = len(current) - len(active) + 1
                status_lbl.configure(text=f"Скачивается {position} из {len(current)}...")
            elif current:
                stopped = any(x["status"] == "cancelled" for x in current)
                status_lbl.configure(text=(f"Остановлено. Скачано {done} из {len(current)}" if stopped
                                           else f"Готово! Скачано {done} из {len(current)}"))

        self._register_renderer("downloader", render_downloads)

    def show_transcribe_panel(self):
        scroll = self._start_panel("transcribe")
        if scroll is None:
            return
        saved = self._panel_state.get("transcribe", {})
        tasks = self.background_tasks
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self.label(frame, "🎙 Транскрибировать", size=22, weight="bold").pack(
            anchor="w", padx=22, pady=(20, 4))
        self.label(
            frame,
            "Загрузи аудио или видео — Groq распознает речь на любом языке автоматически.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=22, pady=(0, 16))

        self.label(frame, "Файл", size=14, weight="bold").pack(anchor="w", padx=22)
        file_row = ctk.CTkFrame(frame, fg_color="transparent")
        file_row.pack(fill="x", padx=22, pady=(6, 14))

        file_label = ctk.CTkLabel(
            file_row, text="  не выбран",
            fg_color="#f5f5f5", text_color="#888",
            anchor="w", corner_radius=6,
            font=ctk.CTkFont(size=12), height=32,
        )
        file_label.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)

        selected = {"path": ""}

        def show_file(f):
            selected["path"] = f
            size_mb = os.path.getsize(f) / (1024 * 1024)
            file_label.configure(text=f"  {Path(f).name}  ({size_mb:.1f} МБ)", text_color="#111")

        def choose_file():
            f = filedialog.askopenfilename(
                parent=self,
                title="Выбери аудио или видео",
                filetypes=[
                    ("Медиа файлы", "*.mp3 *.wav *.m4a *.mp4 *.mov *.avi *.mkv *.webm *.flac *.ogg"),
                    ("Все файлы", "*.*"),
                ],
            )
            if f:
                show_file(f)
                self._panel_state_set("transcribe", file=f)

        if saved.get("file") and os.path.exists(saved["file"]):
            show_file(saved["file"])

        self.button(file_row, "Выбрать файл", choose_file,
                    color=BTN, hover=BTN_HOVER, width=140).pack(side="right")

        status_label = self.label(frame, "", size=12, color=MUTED)
        status_label.pack(anchor="w", padx=22, pady=(0, 8))

        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=22, pady=(0, 14))

        def do_transcribe():
            if not self.groq_api_key:
                messagebox.showerror(
                    "Ошибка",
                    "Сначала добавь Groq API key в Настройках.\n\nРегистрация бесплатно на groq.com",
                    parent=self
                )
                return
            if not selected["path"]:
                messagebox.showerror("Ошибка", "Выбери файл.", parent=self)
                return

            path = selected["path"]
            task_id = tasks.create("transcribe", "transcribe", title=f"Транскрибация {Path(path).name}",
                                   meta={"file": path}, status="running")
            render_transcribe()

            def run():
                try:
                    text, duration_sec = transcribe_with_groq(path, self.groq_api_key, self.log)
                    self._groq_add_seconds(duration_sec)
                    self.save_config()
                    tasks.update(task_id, status="done", progress=1.0, result={"text": text})
                except Exception as e:
                    err = str(e)
                    self.log(f"Ошибка транскрибации: {err}")
                    error_reporter.report("app.transcribe_with_groq", exc=e)
                    tasks.update(task_id, status="error", error=err)

            threading.Thread(target=run, daemon=True).start()

        transcribe_btn = self.button(
            btn_row, "🎙 Транскрибировать", do_transcribe,
            color=BTN_OK, hover=BTN_OK_HOVER, width=220)
        transcribe_btn.pack(side="left")

        groq_limit_label = self.label(btn_row, "", size=12, color=MUTED)
        groq_limit_label.pack(side="left", padx=14)

        def _load_groq_limit():
            if not self.groq_api_key:
                return
            limit_sec = 28800.0
            used_sec = self._groq_seconds()
            remaining_sec = max(0.0, limit_sec - used_sec)
            remaining_hours = remaining_sec / 3600
            used_hours = used_sec / 3600

            if remaining_hours >= 1:
                remaining_text = f"{remaining_hours:.1f} ч"
            else:
                remaining_mins = remaining_sec / 60
                remaining_text = f"{remaining_mins:.0f} мин"

            if used_sec > 0:
                if used_hours >= 1:
                    used_text = f"{used_hours:.1f} ч"
                else:
                    used_mins = used_sec / 60
                    used_text = f"{used_mins:.1f} мин"
                text = f"⏱ использовано {used_text} · осталось {remaining_text}"
            else:
                text = "⏱ до 8 ч/день бесплатно"

            pct = used_sec / limit_sec
            if pct < 0.7:
                color = "#86efac"
            elif pct < 0.9:
                color = "#fcd34d"
            else:
                color = "#fca5a5"

            self.after(0, lambda t=text, c=color: groq_limit_label.configure(
                text=t, text_color=c))

        def reset_counter():
            self._groq_reset()
            self.save_config()
            _load_groq_limit()

        ctk.CTkButton(
            btn_row, text="↺", command=reset_counter,
            fg_color=BTN, hover_color=BTN_HOVER,
            width=32, height=32, font=ctk.CTkFont(size=14),
        ).pack(side="left", padx=(4, 0))

        threading.Thread(target=_load_groq_limit, daemon=True).start()

        self.label(frame, "Результат", size=14, weight="bold").pack(
            anchor="w", padx=22, pady=(8, 4))

        result_box = ctk.CTkTextbox(
            frame, height=280,
            fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=14), wrap="word",
        )
        result_box.pack(fill="x", padx=22, pady=(0, 10))
        result_box.configure(state="disabled")
        self._fix_paste(result_box)

        actions_row = ctk.CTkFrame(frame, fg_color="transparent")
        actions_row.pack(fill="x", padx=22, pady=(0, 20))

        def copy_result():
            text = result_box.get("1.0", "end").strip()
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
                status_label.configure(text="✓ Скопировано!", text_color="#86efac")

        def save_result():
            text = result_box.get("1.0", "end").strip()
            if not text:
                return
            f = filedialog.asksaveasfilename(
                parent=self,
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("Все файлы", "*.*")],
                initialfile="transcript.txt",
            )
            if f:
                with open(f, "w", encoding="utf-8") as fp:
                    fp.write(text)
                status_label.configure(
                    text=f"✓ Сохранено: {Path(f).name}", text_color="#86efac")

        def send_to_voice():
            text = result_box.get("1.0", "end").strip()
            if not text:
                return
            self.show_voice_gen_panel(initial_text=text)

        self.button(actions_row, "📋 Копировать", copy_result,
                    color=BTN, hover=BTN_HOVER, width=140).pack(side="left", padx=(0, 8))
        self.button(actions_row, "💾 Сохранить .txt", save_result,
                    color=BTN, hover=BTN_HOVER, width=160).pack(side="left", padx=(0, 8))
        self.button(actions_row, "🎙 В генератор голоса", send_to_voice,
                    color=BTN_PRIMARY, hover=BTN_PRIMARY_HOVER, width=190).pack(side="left")

        # Ошибки прошлой сессии повторно не показываем.
        announced = {x["id"] for x in tasks.list(kind="transcribe") if x["status"] in TASK_FINISHED}
        shown = {"task": None}

        def render_transcribe():
            task = tasks.latest("transcribe")
            busy = bool(task and task["status"] in ("queued", "running"))
            transcribe_btn.configure(state="disabled" if busy else "normal",
                                     text="Транскрибирую..." if busy else "🎙 Транскрибировать")
            if task is None:
                return
            if busy:
                status_label.configure(text="Отправляю в Groq... (можно переключаться на другие вкладки)",
                                       text_color=MUTED)
            elif task["status"] == "done":
                text = (task.get("result") or {}).get("text", "")
                status_label.configure(text=f"✓ Готово — {len(text)} символов", text_color="#86efac")
                if shown["task"] != task["id"]:
                    result_box.configure(state="normal")
                    result_box.delete("1.0", "end")
                    result_box.insert("1.0", text)
                    result_box.configure(state="disabled")
                    shown["task"] = task["id"]
                    threading.Thread(target=_load_groq_limit, daemon=True).start()
            elif task["status"] == "interrupted":
                status_label.configure(text="Прервано: приложение закрыли во время транскрибации",
                                       text_color="#fcd34d")
            else:
                err = str(task.get("error") or "")
                status_label.configure(text=f"Ошибка: {err[:80]}", text_color="#fca5a5")
                if task["id"] not in announced:
                    announced.add(task["id"])
                    messagebox.showerror("Ошибка транскрибации", err, parent=self)
            if busy and shown["task"] is not None:
                result_box.configure(state="normal")
                result_box.delete("1.0", "end")
                result_box.configure(state="disabled")
                shown["task"] = None

        self._register_renderer("transcribe", render_transcribe)

    def show_groq_translate_panel(self):
        scroll = self._start_panel("groq_translate")
        if scroll is None:
            return
        saved = self._panel_state.get("groq_translate", {})
        tasks = self.background_tasks
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self.label(frame, "🌐 Переводчик", size=22, weight="bold").pack(
            anchor="w", padx=22, pady=(20, 4))
        self.label(
            frame,
            "Groq LLaMA — бесплатно, понимает контекст, адаптирует под культуру.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=22, pady=(0, 14))

        lang_row = ctk.CTkFrame(frame, fg_color="transparent")
        lang_row.pack(fill="x", padx=22, pady=(0, 14))

        self.label(lang_row, "Перевести на:", size=13, weight="bold").pack(
            side="left", padx=(0, 10))

        langs = [
            "RU — Русский", "EN — Английский", "UK — Украинский",
            "DE — Немецкий", "FR — Французский", "ES — Испанский",
            "IT — Итальянский", "PL — Польский", "PT — Португальский",
            "ZH — Китайский", "JA — Японский", "KO — Корейский",
            "AR — Арабский", "TR — Турецкий", "HI — Хинди",
            "NL — Нидерландский", "SV — Шведский", "CS — Чешский",
            "RO — Румынский", "HU — Венгерский", "LT — Литовский",
            "ET — Эстонский", "LV — Латышский",
        ]

        lang_var = ctk.StringVar(value=saved.get("lang") if saved.get("lang") in langs else "EN — Английский")
        ctk.CTkOptionMenu(
            lang_row, values=langs, variable=lang_var,
            fg_color=BTN, button_color=BTN,
            button_hover_color=BTN_HOVER,
            dropdown_fg_color=PANEL,
            width=200,
        ).pack(side="left")

        self.label(frame, "Исходный текст", size=14, weight="bold").pack(
            anchor="w", padx=22, pady=(0, 4))
        source_box = ctk.CTkTextbox(
            frame, height=180,
            fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=14), wrap="word",
        )
        source_box.pack(fill="x", padx=22, pady=(0, 10))
        self._fix_paste(source_box)
        if saved.get("source"):
            source_box.insert("1.0", saved["source"])

        def save_fields():
            self._panel_state_set("groq_translate", lang=lang_var.get(),
                                  source=source_box.get("1.0", "end").rstrip("\n"))

        source_box._textbox.bind("<KeyRelease>", lambda e: save_fields(), add="+")
        lang_var.trace_add("write", lambda *a: save_fields())

        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=22, pady=(0, 10))

        status_label = self.label(btn_row, "", size=12, color=MUTED)
        status_label.pack(side="right", padx=(10, 0))

        def do_translate():
            if not self.groq_api_key:
                messagebox.showerror(
                    "Ошибка",
                    "Сначала добавь Groq API key в Настройках.\n\nРегистрация бесплатно на groq.com",
                    parent=self
                )
                return
            text = source_box.get("1.0", "end").strip()
            if not text:
                messagebox.showerror("Ошибка", "Введи текст для перевода.", parent=self)
                return

            target_lang = lang_var.get().split(" — ")[0].strip()

            save_fields()
            task_id = tasks.create("translate", "groq_translate", title=f"Перевод на {target_lang}",
                                   meta={"lang": target_lang}, status="running")
            render_translate()

            def run():
                try:
                    result = translate_with_groq(text, target_lang, self.groq_api_key, self.log)
                    tasks.update(task_id, status="done", progress=1.0, result={"text": result})
                except Exception as e:
                    err = str(e)
                    self.log(f"Ошибка перевода: {err}")
                    error_reporter.report("app.translate_with_groq", exc=e)
                    tasks.update(task_id, status="error", error=err)

            threading.Thread(target=run, daemon=True).start()

        translate_btn = self.button(
            btn_row, "🌐 Перевести", do_translate,
            color=BTN_OK, hover=BTN_OK_HOVER, width=180)
        translate_btn.pack(side="left")

        self.label(frame, "Перевод", size=14, weight="bold").pack(
            anchor="w", padx=22, pady=(4, 4))

        result_box = ctk.CTkTextbox(
            frame, height=180,
            fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=14), wrap="word",
        )
        result_box.pack(fill="x", padx=22, pady=(0, 10))
        result_box.configure(state="disabled")
        self._fix_paste(result_box)

        actions_row = ctk.CTkFrame(frame, fg_color="transparent")
        actions_row.pack(fill="x", padx=22, pady=(0, 20))

        def copy_result():
            text = result_box.get("1.0", "end").strip()
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
                status_label.configure(text="✓ Скопировано!", text_color="#86efac")

        def send_to_voice():
            text = result_box.get("1.0", "end").strip()
            if not text:
                return
            self.show_voice_gen_panel(initial_text=text)

        def swap_texts():
            res = result_box.get("1.0", "end").strip()
            if not res:
                return
            source_box.delete("1.0", "end")
            source_box.insert("1.0", res)
            result_box.configure(state="normal")
            result_box.delete("1.0", "end")
            result_box.configure(state="disabled")
            status_label.configure(text="", text_color=MUTED)
            latest = tasks.latest("translate")
            self._panel_state_set("groq_translate", cleared_task=latest["id"] if latest else None)
            save_fields()

        self.button(actions_row, "📋 Копировать", copy_result,
                    color=BTN, hover=BTN_HOVER, width=130).pack(side="left", padx=(0, 8))
        self.button(actions_row, "⇄ Поменять", swap_texts,
                    color=BTN, hover=BTN_HOVER, width=120).pack(side="left", padx=(0, 8))
        self.button(actions_row, "🎙 В генератор голоса", send_to_voice,
                    color=BTN_PRIMARY, hover=BTN_PRIMARY_HOVER, width=190).pack(side="left")

        announced = {x["id"] for x in tasks.list(kind="translate") if x["status"] in TASK_FINISHED}
        shown = {"task": None}

        def render_translate():
            task = tasks.latest("translate")
            busy = bool(task and task["status"] in ("queued", "running"))
            translate_btn.configure(state="disabled" if busy else "normal",
                                    text="Перевожу..." if busy else "🌐 Перевести")
            if task is None:
                return
            cleared = self._panel_state_get("groq_translate", "cleared_task") == task["id"]
            if busy:
                status_label.configure(text="Отправляю в Groq... (можно переключаться на другие вкладки)",
                                       text_color=MUTED)
                text = ""
            elif task["status"] == "done" and not cleared:
                text = (task.get("result") or {}).get("text", "")
                status_label.configure(text=f"✓ {len(text)} символов", text_color="#86efac")
            elif task["status"] == "done":
                return
            elif task["status"] == "interrupted":
                status_label.configure(text="Прервано: приложение закрыли во время перевода",
                                       text_color="#fcd34d")
                text = ""
            else:
                err = str(task.get("error") or "")
                status_label.configure(text=f"Ошибка: {err[:60]}", text_color="#fca5a5")
                if task["id"] not in announced:
                    announced.add(task["id"])
                    messagebox.showerror("Ошибка перевода", err, parent=self)
                text = ""
            key = (task["id"], task["status"])
            if shown["task"] != key:
                result_box.configure(state="normal")
                result_box.delete("1.0", "end")
                if text:
                    result_box.insert("1.0", text)
                result_box.configure(state="disabled")
                shown["task"] = key

        self._register_renderer("groq_translate", render_translate)

    def stop_preview(self):
        """Останавливает текущий preview если играет."""
        if self.preview_process and self.preview_process.poll() is None:
            try:
                self.preview_process.terminate()
            except Exception:
                pass
        self.preview_process = None
        self.preview_voice_id = None

    def play_voice_preview(self, voice_id, url):
        """Скачивает (если надо) и играет preview через ffplay/afplay."""
        self.stop_preview()

        cache_dir = Path(tempfile.gettempdir()) / "sheqelmotion_previews"
        ensure_dir(cache_dir)
        cache_path = str(cache_dir / f"{voice_id}.mp3")

        if not os.path.exists(cache_path):
            if url:
                try:
                    r = requests.get(url, timeout=30)
                    r.raise_for_status()
                    with open(cache_path, "wb") as f:
                        f.write(r.content)
                except Exception as e:
                    self.log(f"Не удалось скачать preview: {e}")
                    return
            else:
                # У голоса нет preview_url — генерируем сэмпл через TTS
                self.log(f"Генерирую сэмпл для {voice_id}...")
                sample_text = "Привет, это пример моего голоса. Тестовая фраза для прослушивания."
                try:
                    global ELEVENLABS_API_KEY
                    ELEVENLABS_API_KEY = self.api_key
                    text_to_speech_mp3(sample_text, voice_id, cache_path, self.log)
                except Exception as e:
                    self.log(f"Не удалось сгенерировать сэмпл: {e}")
                    return

        try:
            ffplay = shutil.which("ffplay")
            if not ffplay:
                local = Path(app_base_dir()) / ("ffplay" + (".exe" if sys.platform == "win32" else ""))
                if local.exists():
                    ffplay = str(local)

            if ffplay:
                self.preview_process = subprocess.Popen(
                    [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(cache_path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "darwin":
                self.preview_process = subprocess.Popen(["afplay", str(cache_path)])
            elif sys.platform == "win32":
                os.startfile(cache_path)
            self.preview_voice_id = voice_id
        except Exception as e:
            self.log(f"Не удалось воспроизвести: {e}")

    def _selected_generator_voice(self):
        """(voice_id, имя) голоса, выбранного в генераторе: из поля, состояния вкладки или конфига."""
        state = self._panel_state.get("voice_gen", {})
        name = state.get("voice") or self.main_voice_name
        vg = self._vg
        if vg and vg["voice_box"].winfo_exists():
            name = vg["voice_box"].get("1.0", "end").strip() or name
        voice_id = state.get("voice_id") or self.main_voice_id
        if voice_id and name and name not in (state.get("voice"), self.main_voice_name, voice_id):
            voice_id = ""          # в поле уже другой голос — сверяем по имени
        return voice_id, name

    def open_voice_picker(self, parent_win, on_pick):
        """Окошко с выбором голоса. on_pick(name, voice_id) при выборе."""
        win = ctk.CTkToplevel(parent_win)
        win.title("Мои голоса ElevenLabs")
        win.geometry("620x680")
        win.configure(fg_color=BG)
        win.transient(parent_win)
        win.after(100, win.grab_set)

        def on_close():
            self.stop_preview()
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", on_close)

        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="both", expand=True, padx=16, pady=16)

        self.label(frame, "Мои голоса", size=18, weight="bold").pack(anchor="w", padx=16, pady=(14, 6))

        search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(frame, placeholder_text="🔍 Поиск по имени...",
                                    textvariable=search_var,
                                    fg_color="white", text_color="#111",
                                    border_color="#737373",
                                    font=ctk.CTkFont(size=13))
        search_entry.pack(fill="x", padx=16, pady=(0, 10))
        self._fix_paste(search_entry)

        list_frame = ctk.CTkScrollableFrame(frame, fg_color=BG)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        status_label = self.label(frame, "Загружаю...", size=12, color=MUTED)
        status_label.pack(anchor="w", padx=16, pady=(0, 4))

        voices_data = []

        def _time_ago(ts):
            if not ts:
                return ""
            import time as _time
            diff = int(_time.time()) - ts
            if diff < 0:
                return ""
            minutes = diff // 60
            if minutes < 1:
                return "только что"
            if minutes < 60:
                return f"{minutes} мин назад"
            hours = diff // 3600
            if hours < 24:
                return f"{hours} ч назад"
            days = diff // 86400
            if days < 30:
                return f"{days} дн назад"
            months = days // 30
            return f"{months} мес назад"

        def render_list(filter_text=""):
            for w in list_frame.winfo_children():
                w.destroy()
            filter_lower = filter_text.lower().strip()

            groups = {"cloned": [], "professional": [], "other": []}
            for v in voices_data:
                if filter_lower and filter_lower not in v["name"].lower():
                    continue
                cat = v["category"]
                if cat == "cloned":
                    groups["cloned"].append(v)
                elif cat == "professional":
                    groups["professional"].append(v)
                else:
                    groups["other"].append(v)

            sections = [
                ("🎤 Мои клоны", groups["cloned"], "#86efac"),
                ("⭐ Профи голоса", groups["professional"], "#fbbf24"),
            ]
            if groups["other"]:
                sections.append(("📚 Другие", groups["other"], "#a3a3a3"))

            total_shown = 0
            for section_title, section_voices, section_color in sections:
                if not section_voices:
                    continue
                header = ctk.CTkFrame(list_frame, fg_color=BG)
                header.pack(fill="x", pady=(10, 4))
                ctk.CTkLabel(header, text=f"{section_title}  ·  {len(section_voices)}",
                             font=ctk.CTkFont(size=14, weight="bold"),
                             text_color=section_color
                             ).pack(side="left", padx=4)

                sel_id, sel_name = self._selected_generator_voice()
                for v in section_voices:
                    is_selected = (v["voice_id"] == sel_id) if sel_id else (bool(sel_name) and v["name"] == sel_name)
                    row = ctk.CTkFrame(list_frame, fg_color="#1f3a2e" if is_selected else CARD, corner_radius=14,
                                       border_width=2 if is_selected else 0, border_color="#86efac")
                    row.pack(fill="x", padx=2, pady=3)
                    if is_selected:
                        ctk.CTkLabel(row, text="✓ выбран в генераторе", font=ctk.CTkFont(size=11),
                                     text_color="#86efac").pack(side="right", padx=8)

                    name_col = ctk.CTkFrame(row, fg_color="transparent")
                    name_col.pack(side="left", fill="x", expand=True, padx=12, pady=6)
                    ctk.CTkLabel(name_col, text=v["name"],
                                 font=ctk.CTkFont(size=14, weight="bold"),
                                 text_color=TEXT, anchor="w"
                                 ).pack(anchor="w")
                    ago = _time_ago(v.get("created_at", 0))
                    if ago:
                        ctk.CTkLabel(name_col, text=ago,
                                     font=ctk.CTkFont(size=10),
                                     text_color=MUTED, anchor="w"
                                     ).pack(anchor="w", padx=0)
                    expiry_entry = next(
                        (e for e in self._expiry_snapshot() if e["voice_id"] == v["voice_id"]),
                        None,
                    )
                    if expiry_entry:
                        secs_left = max(0, int(expiry_entry["expires_at"] - time.time()))
                        h_left = secs_left // 3600
                        m_left = (secs_left % 3600) // 60
                        timer_text = f"⏳ удалится через {h_left} ч {m_left} мин"
                        ctk.CTkLabel(name_col, text=timer_text,
                                     font=ctk.CTkFont(size=10),
                                     text_color="#fcd34d", anchor="w"
                                     ).pack(anchor="w", padx=0)

                    
                    def make_play(voice=v):
                        return lambda: self.play_voice_preview(
                            voice["voice_id"], voice.get("preview_url", ""))
                    ctk.CTkButton(row, text="▶", command=make_play(),
                                      fg_color=BTN, hover_color=BTN_HOVER,
                                      width=40, height=28,
                                      font=ctk.CTkFont(size=14)
                                      ).pack(side="right", padx=4)

                    def make_pick(voice=v):
                        def pick():
                            self.stop_preview()
                            on_pick(voice["name"], voice["voice_id"])
                            win.destroy()
                        return pick
                    ctk.CTkButton(row, text="Выбрать", command=make_pick(),
                                  fg_color=BTN_OK, hover_color=BTN_OK_HOVER,
                                  width=90, height=28
                                  ).pack(side="right", padx=4)
                    total_shown += 1

            if total_shown == 0 and voices_data:
                ctk.CTkLabel(list_frame, text="Ничего не найдено",
                             text_color=MUTED).pack(pady=20)
            status_label.configure(text=f"Показано: {total_shown} из {len(voices_data)}",
                                   text_color=MUTED)

        search_var.trace_add("write", lambda *_: render_list(search_var.get()))

        def load():
            try:
                status_label.configure(text="Загружаю список голосов...", text_color=MUTED)
                win.update()
                voices_data.clear()
                voices_data.extend(self.fetch_all_voices())
                if not voices_data:
                    status_label.configure(text="В аккаунте нет голосов",
                                           text_color="#fca5a5")
                    return
                render_list()
            except Exception as e:
                status_label.configure(text=f"Ошибка: {e}", text_color="#fca5a5")

        bottom = ctk.CTkFrame(frame, fg_color=PANEL)
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(bottom, text="🔄 Обновить", command=load,
                      fg_color=BTN, hover_color=BTN_HOVER,
                      width=120, height=32).pack(side="right")

        win.after(100, load)
   

    def show_voice_gen_panel(self, initial_voice="", initial_text=""):
        outer = self._start_panel("voice_gen")
        if outer is not None:
            self._build_voice_gen_panel(outer)
        self._voice_gen_apply_initial(initial_voice, initial_text)

    def _voice_gen_apply_initial(self, initial_voice, initial_text):
        """Голос или текст из другой вкладки. Уже набранный текст без спроса не затирается."""
        vg = self._vg
        if initial_voice:
            vg["voice_box"].delete("1.0", "end")
            vg["voice_box"].insert("1.0", initial_voice)
        if initial_text:
            box = vg["text_box"]
            current = box.get("1.0", "end").strip()
            if current and current != initial_text.strip():
                choice = messagebox.askyesnocancel(
                    "Текст в генераторе",
                    "В генераторе уже есть набранный текст.\n\n"
                    "«Да» — заменить новым\n«Нет» — добавить в конец\n«Отмена» — оставить как есть",
                    parent=self,
                )
                if choice is None:
                    return
                if choice:
                    box.delete("1.0", "end")
                    box.insert("1.0", initial_text)
                else:
                    box.insert("end", "\n\n" + initial_text)
            else:
                box.delete("1.0", "end")
                box.insert("1.0", initial_text)
            vg["update_status"]()
        if initial_voice or initial_text:
            vg["save_fields"]()

    def _build_voice_gen_panel(self, outer):
        saved = self._panel_state.get("voice_gen", {})
        frame = ctk.CTkFrame(outer, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        # ── Header ────────────────────────────────────────────────────────
        self.label(frame, "Сгенерировать full_voice.mp3", size=22, weight="bold").pack(
            anchor="w", padx=22, pady=(20, 4))
        self.label(
            frame,
            "Один текст — два варианта генерации параллельно. Прослушай оба, скачай лучший.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=22, pady=(0, 14))

        # ── Voice picker ──────────────────────────────────────────────────
        self.label(frame, "voice_id или имя голоса", size=14, weight="bold").pack(
            anchor="w", padx=22)
        voice_box = ctk.CTkTextbox(
            frame, height=56, fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=14),
        )
        voice_box.pack(fill="x", padx=22, pady=(6, 8))
        if saved.get("voice") or self.main_voice_name:
            voice_box.insert("1.0", saved.get("voice") or self.main_voice_name)
        self._fix_paste(voice_box)

        def on_voice_picked(name, voice_id):
            voice_box.delete("1.0", "end")
            voice_box.insert("1.0", name)
            self._panel_state.setdefault("voice_gen", {})["voice_id"] = voice_id
            save_fields()

        self.button(frame, "📋 Выбрать из списка моих голосов",
                    lambda: self.open_voice_picker(self, on_voice_picked),
                    color=BTN, hover=BTN_HOVER, width=320).pack(
            anchor="w", padx=22, pady=(0, 14))

        # ── Text input ────────────────────────────────────────────────────
        self.label(frame, "Текст (один для обоих вариантов)", size=14, weight="bold").pack(
            anchor="w", padx=22)
        restore_row = ctk.CTkFrame(frame, fg_color="#2a2d3e", corner_radius=8)
        self.label(restore_row, "Текст очищен после клонирования.", size=12, color="#fcd34d").pack(
            side="left", padx=10, pady=4)
        restore_btn = ctk.CTkButton(restore_row, text="Вернуть", width=90, height=24, fg_color=BTN,
                                    hover_color=BTN_HOVER, text_color=TEXT, corner_radius=8)
        restore_btn.pack(side="left", pady=4)
        text_box = ctk.CTkTextbox(
            frame, height=180,
            fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=14), wrap="word",
        )
        text_box.pack(fill="x", padx=22, pady=(6, 4))
        self._fix_paste(text_box)
        if saved.get("text"):
            text_box.insert("1.0", saved["text"])
        tk_text = text_box._textbox  # внутренний tk.Text для тегов/биндингов

        def render_restore_notice():
            backup = self._panel_state_get("voice_gen", "text_backup")
            if backup and not restore_row.winfo_manager():
                restore_row.pack(fill="x", padx=22, pady=(6, 0), before=text_box)
            elif not backup and restore_row.winfo_manager():
                restore_row.pack_forget()

        def restore_text():
            backup = self._panel_state_get("voice_gen", "text_backup")
            if backup:
                text_box.delete("1.0", "end")
                text_box.insert("1.0", backup)
            self._panel_state.setdefault("voice_gen", {}).pop("text_backup", None)
            render_restore_notice()
            save_fields()
            _update_status()

        restore_btn.configure(command=restore_text)

        # ── Счётчик символов ──────────────────────────────────────────────
        status_text_label = ctk.CTkLabel(
            frame, text="0 символов",
            font=ctk.CTkFont(size=12), text_color=MUTED, anchor="w"
        )
        status_text_label.pack(anchor="w", padx=22, pady=(0, 8))

        # ── Модель и язык ─────────────────────────────────────────────────
        model_row = ctk.CTkFrame(frame, fg_color="transparent")
        model_row.pack(fill="x", padx=22, pady=(0, 10))

        self.label(model_row, "Модель:", size=12, color=MUTED).pack(side="left", padx=(0, 6))
        model_var = ctk.StringVar(
            value=saved.get("model") if saved.get("model") in ("Eleven v4", "Eleven v3") else "Eleven v4")
        ctk.CTkOptionMenu(
            model_row, values=["Eleven v4", "Eleven v3"],
            variable=model_var, fg_color=BTN, button_color=BTN,
            button_hover_color=BTN_HOVER, width=130,
        ).pack(side="left", padx=(0, 20))

        self.label(model_row, "Язык:", size=12, color=MUTED).pack(side="left", padx=(0, 6))
        lang_var = ctk.StringVar(
            value=saved.get("lang") if saved.get("lang") in ELEVEN_TTS_LANGUAGES else "Авто")
        ctk.CTkOptionMenu(
            model_row, values=list(ELEVEN_TTS_LANGUAGES.keys()),
            variable=lang_var, fg_color=BTN, button_color=BTN,
            button_hover_color=BTN_HOVER, width=150,
        ).pack(side="left")

        # ── Кнопка запуска ────────────────────────────────────────────────
        gen_row = ctk.CTkFrame(frame, fg_color="transparent")
        gen_row.pack(fill="x", padx=22, pady=(0, 16))

        gen_btn = self.button(gen_row, "⚡ Сгенерировать A и B",
                              lambda: None,
                              color=BTN_OK, hover=BTN_OK_HOVER, width=240)
        gen_btn.pack(side="left")

        gen_status = self.label(gen_row, "", size=12, color=MUTED)
        gen_status.pack(side="left", padx=14)

        # ── A/B карточки ──────────────────────────────────────────────────
        ab_row = ctk.CTkFrame(frame, fg_color="transparent")
        ab_row.pack(fill="x", padx=22, pady=(0, 20))
        ab_row.columnconfigure(0, weight=1)
        ab_row.columnconfigure(1, weight=1)

        slot_widgets = {}
        notes = {}  # slot -> (id задачи, «✓ Сохранено: …») — показывается, пока это та же задача

        for col, slot in enumerate(["A", "B"]):
            color_accent = "#1c7ed6" if slot == "A" else "#7950f2"
            card = ctk.CTkFrame(ab_row, fg_color=CARD, corner_radius=16)
            card.grid(row=0, column=col, padx=(0 if col == 0 else 8, 0), sticky="nsew")

            hdr = ctk.CTkFrame(card, fg_color=color_accent, corner_radius=10, height=36)
            hdr.pack(fill="x", padx=10, pady=(10, 8))
            hdr.pack_propagate(False)
            ctk.CTkLabel(hdr, text=f"Вариант {slot}",
                         font=ctk.CTkFont(size=15, weight="bold"),
                         text_color="white").pack(expand=True)

            status_lbl = self.label(card, "Ожидает генерации", size=12, color=MUTED)
            status_lbl.pack(anchor="w", padx=12, pady=(0, 8))

            pbar = ctk.CTkProgressBar(card, height=6, progress_color=color_accent)
            pbar.set(0)
            pbar.pack(fill="x", padx=12, pady=(0, 10))

            btn_row = ctk.CTkFrame(card, fg_color="transparent")
            btn_row.pack(fill="x", padx=10, pady=(0, 10))

            play_btn = self.button(btn_row, "▶ Слушать", lambda s=slot: _play(s),
                                   color=BTN, hover=BTN_HOVER, width=110)
            play_btn.pack(side="left", padx=(0, 6))
            play_btn.configure(state="disabled")

            save_btn = self.button(btn_row, "💾 Скачать", lambda s=slot: _save(s),
                                   color=BTN_OK, hover=BTN_OK_HOVER, width=110)
            save_btn.pack(side="left")
            save_btn.configure(state="disabled")

            stop_btn = self.button(btn_row, "⏹", lambda s=slot: _stop(s),
                                   color=BTN_DANGER, hover=BTN_DANGER_HOVER, width=44)
            stop_btn.pack(side="right")
            stop_btn.configure(state="disabled")

            slot_widgets[slot] = {
                "status": status_lbl,
                "pbar": pbar,
                "play": play_btn,
                "save": save_btn,
                "stop": stop_btn,
            }

        # ── Логика: состояние A/B живёт в менеджере задач, не в виджетах ─────
        procs = {"A": None, "B": None}
        tasks = self.background_tasks

        def _current_task(slot):
            batch = self._panel_state_get("voice_gen", "batch")
            return tasks.latest("voice_gen", slot=slot, batch=batch) if batch else None

        def _slot_path(slot):
            task = _current_task(slot)
            path = ((task or {}).get("result") or {}).get("path")
            return path if task and task["status"] == "done" and path and os.path.exists(path) else None

        def render_slots():
            running = False
            for slot in ("A", "B"):
                w = slot_widgets[slot]
                task = _current_task(slot)
                path = _slot_path(slot)
                if task is None:
                    text, color, value = "Ожидает генерации", MUTED, 0
                elif task["status"] in ("queued", "running"):
                    running = True
                    text, color, value = "Генерирую...", MUTED, 0.5
                elif task["status"] == "done" and path:
                    dur = (task.get("result") or {}).get("duration")
                    text = f"✓ Готово — {dur:.1f} сек" if dur else "✓ Готово"
                    color, value = "#86efac", 1.0
                elif task["status"] == "done":
                    text, color, value = "Файл варианта не найден", "#fca5a5", 0
                elif task["status"] == "interrupted":
                    text, color, value = "Прервано: приложение закрыли во время генерации", "#fcd34d", 0
                else:
                    text, color, value = f"Ошибка: {str(task.get('error'))[:60]}", "#fca5a5", 0
                note = notes.get(slot)
                if note and task and note[0] == task["id"]:
                    text, color = note[1], "#86efac"
                w["status"].configure(text=text, text_color=color)
                w["pbar"].set(value)
                w["play"].configure(state="normal" if path and procs[slot] is None else "disabled")
                w["save"].configure(state="normal" if path else "disabled")
            gen_btn.configure(state="disabled" if running else "normal",
                              text="Генерирую..." if running else "⚡ Сгенерировать A и B")
            gen_status.configure(
                text="Генерация идёт — можно переключаться на другие вкладки" if running else "")

        def _stop(slot):
            p = procs[slot]
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
            procs[slot] = None
            slot_widgets[slot]["play"].configure(state="normal" if _slot_path(slot) else "disabled")
            slot_widgets[slot]["stop"].configure(state="disabled")

        def _play(slot):
            p = _slot_path(slot)
            if not p:
                return
            _stop(slot)
            try:
                if sys.platform == "darwin":
                    proc = subprocess.Popen(["afplay", p])
                elif sys.platform == "win32":
                    os.startfile(p)
                    return
                else:
                    proc = subprocess.Popen(["xdg-open", p])
                procs[slot] = proc
                slot_widgets[slot]["play"].configure(state="disabled")
                slot_widgets[slot]["stop"].configure(state="normal")
                def watch(s=slot, pr=proc):
                    pr.wait()
                    self.after(0, lambda: procs.__setitem__(s, None) if procs[s] is pr else None)
                    self.after(0, lambda: slot_widgets[s]["play"].configure(state="normal"))
                    self.after(0, lambda: slot_widgets[s]["stop"].configure(state="disabled"))
                threading.Thread(target=watch, daemon=True).start()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось воспроизвести: {e}", parent=self)

        def _save(slot):
            p = _slot_path(slot)
            if not p:
                return
            chosen = filedialog.askdirectory(title=f"Сохранить вариант {slot}", parent=self)
            if not chosen:
                return
            dst = str(Path(chosen) / Path(p).name)
            try:
                shutil.copy2(p, dst)
                task = _current_task(slot)
                notes[slot] = (task["id"], f"✓ Сохранено: {Path(dst).name}")
                render_slots()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить: {e}", parent=self)

        def save_fields():
            text = text_box.get("1.0", "end").rstrip("\n")
            self._panel_state_set(
                "voice_gen",
                voice=voice_box.get("1.0", "end").strip(),
                text=text,
                model=model_var.get(),
                lang=lang_var.get(),
            )
            # В поле появился новый текст (набор, вставка, переводчик) — старый больше не предлагаем.
            if text.strip() and self._panel_state_get("voice_gen", "text_backup"):
                self._panel_state["voice_gen"].pop("text_backup", None)
                render_restore_notice()

        def _generate_slot(slot, batch):
            voice_value = voice_box.get("1.0", "end").strip()
            full_text = text_box.get("1.0", "end").strip()
            model_id = (ELEVEN_TTS_MODEL_ID_V3 if model_var.get() == "Eleven v3"
                        else ELEVEN_TTS_MODEL_ID)
            language_code = ELEVEN_TTS_LANGUAGES.get(lang_var.get())
            task_id = tasks.create("voice_gen", "voice_gen", title=f"Голос, вариант {slot}",
                                   meta={"slot": slot, "batch": batch}, status="running")

            def run():
                try:
                    global ELEVENLABS_API_KEY
                    ELEVENLABS_API_KEY = self.api_key
                    if re.fullmatch(r"[A-Za-z0-9]{20}", voice_value):
                        voice_id = voice_value
                        vname = voice_value
                    else:
                        voice_id = self.fetch_voice_id_by_name(voice_value)
                        vname = voice_value
                    if not voice_id:
                        tasks.update(task_id, status="error", error="Голос не найден")
                        return
                    eleven_text = full_text.replace("------", '[пауза 3 сек]')
                    safe_vname = safe_name(vname or voice_id)
                    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
                    # Не системный temp: готовые A/B должны пережить перезапуск приложения.
                    os.makedirs(VOICE_DATA_DIR, exist_ok=True)
                    out_path = os.path.join(VOICE_DATA_DIR, f"{safe_vname}_{timestamp}_variant_{slot}.mp3")
                    text_to_speech_mp3(eleven_text, voice_id, out_path, self.log,
                                       model_id=model_id, language_code=language_code)
                    try:
                        dur = get_duration(out_path)
                    except Exception:
                        dur = None
                    tasks.update(task_id, status="done", progress=1.0,
                                 result={"path": out_path, "duration": dur})
                    prune_voice_files(keep_paths=[((t.get("result") or {}).get("path"))
                                                  for t in tasks.list(kind="voice_gen", batch=batch)])
                except Exception as e:
                    err = str(e)
                    self.log(f"Ошибка генерации голоса (вариант {slot}): {err}")
                    error_reporter.report("app.text_to_speech_mp3", exc=e)
                    tasks.update(task_id, status="error", error=err)

            threading.Thread(target=run, daemon=True).start()

        def generate_both():
            if not self.api_key:
                messagebox.showerror("Ошибка", "Сначала добавь ElevenLabs API key.", parent=self)
                return
            if not voice_box.get("1.0", "end").strip():
                messagebox.showerror("Ошибка", "Введите voice_id или имя голоса.", parent=self)
                return
            if not text_box.get("1.0", "end").strip():
                messagebox.showerror("Ошибка", "Введите текст.", parent=self)
                return
            batch = uuid.uuid4().hex[:8]
            notes.clear()
            save_fields()
            self._panel_state_set("voice_gen", batch=batch)
            for slot in ["A", "B"]:
                _generate_slot(slot, batch)
            render_slots()

        gen_btn.configure(command=generate_both)

        def _update_status(*args):
            try:
                tk_text.edit_modified(False)
            except Exception:
                pass
            text = text_box.get("1.0", "end").strip()
            chars = len(text)

            tk_text.tag_remove("error", "1.0", "end")
            tk_text.tag_remove("separator_ok", "1.0", "end")
            tk_text.tag_remove("separator_bad", "1.0", "end")

            tk_text.tag_config("error", underline=True, foreground="#ff4444")
            tk_text.tag_config("separator_ok", foreground="#fcd34d", font=ctk.CTkFont(size=14, weight="bold"))
            tk_text.tag_config("separator_bad", foreground="#ff4444", underline=True)

            errors = 0
            lines = text_box.get("1.0", "end").split("\n")

            for line_idx, line in enumerate(lines):
                line_num = line_idx + 1

                for m in re.finditer(r'  +', line):
                    tk_text.tag_add("error", f"{line_num}.{m.start()}", f"{line_num}.{m.end()}")
                    errors += 1

                for m in re.finditer(r' [,\.!?]', line):
                    tk_text.tag_add("error", f"{line_num}.{m.start()}", f"{line_num}.{m.end()}")
                    errors += 1

                for m in re.finditer(r'[!?]{2,}|\.{4,}|,{2,}', line):
                    tk_text.tag_add("error", f"{line_num}.{m.start()}", f"{line_num}.{m.end()}")
                    errors += 1

                for m in re.finditer(r'-{2,}', line):
                    matched = m.group()
                    if matched == "------":
                        tk_text.tag_add("separator_ok", f"{line_num}.{m.start()}", f"{line_num}.{m.end()}")
                    else:
                        tk_text.tag_add("separator_bad", f"{line_num}.{m.start()}", f"{line_num}.{m.end()}")
                        errors += 1

            if errors == 0:
                status_text_label.configure(
                    text=f"✓ {chars} символов — текст готов к озвучке",
                    text_color="#86efac")
            else:
                status_text_label.configure(
                    text=f"⚠ {chars} символов · {errors} ошибок",
                    text_color="#fcd34d")

        def check_spelling(text):
            try:
                res = requests.post(
                    "https://api.languagetool.org/v2/check",
                    data={"text": text, "language": "auto"},
                    timeout=5,
                )
                if res.status_code != 200:
                    return []
                matches = res.json().get("matches", [])
                return [
                    {
                        "offset": m["offset"],
                        "length": m["length"],
                        "message": m["message"],
                        "type": m["rule"]["issueType"],
                    }
                    for m in matches
                    if m["rule"]["issueType"] in ("misspelling", "grammar")
                ]
            except Exception:
                return []

        spell_timer = {"id": None}

        def _schedule_spell_check(*args):
            if spell_timer["id"]:
                frame.after_cancel(spell_timer["id"])
            spell_timer["id"] = frame.after(800, _run_spell_check)

        def _run_spell_check():
            if not self.spellcheck_online:      # выключено в «Настройках»: на languagetool.org ничего не уходит
                tk_text.tag_remove("spell_error", "1.0", "end")
                return
            text = text_box.get("1.0", "end")
            def run():
                matches = check_spelling(text)
                self.after(0, lambda m=matches: _apply_spell_tags(m))
            threading.Thread(target=run, daemon=True).start()

        def _apply_spell_tags(matches):
            tk_text.tag_remove("spell_error", "1.0", "end")
            tk_text.tag_config("spell_error", underline=True, foreground="#ff6b6b")
            for m in matches:
                start_idx = tk_text.index(f"1.0 + {m['offset']} chars")
                end_idx = tk_text.index(f"1.0 + {m['offset'] + m['length']} chars")
                tk_text.tag_add("spell_error", start_idx, end_idx)

        tk_text.bind("<KeyRelease>", lambda e: (
            self.after(100, _update_status),
            _schedule_spell_check(),
        ))
        tk_text.bind("<<Modified>>", lambda e: self.after(100, _update_status))
        tk_text.bind("<ButtonRelease>", lambda e: self.after(100, _update_status))
        # Поля сохраняются при вводе и переживают переключение вкладок и перезапуск.
        voice_box._textbox.bind("<KeyRelease>", lambda e: save_fields(), add="+")
        tk_text.bind("<KeyRelease>", lambda e: save_fields(), add="+")
        model_var.trace_add("write", lambda *a: save_fields())
        lang_var.trace_add("write", lambda *a: save_fields())
        self._vg = {"voice_box": voice_box, "text_box": text_box, "save_fields": save_fields,
                    "update_status": lambda: self.after(100, _update_status),
                    "render_restore_notice": render_restore_notice}
        render_restore_notice()
        self._register_renderer("voice_gen", render_slots)
        if saved.get("text"):
            self.after(100, _update_status)
    def add_roll(self):
        roll = {
            "id": self.next_roll_id,
            "mode": "1 видео",
            "voice_file": "",
            "text": "",
            "video_single": "",
            "video_start": "",
            "video_end": "",
            "audio_start": "",
            "audio_end": "",
            "status": "Ожидает",
            "face_mode": "default",
            "face_points": {},
        }
        self.next_roll_id += 1
        self.rolls.append(roll)
        self.selected_roll_id = roll["id"]
        self.render_scene_list()
        self.render_scene_detail()
        self.update_status()

    def clear_rolls(self):
        if self.is_processing:
            messagebox.showwarning("Очередь", "Нельзя очищать во время обработки.")
            return
        self.rolls = []
        self.add_roll()

    def render_rolls(self):
        self.render_scene_list()
        self.render_scene_detail()

    def render_scene_list(self):
        for w in self.scene_list_scroll.winfo_children():
            w.destroy()
        for idx, roll in enumerate(self.rolls):
            self._render_scene_item(idx, roll)
        self._enable_mousewheel(self.scene_list_scroll)

    def _render_scene_item(self, idx, roll):
        is_active = (roll["id"] == self.selected_roll_id)
        bg = BTN_PRIMARY if is_active else CARD

        item = ctk.CTkFrame(
            self.scene_list_scroll, fg_color=bg, corner_radius=10, cursor="hand2")
        item.pack(fill="x", padx=8, pady=(0, 5))

        top_row = ctk.CTkFrame(item, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=(8, 2))

        title = self.get_roll_title(idx, roll)
        title_lbl = ctk.CTkLabel(
            top_row, text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT, anchor="w",
        )
        title_lbl.pack(side="left", fill="x", expand=True)

        status_color = MUTED
        if roll["status"] == "Готово":
            status_color = "#86efac"
        elif roll["status"] == "Ошибка":
            status_color = "#fca5a5"
        elif roll["status"] == "В очереди" or roll["status"] == "Обработка":
            status_color = "#fcd34d"

        ctk.CTkLabel(
            top_row, text=roll["status"],
            font=ctk.CTkFont(size=10),
            text_color=status_color, anchor="e",
        ).pack(side="right")

        estimate_text = self._estimate_short_text(self._estimate_for(roll))
        mode_lbl = ctk.CTkLabel(
            item, text=roll["mode"] + (f"  ·  {estimate_text}" if estimate_text else ""),
            font=ctk.CTkFont(size=11),
            text_color=MUTED, anchor="w",
        )
        mode_lbl.pack(anchor="w", padx=10, pady=(0, 8))

        def select(event=None, rid=roll["id"]):
            self.selected_roll_id = rid
            self.render_scene_list()
            self.render_scene_detail()

        def on_enter(event, w=item, rid=roll["id"]):
            if rid != self.selected_roll_id:
                w.configure(fg_color=BTN_HOVER)

        def on_leave(event, w=item, rid=roll["id"]):
            if rid != self.selected_roll_id:
                w.configure(fg_color=CARD)

        for widget in (item, top_row, title_lbl, mode_lbl):
            widget.bind("<Button-1>", select)
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)

        self._add_copy_menu(title_lbl, lambda lbl=title_lbl: lbl.cget("text"))
        self._apply_dnd(item, lambda r=roll: r, also_select=True)

    def render_scene_detail(self):
        # Сцена — отдельный контейнер. Его содержимое перестраивается целиком из self.rolls,
        # остальные панели при этом только скрываются (не уничтожаются).
        container, _ = self._show_container(
            "scene", lambda: ctk.CTkFrame(self.detail_panel, fg_color=BG, corner_radius=0))
        for w in container.winfo_children():
            w.destroy()

        roll = next((r for r in self.rolls if r["id"] == self.selected_roll_id), None)
        if roll is None:
            self.label(container, "Выбери сцену из списка слева",
                       size=15, color=MUTED).pack(expand=True, anchor="center", pady=60)
            return

        idx = self.rolls.index(roll)

        # Scrollable container for detail content
        detail_scroll = ctk.CTkScrollableFrame(
            container, fg_color=BG,
            scrollbar_button_color="#3d3d6a",
            scrollbar_button_hover_color="#ff7eb6",
        )
        detail_scroll.pack(fill="both", expand=True)
        self._enable_mousewheel(detail_scroll)

        # ── Header ────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(detail_scroll, fg_color=CARD, corner_radius=16)
        hdr.pack(fill="x", padx=16, pady=(16, 10))

        title_row = ctk.CTkFrame(hdr, fg_color="transparent")
        title_row.pack(fill="x", padx=16, pady=(14, 6))

        scene_title = self.get_roll_title(idx, roll)
        self.label(title_row, scene_title, size=20, weight="bold").pack(side="left")

        status_color = MUTED
        if roll["status"] == "Готово":
            status_color = "#86efac"
        elif roll["status"] == "Ошибка":
            status_color = "#fca5a5"
        elif roll["status"] in ("В очереди", "Обработка"):
            status_color = "#fcd34d"

        self.label(title_row, roll["status"], size=13, color=status_color).pack(
            side="right", padx=(0, 8))

        self.button(title_row, "Удалить",
                    lambda rid=roll["id"]: self.remove_roll_by_id(rid),
                    color=BTN_DANGER, hover=BTN_DANGER_HOVER, width=100).pack(
            side="right", padx=(0, 10))

        self._sync_fetch_widgets = None
        if roll["status"] == "Прервано":
            self._build_sync_fetch_row(hdr, roll)

        # ── Mode ──────────────────────────────────────────────────────────
        mode_row = ctk.CTkFrame(hdr, fg_color="transparent")
        mode_row.pack(fill="x", padx=16, pady=(0, 14))
        self.label(mode_row, "Режим:", size=13, color=MUTED).pack(side="left", padx=(0, 10))
        mode_menu = ctk.CTkOptionMenu(
            mode_row, values=["1 видео", "Начало + Конец"],
            command=lambda value, i=idx: self.set_roll_mode(i, value),
            fg_color=BTN, button_color=BTN, button_hover_color=BTN_HOVER,
            dropdown_fg_color=PANEL, dropdown_hover_color=BTN_HOVER, width=170,
        )
        mode_menu.set(roll["mode"])
        mode_menu.pack(side="left")

        # ── Summary ───────────────────────────────────────────────────────
        self.label(detail_scroll, self.roll_details(roll),
                   size=12, color=MUTED).pack(anchor="w", padx=20, pady=(0, 4))
        self._scene_estimate_label = self.label(
            detail_scroll, self._estimate_detail_text(self._estimate_for(roll)), size=12, color=MUTED)
        self._scene_estimate_label.pack(anchor="w", padx=20, pady=(0, 10))

        # ── Actions ───────────────────────────────────────────────────────
        actions = ctk.CTkFrame(detail_scroll, fg_color=CARD, corner_radius=16)
        actions.pack(fill="x", padx=16, pady=(0, 10))

        row1 = ctk.CTkFrame(actions, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=(14, 10))

        self._apply_dnd(row1, lambda r=roll: r)

        self.button(row1, "📁 Выбрать папку",
                    lambda i=idx: self.choose_folder_auto(i),
                    color=BTN, hover=BTN_HOVER, width=180).pack(side="left", padx=(0, 10))

        voice_text = "✅ full_voice.mp3" if roll["voice_file"] else "Выбрать full_voice"
        self.button(row1, voice_text, lambda i=idx: self.choose_voice_file(i),
                    color=BTN_OK if roll["voice_file"] else BTN_PRIMARY,
                    hover=BTN_OK_HOVER if roll["voice_file"] else BTN_PRIMARY_HOVER,
                    width=190).pack(side="left")

        if roll["mode"] == "Начало + Конец":
            text_btn = "✅ Текст проверки" if roll["text"] else "Текст проверки"
            self.button(row1, text_btn, lambda i=idx: self.open_roll_text_dialog(i),
                        color=BTN_OK if roll["text"] else BTN_PRIMARY,
                        hover=BTN_OK_HOVER if roll["text"] else BTN_PRIMARY_HOVER,
                        width=180).pack(side="left", padx=(10, 0))

        # ── Video / audio trim ────────────────────────────────────────────
        row2 = ctk.CTkFrame(actions, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(0, 14))
        self._apply_dnd(row2, lambda r=roll: r)

        if roll["mode"] == "1 видео":
            time_row = ctk.CTkFrame(actions, fg_color="transparent")
            time_row.pack(fill="x", padx=16, pady=(0, 10))

            self.label(time_row, "Обрезать аудио (сек):",
                       size=12, color=MUTED).pack(side="left", padx=(0, 8))

            start_entry = ctk.CTkEntry(
                time_row, width=90, placeholder_text="от",
                fg_color="white", text_color="#111", border_color="#737373")
            start_entry.pack(side="left", padx=(0, 6))
            if roll["audio_start"]:
                start_entry.insert(0, roll["audio_start"])

            end_entry = ctk.CTkEntry(
                time_row, width=90, placeholder_text="до",
                fg_color="white", text_color="#111", border_color="#737373")
            end_entry.pack(side="left")
            if roll["audio_end"]:
                end_entry.insert(0, roll["audio_end"])

            def save_times(*_, r=roll):
                if self._roll_is_locked(r, notify=False):
                    # Ролик в обработке: возвращаем сохранённые значения без всплывающего окна.
                    for entry, key in [(start_entry, "audio_start"), (end_entry, "audio_end")]:
                        entry.delete(0, "end")
                        if r[key]:
                            entry.insert(0, r[key])
                    return
                for entry, key in [(start_entry, "audio_start"), (end_entry, "audio_end")]:
                    val = entry.get().strip().replace(",", ".")   # «2,5» с русской раскладки — тоже число
                    if val:
                        try:
                            float(val)
                            r[key] = val
                            if entry.get().strip() != val:
                                entry.delete(0, "end")
                                entry.insert(0, val)
                        except ValueError:
                            entry.delete(0, "end")
                            r[key] = ""
                    else:
                        r[key] = ""
                self._schedule_state_save()

            def save_times_typing(*_, r=roll):
                """При наборе: корректное число сохраняется сразу (переживёт переключение вкладок
                и закрытие), недописанное («1,», «-») не стирается — строгая проверка на выходе из поля."""
                if self._roll_is_locked(r, notify=False):
                    return
                changed = False
                for entry, key in [(start_entry, "audio_start"), (end_entry, "audio_end")]:
                    val = entry.get().strip().replace(",", ".")
                    if val:
                        try:
                            float(val)
                        except ValueError:
                            continue
                    if r.get(key, "") != val:
                        r[key] = val
                        changed = True
                if changed:
                    self._schedule_state_save()

            for e in (start_entry, end_entry):
                e.bind("<FocusOut>", save_times)
                e.bind("<Return>", save_times)
                e.bind("<KeyRelease>", save_times_typing, add="+")

            video_text = "✅ Видео" if roll["video_single"] else "Выбрать видео"
            self.button(row2, video_text, lambda i=idx: self.choose_video(i, "single"),
                        color=BTN_OK if roll["video_single"] else BTN_PRIMARY,
                        hover=BTN_OK_HOVER if roll["video_single"] else BTN_PRIMARY_HOVER,
                        width=170).pack(side="left")
        else:
            start_text = "✅ Видео начало" if roll["video_start"] else "Видео начало"
            end_text = "✅ Видео конец" if roll["video_end"] else "Видео конец"
            self.button(row2, start_text, lambda i=idx: self.choose_video(i, "start"),
                        color=BTN_OK if roll["video_start"] else BTN_PRIMARY,
                        hover=BTN_OK_HOVER if roll["video_start"] else BTN_PRIMARY_HOVER,
                        width=170).pack(side="left")
            self.button(row2, end_text, lambda i=idx: self.choose_video(i, "end"),
                        color=BTN_OK if roll["video_end"] else BTN_PRIMARY,
                        hover=BTN_OK_HOVER if roll["video_end"] else BTN_PRIMARY_HOVER,
                        width=170).pack(side="left", padx=(10, 0))

        # ── Лицо говорящего (Sync: active_speaker_detection) ──────────────
        face_row = ctk.CTkFrame(actions, fg_color="transparent")
        face_row.pack(fill="x", padx=16, pady=(0, 14))
        self.label(face_row, "Лицо говорящего:", size=12, color=MUTED).pack(side="left", padx=(0, 8))
        face_menu = ctk.CTkOptionMenu(
            face_row, values=list(FACE_MODES),
            command=lambda value, i=idx: self.set_roll_face_mode(i, value),
            fg_color=BTN, button_color=BTN, button_hover_color=BTN_HOVER,
            dropdown_fg_color=PANEL, dropdown_hover_color=BTN_HOVER, width=230,
        )
        face_menu.set(FACE_MODE_LABELS[roll.get("face_mode", "default")])
        face_menu.pack(side="left")
        if roll.get("face_mode") == "manual":
            if roll["mode"] == "1 видео":
                face_kinds = [("single", "Отметить лицо")]
            else:
                face_kinds = [("start", "Лицо: начало"), ("end", "Лицо: конец")]
            for kind, caption in face_kinds:
                point = face_point_for_roll(roll, kind)
                self.button(
                    face_row,
                    f"✅ {caption} · кадр {point['frame_number']}" if point else f"🎯 {caption}",
                    lambda i=idx, k=kind: self.open_face_picker(i, k),
                    color=BTN_OK if point else BTN_PRIMARY,
                    hover=BTN_OK_HOVER if point else BTN_PRIMARY_HOVER,
                    width=190,
                ).pack(side="left", padx=(10, 0))

    def set_roll_face_mode(self, idx, label):
        if self._roll_is_locked(self.rolls[idx]):
            self.render_scene_detail()  # вернуть прежнее значение в меню
            return
        self.rolls[idx]["face_mode"] = FACE_MODES[label]
        self.rolls[idx]["status"] = "Ожидает"
        self.refresh_rolls()

    def open_face_picker(self, idx, kind):
        """Окно выбора лица: кадр из видео (та же нумерация и размер, что у входа Sync), клик — точка."""
        roll = self.rolls[idx]
        if self._roll_is_locked(roll):
            return
        video = roll.get(ROLL_VIDEO_FIELDS[kind]) or ""
        if not video or not os.path.exists(video):
            messagebox.showerror("Лицо говорящего", "Сначала выбери видео для этой части ролика.", parent=self)
            return
        part = {"single": "", "start": " — начало", "end": " — конец"}[kind]

        win = ctk.CTkToplevel(self)
        win.title(f"Лицо говорящего{part}")
        win.configure(fg_color=BG)
        win.transient(self)
        win.after(100, win.grab_set)
        box = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=16)
        box.pack(fill="both", expand=True, padx=16, pady=16)
        self.label(box, f"Кликни по лицу говорящего{part}", size=18, weight="bold").pack(
            anchor="w", padx=16, pady=(14, 2))
        hint = self.label(box, "Читаю видео…", size=12, color=MUTED)
        hint.pack(anchor="w", padx=16, pady=(0, 8))
        canvas = tk.Canvas(box, width=FACE_PICKER_MAX_W, height=FACE_PICKER_MAX_H, bg="#000000",
                           highlightthickness=0, cursor="crosshair")
        canvas.pack(padx=16)
        frame_lbl = self.label(box, "", size=12, color=MUTED)
        frame_lbl.pack(anchor="w", padx=16, pady=(8, 0))
        slider = ctk.CTkSlider(box, from_=0, to=1, number_of_steps=1)
        slider.pack(fill="x", padx=16, pady=(4, 10))
        slider.configure(state="disabled")
        btns = ctk.CTkFrame(box, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 14))

        existing = face_point_for_roll(roll, kind)
        tmp_dir = tempfile.mkdtemp(prefix="sheqel_face_")
        st = {"info": None, "frame": (existing or {}).get("frame_number", 0), "scale": 1.0,
              "frame_size": None, "photo": None, "req": 0, "pending": None,
              "point": dict(existing) if existing else None}

        def close():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            win.destroy()

        def alive():
            return win.winfo_exists()

        def draw_marker():
            canvas.delete("marker")
            p = st["point"]
            if not p or p["frame_number"] != st["frame"] or not st["frame_size"]:
                return
            cx, cy = p["coordinates"][0] * st["scale"], p["coordinates"][1] * st["scale"]
            r = 14
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#22c55e", width=3, tags="marker")
            canvas.create_line(cx - r - 6, cy, cx + r + 6, cy, fill="#22c55e", width=2, tags="marker")
            canvas.create_line(cx, cy - r - 6, cx, cy + r + 6, fill="#22c55e", width=2, tags="marker")

        def show_frame(req, n, png):
            if req != st["req"] or not alive():
                return
            img = Image.open(png)
            w, h = img.size
            scale = min(FACE_PICKER_MAX_W / w, FACE_PICKER_MAX_H / h, 1.0)
            if scale < 1.0:
                img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
            st.update(photo=ImageTk.PhotoImage(img, master=canvas), scale=scale, frame_size=(w, h))
            canvas.config(width=img.width, height=img.height)
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=st["photo"])
            fps = st["info"]["fps"]
            last = max(st["info"]["frames"] - 1, 0)
            frame_lbl.configure(text=f"Кадр {n} из {last}" + (f"  ·  {n / fps:.2f} с" if fps else ""))
            draw_marker()

        def show_error(text):
            if alive():
                hint.configure(text=text, text_color="#fca5a5")

        def load_frame(n):
            st["req"] += 1
            req = st["req"]
            png = os.path.join(tmp_dir, f"frame_{req}.png")

            def work():
                try:
                    extract_video_frame(video, n, png)
                    self.after(0, lambda: show_frame(req, n, png))
                except Exception as e:
                    self.after(0, lambda err=e: show_error(f"Не удалось извлечь кадр {n}: {err}"))

            threading.Thread(target=work, daemon=True).start()

        def on_slide(value):
            st["frame"] = int(round(value))
            frame_lbl.configure(text=f"Кадр {st['frame']}…")
            if st["pending"]:
                win.after_cancel(st["pending"])
            st["pending"] = win.after(250, lambda: load_frame(st["frame"]))

        def on_click(event):
            if not st["frame_size"]:
                return
            w, h = st["frame_size"]
            x = min(max(0, round(event.x / st["scale"])), w - 1)
            y = min(max(0, round(event.y / st["scale"])), h - 1)
            st["point"] = {"frame_number": st["frame"], "coordinates": [x, y], "width": w, "height": h,
                           "fps": st["info"]["fps"]}
            draw_marker()
            save_btn.configure(state="normal")
            hint.configure(text=f"Отмечено: кадр {st['frame']}, точка ({x}, {y}) в пикселях видео",
                           text_color="#86efac")

        def save():
            p = st["point"]
            if not p or self._roll_is_locked(roll, parent=win):
                return
            try:
                point = {"video_key": face_video_key(video), **p}
            except OSError as e:
                messagebox.showerror("Лицо говорящего", f"Видео недоступно: {e}", parent=win)
                return
            roll.setdefault("face_points", {})[kind] = point
            roll["status"] = "Ожидает"
            try:
                save_face_point(point)
            except OSError as e:
                self.log(f"Не удалось сохранить точку лица на диск: {e}")
                messagebox.showwarning("Лицо говорящего",
                                       "Точка сохранена в сцене, но не записалась на диск — "
                                       "после перезапуска её придётся отметить заново.", parent=win)
            self.log(f"Лицо говорящего, ролик {idx + 1} ({kind}): кадр {p['frame_number']}, "
                     f"точка ({p['coordinates'][0]}, {p['coordinates'][1]})")
            close()
            self.render_scene_list()
            if self._active_panel == "scene":
                self.render_scene_detail()
            self.update_status()

        self.button(btns, "Отмена", close, color=BTN, hover=BTN_HOVER, width=120).pack(side="right")
        save_btn = self.button(btns, "Сохранить", save, color=BTN_OK, hover=BTN_OK_HOVER, width=160)
        save_btn.pack(side="right", padx=(0, 8))
        save_btn.configure(state="normal" if st["point"] else "disabled")
        canvas.bind("<Button-1>", on_click)
        win.protocol("WM_DELETE_WINDOW", close)

        def init():
            try:
                info = probe_video_frames(video)
            except Exception as e:
                self.after(0, lambda err=e: show_error(f"Не удалось прочитать видео: {err}"))
                return

            def ready():
                if not alive():
                    return
                st["info"] = info
                last = max(info["frames"] - 1, 0)
                st["frame"] = min(st["frame"], last)
                if last > 0:
                    slider.configure(from_=0, to=last, number_of_steps=last, state="normal",
                                     command=on_slide)
                slider.set(st["frame"])
                text = ("Ползунком выбери кадр, где лицо хорошо видно, и кликни по лицу того, "
                        "кто говорит. Лучше кадр из начала ролика.")
                if info["rotation"] % 180:
                    text += (f"\n⚠ В видео поворот {info['rotation']}° из метаданных телефона — "
                             "точка считается для повёрнутого кадра.")
                hint.configure(text=text, text_color=MUTED)
                load_frame(st["frame"])

            self.after(0, ready)

        threading.Thread(target=init, daemon=True).start()

    def roll_details(self, roll):
        parts = []
        voice_name = Path(roll['voice_file']).name.lstrip("._") if roll["voice_file"] else ""
        parts.append(f"Голос: {voice_name}" if voice_name else "Голос: не выбран")
        if roll["text"]:
            preview = roll["text"].replace("\n", " ")
            if len(preview) > 95:
                preview = preview[:95] + "..."
            parts.append(f"Текст: {preview}")
        else:
            parts.append("Текст проверки: пусто")
        if roll["mode"] == "1 видео":
            video_name = Path(roll['video_single']).name.lstrip("._") if roll['video_single'] else ''
            parts.append(f"Видео: {video_name}" if video_name else "Видео: не выбрано")
        else:
            start_name = Path(roll['video_start']).name.lstrip("._") if roll['video_start'] else ''
            end_name = Path(roll['video_end']).name.lstrip("._") if roll['video_end'] else ''
            parts.append(f"Начало: {start_name}" if start_name else "Начало: не выбрано")
            parts.append(f"Конец: {end_name}" if end_name else "Конец: не выбрано")
        return " | ".join(parts)
    
    def get_roll_title(self, idx, roll):
        if roll["mode"] == "1 видео":
            title_video = roll["video_single"]
        else:
            title_video = roll["video_start"] or roll["video_end"]

        if title_video:
            name = Path(title_video).stem
            while name.startswith(".") or name.startswith("_"):
                name = name[1:]
            name = name or f"Сцена {idx + 1}"
            return name[:40] + "..." if len(name) > 40 else name

        return f"Сцена {idx + 1}"

    def refresh_rolls(self):
        self.render_scene_list()
        self.render_scene_detail()
        self.update_status()

    def set_roll_mode(self, idx, value):
        if self._roll_is_locked(self.rolls[idx]):
            self.render_scene_detail()  # вернуть прежнее значение в меню
            return
        self.rolls[idx]["mode"] = value
        self.rolls[idx]["status"] = "Ожидает"
        self.refresh_rolls()



    def remove_roll_by_id(self, roll_id):
        if self.is_processing:
            messagebox.showwarning("Очередь", "Нельзя удалять во время обработки.")
            return

        removed_idx = next(
            (i for i, r in enumerate(self.rolls) if r["id"] == roll_id), None)
        self.rolls = [r for r in self.rolls if r["id"] != roll_id]

        if not self.rolls:
            self.add_roll()
            return

        if self.selected_roll_id == roll_id:
            new_idx = min(removed_idx, len(self.rolls) - 1)
            self.selected_roll_id = self.rolls[new_idx]["id"]

        self.render_scene_list()
        self.render_scene_detail()
        self.update_status()



    def choose_folder_auto(self, idx):
        if self._roll_is_locked(self.rolls[idx]):
            return
        folder = filedialog.askdirectory(
            parent=self, title="Выбери папку с аудио и видео"
        )
        if not folder or self._roll_is_locked(self.rolls[idx]):
            return

        audio_exts = {".mp3", ".wav", ".m4a", ".aac"}
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".MOV"}

        audio_found = None
        video_found = None

        for f in sorted(Path(folder).iterdir()):
            if not f.is_file():
                continue
            # Пропускаем скрытые файлы macOS (._filename, .DS_Store и тд)
            if f.name.startswith(".") or f.name.startswith("._"):
                continue
            if f.suffix.lower() in audio_exts and not audio_found:
                audio_found = str(f)
            if f.suffix.lower() in video_exts and not video_found:
                video_found = str(f)

        messages = []
        if audio_found:
            self.rolls[idx]["voice_file"] = audio_found
            messages.append(f"Аудио: {Path(audio_found).name}")
        else:
            messages.append("⚠ Аудио не найдено")

        if video_found:
            self.rolls[idx]["video_single"] = video_found
            messages.append(f"Видео: {Path(video_found).name}")
        else:
            messages.append("⚠ Видео не найдено")

        self.rolls[idx]["status"] = "Ожидает"
        self.refresh_rolls()

        if audio_found or video_found:
            messagebox.showinfo("Найдено", "\n".join(messages), parent=self)
        else:
            messagebox.showwarning("Ничего не найдено",
                "В папке нет аудио или видео файлов.", parent=self)

    def choose_voice_file(self, idx):
        if self._roll_is_locked(self.rolls[idx]):
            return
        f = filedialog.askopenfilename(
            parent=self, title="Выбери full_voice.mp3 / wav",
            filetypes=[("Аудио", "*.mp3 *.wav *.m4a *.aac"), ("Все файлы", "*.*")])
        if f and not self._roll_is_locked(self.rolls[idx]):
            self.rolls[idx]["voice_file"] = f
            self.rolls[idx]["status"] = "Ожидает"
            self.render_scene_list()
            self.render_scene_detail()
            self.update_status()

    def choose_video(self, idx, kind):
        if self._roll_is_locked(self.rolls[idx]):
            return
        f = filedialog.askopenfilename(
            parent=self, title="Выбери видео",
            filetypes=[("Видео", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm"), ("Все файлы", "*.*")])
        if f and not self._roll_is_locked(self.rolls[idx]):
            if kind == "single":
                self.rolls[idx]["video_single"] = f
            elif kind == "start":
                self.rolls[idx]["video_start"] = f
            elif kind == "end":
                self.rolls[idx]["video_end"] = f
            self.rolls[idx]["status"] = "Ожидает"
            self.render_scene_list()
            self.render_scene_detail()
            self.update_status()

    def open_roll_text_dialog(self, idx):
        roll = self.rolls[idx]
        if self._roll_is_locked(roll):
            return
        win = ctk.CTkToplevel(self)
        win.title(f"Текст проверки — ролик {idx + 1}")
        win.geometry("840x620")
        win.configure(fg_color=BG)
        win.transient(self)
        win.after(100, win.grab_set)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="both", expand=True, padx=24, pady=24)
        roll_title = self.get_roll_title(idx, roll)
        self.label(frame,f"Текст — {roll_title}",size=24,weight="bold").pack(anchor="w", padx=22, pady=(20, 6))
        self.label(frame, "Для режима 'Начало + Конец': до первого ------ = начало, после последнего ------ = конец. Середина игнорируется.", size=13, color=MUTED).pack(anchor="w", padx=22, pady=(0, 14))
        text_box = ctk.CTkTextbox(frame, fg_color="#ffffff", text_color="#111111", border_width=1, border_color="#737373", corner_radius=10, font=ctk.CTkFont(size=14), wrap="word")
        text_box.pack(fill="both", expand=True, padx=22, pady=(0, 18))
        if roll["text"]:
            text_box.insert("1.0", roll["text"])
        self._fix_paste(text_box)
        def save():
            if self._roll_is_locked(roll, parent=win):
                return  # окно не закрываем — набранный текст остаётся в поле
            text_value = text_box.get("1.0", "end").strip()
            if not text_value:
                messagebox.showerror("Ошибка", "Текст пустой.", parent=win)
                return
            if roll["mode"] == "Начало + Конец":
                try:
                    parse_start_end_text(text_value)
                except Exception as e:
                    messagebox.showerror("Ошибка в тексте", str(e), parent=win)
                    return
            roll["text"] = text_value
            roll["status"] = "Ожидает"
            win.destroy()
            self.render_scene_list()
            self.render_scene_detail()
            self.update_status()
        self.button(frame, "Сохранить", save, color=BTN_OK, hover=BTN_OK_HOVER, width=180).pack(anchor="e", padx=22, pady=(0, 20))

    def validate_rolls(self):
        valid, errors = [], []
        for i, roll in enumerate(self.rolls):
            has_any = any([roll["voice_file"], roll["text"], roll["video_single"], roll["video_start"], roll["video_end"]])
            if not has_any:
                continue
            if not roll["voice_file"]:
                errors.append(f"Ролик {i + 1}: не выбран full_voice.mp3.")
            if roll["mode"] == "1 видео":
                if not roll["video_single"]:
                    errors.append(f"Ролик {i + 1}: не выбрано видео.")
            else:
                if not roll["text"]:
                    errors.append(f"Ролик {i + 1}: нет текста проверки.")
                if not roll["video_start"]:
                    errors.append(f"Ролик {i + 1}: не выбрано видео начала.")
                if not roll["video_end"]:
                    errors.append(f"Ролик {i + 1}: не выбрано видео конца.")
                if roll["text"]:
                    try:
                        parse_start_end_text(roll["text"])
                    except Exception as e:
                        errors.append(f"Ролик {i + 1}: {e}")
            if roll.get("face_mode") == "manual":
                kinds = [("single", "видео")] if roll["mode"] == "1 видео" else [
                    ("start", "видео начала"), ("end", "видео конца")]
                for kind, label in kinds:
                    if roll.get(ROLL_VIDEO_FIELDS[kind]) and not face_point_for_roll(roll, kind):
                        errors.append(f"Ролик {i + 1}: выбран ручной режим лица, но лицо не отмечено ({label}).")
            complete = False
            if roll["mode"] == "1 видео":
                complete = bool(roll["voice_file"] and roll["video_single"])
            else:
                complete = bool(roll["voice_file"] and roll["text"] and roll["video_start"] and roll["video_end"])
            if complete:
                valid.append((i, roll))
        return valid, errors

    def start_queue(self):
        if self.is_processing:
            messagebox.showinfo("Очередь", "Очередь уже обрабатывается.")
            return
        valid, errors = self.validate_rolls()
        if errors:
            messagebox.showerror("Ошибка", "\n".join(errors))
            return
        if not valid:
            messagebox.showerror("Ошибка", "Нет роликов для обработки.")
            return
        if not self.sync_key or "ВСТАВЬ" in self.sync_key:
            messagebox.showerror(
                "Ошибка",
                "Не задан Sync API key. Открой «Настройки» → «Ключи» → Sync → «Изменить»."
            )
            return
        if self.sync_storage == "cloudinary" and not (
                self.cloudinary_cloud_name and self.cloudinary_api_key and self.cloudinary_api_secret):
            messagebox.showerror(
                "Ошибка",
                "Выбрано хранилище Cloudinary, но ключи не заданы.\nЗадай их в «Настройки» → «Ключи» → "
                "Cloudinary или выбери в «Настройки» → «Липсинк Sync» хранилище «Sync (рекомендуется)»."
            )
            return
        global SYNC_API_KEY
        SYNC_API_KEY = self.sync_key
        self._confirm_queue_cost(valid)

    def _confirm_queue_cost(self, valid):
        """Оценка перед запуском. Любая ошибка расчёта очередь не блокирует — строка «оценка недоступна»."""
        self.start_btn.configure(state="disabled", text="СЧИТАЮ СТОИМОСТЬ…")
        rolls = list(valid)

        def one(item):
            try:
                return self._estimate_for(item[1], wait=True) or {"error": "не хватает файлов"}
            except Exception as e:
                return {"error": str(e)}

        def work():
            try:
                with ThreadPoolExecutor(max_workers=4) as ex:
                    estimates = list(ex.map(one, rolls))
            except Exception as e:
                estimates = [{"error": str(e)} for _ in rolls]
            self.after(0, lambda: decide(estimates))

        def decide(estimates):
            self.start_btn.configure(state="normal", text="НАЧАТЬ ОЧЕРЕДЬ")
            self._persist_sync_price()
            total = sum(e["cost"] for e in estimates if not e.get("error"))
            if self.cost_confirm_enabled or total > self.cost_confirm_threshold:
                self._show_cost_confirm_window(rolls, estimates,
                                               lambda: self._launch_queue(rolls, estimates))
            else:
                self._launch_queue(rolls, estimates)

        threading.Thread(target=work, daemon=True).start()

    def _show_cost_confirm_window(self, valid, estimates, on_start):
        win = ctk.CTkToplevel(self)
        win.title("Стоимость очереди")
        win.configure(fg_color=BG)
        win.transient(self)
        win.after(100, win.grab_set)
        box = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=16)
        box.pack(fill="both", expand=True, padx=16, pady=16)
        self.label(box, "Перед запуском: оценка Sync", size=18, weight="bold").pack(
            anchor="w", padx=16, pady=(14, 8))

        rows = sum(1 + (len(e["parts"]) if not e.get("error") and len(e["parts"]) > 1 else 0)
                   for e in estimates)
        table = ctk.CTkScrollableFrame(box, fg_color=CARD, corner_radius=10,
                                       height=min(360, 40 + rows * 28), width=720)
        table.pack(fill="both", expand=True, padx=16)
        for col, (head, weight) in enumerate(
                [("Сцена", 3), ("Длительность", 1), ("Кадры", 1), ("≈ Цена", 1), ("≈ Время", 1)]):
            table.grid_columnconfigure(col, weight=weight)
            self.label(table, head, size=12, weight="bold", color=MUTED).grid(
                row=0, column=col, sticky="w", padx=8, pady=(6, 4))

        def add_row(r, cells, color=TEXT):
            for col, text in enumerate(cells):
                self.label(table, text, size=12, color=color).grid(row=r, column=col, sticky="w",
                                                                   padx=8, pady=2)

        names = {"single": "Видео", "start": "начало", "end": "конец"}
        r = 1
        for (idx, roll), est in zip(valid, estimates):
            title = self.get_roll_title(idx, roll)
            if est.get("error"):
                add_row(r, [title, "—", "—", "оценка недоступна", "—"], color="#fcd34d")
                r += 1
                continue
            add_row(r, [title, f"{est['billable']:.1f} с", str(est["frames"]),
                        f"${est['cost']:.2f}", format_minutes(est["time"])])
            r += 1
            if len(est["parts"]) > 1:
                for p in est["parts"]:
                    add_row(r, [f"    {names[p['kind']]}", f"{p['billable']:.1f} с", str(p["frames"]),
                                f"${p['cost']:.2f}", format_minutes(p["time"])], color=MUTED)
                    r += 1

        known = [e for e in estimates if not e.get("error")]
        total = sum(e["cost"] for e in known)
        frames = sum(e["frames"] for e in known)
        queue_time = estimate_queue_time([e["time"] for e in known])
        summary = (f"Итого: {len(valid)} рол. · {frames} кадров · ≈ ${total:.2f} · "
                   f"очередь ≈ {format_minutes(queue_time)} (параллельно {CONCURRENT_ROLLS})")
        self.label(box, summary, size=14, weight="bold").pack(anchor="w", padx=16, pady=(10, 2))
        sources = {e["source"] for e in known}
        if sources == {"sync"}:
            price_note = "Цена — по данным Sync."
        elif sources == {"local"}:
            price_note = (f"Цена — локальная оценка (${local_price_per_frame():.5f} за кадр): "
                          "Sync не ответил.")
        elif sources:
            price_note = "Цена — по данным Sync, для части роликов — локальная оценка."
        else:
            price_note = "Оценка недоступна — очередь всё равно можно запустить."
        if len(known) < len(estimates):
            price_note += f" Для {len(estimates) - len(known)} рол. оценка недоступна."
        self.label(box, price_note, size=12, color=MUTED).pack(anchor="w", padx=16)
        self.label(box, "Время — оценка: 20–30 с на секунду видео + 1–2 мин ожидания Sync на каждую "
                        "часть, без локальной подготовки.", size=12, color=MUTED).pack(anchor="w", padx=16)

        btns = ctk.CTkFrame(box, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(12, 14))

        def start():
            win.destroy()
            on_start()

        self.button(btns, "Запустить", start, color=BTN_OK, hover=BTN_OK_HOVER, width=160).pack(side="right")
        self.button(btns, "Отмена", win.destroy, color=BTN, hover=BTN_HOVER, width=120).pack(
            side="right", padx=(0, 8))

    def _launch_queue(self, valid, estimates):
        if self.is_processing:
            return
        with self._progress_lock:
            self._queue_progress = {
                order: {"title": self.get_roll_title(idx, roll),
                        "range": None if est.get("error") else est["time"],
                        "cost": None if est.get("error") else est["cost"],
                        "source": est.get("source"), "start": None, "end": None, "state": ""}
                for order, ((idx, roll), est) in enumerate(zip(valid, estimates), start=1)
            }
        self.is_processing = True
        self._qp_on_queue_start()
        self.start_btn.configure(state="disabled", text="ОБРАБАТЫВАЮ ОЧЕРЕДЬ...")
        thread = threading.Thread(target=self.queue_thread, args=(valid,), daemon=True)
        thread.start()
        self._tick_queue_progress()

    def _mark_roll_progress(self, order_num, **fields):
        with self._progress_lock:
            self._queue_progress.setdefault(order_num, {"title": f"Ролик {order_num}"}).update(fields)

    def _tick_queue_progress(self):
        self._render_queue_panel()
        if self.is_processing:
            self.after(1000, self._tick_queue_progress)


    def _process_single_roll(self, order_num, idx, roll, batch_dir, log):
        """Обрабатывает один ролик (используется и в последовательном, и в параллельном режиме)."""
        if roll["mode"] == "1 видео":
            roll_video_name = Path(roll["video_single"]).stem
        else:
            roll_video_name = Path(roll["video_start"]).stem

        temp_dir = str(Path(batch_dir) / f"tmp_{order_num:02d}_{safe_name(roll_video_name)}")
        ensure_dir(temp_dir)

        # Копируем с безопасным именем без точек в начале
        full_voice_copy = copy_file(
            roll["voice_file"],
            str(Path(temp_dir) / "full_voice.mp3")
        )

        # Если исходный файл не скопировался — пробуем через shutil напрямую
        if not file_exists_ok(full_voice_copy):
            import shutil as _sh
            _sh.copy2(roll["voice_file"], str(Path(temp_dir) / "full_voice.mp3"))
        if roll["text"]:
            with open(Path(temp_dir) / "full_text.txt", "w", encoding="utf-8") as f:
                f.write(roll["text"])

        log("=" * 60)
        log(f"Ролик {idx + 1} / очередь {order_num}")
        log(f"Режим: {roll['mode']}")
        log("=" * 60)

        if roll["mode"] == "1 видео":
            audio_wav = str(Path(temp_dir) / "full_voice.wav")
            convert_audio_to_wav_trimmed(
                full_voice_copy,
                audio_wav,
                start_sec=roll.get("audio_start", ""),
                end_sec=roll.get("audio_end", ""),
            )
            if roll.get("audio_start") or roll.get("audio_end"):
                log(f"Обрезка: {roll.get('audio_start') or '0'} → {roll.get('audio_end') or 'конец'} сек")

            video_name = safe_name(Path(roll["video_single"]).stem)
            out = str(Path(batch_dir) / f"{order_num:02d}_{video_name}.mp4")
            process_lipsync(roll["video_single"], audio_wav, out, temp_dir, log,
                            face_mode=roll.get("face_mode", "default"),
                            face_point=face_point_for_roll(roll, "single"),
                            sync_fps=sync_target_fps(self.sync_fps_mode),
                            storage=self.sync_storage,
                            journal_ctx={"roll_id": roll.get("id"), "part": "single", "batch": batch_dir})
            log(f"ГОТОВО: {out}")
        else:
            start_text, end_text, sep_count = parse_start_end_text(roll["text"])
            part_start, part_end = split_start_end_by_silence(
                full_voice_copy, temp_dir, log,
                expected_separators=sep_count,
            )
            video_name_start = safe_name(Path(roll["video_start"]).stem)
            video_name_end = safe_name(Path(roll["video_end"]).stem)

            out_start = str(Path(batch_dir) / f"{order_num:02d}_{video_name_start}_start.mp4")
            out_end = str(Path(batch_dir) / f"{order_num:02d}_{video_name_end}_end.mp4")
            log("Lipsync start...")
            process_lipsync(roll["video_start"], part_start, out_start, temp_dir, log,
                            face_mode=roll.get("face_mode", "default"),
                            face_point=face_point_for_roll(roll, "start"),
                            sync_fps=sync_target_fps(self.sync_fps_mode),
                            storage=self.sync_storage,
                            journal_ctx={"roll_id": roll.get("id"), "part": "start", "batch": batch_dir})
            log("Lipsync end...")
            process_lipsync(roll["video_end"], part_end, out_end, temp_dir, log,
                            face_mode=roll.get("face_mode", "default"),
                            face_point=face_point_for_roll(roll, "end"),
                            sync_fps=sync_target_fps(self.sync_fps_mode),
                            storage=self.sync_storage,
                            journal_ctx={"roll_id": roll.get("id"), "part": "end", "batch": batch_dir})
            log(f"ГОТОВО: {out_start}")
            log(f"ГОТОВО: {out_end}")

        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass    

    def queue_thread(self, valid_rolls):
        try:
            failed_videos = []
            failed_lock = threading.Lock()
            base_output = str(Path(desktop_dir()) / "Lipsync_Queue_Output")
            ensure_dir(base_output)

            batch_name = time.strftime("%d-%m-%Y_%H-%M-%S")
            batch_dir = str(Path(base_output) / batch_name)
            suffix = 2
            while True:
                try:
                    # exist_ok=False — атомарно «занимаем» папку, даже если
                    # параллельно запущен второй экземпляр приложения.
                    os.makedirs(batch_dir)
                    break
                except FileExistsError:
                    batch_dir = str(Path(base_output) / f"{batch_name}_{suffix}")
                    suffix += 1

            total = len(valid_rolls)
            self._queue_batch_dir = batch_dir
            self.log("=" * 70)
            self.log(f"Папка партии: {batch_dir}")
            self.log(f"Роликов в очереди: {total}")
            self.log(f"Параллельность: {CONCURRENT_ROLLS} ролика одновременно")
            self.log(f"Стартовая задержка между запусками: {PAUSE_BETWEEN_ROLLS_SEC} сек")
            self.log("=" * 70)

            def process_one(order_num, idx, roll):
                prefix = f"[#{order_num:02d}]"

                def prefixed_log(msg):
                    self.log(f"{prefix} {msg}")

                # Стагер старта: тред N стартует через N*PAUSE сек — предотвращает
                # одновременный удар по Sync API несколькими потоками
                stagger = (order_num - 1) * PAUSE_BETWEEN_ROLLS_SEC
                if stagger > 0:
                    prefixed_log(f"Жду {stagger} сек перед стартом...")
                    time.sleep(stagger)

                self._mark_roll_progress(order_num, start=time.time(), end=None)
                with self._progress_lock:
                    planned = dict(self._queue_progress.get(order_num, {}))
                if planned.get("cost") is not None:
                    prefixed_log(f"Оценка ролика: ≈ ${planned['cost']:.2f} "
                                 f"({format_estimate_source(planned.get('source'))}), "
                                 f"время ≈ {format_minutes(planned['range'])}")
                else:
                    prefixed_log("Оценка ролика недоступна")
                try:
                    self.set_roll_status(idx, f"Обработка {order_num}/{total}")
                    self._process_single_roll(order_num, idx, roll, batch_dir, prefixed_log)
                    self.set_roll_status(idx, "Готово")
                    self._mark_roll_progress(order_num, end=time.time(), state="готово")
                except Exception as e:
                    self._mark_roll_progress(order_num, end=time.time(), state="ошибка")
                    if roll["mode"] == "1 видео":
                        video_name = Path(roll.get("video_single", "")).name or f"ролик {idx + 1}"
                    else:
                        s = Path(roll.get("video_start", "")).name
                        en = Path(roll.get("video_end", "")).name
                        video_name = f"{s} + {en}" if (s and en) else f"ролик {idx + 1}"

                    error_text = str(e)
                    prefixed_log(f"❌ ОШИБКА в '{video_name}': {error_text}")
                    self.set_roll_status(idx, "Ошибка")

                    with failed_lock:
                        failed_videos.append({
                            "video_name": video_name,
                            "error": error_text,
                            "roll_id": roll.get("id"),   # для job_id Sync в «Подробнее»
                        })

            with ThreadPoolExecutor(max_workers=CONCURRENT_ROLLS) as executor:
                futures = [
                    executor.submit(process_one, order_num, idx, roll)
                    for order_num, (idx, roll) in enumerate(valid_rolls, start=1)
                ]
                for f in futures:
                    f.result()

            failed_copy = list(failed_videos)
            self.log(f"Очередь завершена: успешно {total - len(failed_copy)} из {total}, ошибок {len(failed_copy)}")
            self.after(0, lambda: self._show_queue_summary(batch_dir, total, failed_copy))

        except Exception as e:
            err = str(e)
            self.log(f"ОБЩАЯ ОШИБКА: {err}")
            self.after(0, lambda: messagebox.showerror("Ошибка", err))
        finally:
            self.is_processing = False
            self.after(0, lambda: self.start_btn.configure(state="normal", text="НАЧАТЬ ОЧЕРЕДЬ"))
            self.after(0, self._tick_queue_progress)  # финальное состояние в окне прогресса

    def _show_queue_summary(self, batch_dir, total, failed):
        """Итог очереди — строкой в панели хода очереди. Не модально, папку сам не открывает,
        остаётся, пока пользователь не закроет."""
        self._queue_summary = {"batch_dir": batch_dir, "total": total, "failed": list(failed),
                               "time": time.time()}
        self._qp_details_open = False
        self._qp_set_state(hidden=False)
        self._render_queue_panel()

    # ── Панель хода очереди (внизу главного окна) ────────────────────────
    def _qp_state(self, key, default=None):
        return self._panel_state_get("queue_panel", key, default)

    def _build_queue_panel(self, parent, before):
        """Создаётся один раз. Показ и скрытие — pack/pack_forget, данные — из памяти."""
        self._qp_parent_before = before
        qp = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12)
        self._qp = qp
        head = ctk.CTkFrame(qp, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=6)
        w = {"head": head}
        w["title"] = self.label(head, "", size=12, weight="bold")
        w["title"].pack(side="left")
        w["bar"] = ctk.CTkProgressBar(head, width=140, height=6)
        w["bar"].set(0)
        w["bar"].pack(side="left", padx=10)
        w["eta"] = self.label(head, "", size=12, color=MUTED)
        w["eta"].pack(side="left")
        small = dict(height=24, corner_radius=8, font=ctk.CTkFont(size=12), fg_color=BTN, hover_color=BTN_HOVER,
                     text_color=TEXT)
        w["close"] = ctk.CTkButton(head, text="✕", width=28, command=self._qp_close, **small)
        w["close"].pack(side="right")
        w["toggle"] = ctk.CTkButton(head, text="Развернуть", width=100, command=self._qp_toggle, **small)
        w["toggle"].pack(side="right", padx=(6, 6))
        w["folder"] = ctk.CTkButton(head, text="Открыть папку", width=120, command=self._qp_open_folder, **small)
        w["details_btn"] = ctk.CTkButton(head, text="Подробнее", width=100, command=self._qp_toggle_details, **small)
        mono = ctk.CTkFont(family="Courier New", size=12)
        # Подробности итога: ошибки с причинами и job_id, текст копируется
        w["details"] = ctk.CTkFrame(qp, fg_color="transparent")
        w["details_box"] = ctk.CTkTextbox(w["details"], height=110, fg_color="#111111", text_color="#e5e5e5",
                                          font=mono, corner_radius=8, wrap="word")
        w["details_box"].pack(fill="x", padx=12)
        ctk.CTkButton(w["details"], text="Копировать", width=110,
                      command=lambda: self._qp_copy(w["details_box"]), **small).pack(anchor="e", padx=12, pady=(4, 6))
        # Развёрнутая часть: строки роликов и лог
        w["body"] = ctk.CTkFrame(qp, fg_color="transparent")
        w["rolls"] = ctk.CTkTextbox(w["body"], height=60, fg_color=BG, text_color=TEXT, font=mono,
                                    corner_radius=8, wrap="none")
        w["rolls"].pack(fill="x", padx=12, pady=(0, 6))
        w["log"] = ctk.CTkTextbox(w["body"], height=120, fg_color="#111111", text_color="#e5e5e5", font=mono,
                                  corner_radius=8)
        w["log"].pack(fill="both", expand=True, padx=12)
        w["log"].bind("<Command-a>", lambda e: w["log"].tag_add("sel", "1.0", "end"))
        btns = ctk.CTkFrame(w["body"], fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=6)
        ctk.CTkButton(btns, text="Копировать лог", width=130, command=lambda: self._qp_copy(w["log"]),
                      **small).pack(side="left")
        ctk.CTkButton(btns, text="Очистить", width=90, command=self._qp_clear_log, **small).pack(side="left", padx=6)
        w["folder2"] = ctk.CTkButton(btns, text="Открыть папку", width=120, command=self._qp_open_folder, **small)
        w["folder2"].pack(side="left")
        self._qp_w = w
        self._qp_log_shown = 0      # сквозной номер последней строки лога, уже показанной в поле
        self.bind("<Configure>", self._qp_on_resize, add="+")

    # Видимость
    def _qp_visible_wanted(self):
        if self._qp_forced:
            return True
        if self._qp_state("hidden", False):
            return False
        mode = self.queue_panel_mode
        if self.is_processing:
            return mode == "running"
        if self._queue_summary is not None:
            return mode in ("running", "summary")
        return False

    def _qp_set_state(self, **fields):
        self._panel_state_set("queue_panel", **fields)

    def _qp_toggle(self):
        self._qp_set_state(expanded=not self._qp_state("expanded", False))
        self._render_queue_panel()

    def _qp_close(self):
        """Крестик: скрыть панель. Итог закрывается совсем; во время работы — скрыта до итога."""
        self._qp_forced = False
        if not self.is_processing:
            self._queue_summary = None
            self._qp_details_open = False
        self._qp_set_state(hidden=True)
        self._render_queue_panel()

    def _qp_toggle_details(self):
        self._qp_details_open = not self._qp_details_open
        self._render_queue_panel()

    def _qp_open_folder(self):
        folder = (self._queue_summary or {}).get("batch_dir") or self._queue_batch_dir
        if folder:
            open_folder(folder)

    def _qp_copy(self, box):
        try:
            text = box.get("1.0", "end").strip()
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass

    def _qp_clear_log(self):
        self._log_lines.clear()
        self._qp_log_shown = self._log_seq
        self._qp_w["log"].delete("1.0", "end")

    def _qp_on_resize(self, event):
        if event.widget is self and self._qp_w and self._qp_state("expanded", False):
            self._qp_fit_height()

    def _qp_fit_height(self):
        """Развёрнутая панель — не выше ~35% окна: при высоте 640 основное содержимое остаётся рабочим."""
        total = max(self.winfo_height(), 400)
        budget = int(total * 0.35) - 44 - 40
        n = max(1, min(len(self._queue_progress), 6))
        rolls_h = min(22 * n + 10, max(40, budget // 2))
        self._qp_w["rolls"].configure(height=rolls_h)
        self._qp_w["log"].configure(height=max(60, budget - rolls_h - 10))

    # Данные
    def _qp_rows(self):
        now = time.time()
        with self._progress_lock:
            items = sorted((k, dict(v)) for k, v in self._queue_progress.items())
        rows = []
        for order, p in items:
            expected = f"ожидается ≈ {format_minutes(p['range'])}" if p.get("range") else "оценка недоступна"
            if p.get("start") is None:
                icon, status = "⏳", f"ждёт · {expected}"
            else:
                elapsed = (p.get("end") or now) - p["start"]
                clock = f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
                if p.get("end"):
                    icon = "✗" if p.get("state") == "ошибка" else "✓"
                    status = f"{p.get('state', '')} за {clock}"
                else:
                    icon, status = "▶", f"идёт {clock} · {expected}"
            rows.append((order, icon, p.get("title", f"Ролик {order}"), status, p))
        return rows

    def _qp_progress(self):
        """(готово, всего, идущие номера, доля, «ещё ≈ T» или '')."""
        now = time.time()
        rows = self._qp_rows()
        total = len(rows)
        done = sum(1 for r in rows if r[4].get("end"))
        running = [r[0] for r in rows if r[4].get("start") and not r[4].get("end")]
        frac, remaining = float(done), []
        for _order, _icon, _title, _status, p in rows:
            if p.get("end"):
                continue
            rng = p.get("range")
            if not rng:
                remaining = None          # у ролика нет оценки — общее «ещё ≈» не показываем
                continue
            if p.get("start"):
                elapsed = now - p["start"]
                frac += min(elapsed / max(sum(rng) / 2, 1), 0.95)
                rng = (max(rng[0] - elapsed, 0), max(rng[1] - elapsed, 0))
            if remaining is not None:
                remaining.append(rng)
        eta = ""
        if remaining:
            lo, hi = estimate_queue_time(remaining)
            eta = f"ещё ≈ {format_minutes((lo, hi))}" if hi > 0 else ""
        return done, total, running, (frac / total if total else 0.0), eta

    # Рендер
    def _render_queue_panel(self):
        if not self._qp_w:
            return
        w = self._qp_w
        if not self._qp_visible_wanted():
            if self._qp.winfo_manager():
                self._qp.pack_forget()
            return
        if not self._qp.winfo_manager():
            self._qp.pack(fill="x", padx=20, pady=(4, 0), before=self._qp_parent_before)
        expanded = bool(self._qp_state("expanded", False))
        summary = self._queue_summary
        if self.is_processing or (summary is None and self._queue_progress and not self._qp_forced):
            done, total, running, frac, eta = self._qp_progress()
            now_txt = (f" · идёт ролик {', '.join(str(r) for r in running)}" if running else "")
            w["title"].configure(text=f"Очередь: {done} из {total}{now_txt}", text_color=TEXT)
            w["bar"].set(frac)
            w["eta"].configure(text=eta)
        elif summary is not None:
            failed = summary["failed"]
            ok = summary["total"] - len(failed)
            w["title"].configure(text=f"Готово {ok} из {summary['total']}"
                                      + (f" · {len(failed)} с ошибкой" if failed else ""),
                                 text_color="#fcd34d" if failed else "#86efac")
            w["bar"].set(1.0)
            w["eta"].configure(text="")
        else:
            w["title"].configure(text="Лог приложения", text_color=TEXT)
            w["bar"].set(0)
            w["eta"].configure(text="")
        has_summary = summary is not None and not self.is_processing
        folder = (summary or {}).get("batch_dir") or self._queue_batch_dir
        for key, show, side_opts in (("folder", bool(folder) and has_summary, {"side": "right", "padx": (6, 0)}),
                                     ("details_btn", has_summary, {"side": "right", "padx": (6, 0)})):
            if show and not w[key].winfo_manager():
                w[key].pack(before=w["toggle"], **side_opts)
            elif not show and w[key].winfo_manager():
                w[key].pack_forget()
        w["details_btn"].configure(text="Скрыть подробности" if self._qp_details_open else "Подробнее")
        w["close"].configure(text="✕")
        # Подробности итога
        if has_summary and self._qp_details_open:
            text = self._qp_summary_text(summary)
            if w["details_box"].get("1.0", "end").strip() != text.strip():
                w["details_box"].configure(state="normal")
                w["details_box"].delete("1.0", "end")
                w["details_box"].insert("1.0", text)
                w["details_box"].configure(state="disabled")
            if not w["details"].winfo_manager():
                w["details"].pack(fill="x", after=w["head"])
        elif w["details"].winfo_manager():
            w["details"].pack_forget()
        # Развёрнутая часть
        w["toggle"].configure(text="Свернуть" if expanded else "Развернуть")
        if expanded:
            if not w["body"].winfo_manager():
                w["body"].pack(fill="both", expand=True)
                self._qp_fit_height()
                self._qp_sync_log(force=True)
            lines = "\n".join(f"{icon} #{order:02d} {title} — {status}"
                              for order, icon, title, status, _p in self._qp_rows())
            if w["rolls"].get("1.0", "end").rstrip("\n") != lines:
                w["rolls"].configure(state="normal")
                w["rolls"].delete("1.0", "end")
                w["rolls"].insert("1.0", lines or "Очередь не запускалась")
                w["rolls"].configure(state="disabled")
        elif w["body"].winfo_manager():
            w["body"].pack_forget()
        f = w["folder2"]
        f.configure(state="normal" if folder else "disabled")

    def _qp_summary_text(self, summary):
        parts = [f"Папка: {summary['batch_dir']}"]
        if not summary["failed"]:
            parts.append("Все ролики обработаны успешно.")
        with self._journal_lock:
            journal = list(self._sync_journal)
        for item in summary["failed"]:
            lines = [f"✗ {item['video_name']}", f"  Причина: {item['error']}"]
            rid = item.get("roll_id")
            if rid is not None:
                jobs = [v["job_id"] for v in sync_jobs_for_roll(journal, rid).values()
                        if v.get("batch") == summary["batch_dir"]]
                if jobs:
                    lines.append(f"  job_id Sync: {', '.join(jobs)}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def _qp_sync_log(self, force=False):
        """Дописывает в поле лога новые строки из памяти (по сквозному номеру строки). Прокрутка
        к концу — только если пользователь и так внизу, чтобы не сбивать чтение старых строк."""
        box = self._qp_w.get("log") if self._qp_w else None
        if box is None or (not box.winfo_manager() and not force):
            return
        lines = list(self._log_lines)                 # [(номер, текст)], последние LOG_PANEL_MAX_LINES
        first = lines[0][0] if lines else self._log_seq + 1
        if force or self._qp_log_shown < first - 1:   # пропущены строки, вытесненные из памяти
            box.delete("1.0", "end")
            if lines:
                box.insert("end", "\n".join(text for _n, text in lines) + "\n")
            self._qp_log_shown = self._log_seq
            box.see("end")
            return
        new = [text for n, text in lines if n > self._qp_log_shown]
        if not new:
            return
        at_bottom = box.yview()[1] >= 0.999
        box.insert("end", "\n".join(new) + "\n")
        excess = int(box.index("end-1c").split(".")[0]) - 1 - LOG_PANEL_MAX_LINES
        if excess > 0:
            box.delete("1.0", f"{excess + 1}.0")
        self._qp_log_shown = self._log_seq
        if at_bottom:
            box.see("end")

    def _qp_on_queue_start(self, batch_dir=None):
        self._queue_summary = None
        self._qp_details_open = False
        self._queue_batch_dir = batch_dir
        self._qp_set_state(hidden=False)   # новая очередь снова показывает панель (по настройке)
        self._render_queue_panel()

    def _on_queue_panel_mode_change(self, label):
        self.queue_panel_mode = QUEUE_PANEL_MODES[label]
        self.save_config()
        self._render_queue_panel()

    def open_log_window(self):
        """«Открыть лог»: разворачивает панель хода очереди с логом (отдельного окна больше нет)."""
        self._qp_forced = True
        self._qp_set_state(hidden=False, expanded=True)
        self._render_queue_panel()

    def set_roll_status(self, idx, status):
        def update():
            if 0 <= idx < len(self.rolls):
                self.rolls[idx]["status"] = status
                self.render_scene_list()
                # Перерисовываем детали только если пользователь сейчас на панели сцены —
                # иначе уничтожили бы открытую панель (генератор, переводчик…) вместе с вводом.
                if (self._active_panel == "scene"
                        and self.rolls[idx]["id"] == self.selected_roll_id):
                    self.render_scene_detail()
                self.update_status()
        self.after(0, update)

    def _refresh_balance_badge(self):
        if not self.api_key:
            return

        def run():
            balance = fetch_elevenlabs_balance(self.api_key)
            self.log(f"Balance fetch result: {balance}")
            if balance:
                self._el_balance = balance
                remaining = balance["remaining"]
                label = f"{remaining // 1000}k" if remaining >= 1000 else str(remaining)
                pct = balance["remaining"] / max(balance["limit"], 1)
                if pct > 0.3:
                    badge_color = "#0a5c47"
                    badge_text_color = "#86efac"
                elif pct > 0.1:
                    badge_color = "#7a4f00"
                    badge_text_color = "#fcd34d"
                else:
                    badge_color = "#7a1500"
                    badge_text_color = "#fca5a5"
                self.after(500, lambda l=label, bc=badge_color, tc=badge_text_color:
                    self._update_api_btn_badge(l, bc, tc))

        threading.Thread(target=run, daemon=True).start()

    def _update_api_btn_badge(self, label, badge_color, text_color):
        """Баланс ElevenLabs теперь в нижнем блоке сайдбара и в «Настройках»."""
        try:
            self._render_balance()
        except Exception:
            pass

    def update_status(self):
        self._schedule_state_save()  # вызывается после любых изменений роликов
        valid, _ = self.validate_rolls()
        queue_estimate = self._queue_estimate_text()
        self.status_label.configure(
            text=f"готовых роликов: {len(valid)} | всего роликов: {len(self.rolls)}"
                 + (f" | {queue_estimate}" if queue_estimate else ""))
        self._render_key_status()   # точки ключей, баланс, строки «Настроек»
        self._render_nav_active()

    def log(self, msg):
        msg = str(msg)
        write_log_file(msg)
        self.log_queue.put(msg)

    def report_callback_exception(self, exc, val, tb):
        write_log_file("ИСКЛЮЧЕНИЕ В UI-КОЛБЭКЕ:\n" + "".join(
            traceback.format_exception(exc, val, tb)))
        error_reporter.report(_where_from_tb(tb), exc=val, exc_type=exc.__name__)
        traceback.print_exception(exc, val, tb)

    def poll_logs(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                for line in str(msg).splitlines() or [""]:
                    self._log_seq += 1
                    self._log_lines.append((self._log_seq, line))
        except queue.Empty:
            pass
        try:
            self._qp_sync_log()      # копится всегда; в поле — только если панель развёрнута
        except Exception:
            self.report_callback_exception(*sys.exc_info())
        try:
            self._process_task_changes()
            if self._compress_dirty:
                self._compress_dirty = False
                self._render_panel("compress")
            if self._journal_dirty:
                self._journal_dirty = False
                self._schedule_state_save()
        except Exception:
            self.report_callback_exception(*sys.exc_info())
        self.after(120, self.poll_logs)

    # ── Общие данные потоков: список автоудаления голосов и счётчик Groq ──
    def _expiry_add(self, entry):
        with self._expiry_lock:
            self.voice_expiry_list.append(dict(entry))

    def _expiry_remove(self, voice_id):
        with self._expiry_lock:
            self.voice_expiry_list[:] = [e for e in self.voice_expiry_list if e.get("voice_id") != voice_id]

    def _expiry_snapshot(self):
        return _locked_copy(self, "_expiry_lock", "voice_expiry_list")

    def _groq_add_seconds(self, seconds):
        with self._groq_lock:
            self.groq_used_seconds += float(seconds or 0)

    def _groq_seconds(self):
        with self._groq_lock:
            return self.groq_used_seconds

    def _groq_reset(self):
        with self._groq_lock:
            self.groq_used_seconds = 0.0

    def _voice_expiry_watcher(self):
        while True:
            time.sleep(60)
            self._delete_expired_voices("автоудалён (истёк срок)")

    def _delete_expired_voices(self, reason):
        """Удаляет просроченные клоны. Список читается копией под блокировкой: его параллельно
        дополняет клонирование в другом потоке."""
        now = time.time()
        expired = [v for v in self._expiry_snapshot() if v["expires_at"] <= now]
        changed = False
        for v in expired:
            try:
                if not self.api_key:
                    continue
                if delete_elevenlabs_voice(v["voice_id"], self.api_key):
                    self.log(f"Голос «{v['name']}» {reason}")
                    self._expiry_remove(v["voice_id"])
                    changed = True
            except Exception as e:
                self.log(f"Ошибка удаления голоса «{v['name']}»: {e}")
        if changed:
            self.save_config()

    def _cleanup_expired_voices_on_start(self):
        if not self.api_key or not self._expiry_snapshot():
            return
        # Сеть — в фоне: старт приложения не ждёт ответов ElevenLabs.
        threading.Thread(target=self._delete_expired_voices, args=("удалён при старте (просрочен)",),
                         daemon=True).start()

    def load_config(self):
        """Загружает API ключи и настройки из ~/.sheqelmotion.json."""
        try:
            if not os.path.exists(CONFIG_PATH):
                return
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.api_key = data.get("elevenlabs_api_key", "") or self.api_key
            sync_key = data.get("sync_api_key", "")
            if sync_key:
                self.sync_key = sync_key
                global SYNC_API_KEY
                SYNC_API_KEY = sync_key
            self.cloudinary_cloud_name = data.get("cloudinary_cloud_name", "") or self.cloudinary_cloud_name
            self.cloudinary_api_key    = data.get("cloudinary_api_key", "")    or self.cloudinary_api_key
            self.cloudinary_api_secret = data.get("cloudinary_api_secret", "") or self.cloudinary_api_secret
            global CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
            CLOUDINARY_CLOUD_NAME  = self.cloudinary_cloud_name
            CLOUDINARY_API_KEY     = self.cloudinary_api_key
            CLOUDINARY_API_SECRET  = self.cloudinary_api_secret
            self.main_voice_name = data.get("main_voice_name", "") or self.main_voice_name
            self.main_voice_id = data.get("main_voice_id", "") or getattr(self, "main_voice_id", "")
            self.clear_text_after_clone = bool(data.get("clear_text_after_clone", True))
            self.spellcheck_online = bool(data.get("spellcheck_online", True))
            with _lock_of(self, "_expiry_lock"):
                self.voice_expiry_list = list(data.get("voice_expiry_list", []))
            self.groq_api_key = data.get("groq_api_key", "") or self.groq_api_key
            with _lock_of(self, "_groq_lock"):
                self.groq_used_seconds = float(data.get("groq_used_seconds", 0.0))
            self.install_id = data.get("install_id", "") or self.install_id
            self.error_reports_enabled = bool(data.get("error_reports_enabled", True))
            self.error_reports_notice_shown = bool(data.get("error_reports_notice_shown", False))
            self.cost_confirm_enabled = bool(data.get("cost_confirm_enabled", True))
            self.cost_confirm_threshold = float(data.get("cost_confirm_threshold",
                                                         COST_CONFIRM_DEFAULT_THRESHOLD))
            if data.get("sync_price_per_frame"):
                sync_price_state["per_frame"] = float(data["sync_price_per_frame"])
            choice = data.get("sync_storage")
            self._sync_storage_choice = choice if choice in SYNC_STORAGE_MODES.values() else None
            has_cloudinary = bool(self.cloudinary_cloud_name and self.cloudinary_api_key
                                  and self.cloudinary_api_secret)
            # Без явного выбора — Sync для всех (в том числе при настроенном Cloudinary).
            # Явный выбор Cloudinary сохраняется; откат на Cloudinary при ошибке загрузки — как раньше.
            self.sync_storage = self._sync_storage_choice or "sync"
            self.queue_panel_mode = data.get("queue_panel_mode", "running")
            if self.queue_panel_mode not in QUEUE_PANEL_MODES.values():
                self.queue_panel_mode = "running"
            self.sync_fps_mode = data.get("sync_fps_mode", "source")
            if self.sync_fps_mode not in SYNC_FPS_MODES.values():
                self.sync_fps_mode = "source"
        except Exception as e:
            # Не сохраняем конфиг до конца сессии: иначе пустые поля затрут ключи в файле.
            self._config_load_failed = True
            backup = f"{CONFIG_PATH}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                shutil.copy2(CONFIG_PATH, backup)
            except OSError:
                backup = "(не удалось сделать копию)"
            self.log(f"Не удалось загрузить конфиг {CONFIG_PATH}: {e}. Копия: {backup}. "
                     "Сохранение настроек отключено до перезапуска.")
            self.after(1000, lambda: messagebox.showwarning(
                "Настройки не загрузились",
                f"Файл настроек повреждён и не прочитан:\n{CONFIG_PATH}\n\n"
                f"Копия сохранена: {backup}\n\n"
                "Чтобы не затереть твои ключи, сохранение настроек в этой сессии "
                "отключено. Восстанови файл из копии или сообщи разработчику.",
                parent=self,
            ))

    def save_config(self):
        """Сохраняет API ключи и настройки в ~/.sheqelmotion.json."""
        if self._config_load_failed:
            self.log("Сохранение настроек пропущено: конфиг не загрузился при старте.")
            return
        try:
            data = {
                "elevenlabs_api_key":    self.api_key,
                "sync_api_key":          self.sync_key,
                "cloudinary_cloud_name": self.cloudinary_cloud_name,
                "cloudinary_api_key":    self.cloudinary_api_key,
                "cloudinary_api_secret": self.cloudinary_api_secret,
                "main_voice_name":       self.main_voice_name,
                "main_voice_id":         getattr(self, "main_voice_id", ""),
                "clear_text_after_clone": getattr(self, "clear_text_after_clone", True),
                "spellcheck_online":     getattr(self, "spellcheck_online", True),
                "voice_expiry_list":     _locked_copy(self, "_expiry_lock", "voice_expiry_list"),
                "groq_api_key":          self.groq_api_key,
                "groq_used_seconds":     _locked_copy(self, "_groq_lock", "groq_used_seconds"),
                "install_id":            self.install_id,
                "error_reports_enabled": self.error_reports_enabled,
                "error_reports_notice_shown": self.error_reports_notice_shown,
                "cost_confirm_enabled":  self.cost_confirm_enabled,
                "cost_confirm_threshold": self.cost_confirm_threshold,
                "queue_panel_mode":      getattr(self, "queue_panel_mode", "running"),
                "sync_price_per_frame":  sync_price_state["per_frame"],
                "sync_fps_mode":         self.sync_fps_mode,
                "sync_storage":          self._sync_storage_choice,
            }
            with self._config_lock:
                tmp_path = CONFIG_PATH + ".tmp"
                with open_private(tmp_path) as f:          # сразу 0600: в файле ключи
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, CONFIG_PATH)
                secure_path(CONFIG_PATH)
        except Exception as e:
            self.log(f"Не удалось сохранить конфиг: {e}")

    def _enable_mousewheel(self, scrollable):
    #Включает скролл колесом мыши на всех виджетах внутри scrollable frame.
        canvas = scrollable._parent_canvas

        def on_wheel(event):
            if sys.platform == "darwin":
                delta = -1 * event.delta
            else:
                delta = int(-1 * (event.delta / 120))

            canvas.yview_scroll(delta, "units")

        def on_b4(event):
            canvas.yview_scroll(-1, "units")

        def on_b5(event):
            canvas.yview_scroll(1, "units")

        def bind_recursive(w):
            w.bind("<MouseWheel>", on_wheel, add="+")
            w.bind("<Button-4>", on_b4, add="+")
            w.bind("<Button-5>", on_b5, add="+")
            for child in w.winfo_children():
                bind_recursive(child)

        bind_recursive(scrollable)


def main():
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())            # проверка скачанного обновления: без окна, сети и записи файлов
    after_update = "--after-update" in sys.argv
    # Одна копия: вторая не запускается; после обновления ждём, пока старая закроется (до 15 с).
    ok, info = acquire_instance_lock(wait_sec=AFTER_UPDATE_WAIT_SEC if after_update else 0)
    if not ok:
        notify_already_running(info, after_update)
        return
    import atexit
    atexit.register(release_instance_lock)
    rotate_log_file()
    secure_private_files()   # права уже существующих файлов: 0600, папка ~/.sheqelmotion — 0700
    threading.excepthook = _thread_excepthook
    write_log_file(f"===== Запуск SheqelMotion Studio {APP_VERSION} =====")
    app = LipsyncTwoModeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
