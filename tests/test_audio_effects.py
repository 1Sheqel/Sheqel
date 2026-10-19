"""Эффекты звука ElevenLabs без сети: каталог, кэш, тело запроса, формат ulaw, ошибки 400/404, режим A/B,
отчёт без текста, паузы для «2 видео» при шуме."""
import os, sys, json, stat, tempfile, subprocess, types
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

# Формат как у настоящего каталога (id из ответа ElevenLabs, описания вымышленные)
RAW = {
    "filter_presets": [{"filter_preset_id": "old_radio", "name": "Old Radio", "description": "Lo-fi"},
                       {"filter_preset_id": "phone", "name": "Phone", "description": None},
                       {"filter_preset_id": "", "name": "Без id"}],
    "environments": [{"environment_id": "small_room", "name": "Small Room", "description": "Tight space"},
                     {"environment_id": "hall", "name": "Hall", "description": None}],
    "background_noises": [{"background_noise_id": "cafe", "name": "Café", "description": None},
                          {"background_noise_id": "keyboard", "name": "Keyboard"}],
}
class R:
    def __init__(self, code, data=None, text="", content=b""):
        self.status_code, self._data, self.text, self.content = code, data, text, content
    def json(self):
        if self._data is None:
            raise ValueError("нет json")
        return self._data

# ── 1. Каталог ──
cat = app.parse_audio_effects_catalog(RAW)
check("1a. каталог разобран: порядок и имена как в ответе, элемент без id отброшен",
      [i["name"] for i in cat["filter"]] == ["Old Radio", "Phone"] and [i["id"] for i in cat["environment"]] ==
      ["small_room", "hall"] and cat["noise"][0]["name"] == "Café")
seen = {}
def fake_get(url, headers=None, timeout=None):
    seen.update(url=url, headers=headers)
    return R(200, RAW)
got = app.fetch_audio_effects_catalog("-".join(("fake", "key")), get=fake_get)
check("1b. GET /v1/audio-effects/catalog с ключом в заголовке xi-api-key",
      seen["url"].endswith("/v1/audio-effects/catalog") and seen["headers"] == {"xi-api-key": "fake-key"} and got == cat)
try:
    app.fetch_audio_effects_catalog("-".join(("fake", "key")), get=lambda *a, **k: R(401, text="unauthorized fake-key"))
    err = ""
except RuntimeError as e:
    err = str(e)
check("1c. ошибка 401 → только код, без ключа и тела", err == "каталог эффектов: HTTP 401", err)
try:
    app.fetch_audio_effects_catalog("k", get=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("x")))
    err = ""
except RuntimeError as e:
    err = str(e)
check("1d. нет сети → «каталог эффектов недоступен»", "недоступен" in err)
empty = app.parse_audio_effects_catalog({"filter_presets": [], "environments": [], "background_noises": []})
check("1e. пустой каталог распознан как пустой", app.audio_effects_catalog_empty(empty) and
      not app.audio_effects_catalog_empty(cat))
try:
    app.parse_audio_effects_catalog({"filter_presets": []})
    bad = False
except ValueError:
    bad = True
check("1f. ответ без нужных списков → ошибка разбора", bad)

# ── 2. Кэш ──
d = tempfile.mkdtemp()
path = os.path.join(d, "audio_effects_catalog.json")
app.save_audio_effects_cache(cat, path, now=1000.0)
back, at = app.load_audio_effects_cache(path)
check("2a. кэш: запись и чтение, дата сохранена", back == cat and at == 1000.0)
check("2b. права кэша 0600", stat.S_IMODE(os.stat(path).st_mode) == 0o600)
check("2c. срок: 23 ч — свежий, 25 ч — устарел", not app.audio_effects_cache_stale(1000, now=1000 + 23 * 3600)
      and app.audio_effects_cache_stale(1000, now=1000 + 25 * 3600))
open(path, "w").write("{сломан")
check("2d. повреждённый кэш → (None, 0)", app.load_audio_effects_cache(path) == (None, 0.0))
check("2e. кэша нет → (None, 0)", app.load_audio_effects_cache(os.path.join(d, "нет.json")) == (None, 0.0))

# ── 3. Нормализация и тело запроса ──
n = app.normalize_audio_effects({"filter": "old_radio", "environment": "нет_такого", "distance": 3, "pan": -7,
                                 "send_level": "x"}, cat)
check("3a. границы и неизвестный id: distance 1, pan −1, send_level 0.5 (умолчание), пространство сброшено",
      n["distance"] == 1.0 and n["pan"] == -1.0 and n["send_level"] == 0.5 and n["environment"] is None
      and n["filter"] == "old_radio")
check("3b. ничего не выбрано → audio_effects не отправляется",
      app.build_audio_effects_payload({}) is None and app.build_audio_effects_payload({"send_level": 0.9}) is None)
check("3c. фильтр + шум → только эти поля",
      app.build_audio_effects_payload({"filter": "old_radio", "noise": "cafe"}) ==
      {"filter_preset_id": "old_radio", "background_noise_id": "cafe"})
check("3d. пространство → environment_id и send_level 0.5 по умолчанию",
      app.build_audio_effects_payload({"environment": "hall"}) == {"environment_id": "hall", "send_level": 0.5})
check("3e. расстояние и панорама", app.build_audio_effects_payload({"distance": 0.5, "pan": 0.3}) ==
      {"distance": 0.5, "pan": 0.3})
check("3f. формат ulaw/alaw/pcm/opus → эффекты не отправляются",
      all(app.build_audio_effects_payload({"environment": "hall"}, f) is None
          for f in ("ulaw_8000", "alaw_8000", "pcm_44100", "opus_48000_128")))
check("3g. текущий формат приложения mp3 → отправляются", app.ELEVEN_OUTPUT_FORMAT.startswith("mp3_")
      and app.build_audio_effects_payload({"filter": "phone"}) == {"filter_preset_id": "phone"})

# ── 4. Режим A/B ──
fx = {"noise": "cafe"}
check("4a. по умолчанию «только B»: A без эффектов, B с эффектами", app.AUDIO_EFFECTS_TARGET_DEFAULT == "B"
      and app.audio_effects_for_slot(fx, "B", "A") is None and app.audio_effects_for_slot(fx, "B", "B") == fx)
check("4b. «к обоим» и «только A»", app.audio_effects_for_slot(fx, "both", "A") == fx
      and app.audio_effects_for_slot(fx, "A", "B") is None)
check("4c. эффект не выбран → ни одному варианту", app.audio_effects_for_slot({}, "both", "A") is None)
check("4d. сводка для карточки — имена из каталога",
      app.audio_effects_summary({"filter": "old_radio", "environment": "small_room", "noise": "cafe", "pan": -0.3},
                                cat) == "Old Radio · Small Room 50% · Café · панорама Л 0.3")

# ── 5. Запрос TTS с подставным ElevenLabs ──
FAKE_MP3 = b"ID3" + b"\0" * 8192
bodies = []
def tts(responses, spec, model=None, fmt=None):
    def post(url, headers=None, json=None, timeout=None):
        bodies.append({"url": url, "json": json})
        return responses.pop(0)
    out = os.path.join(d, "o.mp3")
    with patch.object(app.requests, "post", post), patch.object(app, "get_duration", lambda p: 1.0), \
         patch.object(app, "ELEVEN_OUTPUT_FORMAT", fmt or app.ELEVEN_OUTPUT_FORMAT):
        try:
            app.text_to_speech_mp3("Привет", "VoiceId0000000000001", out, lambda *_: None, model_id=model,
                                   audio_effects=spec)
            return None
        except Exception as e:
            return e
ok = R(200, content=FAKE_MP3)
e = tts([ok], {"filter": "old_radio", "environment": "small_room"})
b = bodies[-1]["json"]
check("5a. тело запроса: audio_effects с эффектами, voice_settings v4 без style/speed/boost",
      e is None and b["audio_effects"] == {"filter_preset_id": "old_radio", "environment_id": "small_room",
                                            "send_level": 0.5} and set(b["voice_settings"]) ==
      {"stability", "similarity_boost"})
tts([R(200, content=FAKE_MP3)], {})
check("5b. без эффектов — поля audio_effects нет", "audio_effects" not in bodies[-1]["json"])
tts([R(200, content=FAKE_MP3)], {"environment": "hall"}, fmt="ulaw_8000")
check("5c. формат ulaw_8000 → audio_effects не отправлен", "audio_effects" not in bodies[-1]["json"]
      and "ulaw_8000" in bodies[-1]["url"])
e = tts([R(404, text=json.dumps({"detail": {"status": "not_found", "param": "audio_effects.environment_id",
                                            "message": "Unknown environment"}}))], {"environment": "hall"})
check("5d. 404 на эффекте → AudioEffectsError: поле и код, без тела ответа",
      isinstance(e, app.AudioEffectsError) and str(e) == "Эффект не принят ElevenLabs: пространство (HTTP 404)"
      and e.param == "audio_effects.environment_id", str(e))
e = tts([R(400, text=json.dumps({"detail": {"message": "audio_effects: stereo not allowed for this format"}}))],
        {"pan": 0.5})
check("5e. 400 про audio_effects → понятная ошибка эффекта", isinstance(e, app.AudioEffectsError) and e.status == 400,
      str(e))
e = tts([R(400, text=json.dumps({"detail": {"message": "text too long"}}))], {"pan": 0.5})
check("5f. 400 не про эффекты → обычная ошибка TTS", type(e) is RuntimeError and "TTS error: 400" in str(e))
tts([R(200, content=FAKE_MP3)], {"noise": "cafe"}, model=app.ELEVEN_TTS_MODEL_ID_V3)
check("5g. v3: audio_effects передаётся, настройки v3 прежние",
      bodies[-1]["json"]["audio_effects"] == {"background_noise_id": "cafe"}
      and "style" in bodies[-1]["json"]["voice_settings"])

# ── 6. Отчёт об ошибке ──
err = app.AudioEffectsError(404, "audio_effects.environment_id")
payload = app.error_reporter.build_payload("app.audio_effects.tts", "AudioEffectsError", f"HTTP {err.status} {err.param}")
check("6. отчёт: место и код, текста сценария нет", payload["where"] == "app.audio_effects.tts"
      and "404" in payload["message"] and "Привет" not in repr(payload), payload["message"])

# ── 7. Паузы для «2 видео»: шум мешает разделению, ошибка понятная ──
ff = app.ffmpeg_bin()
clean, noisy = os.path.join(d, "clean.wav"), os.path.join(d, "noisy.wav")
base = ["-f", "lavfi", "-i", "sine=f=300:d=2", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=3",
        "-f", "lavfi", "-i", "sine=f=300:d=2"]
subprocess.run([ff, "-v", "error", "-y", *base, "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", clean], check=True)
subprocess.run([ff, "-v", "error", "-y", *base, "-filter_complex",
                "[0][1][2]concat=n=3:v=0:a=1[v];anoisesrc=d=7:a=0.2:c=pink[n];[v][n]amix=inputs=2:duration=first:"
                "normalize=0", noisy], check=True)
check("7a. чистая пауза находится: 1 из 1", app.split_pause_count(clean, 1) == (1, 1))
found, expected = app.split_pause_count(noisy, 1)
check("7b. шум под голосом: пауза не найдена", (found, expected) == (0, 1))
msg = app.split_pause_error(found, expected)
check("7c. текст ошибки", msg == "найдено паузы: 0 из 1; шум или реверберация мешают разделению, "
                               "отключите эффект или разделите вручную", msg)
try:
    app.split_start_end_by_silence(noisy, d, lambda *_: None, expected_separators=1)
    split_err = ""
except RuntimeError as e:
    split_err = str(e)
check("7d. разделение при обработке ролика — та же понятная ошибка", "найдено паузы: 0 из 1" in split_err, split_err)

# ── 8. Хвост реверберации: замер и прибавка Sync ──
dry, wet = os.path.join(d, "dry.wav"), os.path.join(d, "wet.wav")
subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=2", "-af", "apad=pad_dur=0.05", dry], check=True)
subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=2", "-af", "apad=pad_dur=1.5", wet], check=True)
check("8a. чистый файл: хвоста нет (< 0.2 с); с тихим концом 1.5 с — ≈ 1.5 с",
      app.audio_trailing_tail(dry) < 0.2 and abs(app.audio_trailing_tail(wet) - 1.5) < 0.15,
      f"{app.audio_trailing_tail(dry)} / {app.audio_trailing_tail(wet)}")
check("8b. прибавка: 1.5 с при 25 fps и цене части $2.135 за 500 кадров → +38 кадров, +$0.16",
      app.reverb_tail_cost(1.5, {"fps": 25.0, "cost": 2.135, "frames": 500}) == (38, 0.16))

print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
