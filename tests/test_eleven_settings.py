"""Тело запроса ElevenLabs TTS: v4 — только stability и similarity_boost; v3 — как раньше. Без сети."""
import os, sys, tempfile
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

class Captured(Exception):
    pass

def body_for(model_id):
    seen = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(json); raise Captured()        # запрос не уходит
    with patch.object(app.requests, "post", fake_post):
        try:
            app.text_to_speech_mp3("Привет", "VoiceId12345678", os.path.join(tempfile.mkdtemp(), "o.mp3"),
                                   lambda *_: None, model_id=model_id)
        except Captured:
            pass
    return seen

v4 = body_for(None)                       # по умолчанию — v4
vs = v4.get("voice_settings", {})
check("1. по умолчанию eleven_v4", v4.get("model_id") == "eleven_v4")
check("2. v4: в voice_settings только stability и similarity_boost", set(vs) == {"stability", "similarity_boost"}, str(vs))
check("3. v4: style, speed и speaker_boost не отправляются",
      not any(k in vs or k in v4 for k in ("style", "speed", "use_speaker_boost", "speaker_boost")))
v3 = body_for(app.ELEVEN_TTS_MODEL_ID_V3)["voice_settings"]
check("4. v3 — без изменений (style и speaker_boost есть)",
      v3 == {"stability": 0.55, "similarity_boost": 1.0, "style": 0.45, "use_speaker_boost": True}, str(v3))

print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
