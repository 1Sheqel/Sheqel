"""Пакетное добавление папок и кнопка «Очистить» под «Новая задача». Без сети и оплаты, все пути во временной папке."""
import sys, os, json, time, shutil, threading, traceback, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import customtkinter as ctk
from tkinter import messagebox

SCR = os.path.join(tempfile.gettempdir(), "sheqel_tests", "folders_clear")
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
app.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сеть в тесте запрещена"))
threading.excepthook = app._thread_excepthook

results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)

# ── подготовка папок ──
base = os.path.join(SCR, "batch")
def mk(name, files):
    d = os.path.join(base, name); os.makedirs(d, exist_ok=True)
    for f in files:
        open(os.path.join(d, f), "wb").write(b"x")
    return d
d1 = mk("Ролик 1 кириллица", ["voice.mp3", "clip.mp4", ".DS_Store", "._voice.mp3"])
d2 = mk("two", ["a.wav", "b.MOV"])
d3 = mk("three", ["a.m4a", "b.mkv"])
d_empty = mk("empty", [])
d_audio = mk("only audio", ["a.mp3"])
d_video = mk("only video", ["b.mp4"])
d_hidden = mk("hidden", ["._a.mp3", ".b.mp4"])
d_missing = os.path.join(base, "нет такой")
f_not_dir = os.path.join(base, "file.txt"); open(f_not_dir, "w").write("x")

# ── чистые функции ──
check("API: find_folder_media есть", hasattr(app, "find_folder_media"))
check("API: scan_folder_for_roll есть", hasattr(app, "scan_folder_for_roll"))
check("API: parse_folder_list есть", hasattr(app, "parse_folder_list"))
if hasattr(app, "scan_folder_for_roll"):
    f, why = app.scan_folder_for_roll(d1)
    check("папка с парой → поля ролика", f and f["voice_file"].endswith("voice.mp3") and f["video_single"].endswith("clip.mp4") and not why, str(why))
    f, why = app.scan_folder_for_roll(d2)
    check("расширения .wav и .MOV находятся", f and f["voice_file"].endswith("a.wav") and f["video_single"].endswith("b.MOV"))
    for nm, d, word in (("пустая", d_empty, ""), ("только аудио", d_audio, "видео"), ("только видео", d_video, "озвуч"),
                        ("скрытые файлы не считаются", d_hidden, ""), ("нет папки", d_missing, "не найден"), ("файл вместо папки", f_not_dir, "не папка")):
        f, why = app.scan_folder_for_roll(d)
        check(f"пропуск: {nm}", f is None and bool(why) and word in why, why)
if hasattr(app, "parse_folder_list"):
    got = app.parse_folder_list(f'  "{d1}"\n\n{d2}\n{d1}\n')
    check("parse_folder_list: кавычки, пустые строки, порядок", got == [d1, d2, d1], str(got))

# ── окно ──
w = app.LipsyncTwoModeApp()
errors = []
w.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
shown = []
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, lambda *a, _n=name, **k: shown.append((_n, a)))
answers = []
def fake_yesno(*a, **k):
    answers.append(a); return ask_result[0]
ask_result = [True]
messagebox.askyesno = fake_yesno

def pump(cond, timeout=10):
    t = time.time()
    while time.time() - t < timeout and not cond():
        w.update(); time.sleep(0.02)
    w.update()
    return cond()

def scenario():
    w.rolls = []
    w.add_roll()
    check("кнопка «Очистить» создана", hasattr(w, "clear_scene_btn"))
    check("кнопка пакета папок создана", hasattr(w, "batch_folders_btn"))

    # --- пакет ---
    state = {}
    def run_batch(folders):
        state.clear()
        w.add_folders_batch(folders, on_progress=lambda a, t, s: state.setdefault("prog", []).append((a, t, s)),
                            on_done=lambda summ: state.update(done=summ))
        return pump(lambda: "done" in state)

    before_ids = [r["id"] for r in w.rolls]
    st_before = json.dumps(w.rolls, sort_keys=True)
    ok = run_batch([d1, d_empty, d2, d1, d_missing, d3, d_audio])
    summ = state.get("done") or {}
    check("батч завершился", ok)
    check("добавлено 3 (пустая папка и дубликат пропущены)", summ.get("added") == 3, str(summ))
    check("пропущено 4 с причинами", len(summ.get("skipped", [])) == 4 and all(len(x) == 2 and x[1] for x in summ.get("skipped", [])), str(summ.get("skipped")))
    check("пустая стартовая сцена переиспользована, всего 3", len(w.rolls) == 3, str(len(w.rolls)))
    order = [os.path.basename(os.path.dirname(r["voice_file"])) for r in w.rolls]
    check("порядок как выбрано", order == ["Ролик 1 кириллица", "two", "three"], str(order))
    check("статус «Ожидает», запуска нет", all(r["status"] == "Ожидает" for r in w.rolls) and not w.is_processing)
    check("прогресс доходит до 7 из 7", bool(state.get("prog")) and state["prog"][-1][1] == 7, str(state.get("prog")))
    ids = [r["id"] for r in w.rolls]
    check("id уникальны", len(set(ids)) == len(ids))
    # повтор тех же папок → всё дубликаты
    ok = run_batch([d1, d2, d3])
    summ = state.get("done") or {}
    check("повтор тех же папок: ничего не добавлено", summ.get("added") == 0 and len(summ.get("skipped", [])) == 3 and len(w.rolls) == 3, str(summ))
    check("причина дубликата понятна", all("уже" in x[1] for x in summ.get("skipped", [])), str(summ.get("skipped")))
    check("ошибок UI нет (батч)", not errors, str(errors)[:300])

    # --- Очистить ---
    w.rolls[0]["status"] = "Готово"
    w.rolls[1]["status"] = app.ROLL_QUEUED
    w.rolls[2]["status"] = "Обработка 1/2"
    n_before = len(w.rolls)
    answers.clear(); ask_result[0] = False
    w.clear_rolls_confirm()
    check("отказ в подтверждении: ничего не удалено", len(w.rolls) == n_before and len(answers) == 1)
    text = " ".join(str(x) for x in answers[0])
    check("в подтверждении: убирается 1, остаются 2 в очереди, файлы целы", "списка: 1" in text and "(2)" in text and "не удаляются" in text, text[:300])
    ask_result[0] = True
    w.clear_rolls_confirm()
    check("очередь идёт: сцены «В очереди»/«Обработка» остались", [r["status"] for r in w.rolls] == [app.ROLL_QUEUED, "Обработка 1/2"], str([r["status"] for r in w.rolls]))
    # всё в очереди: нечего очищать, окно подтверждения не нужно
    answers.clear(); shown.clear()
    w.clear_rolls_confirm()
    check("нечего очищать → сообщение, а не вопрос", not answers and shown)
    # обычная очистка
    w.rolls[0]["status"] = "Ожидает"; w.rolls[1]["status"] = "Готово"
    w.clear_rolls_confirm()
    check("после очистки одна пустая сцена", len(w.rolls) == 1 and not w.rolls[0]["voice_file"] and w.rolls[0]["status"] == "Ожидает")
    # пустой список: вопрос не задаётся
    answers.clear()
    w.clear_rolls_confirm()
    check("пустой список: без вопроса, остаётся одна сцена", not answers and len(w.rolls) == 1)
    check("ошибок UI нет (очистка)", not errors, str(errors)[:300])
    check("файлы на диске целы", os.path.exists(os.path.join(d1, "voice.mp3")) and os.path.exists(os.path.join(d2, "b.MOV")))

    # --- старый state.json читается ---
    old = {"rolls": [{"id": 5, "mode": "1 видео", "voice_file": "", "text": "", "video_single": "", "video_start": "", "video_end": "",
                      "audio_start": "", "audio_end": "", "status": "Ожидает"}], "next_roll_id": 6}
    os.makedirs(os.path.dirname(app.STATE_PATH), exist_ok=True)
    w2_ok = True
    try:
        json.dump(old, open(app.STATE_PATH, "w", encoding="utf-8"))
        w._restore_state()
        w2_ok = len(w.rolls) == 1 and w.rolls[0]["id"] == 5
    except Exception as e:
        w2_ok = False; print(e)
    check("старый state.json читается", w2_ok)

    w.quit()


w.after(300, scenario)
try:
    w.mainloop()
except Exception:
    traceback.print_exc()
try:
    w.destroy()
except Exception:
    pass
print(f"\nИтог: {sum(results)}/{len(results)}")
sys.exit(0 if all(results) else 1)
