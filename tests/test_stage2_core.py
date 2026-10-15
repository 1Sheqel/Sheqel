"""Этап 2, ядро без GUI: менеджер задач, состояние на диске, журнал Sync, папка голосов."""
import sys, os, json, time, threading, shutil, types
from unittest.mock import patch
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import requests
import app

SCR = __import__("tempfile").mkdtemp(prefix="s2core_")
shutil.rmtree(SCR, ignore_errors=True); os.makedirs(SCR)
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ── 1. Менеджер задач ───────────────────────────────────────────────────────
m = app.BackgroundTaskManager()
a = m.create("voice_gen", "voice_gen", "A", meta={"slot": "A", "batch": "b1"}, status="running")
b = m.create("voice_gen", "voice_gen", "B", meta={"slot": "B", "batch": "b1"})
c = m.create("compress", "compress", "Сжать видео", meta={"file": "x.mp4"})   # будущая вкладка
m.update(a, status="done", progress=1.0, result={"path": "/tmp/a.mp3"})
check("1a. create/update/get: статус и результат", m.get(a)["status"] == "done" and m.get(a)["result"]["path"] == "/tmp/a.mp3")
check("1b. фильтр по kind и meta, latest", m.latest("voice_gen", slot="B", batch="b1")["id"] == b
      and [t["id"] for t in m.list(kind="voice_gen")] == [a, b])
check("1c. универсальность: задача другого типа (сжатие) живёт там же", m.get(c)["kind"] == "compress"
      and [t["id"] for t in m.active(panel="compress")] == [c])
m.request_cancel(c)
check("1d. отмена по флагу (для будущего сжатия)", m.cancel_requested(c) and not m.cancel_requested(a))
check("1e. get возвращает копию — снаружи не испортить", (lambda t: (t["meta"].update(slot="Z"), m.get(b)["meta"]["slot"])[1])(m.get(b)) == "B")
check("1f. изменения приходят в очередь для главного потока", m.drain_changes() == {a, b, c} and m.drain_changes() == set())

snap = m.snapshot()
m2 = app.BackgroundTaskManager()
m2.restore(json.loads(json.dumps(snap)))
check("1g. snapshot → JSON → restore: незавершённые стали «interrupted», готовые сохранились",
      m2.get(a)["status"] == "done" and m2.get(b)["status"] == "interrupted" and m2.get(c)["status"] == "interrupted"
      and "закрыто" in m2.get(b)["message"])

m3 = app.BackgroundTaskManager()
ids = [m3.create("download", "downloader", str(i)) for i in range(8)]
errs = []
def hammer(tid):
    try:
        for k in range(500):
            m3.update(tid, progress=k / 499, message=f"{k}")
            m3.list(kind="download")
    except Exception as e:
        errs.append(e)
ths = [threading.Thread(target=hammer, args=(i,)) for i in ids]
[t.start() for t in ths]; [t.join() for t in ths]
check("1h. 8 потоков × 500 обновлений — без ошибок, итоговые значения верные",
      not errs and all(m3.get(i)["progress"] == 1.0 and m3.get(i)["message"] == "499" for i in ids))

# ── 2. Хранилище состояния ──────────────────────────────────────────────────
sp = os.path.join(SCR, "state", "state.json")
st = app.AppStateStore(sp)
data = {"rolls": [{"id": 1, "mode": "1 видео", "text": "Привет"}], "panels": {"voice_gen": {"text": "Текст"}}}
st.save(data)
check("2a. запись атомарная: папка создана, .tmp не осталось, чтение совпадает",
      st.load() == data and not os.path.exists(sp + ".tmp"))
open(sp, "w").write('{"rolls": [{"id": 1,')
st2 = app.AppStateStore(sp)
loaded = st2.load()
backups = [f for f in os.listdir(os.path.dirname(sp)) if f.startswith("state.json.corrupt-")]
check("2b. битый файл → пустое состояние, копия сохранена, ошибка описана", loaded == {} and backups and st2.load_error)
check("2c. файла нет → пустое состояние без ошибки", app.AppStateStore(os.path.join(SCR, "nope.json")).load() == {})

# ── 3. Папка голосов ────────────────────────────────────────────────────────
vd = os.path.join(SCR, "voices"); os.makedirs(vd)
paths = []
for i in range(10):
    p = os.path.join(vd, f"v{i}.mp3"); open(p, "w").write("x"); os.utime(p, (i, i)); paths.append(p)
with patch.object(app, "VOICE_DATA_DIR", vd):
    app.prune_voice_files(keep_paths=[paths[0]], limit=4)
left = sorted(os.listdir(vd))
check("3. лишние голосовые файлы удаляются (старые первыми), текущие A/B не трогаются",
      len(left) == 4 and "v0.mp3" in left and "v9.mp3" in left, str(left))

# ── 4. Журнал Sync из apply_lipsync_sync ───────────────────────────────────
class FR:
    def __init__(self, code, js=None, content=b""):
        self.status_code, self._js, self.content, self.text = code, js, content, str(js)
        self.headers = {}
    def json(self): return self._js
    def raise_for_status(self): pass

def run_sync(final_state, download_ok=True):
    events = []
    polls = {"n": 0}
    def post(url, json=None, headers=None, **kw):
        return FR(201, {"id": "job-1"})
    def get(url, **kw):
        if url.startswith(app.SYNC_GENERATE_URL + "/"):
            polls["n"] += 1
            js = {"id": "job-1", "status": final_state, "outputUrl": "https://o/v.mp4", "outputDuration": 3.0}
            if final_state == "FAILED":
                js.update(errorCode="generation_pipeline_failed", error="pipeline")
            return FR(200, js)
        return FR(200 if download_ok else 403, content=b"V" * 4096)
    v = os.path.join(SCR, "v.mp4"); open(v, "wb").write(b"x" * 2048)
    a_ = os.path.join(SCR, "a.wav"); open(a_, "wb").write(b"y" * 2048)
    with patch.object(app, "sync_job_hook", events.append), patch.object(app, "SYNC_RETRIES", 1), \
         patch.object(app, "upload_to_cloudinary", lambda p, log: ("https://cdn/x", "pub")), \
         patch.object(app, "delete_from_cloudinary", lambda *a: None), \
         patch.object(requests.Session, "post", side_effect=post), patch.object(requests.Session, "get", side_effect=get), \
         patch("time.sleep", lambda s: None):
        if not download_ok:
            FR.raise_for_status = lambda self: (_ for _ in ()).throw(requests.exceptions.HTTPError("403"))
        try:
            app.apply_lipsync_sync(v, a_, os.path.join(SCR, "out.mp4"), lambda m: None)
        except Exception:
            pass
        FR.raise_for_status = lambda self: None
    return events

ev = run_sync("COMPLETED")
check("4a. успех: created → completed (outputUrl) → saved (путь)",
      [e["event"] for e in ev] == ["created", "completed", "saved"] and ev[0]["job_id"] == "job-1"
      and ev[0]["idempotency_key"].startswith("sheqel-") and ev[1]["output_url"] == "https://o/v.mp4"
      and ev[2]["path"].endswith("out.mp4"))
ev = run_sync("FAILED")
check("4b. провал Sync: created → failed с кодом ошибки", [e["event"] for e in ev] == ["created", "failed"]
      and ev[1]["error_code"] == "generation_pipeline_failed")
ev = run_sync("COMPLETED", download_ok=False)
check("4c. COMPLETED, но скачать не удалось: в журнале outputUrl для ручного забора",
      [e["event"] for e in ev] == ["created", "completed", "download_failed"] and ev[2]["output_url"] == "https://o/v.mp4")
with patch.object(app, "sync_job_hook", lambda e: 1 / 0), patch.object(app, "LOG_FILE_PATH", os.path.join(SCR, "l.log")):
    app.journal_sync_job("created", job_id="x")
check("4d. сбой обработчика журнала не роняет очередь (только запись в лог)",
      "Журнал Sync: не удалось" in open(os.path.join(SCR, "l.log"), encoding="utf-8").read())

print("\n" + "=" * 60)
print(f"Итого: {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
