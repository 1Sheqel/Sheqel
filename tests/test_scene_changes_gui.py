"""Готовая сцена после правки снова «Ожидает», прошлый результат (путь файла) остаётся. Без Sync. argv: 1 | 2."""
import sys, os, json, time, shutil, threading, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import customtkinter as ctk
from tkinter import messagebox, filedialog

PHASE = sys.argv[1]
SCR = os.path.join(__import__("tempfile").gettempdir(), "sheqel_tests", "scenechg")
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
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, lambda *a, **k: None)
messagebox.askyesno = lambda *a, **k: True

w = app.LipsyncTwoModeApp()
errors = []
w.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)

def fake_process(nn, idx, roll, batch_dir, log):
    time.sleep(0.1)
    out = os.path.join(batch_dir, f"{nn:02d}_{roll['video_single'].strip('/').split('.')[0]}.mp4")
    open(out, "wb").write(b"\0" * 64)
    return [out]
w._process_single_roll = fake_process
PART = {"kind": "single", "video_sec": 1.0, "audio_sec": 1.0, "billable": 1.0, "fps": 25.0, "source_fps": 25.0,
        "width": 1280, "height": 720, "frames": 25, "cost": 0.1, "source": "local", "time": (5, 9), "note": "",
        "video_shorter": False}
w._estimate_for = lambda roll, wait=False: {"time": (5, 9), "cost": 0.1, "frames": 25, "billable": 1.0,
                                            "parts": [dict(PART)], "source": "local"}
def idle():
    return not w.is_processing and not w._queue_costing
def walk(x):
    yield x
    for c in x.winfo_children():
        yield from walk(c)
def card_texts():
    return [x.cget("text") for x in walk(w._containers["scene"] if hasattr(w, "_containers") else w.detail_panel)
            if isinstance(x, (ctk.CTkLabel, ctk.CTkButton))]


def run_done(r):
    r["status"] = "Готово"


def phase1():
    yield 1.0
    w.rolls = []
    w.add_roll()
    r = w.rolls[0]
    r.update(voice_file="/a.mp3", video_single="/a.mp4")
    w.start_queue(); yield lambda: w.is_processing; yield idle; yield 0.4
    last = r.get("last_result") or {}
    check("1. после очереди: «Готово» и путь результата у сцены", r["status"] == "Готово"
          and last.get("files") and last["files"][0].endswith("01_a.mp4") and os.path.exists(last["files"][0]))
    idx = 0
    changes = {
        "видео": lambda: (setattr(filedialog, "askopenfilename", lambda **k: "/a2.mp4"), w.choose_video(idx, "single")),
        "голос": lambda: (setattr(filedialog, "askopenfilename", lambda **k: "/a2.mp3"), w.choose_voice_file(idx)),
        "режим": lambda: w.set_roll_mode(idx, "Начало + Конец"),
        "лицо": lambda: w.set_roll_face_mode(idx, next(iter(app.FACE_MODES))),
    }
    ok = {}
    for name, act in changes.items():
        r["status"] = "Готово"
        act()
        yield 0.1
        ok[name] = r["status"] == "Ожидает"
    check("2. «Готово» → «Ожидает» при смене видео, голоса, режима, лица", all(ok.values()), str(ok))
    w.set_roll_mode(idx, "1 видео"); yield 0.1
    r["status"] = "Готово"; w.render_scene_detail(); yield 0.2
    entries = [x for x in walk(w.detail_panel) if isinstance(x, ctk.CTkEntry)
               and x.cget("placeholder_text") and "сек" in str(x.cget("placeholder_text"))]
    if not entries:
        entries = [x for x in walk(w.detail_panel) if isinstance(x, ctk.CTkEntry)][:2]
    e = entries[0]
    e._entry.focus_force(); w.update(); e.insert(0, "1.5"); e._entry.event_generate("<KeyRelease>", keysym="5"); yield 0.2
    check("3. обрезка: ввод начала → «Ожидает»", r["status"] == "Ожидает" and r.get("audio_start") == "1.5",
          f"{r['status']} {r.get('audio_start')!r}")
    r["status"] = "Ошибка"
    w.choose_video(idx, "single"); yield 0.1
    check("4. «Ошибка» → «Ожидает» при правке", r["status"] == "Ожидает")
    w.render_scene_detail(); yield 0.2
    t = [x.cget("text") for x in walk(w.detail_panel) if isinstance(x, (ctk.CTkLabel, ctk.CTkButton))]
    check("5. карточка: «Прошлый результат (…): 01_a.mp4» и «Показать в папке», путь не потерян",
          any(x.startswith("Прошлый результат") and "01_a.mp4" in x for x in t) and "Показать в папке" in t
          and r["last_result"]["files"][0].endswith("01_a.mp4"))
    check("6. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
    w._on_close()


def phase2():
    yield 1.0
    r = w.rolls[0]
    check("R1. после перезапуска путь прошлого результата у сцены сохранён",
          (r.get("last_result") or {}).get("files", [""])[0].endswith("01_a.mp4") and r["status"] == "Ожидает")
    check("R2. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
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
