"""История партий и итог очереди после перезапуска (без Sync: обработка ролика подменена). argv: 1 | 2."""
import sys, os, json, time, shutil, threading, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import customtkinter as ctk
from tkinter import messagebox

PHASE = sys.argv[1]
SCR = os.path.join(__import__("tempfile").gettempdir(), "sheqel_tests", "queuehist")
if PHASE == "1":
    shutil.rmtree(SCR, ignore_errors=True)
    os.makedirs(os.path.join(SCR, "desktop"))
    json.dump({"sync_api_key": "-".join(("fake", "sync")), "spellcheck_online": False, "cost_confirm_enabled": False,
               "cost_confirm_threshold": 1e9, "error_reports_notice_shown": True},
              open(os.path.join(SCR, "config.json"), "w"), ensure_ascii=False)
app.CONFIG_PATH = os.path.join(SCR, "config.json")
app.STATE_PATH = os.path.join(SCR, "data", "state.json")
app.VOICE_DATA_DIR = os.path.join(SCR, "data", "voices")
app.LOG_FILE_PATH = os.path.join(SCR, "app.log")
app.FACE_POINTS_PATH = os.path.join(SCR, "faces.json")
app.APP_DATA_DIR = os.path.join(SCR, "appdata")
app.ERROR_REPORT_URL = ""
app.desktop_dir = lambda: os.path.join(SCR, "desktop")
app.fetch_elevenlabs_balance = lambda key: None
app.PAUSE_BETWEEN_ROLLS_SEC = 0.05
app.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сеть в тесте запрещена"))
threading.excepthook = app._thread_excepthook
msgs = []
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, (lambda n: lambda title, text, **k: msgs.append((n, title, text)))(name))
messagebox.askyesno = lambda *a, **k: True

w = app.LipsyncTwoModeApp()
errors = []
w.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)

FAIL_IDS = set()
def fake_process(nn, idx, roll, batch_dir, log):
    time.sleep(0.2)
    if roll["id"] in FAIL_IDS:
        raise RuntimeError("тестовая ошибка")
    out = os.path.join(batch_dir, f"{nn:02d}_{roll['video_single'].strip('/').split('.')[0]}.mp4")
    open(out, "wb").write(b"\0" * 300_000)
    return [out]
w._process_single_roll = fake_process
PART = {"kind": "single", "video_sec": 1.0, "audio_sec": 1.0, "billable": 1.0, "fps": 25.0, "source_fps": 25.0,
        "width": 1280, "height": 720, "frames": 25, "cost": 0.1, "source": "local", "time": (5, 9), "note": "",
        "video_shorter": False}
w._estimate_for = lambda roll, wait=False: {"time": (5, 9), "cost": 0.1, "frames": 25, "billable": 1.0,
                                            "parts": [dict(PART)], "source": "local"}

def new_roll(name):
    w.add_roll()
    r = w.rolls[-1]
    r.update(voice_file=f"/{name}.mp3", video_single=f"/{name}.mp4")
    return r
def idle():
    return not w.is_processing and not w._queue_costing
def walk(x):
    yield x
    for c in x.winfo_children():
        yield from walk(c)
def texts(panel):
    return [x.cget("text") for x in walk(w._panels[panel]) if isinstance(x, (ctk.CTkLabel, ctk.CTkButton))]


def phase1():
    yield 1.0
    w.rolls = []
    new_roll("a"); new_roll("b")
    w.rolls = [r for r in w.rolls if r.get("voice_file")]
    w.start_queue(); yield lambda: w.is_processing; yield idle; yield 0.3
    first = w._queue_batch_dir
    c = new_roll("c"); FAIL_IDS.add(c["id"]); new_roll("d")
    w.start_queue(); yield lambda: w.is_processing; yield idle; yield 0.3
    second = w._queue_batch_dir
    check("1. история: две партии, новая первой, готово/ошибок", [h["batch_dir"] for h in w._queue_history] ==
          [second, first] and w._queue_history[0]["ok"] == 1 and w._queue_history[0]["failed"] == 1)
    check("1b. пункт «История» в группе ЗАДАЧИ бокового меню",
          any(item[0] == "history" for g in w._nav_spec() if g[0] == "tasks" for item in g[2])
          if hasattr(w, "_nav_spec") else "history" in w._nav_buttons)
    w.show_history_panel(); yield 0.6
    t = texts("history")
    check("2. панель «История»: даты, «готово 1 · ошибок 1», размер, «Открыть папку», «Скопировать путь»",
          any("готово 1 · ошибок 1" in x for x in t) and t.count("Открыть папку") == 2
          and t.count("Скопировать путь") == 2 and any("КБ" in x or "МБ" in x for x in t), str(t[:8]))
    next(x for x in walk(w._panels["history"]) if isinstance(x, ctk.CTkButton)
         and x.cget("text") == "Скопировать путь").invoke()
    check("3. «Скопировать путь» → путь в буфере", w.clipboard_get() == second)
    shutil.rmtree(first)
    w.show_settings_panel(); yield 0.2
    w.show_history_panel(); yield 0.4
    t = texts("history")
    check("4. папки нет → «папка не найдена» и «Убрать из списка»", "папка не найдена" in t and "Убрать из списка" in t)
    next(x for x in walk(w._panels["history"]) if isinstance(x, ctk.CTkButton)
         and x.cget("text") == "Убрать из списка").invoke(); yield 0.3
    check("5. «Убрать из списка» → в истории одна партия", [h["batch_dir"] for h in w._queue_history] == [second])
    check("6. итог последней очереди есть", w._queue_summary and w._queue_summary["batch_dir"] == second)
    check("7. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
    w._on_close()


def phase2():
    yield 1.2
    s = w._queue_summary or {}
    check("R1. после перезапуска итог очереди на месте и показан в панели хода",
          s.get("total") == 2 and len(s.get("failed", [])) == 1 and bool(w._qp.winfo_manager())
          and w._qp_w["title"].cget("text").startswith("Готово 1 из 2"), w._qp_w["title"].cget("text"))
    check("R2. история пережила перезапуск", len(w._queue_history) == 1)
    w.show_history_panel(); yield 0.6
    check("R3. панель «История» после перезапуска", "Открыть папку" in texts("history"))
    w.clear_saved_data(); yield 0.3
    check("R4. «Очистить сохранённые данные» очищает историю и итог", not w._queue_history and w._queue_summary is None)
    check("R5. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
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
    if callable(r):
        start = time.time()
        def poll():
            ok = False
            try: ok = r()
            except Exception: pass
            if ok or time.time() - start > 30: tick()
            else: w.after(50, poll)
        w.after(50, poll)
    else:
        w.after(int(r * 1000), tick)
w.after(300, tick)
try:
    w.mainloop()
except Exception:
    traceback.print_exc()
print(f"Итого phase {PHASE}: {sum(results)}/{len(results)}", flush=True)
os._exit(0 if results and all(results) else 1)
