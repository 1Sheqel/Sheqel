"""Подстановка клонированного голоса в генератор (_apply_cloned_voice) без окон и без сети.

Голос подставляется первым; текст очищается только после успеха; ошибка внутри не роняет
программу и не меняет текст. Ответ клонирования — объект SDK или словарь REST API.
Отчёт об AttributeError сохраняет имена классов и атрибутов.
"""
import os, sys, types
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""   # без отправки отчётов
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

VID = "AbCdEfGh12345678"
TEXT = "Привет, это текст для генерации."

class FakeBox:
    def __init__(self, text="", fail_on=()):
        self.text, self.fail_on = text, set(fail_on)
    def winfo_exists(self): return True
    def get(self, a, b): return self.text + "\n"
    def delete(self, a, b):
        if "delete" in self.fail_on: raise RuntimeError("delete сломан")
        self.text = ""
    def insert(self, a, s):
        if "insert" in self.fail_on: raise AttributeError("'CTkTextbox' object has no attribute '_textbox'")
        self.text = s + self.text

def fake_self(vg=None, text=TEXT, clear=True):
    s = types.SimpleNamespace(_panel_state={"voice_gen": {"voice": "Старый", "voice_id": "OldVoice12345678", "text": text}},
                              main_voice_name="Старый", main_voice_id="OldVoice12345678",
                              clear_text_after_clone=clear, logs=[], saved=0)
    s.log = s.logs.append
    s._schedule_state_save = lambda: None
    s._panel_state_set = lambda panel, **kw: None
    s.save_config = lambda: None
    if vg is not None:
        s._vg = vg
    return s

def make_vg(voice_fail=(), text_fail=()):
    return {"voice_box": FakeBox("Старый", voice_fail), "text_box": FakeBox(TEXT, text_fail),
            "update_status": lambda: None, "render_restore_notice": lambda: None}

def apply(s, name, resp):
    reports, warns = [], []
    with patch.object(app.error_reporter, "report", lambda where, exc=None, **kw: reports.append((where, exc))), \
         patch.object(app.messagebox, "showwarning", lambda *a, **kw: warns.append(a)):
        ok = app.LipsyncTwoModeApp._apply_cloned_voice(s, name, resp)
    return ok, reports, warns

sdk = types.SimpleNamespace(voice_id=VID, requires_verification=False)   # как AddVoiceResponseModel
rest = {"voice_id": VID}

# 1. исходный сбой: вкладки генератора ещё нет (_vg не создан) — объект SDK
s = fake_self()
ok, rep, w = apply(s, "Клон", sdk)
st = s._panel_state["voice_gen"]
check("1. без вкладки генератора, объект SDK: голос подставлен, текст очищен с резервом",
      ok and st["voice_id"] == VID and s.main_voice_id == VID and st["text"] == "" and st["text_backup"] == TEXT and not rep)

# 2. словарь REST API, вкладка открыта
vg = make_vg(); s = fake_self(vg)
ok, rep, w = apply(s, "Клон", rest)
check("2. словарь REST, вкладка открыта: голос в поле, текст очищен",
      ok and vg["voice_box"].text == "Клон" and vg["text_box"].text == "" and s._panel_state["voice_gen"]["text_backup"] == TEXT)

# 3. поле голоса падает (AttributeError) → голос как был, текст не тронут, отчёт, без падения
vg = make_vg(voice_fail=("insert",)); s = fake_self(vg)
ok, rep, w = apply(s, "Клон", sdk)
st = s._panel_state["voice_gen"]
check("3. ошибка при подстановке голоса: False, голос прежний, текст не тронут, предупреждение и отчёт",
      not ok and st["voice_id"] == "OldVoice12345678" and s.main_voice_id == "OldVoice12345678"
      and vg["text_box"].text == TEXT and st["text"] == TEXT and "text_backup" not in st and w
      and rep and isinstance(rep[0][1], AttributeError))

# 4. ответ без voice_id → голос и текст не тронуты
vg = make_vg(); s = fake_self(vg)
ok, rep, w = apply(s, "Клон", {"name": "Клон"})
check("4. ответ без voice_id: False, текст не тронут", not ok and vg["text_box"].text == TEXT
      and s._panel_state["voice_gen"]["voice_id"] == "OldVoice12345678")

# 5. голос подставлен, но очистка текста падает → голос новый, текст остаётся
vg = make_vg(text_fail=("delete",)); s = fake_self(vg)
ok, rep, w = apply(s, "Клон", sdk)
st = s._panel_state["voice_gen"]
check("5. сбой очистки текста: голос подставлен, текст остался, резерва нет",
      ok and st["voice_id"] == VID and vg["text_box"].text == TEXT and st["text"] == TEXT and "text_backup" not in st)

# 6. «очищать текст» выключено → текст не трогаем
vg = make_vg(); s = fake_self(vg, clear=False)
ok, rep, w = apply(s, "Клон", rest)
check("6. очистка выключена: голос подставлен, текст на месте", ok and vg["text_box"].text == TEXT)

# 7. отчёт об AttributeError сохраняет имена классов и атрибутов
msg = app.report_safe_message("'_tkinter.tkapp' object has no attribute '_vg'", where="app._apply_cloned_voice",
                              exc_type="AttributeError")
other = app.report_safe_message("401 Unauthorized: 'my secret text'", exc_type="RuntimeError")
check("7. санитайзер: AttributeError с именами, прочий текст в кавычках скрыт",
      msg == "'_tkinter.tkapp' object has no attribute '_vg'" and "secret" not in other, f"{msg} | {other}")

print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
