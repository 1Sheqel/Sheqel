"""«Клонировать голос»: файл выбирается первым, «Выбрать файл» и «Клонировать» видны без прокрутки на 900×640,
повторное извлечение звука после перезапуска, если временный файл пропал. Без сети. argv: 1 | 2 | 3."""
import sys, os, json, time, shutil, threading, traceback, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import customtkinter as ctk
from tkinter import messagebox, filedialog

PHASE = sys.argv[1]
SCR = os.path.join(__import__("tempfile").gettempdir(), "sheqel_tests", "clonelayout")
VIDEO = os.path.join(SCR, "Интервью диктора — длинное имя файла для проверки сокращения пути.mp4")
if PHASE == "1":
    shutil.rmtree(SCR, ignore_errors=True)
    os.makedirs(SCR)
    json.dump({"elevenlabs_api_key": "-".join(("fake", "eleven", "key")), "spellcheck_online": False,
               "error_reports_notice_shown": True}, open(os.path.join(SCR, "config.json"), "w"), ensure_ascii=False)
    subprocess.run([app.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:duration=3",
                    "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", VIDEO], check=True)
app.CONFIG_PATH = os.path.join(SCR, "config.json")
app.STATE_PATH = os.path.join(SCR, "data", "state.json")
app.VOICE_DATA_DIR = os.path.join(SCR, "data", "voices")
app.LOG_FILE_PATH = os.path.join(SCR, "app.log")
app.APP_DATA_DIR = os.path.join(SCR, "appdata")
app.ERROR_REPORT_URL = ""
app.fetch_elevenlabs_balance = lambda key: None
app.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сеть в тесте запрещена"))
threading.excepthook = app._thread_excepthook
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, lambda *a, **k: None)
filedialog.askopenfilename = lambda **k: VIDEO

w = app.LipsyncTwoModeApp()
w.geometry("900x640+20+20")
errors = []
w.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
def walk(x):
    yield x
    for c in x.winfo_children():
        yield from walk(c)
def panel():
    return w._panels["voice_clone"]
def buttons(text):
    return [b for b in walk(panel()) if isinstance(b, ctk.CTkButton) and b.cget("text") == text and b.winfo_viewable()]
def labels():
    return [x.cget("text") for x in walk(panel()) if isinstance(x, ctk.CTkLabel) and x.winfo_viewable()]
def in_view(widget):
    y0, y1 = widget.winfo_rooty(), widget.winfo_rooty() + widget.winfo_height()
    ok = w.winfo_rooty() <= y0 and y1 <= w.winfo_rooty() + w.winfo_height()
    canvas = panel()._pin["scroll"]._parent_canvas
    if str(widget).startswith(str(panel()._pin["scroll"])):
        ok = ok and canvas.winfo_rooty() <= y0 and y1 <= canvas.winfo_rooty() + canvas.winfo_height()
    return ok
def extract_done():
    t = w.background_tasks.latest("clone_extract")
    return t and t["status"] == "done" and os.path.exists((t.get("result") or {}).get("path") or "")


def phase1():
    yield 1.0
    w.show_voice_clone_panel(); w.update(); yield 0.4
    order = [x.cget("text") for x in walk(panel()) if isinstance(x, ctk.CTkLabel) and x.cget("text") in
             ("Аудио-сэмпл", "Имя голоса", "Описание (опционально)")]
    check("1. блок файла первым: «Аудио-сэмпл» выше «Имя голоса» и «Описание»",
          order[:3] == ["Аудио-сэмпл", "Имя голоса", "Описание (опционально)"], str(order))
    check("1b. подсказка обновлена", any(x.startswith("Достаточно 10–30 секунд чистой речи без шума и музыки; для "
                                                       "лучшего результата 1–3 минуты.") for x in labels()))
    check("2. 900×640, аудио: «Выбрать файл» и «Клонировать» видны без прокрутки",
          buttons("Выбрать файл") and in_view(buttons("Выбрать файл")[0]) and in_view(buttons("🎙 Клонировать")[0]))
    buttons("🎬 Видео файл")[0].invoke(); yield 0.3
    check("2b. 900×640, видео: «Выбрать файл» и «Клонировать» видны без прокрутки",
          in_view(buttons("Выбрать файл")[0]) and in_view(buttons("🎙 Клонировать")[0]))
    buttons("Выбрать файл")[0].invoke(); yield extract_done; yield 0.4
    field = next(x for x in walk(panel()) if isinstance(x, ctk.CTkLabel) and "…" in x.cget("text"))
    check("3. длинное имя видео сокращено «…», кнопка на месте", "…" in field.cget("text")
          and field.winfo_width() + buttons("Выбрать файл")[0].winfo_width() <= panel().winfo_width(), field.cget("text"))
    check("3b. звук извлечён", any(x.startswith("✓ Аудио извлечено") for x in labels()))
    t = w.background_tasks.latest("clone_extract")
    shutil.rmtree(os.path.dirname(t["result"]["path"]), ignore_errors=True)    # временный файл «пропал»
    check("4. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
    w._on_close()


def phase2():
    yield 1.0
    before = len(w.background_tasks.list(kind="clone_extract"))
    w.show_voice_clone_panel(); w.update(); yield 0.15
    yield extract_done; yield 0.3
    log = open(app.LOG_FILE_PATH, encoding="utf-8").read()
    check("R1. после перезапуска: видео-режим восстановлен, извлечённого нет → звук извлекается заново сам",
          "извлекаю звук из видео заново" in log and not any("выбери видео заново" in x for x in labels()))
    check("R2. звук извлечён заново в фоне", any(x.startswith("✓ Аудио извлечено") for x in labels())
          and len(w.background_tasks.list(kind="clone_extract")) == before + 1)
    t = w.background_tasks.latest("clone_extract")
    shutil.rmtree(os.path.dirname(t["result"]["path"]), ignore_errors=True)
    os.remove(VIDEO)                                                         # для фазы 3: видео тоже нет
    check("R3. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
    w._on_close()


def phase3():
    yield 1.0
    w.show_voice_clone_panel(); w.update(); yield 0.4
    t = labels()
    check("S1. видео нет → «Видео не найдено — выбери файл заново», без попытки извлечь",
          any(x == "Видео не найдено — выбери файл заново" for x in t)
          and not any(x.startswith("Извлекаю звук") for x in t), str(t[-4:]))
    check("S2. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
    w._on_close()


gen = {"1": phase1, "2": phase2, "3": phase3}[PHASE]()
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
