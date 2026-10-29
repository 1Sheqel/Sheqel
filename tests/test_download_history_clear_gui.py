"""«История загрузок» очищается при закрытии программы и не восстанавливается из state.json.
Файлы на диске остаются, остальные задачи и История партий не трогаются. Без сети и оплаты. argv: 1 | 2."""
import sys, os, json, time, shutil, threading, traceback, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import customtkinter as ctk
from tkinter import messagebox

PHASE = sys.argv[1]
SCR = os.path.join(tempfile.gettempdir(), "sheqel_tests", "dl_history_clear")
STATE = os.path.join(SCR, "data", "state.json")
DL_FILE = os.path.join(SCR, "downloads", "ролик.mp4")
BATCH = os.path.join(SCR, "desktop", "партия 1")


def task(tid, kind, status, panel, **extra):
    t = {"id": tid, "kind": kind, "panel": panel, "title": f"{kind} {tid}", "status": status, "progress": None,
         "message": "", "result": None, "error": None, "meta": {}, "created": time.time(), "updated": time.time()}
    t.update(extra)
    return t


if PHASE == "1":
    shutil.rmtree(SCR, ignore_errors=True)
    os.makedirs(os.path.join(SCR, "desktop"))
    os.makedirs(os.path.dirname(STATE))
    os.makedirs(os.path.dirname(DL_FILE))
    os.makedirs(BATCH)
    open(DL_FILE, "wb").write(b"\0" * 1000)
    json.dump({"sync_api_key": "-".join(("fake", "sync")), "spellcheck_online": False, "cost_confirm_enabled": False,
               "cost_confirm_threshold": 1e9, "error_reports_notice_shown": True},
              open(os.path.join(SCR, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)
    # Старый state.json (v1.5.2 и раньше) — с записями загрузок всех статусов.
    old_state = {
        "version": 1,
        "panels": {"downloader": {"batch": "b-old"}},
        "queue_history": [{"batch_dir": BATCH, "time": time.time(), "done": 1, "failed": 0, "total": 1}],
        "tasks": [
            task("dl_done", "download", "done", "downloader", meta={"url": "https://x/1", "batch": "b-old"},
                 result={"name": "ролик", "files": [DL_FILE]}),
            task("dl_err", "download", "error", "downloader", error="сбой", meta={"batch": "b-old"}),
            task("dl_run", "download", "running", "downloader", meta={"batch": "b-old"}),
            task("dl_cnc", "download", "cancelled", "downloader"),
            task("dl_int", "download", "interrupted", "downloader"),
            task("cmp_done", "compress", "done", "compress"),
            task("vg_done", "voice_gen", "done", "voice_gen"),
        ],
    }
    json.dump(old_state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

app.CONFIG_PATH = os.path.join(SCR, "config.json")
app.STATE_PATH = STATE
app.VOICE_DATA_DIR = os.path.join(SCR, "data", "voices")
app.LOG_FILE_PATH = os.path.join(SCR, "app.log")
app.FACE_POINTS_PATH = os.path.join(SCR, "faces.json")
app.APP_DATA_DIR = os.path.join(SCR, "appdata")
app.ERROR_REPORT_URL = ""
app.desktop_dir = lambda: os.path.join(SCR, "desktop")
app.fetch_elevenlabs_balance = lambda key: None
app.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сеть в тесте запрещена"))
threading.excepthook = app._thread_excepthook
msgs = []
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, (lambda n: lambda title, text, **k: msgs.append((n, title, text)))(name))
messagebox.askyesno = lambda *a, **k: True

results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)

# ── без окна: менеджер задач ──
if PHASE == "1":
    m = app.BackgroundTaskManager()
    m.restore([task("a", "download", "done", "downloader"), task("b", "compress", "running", "compress")])
    check("M1. restore не берёт загрузки из состояния", [t["kind"] for t in m.list()] == ["compress"])
    m.create("download", "downloader", title="u")
    snap = m.snapshot()
    check("M2. snapshot не сохраняет загрузки", [t["kind"] for t in snap] == ["compress"], str(snap))
    check("M3. в памяти загрузка остаётся до закрытия", len(m.list(kind="download")) == 1)

w = app.LipsyncTwoModeApp()
errors = []
w.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
def walk(x):
    yield x
    for c in x.winfo_children():
        yield from walk(c)
def texts(panel):
    return [x.cget("text") for x in walk(w._panels[panel]) if isinstance(x, (ctk.CTkLabel, ctk.CTkButton))]
def saved():
    return json.load(open(STATE, encoding="utf-8"))


def phase1():
    yield 1.0
    tasks = w.background_tasks
    check("1. старый state.json с загрузками прочитан: загрузок нет", tasks.list(kind="download") == [])
    check("2. другие задачи восстановлены", {t["id"] for t in tasks.list()} == {"cmp_done", "vg_done"},
          str([t["id"] for t in tasks.list()]))
    check("3. История партий на месте", [h["batch_dir"] for h in w._queue_history] == [BATCH])
    w.show_downloader_panel(); yield 0.5
    t = texts("downloader")
    check("4. панель «Скачать»: старых записей нет", not any("ролик" in str(x) or "Прервано" in str(x) for x in t))
    check("5. подсказка про очистку при закрытии", any("Очищается при закрытии программы" in str(x) for x in t))
    # загрузки текущей сессии: готовая и идущая (закрытие во время загрузки)
    d1 = tasks.create("download", "downloader", title="https://x/2", meta={"batch": "b-new"}, status="running")
    tasks.update(d1, status="done", result={"name": "ролик", "files": [DL_FILE]})
    tasks.create("download", "downloader", title="https://x/3", meta={"batch": "b-new"}, status="running")
    yield 1.2     # плановое сохранение (STATE_SAVE_DELAY_MS) во время сессии
    check("6. в сессии загрузки видны в списке", len(tasks.list(kind="download")) == 2)
    check("7. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
    w._on_close()
    st = saved()
    kinds = [t["kind"] for t in st.get("tasks", [])]
    check("8. после закрытия в state.json нет загрузок", "download" not in kinds, str(kinds))
    check("9. остальные задачи сохранены", sorted(kinds) == ["compress", "voice_gen"], str(kinds))
    check("10. queue_history сохранена", [h.get("batch_dir") for h in st.get("queue_history", [])] == [BATCH])
    check("11. скачанный файл не удалён", os.path.isfile(DL_FILE))


def phase2():
    yield 1.0
    tasks = w.background_tasks
    check("R1. после перезапуска загрузок нет", tasks.list(kind="download") == [])
    check("R2. другие задачи на месте", {t["kind"] for t in tasks.list()} == {"compress", "voice_gen"})
    check("R3. История партий на месте", len(w._queue_history) == 1)
    w.show_downloader_panel(); yield 0.5
    check("R4. кнопка «Скачать» доступна", any(x == "⬇ Скачать" for x in texts("downloader")))
    check("R5. файл на месте", os.path.isfile(DL_FILE))
    check("R6. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
    w._on_close()


gen = phase1() if PHASE == "1" else phase2()
def tick():
    try:
        r = next(gen)
    except StopIteration:
        return
    except Exception:
        traceback.print_exc(); results.append(False)
        try: w.destroy()
        except Exception: pass
        return
    w.after(int(r * 1000), tick)
w.after(300, tick)
try:
    w.mainloop()
except Exception:
    traceback.print_exc()
print(f"Итого phase {PHASE}: {sum(results)}/{len(results)}", flush=True)
os._exit(0 if results and all(results) else 1)
