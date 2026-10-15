"""Санитайзер отчётов на строках в форматах реальных логов app.py."""
import sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import app

S = app.sanitize_for_report
CASES = [
    # (исходная строка, что должно исчезнуть, что должно остаться)
    ("xi-api-key: " + "sk_" + "0f" * 24,
     ["sk_3f9a"], ["<key>"]),
    ("Authorization: Bearer gsk_AbCdEf123456GhIjKl789MnOp",
     ["gsk_", "AbCdEf123456"], ["<key>"]),
    ("cloudinary.config(api_key=873498273498234, api_secret=Ab3dEf_Gh1jKl2MnOpQr4StUvWx)",
     ["873498273498234", "Ab3dEf_Gh1jKl2MnOpQr4StUvWx"], []),
    ("  → https://res.cloudinary.com/dxyz123/video/upload/v1728740000/a1b2c3d4e5f6g7h8i9j0.mp4",
     ["cloudinary.com", "a1b2c3d4e5f6g7h8i9j0", ".mp4"], ["<url>"]),
    ("Загружаю 01_Моё_видео_sync_input.mp4 (3.2 MB) на Cloudinary...",
     ["Моё_видео", ".mp4"], ["Загружаю"]),
    ("Cloudinary: удалён a1b2c3d4e5f6g7h8i9j0",
     ["a1b2c3d4e5f6g7h8i9j0"], ["Cloudinary: удалён"]),
    ('{"id":"4f1c2b3a-9d8e-4c7b-a6f5-0e1d2c3b4a59","status":"PENDING","input":[{"type":"video",'
     '"url":"https://res.cloudinary.com/x/video/upload/v1/abc.mp4"}],"outputFileName":"01_promo_ivan"}',
     ["cloudinary", "promo_ivan", "4f1c2b3a-9d8e"], ['"status":"PENDING"', '"outputFileName":"…"']),
    ("Sync job 4f1c2b3a-9d8e-4c7b-a6f5-0e1d2c3b4a59 COMPLETED, outputUrl: "
     "https://sync-output.s3.amazonaws.com/abc/result.mp4?X-Amz-Signature=deadbeefcafe1234",
     ["amazonaws", "X-Amz-Signature", "deadbeef"], ["COMPLETED, outputUrl: <url>"]),
    ("Sync failed: {'id': 'abc-123', 'status': 'FAILED', 'error': 'input video URL is inaccessible', "
     "'errorCode': 'media_url_inaccessible', 'outputFileName': 'ivan_secret'}",
     ["ivan_secret"], ["input video URL is inaccessible", "media_url_inaccessible"]),
    ("Аудио сохранено: /var/folders/k2/abc123/T/tmpx9y8z7/Roger_2026-10-12_17-13-48_variant_A.mp3",
     ["Roger", ".mp3", "/var/folders"], ["Аудио сохранено:"]),
    ("Папка партии: /Users/ivan.petrov/Desktop/Lipsync_Queue_Output/12-10-2026_17-12-51",
     ["ivan.petrov", "/Users/", "Desktop"], ["Папка партии: ~/…"]),
    (r"Ошибка скачивания/записи результата job abc (попытка 1/3): [Errno 2] No such file or directory: "
     r"'C:\Users\Иван\OneDrive\Рабочий стол\Lipsync_Queue_Output\tmp_01_sync\01_sync_raw.mp4'",
     ["Иван", "OneDrive", r"C:\Users", ".mp4"], ["[Errno 2] No such file or directory", "попытка 1/3"]),
    ("Голос «Мой клон Иван 2» автоудалён (истёк срок)",
     ["Мой клон", "Иван"], ["Голос «…» автоудалён"]),
    ("[download] Destination: /Users/ivan/Desktop/SheqelMotion_Downloads/12-10_My Secret Video Title.mp4",
     ["ivan", "Secret", "Title", ".mp4"], ["[download] Destination:"]),
    ("Скачано: https://www.youtube.com/watch?v=dQw4w9WgXcQ → /Users/ivan/Desktop/SheqelMotion_Downloads/x.mp4",
     ["youtube.com", "dQw4w9WgXcQ", "ivan"], ["Скачано:"]),
    ('ElevenLabs TTS error: 404\n{"detail":{"status":"voice_not_found","message":'
     '"A voice with the voice_id 21m00Tcm4TlvDq8ikWAM was not found."}}',
     ["21m00Tcm4TlvDq8ikWAM"], ["ElevenLabs TTS error: 404", "voice_not_found"]),
    ('  File "/Users/ivan/SheqelM/app.py", line 812, in apply_lipsync_sync',
     ["ivan"], ["line 812, in apply_lipsync_sync"]),
    (r'  File "C:\Users\Иван\Downloads\SheqelM\app.py", line 812, in apply_lipsync_sync',
     ["Иван"], ["line 812, in apply_lipsync_sync"]),
    ("Использую голос аккаунта: Roger - Laid-Back, Casual, Resonant (voice_id: JBFqnCBsd6RMkjVDRZzb)",
     ["JBFqnCBsd6RMkjVDRZzb"], []),
]
# Полезные строки должны пройти без изменений
UNCHANGED = [
    "[TIMING] ElevenLabs API (ожидание ответа): 1.49 сек  (статус 200)",
    "Sync ошибка на попытке 1: Sync status error после повторов: 500",
    "Пробую cookies из браузера: Firefox (1/5)...",
    "Перевожу на английский через Groq LLaMA...",
    "Sync status: PROCESSING",
    "Пауза 60 секунд перед повтором...",
    "[TIMING] Текст: 52 символов, модель: eleven_v4",
    "Отправляю в Sync... попытка 2/3",
]

ok = True
for raw, gone, kept in CASES:
    out = S(raw)
    bad = [g for g in gone if g in out] + [k for k in kept if k not in out]
    ok &= not bad
    print(f"[{'PASS' if not bad else 'FAIL'}] {raw[:70]!r}\n        → {out!r}" + (f"\n        ✗ {bad}" if bad else ""))
for raw in UNCHANGED:
    out = S(raw)
    good = out == raw
    ok &= good
    print(f"[{'PASS' if good else 'FAIL'}] без изменений: {raw!r}" + ("" if good else f" → {out!r}"))
print("\nСАНИТАЙЗЕР:", "ВСЁ ПРОШЛО" if ok else "ЕСТЬ ПРОВАЛЫ")
sys.exit(0 if ok else 1)
