"""Умолчания в «Настройках»: хранилище без явного выбора (Cloudinary, если настроен, иначе Sync), порог 20 $. Четыре комбинации конфига
и сохранение после перезапуска. argv: режим (A|B|C|D|persist1|persist2)."""
import sys, os, json, time, shutil, threading, traceback
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import app
import customtkinter as ctk
from tkinter import messagebox
from PIL import ImageGrab

MODE = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))
SCR = os.path.join(os.path.join(__import__("tempfile").gettempdir(), "sheqel_tests"), "defgui", "persist" if MODE.startswith("persist") else MODE)
COMBOS = {  # Cloudinary настроен?, явный выбор
    "A": (False, None), "B": (True, None), "C": (True, "sync"), "D": (False, "cloudinary"),
    "persist1": (True, None),
}
if MODE != "persist2":
    shutil.rmtree(SCR, ignore_errors=True); os.makedirs(SCR)
    cfg = json.load(open(app.CONFIG_PATH))
    for k in list(cfg):
        if any(x in k for x in ("key", "secret", "cloud_name")):
            cfg[k] = ""
    cfg.pop("cost_confirm_threshold", None)          # значения в конфиге нет
    has_cl, choice = COMBOS[MODE]
    cfg["sync_storage"] = choice
    if has_cl:
        cfg.update(cloudinary_cloud_name="demo-cloud", cloudinary_api_key="123456789012345",
                   cloudinary_api_secret="fake-secret-for-test")
    json.dump(cfg, open(os.path.join(SCR, "config.json"), "w"), ensure_ascii=False, indent=2)
app.CONFIG_PATH = os.path.join(SCR, "config.json")
app.STATE_PATH = os.path.join(SCR, "data", "state.json")
app.VOICE_DATA_DIR = os.path.join(SCR, "data", "voices")
app.LOG_FILE_PATH = os.path.join(SCR, "app.log")
app.FACE_POINTS_PATH = os.path.join(SCR, "faces.json")
app.fetch_elevenlabs_balance = lambda key: None
threading.excepthook = app._thread_excepthook
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, lambda *a, **k: None)
messagebox.askyesno = lambda *a, **k: True

w = app.LipsyncTwoModeApp()
errors = []
w.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {MODE}: {name}" + (f" — {detail}" if detail else ""), flush=True)


def walk(x):
    yield x
    for c in x.winfo_children():
        yield from walk(c)


def view():
    sp = w._panels["settings"]
    storage = next(m for m in walk(sp) if isinstance(m, ctk.CTkOptionMenu)
                   and set(m.cget("values")) == set(app.SYNC_STORAGE_MODES))
    entry = next(x for x in walk(sp) if isinstance(x, ctk.CTkEntry))
    texts = [x.cget("text") for x in walk(sp) if isinstance(x, ctk.CTkLabel)]
    hint = next((t for t in texts if t.startswith("По умолчанию сейчас:")), "")
    thr_hint = next((t for t in texts if t.startswith("По умолчанию: 20")), "")
    return storage, entry, hint, thr_hint


def run():
    yield 1.2
    w.geometry("1100x700+40+40")
    w.show_settings_panel(); yield 0.6
    storage, entry, hint, thr_hint = view()
    if MODE in ("A", "B", "C", "D"):
        has_cl, choice = COMBOS[MODE]
        exp_label = app.SYNC_STORAGE_LABELS[choice or ("cloudinary" if has_cl else "sync")]
        exp_default = app.SYNC_STORAGE_LABELS["cloudinary" if has_cl else "sync"]
        exp_hint = "Выбрано вами." if choice else "Сейчас действует умолчание."
        check(f"Cloudinary {'есть' if has_cl else 'нет'}, явный выбор {choice or 'нет'} → «{exp_label}», подсказка «{exp_hint}»",
              storage.get() == exp_label and hint.endswith(exp_hint)
              and hint.startswith(f"По умолчанию сейчас: {exp_default}"), f"меню «{storage.get()}», подсказка «{hint}»")
        check("порог без значения в конфиге → 20, подсказка «По умолчанию: 20 $»",
              entry.get() == "20" and w.cost_confirm_threshold == 20.0 and thr_hint == "По умолчанию: 20 $", entry.get())
        if MODE == "B":
            canvas = w._panels["settings"]._parent_canvas
            canvas.yview_moveto(0.36)
            w.attributes("-topmost", True); w.lift()
            for _ in range(25):
                w.update(); time.sleep(0.1)
            x, y = w.winfo_rootx(), w.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + w.winfo_width(), y + w.winfo_height())).save(
                os.path.join(__import__("tempfile").gettempdir(), "settings_defaults.png"))
    elif MODE == "persist1":
        check("до выбора: Cloudinary по умолчанию при настроенном Cloudinary", storage.get() == app.SYNC_STORAGE_LABELS["cloudinary"]
              and hint.endswith("Сейчас действует умолчание."))
        storage._command(app.SYNC_STORAGE_LABELS["sync"])
        entry.delete(0, "end"); entry.insert(0, "12"); entry._entry.focus_force(); w.update()
        entry._entry.event_generate("<Return>", when="tail"); yield 0.4
        storage, entry, hint, thr_hint = view()
        check("явный выбор Sync и порог 12: подсказка сразу «Выбрано вами.»",
              hint.endswith("Выбрано вами.") and w.cost_confirm_threshold == 12.0)
    else:
        cfg = json.load(open(app.CONFIG_PATH))
        check("после перезапуска: явный Sync (при настроенном Cloudinary) и порог 12 сохранились",
              storage.get() == app.SYNC_STORAGE_LABELS["sync"] and hint.endswith("Выбрано вами.")
              and entry.get() == "12" and cfg.get("sync_storage") == "sync" and cfg.get("cost_confirm_threshold") == 12.0)
    check("исключений нет", not errors, (errors[:1] or [""])[0][-300:])
    w._on_close()


gen = run()


def tick():
    try:
        r = next(gen)
    except StopIteration:
        return
    except Exception:
        traceback.print_exc(); results.append(False)
        try:
            w.destroy()
        except Exception:
            pass
        return
    w.after(int(r * 1000), tick)


w.after(300, tick)
try:
    w.mainloop()
except Exception:
    traceback.print_exc()
os._exit(0 if results and all(results) else 1)
