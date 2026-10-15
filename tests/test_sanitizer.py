"""Санитайзер отчётов на строках в форматах реальных логов app.py."""
import sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import app

S = app.sanitize_for_report

# Ключи и токены здесь вымышленные и собираются из частей при запуске: в исходнике нет ни одной
# цельной строки, похожей на ключ (иначе её ловят сканеры секретов, в том числе GitHub).
def _j(*parts):
    return "".join(parts)
FAKE_SK = _j("s", "k_", "0f" * 24)                                   # вид ключа ElevenLabs
FAKE_GSK = _j("g", "sk_", "AbCdEf", "123456", "GhIjKl", "789MnOp")    # вид ключа Groq
FAKE_CL_KEY = _j("873498", "273498234")
FAKE_CL_SECRET = _j("Ab3dEf_", "Gh1jKl2", "MnOpQr4", "StUvWx")
FAKE_PUBLIC_ID = _j("a1b2c3d4e5", "f6g7h8i9j0")
FAKE_UUID = _j("4f1c2b3a-9d8e-", "4c7b-a6f5-", "0e1d2c3b4a59")
FAKE_SIG = _j("deadbeef", "cafe1234")
FAKE_VOICE_1 = _j("21m00Tcm4T", "lvDq8ikWAM")
FAKE_VOICE_2 = _j("JBFqnCBsd6", "RMkjVDRZzb")
CASES = [
    # (исходная строка, что должно исчезнуть, что должно остаться)
    ("xi-api-key: " + FAKE_SK,
     [FAKE_SK, FAKE_SK[:10]], ["<key>"]),
    ("Authorization: Bearer " + FAKE_GSK,
     [FAKE_GSK[:4], FAKE_GSK[4:16]], ["<key>"]),
    ("cloudinary.config(api_key=" + FAKE_CL_KEY + ", api_secret=" + FAKE_CL_SECRET + ")",
     [FAKE_CL_KEY, FAKE_CL_SECRET], []),
    ("  → https://res.cloudinary.com/dxyz123/video/upload/v1728740000/" + FAKE_PUBLIC_ID + ".mp4",
     ["cloudinary.com", FAKE_PUBLIC_ID, ".mp4"], ["<url>"]),
    ("Загружаю 01_Моё_видео_sync_input.mp4 (3.2 MB) на Cloudinary...",
     ["Моё_видео", ".mp4"], ["Загружаю"]),
    ("Cloudinary: удалён " + FAKE_PUBLIC_ID,
     [FAKE_PUBLIC_ID], ["Cloudinary: удалён"]),
    ('{"id":"' + FAKE_UUID + '","status":"PENDING","input":[{"type":"video",'
     '"url":"https://res.cloudinary.com/x/video/upload/v1/abc.mp4"}],"outputFileName":"01_promo_ivan"}',
     ["cloudinary", "promo_ivan", FAKE_UUID[:13]], ['"status":"PENDING"', '"outputFileName":"…"']),
    ("Sync job " + FAKE_UUID + " COMPLETED, outputUrl: "
     "https://sync-output.s3.amazonaws.com/abc/result.mp4?X-Amz-Signature=" + FAKE_SIG,
     ["amazonaws", "X-Amz-Signature", FAKE_SIG[:8]], ["COMPLETED, outputUrl: <url>"]),
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
     '"A voice with the voice_id ' + FAKE_VOICE_1 + ' was not found."}}',
     [FAKE_VOICE_1], ["ElevenLabs TTS error: 404", "voice_not_found"]),
    ('  File "/Users/ivan/SheqelM/app.py", line 812, in apply_lipsync_sync',
     ["ivan"], ["line 812, in apply_lipsync_sync"]),
    (r'  File "C:\Users\Иван\Downloads\SheqelM\app.py", line 812, in apply_lipsync_sync',
     ["Иван"], ["line 812, in apply_lipsync_sync"]),
    ("Использую голос аккаунта: Roger - Laid-Back, Casual, Resonant (voice_id: " + FAKE_VOICE_2 + ")",
     [FAKE_VOICE_2], []),
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
