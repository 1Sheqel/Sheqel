"""Часть Б без окна: эхо сценария в ошибках сервисов, хвост лога, имена файлов, права 0600/0700."""
import sys, os, json, stat, tempfile, types, threading, subprocess
from unittest.mock import patch
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""  # тесты не шлют настоящих отчётов на воркер
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

SCRIPTS = {
    "ru": "Привет, меня зовут Иван, и сегодня я расскажу о новом продукте",
    "en": "Hello my name is John and today I will tell you about our product",
    "de": "Hallo mein Name ist Hans und heute erzähle ich von unserem Produkt",
    "zh": "大家好我叫王伟今天我来介绍我们的新产品",
}
def leaked(text, script):
    words = [w for w in script.replace(",", " ").split() if len(w) >= 4] or [script[:6]]
    low = text.lower()
    return [w for w in words if w.lower() in low] if " " in script else ([script[:6]] if script[:6] in text else [])

CASES = []
for lang, s in SCRIPTS.items():
    CASES += [
        (f"ElevenLabs JSON, {lang}", "app.text_to_speech_mp3:900",
         f'ElevenLabs TTS error: 400\n{{"detail":{{"status":"invalid_text","message":"text: {s}"}}}}', s, ["HTTP 400", "status=invalid_text"]),
        (f"ElevenLabs без кавычек, {lang}", "app.text_to_speech_mp3:900",
         f"ElevenLabs TTS error: 422 text {s}", s, ["HTTP 422"]),
        (f"Groq перевод, {lang}", "app.translate_with_groq:3500",
         f'Groq API error 400: {{"error":{{"message":"Input too long: {s}","type":"invalid_request_error","code":"context_length_exceeded"}}}}',
         s, ["HTTP 400", "type=invalid_request_error", "code=context_length_exceeded"]),
        (f"Sync FAILED, {lang}", "app.apply_lipsync_sync",
         f"Sync не сработал после 3 попыток, после создания задачи job-123 (генерация Sync): Sync failed: "
         f"{{'status': 'FAILED', 'error': 'Audio: {s}', 'errorCode': 'generation_pipeline_failed'}}", s,
         ["FAILED", "errorCode=generation_pipeline_failed", "после создания задачи"]),
        (f"Groq транскрибация в одинарных кавычках, {lang}", "app.transcribe_with_groq:3600",
         f"Groq transcription error 413: '{s}' is too large", s, ["HTTP 413"]),
    ]
CASES += [
    ("yt-dlp", "app.download_from_url", "ERROR: [youtube] dQw4w9WgXcQ: Видео «Клиент Иванов — промо» недоступно. Video unavailable",
     "Клиент Иванов", ["Video unavailable"]),
    ("ffmpeg", "app.compress_video_file", "Command '['/opt/homebrew/bin/ffmpeg', '-i', '/Users/ivan/Видео/Клиент Иванов промо.mp4', "
     "'/Users/ivan/out.mp4']' returned non-zero exit status 183.", "Клиент Иванов", ["exit status 183"]),
    ("Cloudinary", "app.upload_to_cloudinary", "Cloudinary error: Invalid video file 'Клиент Иванов промо.mp4' (text: Привет мир)",
     "Клиент Иванов", []),
]
for name, where, msg, script, codes in CASES:
    p = app.error_reporter.build_payload(where, "RuntimeError", msg)
    out = p["message"]
    lk = leaked(out, script)
    check(f"1. {name}: в message нет текста запроса или тела ответа, есть коды, ≤ 200",
          not lk and all(c in out for c in codes) and len(out) <= 200, f"{out!r}" + (f" · утечка {lk}" if lk else ""))

own = app.report_safe_message('Не удалось сохранить состояние: "Привет, меня зовут Иван" — text: секретный текст')
check("2. своя ошибка: кавычки и хвост «text: …» вычищены", "Привет" not in own and "секретный" not in own, own)

# ── хвост лога ─────────────────────────────────────────────────────────────
app._recent_log_lines.clear()
with patch.object(app, "LOG_FILE_PATH", os.path.join(tempfile.mkdtemp(), "l.log")):
    good = ["[#01] Sync status: PROCESSING", "[#01] Отправляю в Sync... попытка 1/3", "Ролик 1 / очередь 1",
            "Оценка части: 7.2 с · 25 fps · 180 кадров · ≈ $0.77 (по данным Sync) · ≈ 2–3 мин",
            "[TIMING] Текст: 245 символов, модель: eleven_v4", "Очередь завершена: успешно 1 из 2, ошибок 1",
            "[#02] Sync ошибка на попытке 2: Sync failed: {'error': 'Audio: " + SCRIPTS["ru"] + "'}"]
    bad = ["Ошибка генерации голоса (вариант A): ElevenLabs TTS error: 400",
           '{"detail":{"status":"invalid_text","message":"text: ' + SCRIPTS["de"] + '"}}',
           "Перевод готов: «" + SCRIPTS["en"] + "»", "Распознанный текст: " + SCRIPTS["zh"],
           "Сжато: Клиент Иванов промо_compressed.mp4 · 448.3 → 4.7 МБ",
           "Клон «Иван Петров диктор» подставлен в генератор", "Lipsync готов: /Users/ivan/Desktop/01_Клиент.mp4"]
    for l in good[:3] + bad[:4] + good[3:] + bad[4:]:
        app.write_log_file(l)
    tail = app.error_reporter.build_payload("app.x:1", "RuntimeError", "x")["log_tail"]
joined = "\n".join(tail)
check("3a. хвост: только строки известных форматов (статусы, попытки, оценки, итог очереди)",
      "[#01] Sync status: PROCESSING" in tail and "Ролик 1 / очередь 1" in tail
      and any(t.startswith("Оценка части:") for t in tail) and "Очередь завершена: успешно 1 из 2, ошибок 1" in tail, json.dumps(tail, ensure_ascii=False)[:300])
check("3b. хвост: от «Sync ошибка на попытке» остался только номер попытки, без ответа API",
      "[#02] Sync ошибка на попытке 2" in tail and "Audio" not in joined)
check("3c. хвост: нет текстов сценария, перевода, транскрипта, имён роликов и клонов",
      not any(leaked(joined, s) for s in SCRIPTS.values()) and "Иванов" not in joined and "Петров" not in joined
      and "invalid_text" not in joined)
check("3d. хвост: число скрытых строк указано", any(t.startswith("(строк лога не включено:") for t in tail))

# ── имена файлов ───────────────────────────────────────────────────────────
names = ["Клиент Иванов промо.mp4", "new year party final v2.MOV", "Мой ролик (финал).mkv", "voice 01 Анна.mp3",
         "Ролик_с_подчёркиваниями 2026.webm"]
for nm in names:
    for ctx in (f"Сжатие: {nm} → готово", f"Не найден файл {nm}", f"Ошибка в «{nm}»", f"файл '{nm}' повреждён"):
        out = app.sanitize_for_report(ctx)
        stem = nm.rsplit(".", 1)[0]
        bits = [w for w in stem.replace("(", " ").replace(")", " ").replace("_", " ").split() if len(w) >= 4 and not w.isdigit()]
        bad_bits = [b for b in bits if b in out]
        if bad_bits:
            check(f"4. имя «{nm}» в «{ctx}» скрыто", False, f"{out!r}")
            break
    else:
        check(f"4. имя «{nm}» скрыто во всех контекстах", True)
check("4b. обычный текст и версии не портятся", app.sanitize_for_report("Статус COMPLETED, версия 1.3.0, 25 fps")
      == "Статус COMPLETED, версия 1.3.0, 25 fps")

# ── права ──────────────────────────────────────────────────────────────────
d = tempfile.mkdtemp()
paths = {n: os.path.join(d, n) for n in ("config.json", "faces.json", "reports.json", "app.log")}
data_dir = os.path.join(d, "data"); os.makedirs(data_dir); os.chmod(data_dir, 0o755)
state = os.path.join(data_dir, "state.json")
for p in list(paths.values()) + [state]:
    open(p, "w").write("{}"); os.chmod(p, 0o644)
mode = lambda p: oct(os.stat(p).st_mode & 0o777)
with patch.multiple(app, CONFIG_PATH=paths["config.json"], STATE_PATH=state, LOG_FILE_PATH=paths["app.log"],
                    FACE_POINTS_PATH=paths["faces.json"], ERROR_REPORT_STATE_PATH=paths["reports.json"], APP_DATA_DIR=data_dir):
    app.secure_private_files()
    check("5a. при запуске уже существующие файлы → 0600, папка данных → 0700",
          all(mode(p) == "0o600" for p in list(paths.values()) + [state]) and mode(data_dir) == "0o700",
          ", ".join(f"{os.path.basename(p)} {mode(p)}" for p in list(paths.values()) + [state]) + f", data {mode(data_dir)}")
    # новые файлы — сразу 0600
    for p in list(paths.values()) + [state]:
        os.remove(p)
    os.chmod(data_dir, 0o755)
    app.AppStateStore(state).save({"x": 1})
    ns = types.SimpleNamespace(_config_load_failed=False, _config_lock=threading.Lock(), api_key="k", sync_key="", cloudinary_cloud_name="",
        cloudinary_api_key="", cloudinary_api_secret="", main_voice_name="", voice_expiry_list=[], groq_api_key="",
        groq_used_seconds=0.0, install_id="i", error_reports_enabled=True, error_reports_notice_shown=True,
        cost_confirm_enabled=True, cost_confirm_threshold=20.0, sync_fps_mode="source", _sync_storage_choice=None, log=print)
    app.LipsyncTwoModeApp.save_config(ns)
    app._log_secured_path = None
    app.write_log_file("строка")
    app.save_face_point({"video_key": "k", "x": 1, "y": 1})
    app.ErrorReporter()._save_state({"day": "x"})
    check("5b. новые файлы создаются сразу с 0600: конфиг, state.json, лог, точки лица, состояние отчётов; папка 0700",
          all(mode(p) == "0o600" for p in list(paths.values()) + [state]) and mode(data_dir) == "0o700",
          ", ".join(f"{os.path.basename(p)} {mode(p)}" for p in list(paths.values()) + [state]))
    os.chmod(paths["config.json"], 0o644)
    app.LipsyncTwoModeApp.save_config(ns)
    check("5c. повторное сохранение конфига возвращает 0600, временного файла не остаётся",
          mode(paths["config.json"]) == "0o600" and not os.path.exists(paths["config.json"] + ".tmp"))
    with patch.object(app.os, "name", "nt"):
        try:
            app.secure_path(paths["config.json"]); app.secure_private_files(); ok = True
        except Exception:
            ok = False
    check("5d. Windows: без ошибок, права не трогаются (профиль и так закрыт ACL)", ok)
print("=" * 60); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
