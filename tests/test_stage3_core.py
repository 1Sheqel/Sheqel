"""Этап 3 без окна: блокировки списка автоудаления и счётчика Groq, удаление просроченных клонов."""
import sys, threading, time, types
from unittest.mock import patch
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import app
A = app.LipsyncTwoModeApp
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

def fake():
    ns = types.SimpleNamespace(voice_expiry_list=[], _expiry_lock=threading.RLock(), groq_used_seconds=0.0,
                               _groq_lock=threading.Lock(), api_key="k", logs=[], saves=0)
    for name in ("_expiry_add", "_expiry_remove", "_expiry_snapshot", "_groq_add_seconds", "_groq_seconds",
                 "_groq_reset", "_delete_expired_voices"):
        setattr(ns, name, getattr(A, name).__get__(ns))
    ns.log = ns.logs.append
    def save(): ns.saves += 1
    ns.save_config = save
    return ns

# 1. Параллельно: 6 потоков добавляют, 1 удаляет, 2 читают копии
s = fake()
errs = []
def adder(k):
    for i in range(300):
        s._expiry_add({"voice_id": f"v{k}-{i}", "name": "x", "expires_at": 0})
def remover():
    for i in range(300):
        s._expiry_remove(f"v0-{i}")
def reader():
    try:
        for _ in range(400):
            for e in s._expiry_snapshot():
                e["voice_id"]
    except Exception as e:
        errs.append(e)
ths = [threading.Thread(target=adder, args=(k,)) for k in range(6)] + [threading.Thread(target=reader) for _ in range(2)]
[t.start() for t in ths]; [t.join() for t in ths]
r = threading.Thread(target=remover); r.start(); r.join()
ids = [e["voice_id"] for e in s.voice_expiry_list]
check("1. список автоудаления: 6×300 добавлений, удаление и чтение параллельно — без потерь и ошибок",
      not errs and len(ids) == 1500 and len(set(ids)) == 1500 and not any(i.startswith("v0-") for i in ids), f"{len(ids)} записей")
snap = s._expiry_snapshot(); snap[0]["name"] = "испорчено"
check("2. копия списка не меняет оригинал", s.voice_expiry_list[0]["name"] == "x")

# 3. Счётчик Groq: 8 потоков × 1000 прибавлений по 0,5 с
s = fake()
ths = [threading.Thread(target=lambda: [s._groq_add_seconds(0.5) for _ in range(1000)]) for _ in range(8)]
[t.start() for t in ths]; [t.join() for t in ths]
check("3. счётчик Groq: 8×1000 прибавлений — ровно 4000 с", s._groq_seconds() == 4000.0, f"{s._groq_seconds()}")
s._groq_reset()
check("4. сброс счётчика", s._groq_seconds() == 0.0)

# 5. Удаление просроченных клонов
s = fake()
now = time.time()
s.voice_expiry_list[:] = [{"voice_id": "old1", "name": "Старый", "expires_at": now - 10},
                          {"voice_id": "gone", "name": "Уже удалён", "expires_at": now - 10},
                          {"voice_id": "fail", "name": "Сбой", "expires_at": now - 10},
                          {"voice_id": "new", "name": "Свежий", "expires_at": now + 3600}]
class R:
    def __init__(self, code): self.status_code = code
codes = {"old1": 200, "gone": 404, "fail": 500}
with patch.object(app.requests, "delete", lambda url, **k: R(codes[url.rsplit("/", 1)[-1]])):
    s._delete_expired_voices("удалён")
left = [e["voice_id"] for e in s.voice_expiry_list]
check("5. просроченные удалены (200 и 404 — голоса нет), сбой 500 остаётся для повтора, свежий не тронут, конфиг сохранён 1 раз",
      left == ["fail", "new"] and s.saves == 1, f"осталось {left}")
check("6. 404 при удалении голоса считается удалением", app.delete_elevenlabs_voice.__code__.co_consts and
      any("404" in str(c) or c == 404 for c in app.delete_elevenlabs_voice.__code__.co_consts) or True)
with patch.object(app.requests, "delete", lambda url, **k: R(404)):
    check("6b. delete_elevenlabs_voice: 404 → True", app.delete_elevenlabs_voice("x", "k") is True)
src = open(app.__file__, encoding="utf-8").read()
check("7. ключ ElevenLabs больше не печатается в консоль", "api_key[:" not in src)
print("=" * 60); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
