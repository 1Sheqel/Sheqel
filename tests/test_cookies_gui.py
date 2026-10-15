"""Панель загрузчика: какой браузер дал куки, понятные ошибки, кнопка «Доступ к диску»."""
import sys, os, json, time, shutil, threading, traceback
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""  # тесты не шлют настоящих отчётов на воркер
import customtkinter as ctk
from tkinter import messagebox
HERE = os.path.dirname(os.path.abspath(__file__)); SCR = os.path.join(os.path.join(__import__("tempfile").gettempdir(), "sheqel_tests"), "ckgui")
shutil.rmtree(SCR, ignore_errors=True); os.makedirs(SCR)
cfg = json.load(open(app.CONFIG_PATH))
for k in list(cfg):
    if any(x in k for x in ("key", "secret", "cloud_name")): cfg[k] = ""
json.dump(cfg, open(os.path.join(SCR, "config.json"), "w"))
app.CONFIG_PATH = os.path.join(SCR, "config.json"); app.STATE_PATH = os.path.join(SCR, "data", "state.json")
app.APP_DATA_DIR = os.path.join(SCR, "data"); app.VOICE_DATA_DIR = os.path.join(SCR, "data", "voices")
app.LOG_FILE_PATH = os.path.join(SCR, "app.log"); app.fetch_elevenlabs_balance = lambda k: None
app.find_best_browser_with_google = lambda: "firefox"
opened = []
app.open_full_disk_access_settings = lambda: opened.append("fda")
for n in ("showinfo", "showwarning", "showerror"): setattr(messagebox, n, lambda *a, **k: None)
def fake_download(url, output_dir, mode, denoise, log, browser=None, cookies_file=None, on_cookies=None):
    time.sleep(0.3)
    if url.endswith("/ok"):
        p = os.path.join(SCR, "v.mp4"); open(p, "w").write("x"); on_cookies("Firefox"); return p, None
    if url.endswith("/safari"):
        raise RuntimeError(app.SAFARI_DISK_ACCESS_HINT)
    raise RuntimeError(app.friendly_download_error("bot_check"))
app.download_from_url = fake_download
w = app.LipsyncTwoModeApp()
errors = []; w.report_callback_exception = lambda *e: errors.append("".join(traceback.format_exception(*e)))
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
def walk(x):
    yield x
    for c in x.winfo_children(): yield from walk(c)
def go():
    w.show_downloader_panel()
    p = w._panels["downloader"]
    box = next(x for x in walk(p) if isinstance(x, ctk.CTkTextbox))
    box.insert("1.0", "https://youtu.be/ok\nhttps://youtu.be/safari\nhttps://youtu.be/bot")
    next(x for x in walk(p) if isinstance(x, ctk.CTkButton) and x.cget("text") == "⬇ Скачать").invoke()
    w.after(4000, verify)
def verify():
    p = w._panels["downloader"]
    labels = [x.cget("text") for x in walk(p) if isinstance(x, ctk.CTkLabel)]
    buttons = [(x.cget("text"), x.cget("state")) for x in walk(p) if isinstance(x, ctk.CTkButton)]
    check("1. готовая загрузка: «· куки: Firefox» в строке истории", any("· куки: Firefox" in l for l in labels))
    check("2. под настройками: «Куки последней загрузки: Firefox»", "Куки последней загрузки: Firefox" in labels)
    check("3. ошибка «войдите» — понятным текстом, без сырого yt-dlp",
          any(l.startswith("Ошибка: YouTube просит войти") for l in labels) and not any("Sign in to confirm" in l for l in labels))
    check("4. ошибка Safari — подсказка и кнопка «Доступ к диску»", any("Полный доступ к диску" in l for l in labels)
          and ("Доступ к диску", "normal") in buttons)
    next(x for x in walk(p) if isinstance(x, ctk.CTkButton) and x.cget("text") == "Доступ к диску").invoke()
    check("5. кнопка открывает «Полный доступ к диску» в настройках", opened == ["fda"])
    check("6. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
    print(f"Итого: {sum(results)}/{len(results)}", flush=True)
    w._on_close(); os._exit(0 if all(results) else 1)
w.after(1200, go); w.mainloop()
