"""Живая очередь в настоящем окне, без Sync (обработка ролика подменена, файлы пишутся в настоящую папку партии):
добавление во время работы, гонка с завершающимся потоком, «Убрать из очереди», «Остановить после текущего»,
блокировка правки и удаления, повторный запуск с готовыми, пути готовых файлов и кнопки, перезапуск посреди
очереди (фаза 2). argv: фаза 1 | 2."""
import sys, os, json, time, shutil, threading, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import customtkinter as ctk
from tkinter import messagebox

PHASE = sys.argv[1]
SCR = os.path.join(__import__("tempfile").gettempdir(), "sheqel_tests", "queuelive")
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
app.requests.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сеть в тесте запрещена"))
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

RUN = {"active": 0, "max": 0, "lock": threading.Lock(), "dur": {}, "started": [], "finish_delay": 0.0}
def fake_process(nn, idx, roll, batch_dir, log):
    with RUN["lock"]:
        RUN["active"] += 1
        RUN["max"] = max(RUN["max"], RUN["active"])
        RUN["started"].append((roll["id"], nn, batch_dir))
    try:
        time.sleep(RUN["dur"].get(roll["id"], 0.4))
        out = os.path.join(batch_dir, f"{nn:02d}_{roll['video_single'].strip('/').split('.')[0]}.mp4")
        open(out, "wb").write(b"\0" * 64)
        return [out]
    finally:
        with RUN["lock"]:
            RUN["active"] -= 1
w._process_single_roll = fake_process
PART = {"kind": "single", "video_sec": 1.0, "audio_sec": 1.0, "billable": 1.0, "fps": 25.0, "source_fps": 25.0,
        "width": 1280, "height": 720, "frames": 25, "cost": 0.1, "source": "local", "time": (5, 9), "note": "",
        "video_shorter": False}
w._estimate_for = lambda roll, wait=False: {"time": (5, 9), "cost": 0.1, "frames": 25, "billable": 1.0,
                                            "parts": [dict(PART)], "source": "local"}
orig_finish = w._queue_finish
def slow_finish():
    time.sleep(RUN["finish_delay"])
    orig_finish()
w._queue_finish = slow_finish


def new_roll(name, dur=0.4):
    w.add_roll()
    r = w.rolls[-1]
    r.update(voice_file=f"/{name}.mp3", video_single=f"/{name}.mp4")
    RUN["dur"][r["id"]] = dur
    return r
def by_id(rid):
    return next(r for r in w.rolls if r["id"] == rid)
def idle():
    return not w.is_processing and not w._queue_costing
def rows():
    with w._progress_lock:
        return {k: dict(v) for k, v in w._queue_progress.items()}
def batch_files(d):
    return sorted(f for f in os.listdir(d) if f.endswith(".mp4"))


def phase1():
    yield 1.0
    w.rolls = []
    a, b, c = new_roll("a", 1.5), new_roll("b", 1.5), new_roll("c", 1.5)
    w.rolls = [r for r in w.rolls if r.get("voice_file")]
    w.render_scene_list(); w.update_status()
    w.start_queue(); yield lambda: w.is_processing; yield 0.2
    check("1. старт: три сцены «В очереди»/«Обработка», строк хода 3",
          all(app.roll_in_queue(by_id(r["id"])) for r in (a, b, c)) and len(rows()) == 3)
    yield lambda: w._queue_batch_dir is not None; yield 0.1
    batch = w._queue_batch_dir
    d, e = new_roll("d", 0.3), new_roll("e", 0.3)
    w.update_status(); yield 0.1
    check("2. во время работы кнопка «ДОБАВИТЬ В ОЧЕРЕДЬ (2)»", w.start_btn.cget("text") == "ДОБАВИТЬ В ОЧЕРЕДЬ (2)"
          and w.start_btn.cget("state") == "normal", w.start_btn.cget("text"))
    check("2b. «Открыть папку партии» видна во время работы", bool(w._qp_w["folder"].winfo_manager()) and batch)
    w.start_queue(); yield lambda: not w._queue_costing; yield 0.1
    check("3. добавленные — в ту же очередь: строк 5, статусы «В очереди»",
          len(rows()) == 5 and by_id(d["id"])["status"] == app.ROLL_QUEUED)
    w.update_status(); yield 0.1
    check("3a. добавлять нечего во время работы: кнопка активна — «ОЧЕРЕДЬ ИДЁТ · ДОБАВИТЬ СЦЕНУ»",
          w.start_btn.cget("text") == "ОЧЕРЕДЬ ИДЁТ · ДОБАВИТЬ СЦЕНУ" and w.start_btn.cget("state") == "normal",
          w.start_btn.cget("text"))
    msgs.clear(); w.start_queue(); yield 0.1
    check("3b. нажатие → понятное сообщение, а не тишина", any("Все сцены готовы" in m[2] and "Уже в очереди" in m[2]
                                                                for m in msgs), str(msgs))
    f = new_roll("f", 0.3)
    w.start_queue(); yield lambda: not w._queue_costing; yield 0.05
    removed = w._queue_remove_roll(f["id"])
    check("4. «Убрать из очереди» ждущую → «Ожидает», строка убрана",
          removed and by_id(f["id"])["status"] == "Ожидает" and len(rows()) == 5)
    msgs.clear()
    w.remove_roll_by_id(d["id"]); w.set_roll_mode(w.rolls.index(by_id(d["id"])), "Начало + Конец")
    check("5. сцену «В очереди» нельзя удалить и править", any(r["id"] == d["id"] for r in w.rolls)
          and by_id(d["id"])["mode"] == "1 видео" and any("Убрать из очереди" in m[2] for m in msgs))
    w.remove_roll_by_id(f["id"])
    check("5b. другую сцену («Ожидает») во время работы удалить можно", not any(r["id"] == f["id"] for r in w.rolls))
    yield idle; yield 0.3
    files = batch_files(batch)
    check("6. одна папка партии, нумерация продолжается: 01…05",
          files == ["01_a.mp4", "02_b.mp4", "03_c.mp4", "04_d.mp4", "05_e.mp4"] and
          all(x[2] == batch for x in RUN["started"]), str(files))
    check("6b. одновременно не больше 2 роликов", RUN["max"] <= 2, str(RUN["max"]))
    check("6c. итог: «Готово 5 из 5»", w._queue_summary and w._queue_summary["total"] == 5
          and not w._queue_summary["failed"])
    w._qp_set_state(expanded=True, hidden=False); w._render_queue_panel(); yield 0.3
    texts = [x.cget("text") for x in walk(w._qp_w["rolls"]) if isinstance(x, ctk.CTkButton)]
    check("7. у готовых строк — «Показать в папке» и «Открыть»", texts.count("Показать в папке") == 5
          and texts.count("Открыть") == 5, str(texts[:4]))
    msgs.clear()
    w.start_queue(); yield 0.2
    check("8. повторный запуск: все готовы → «Все сцены готовы. Нажми «Запустить заново» у нужной сцены»",
          not w.is_processing and any(m[2].startswith("Все сцены готовы. Нажми «Запустить заново» у нужной сцены.")
                                      and "Готовых: 5" in m[2] for m in msgs), str(msgs[-1:]))
    check("8a. кнопка не выглядит сломанной: «НАЧАТЬ ОЧЕРЕДЬ», нажимается",
          w.start_btn.cget("text") == "НАЧАТЬ ОЧЕРЕДЬ" and w.start_btn.cget("state") == "normal")
    w.rerun_roll(a["id"]); w.start_queue(); yield lambda: w.is_processing; yield idle; yield 0.2
    check("8b. «Запустить заново» → только эта сцена, новая папка", w._queue_summary["total"] == 1
          and w._queue_batch_dir != batch)
    # Гонка: последний поток выходит, а пользователь добавляет сцену
    RUN["finish_delay"] = 0.8
    g = new_roll("g", 0.2)
    w.start_queue(); yield lambda: w.is_processing
    batch_g = w._queue_batch_dir
    yield lambda: not w._queue_accepting and w.is_processing     # поток вышел, итог ещё не подведён
    h = new_roll("h", 0.2)
    w.start_queue(); yield 0.1
    RUN["finish_delay"] = 0.0
    yield lambda: by_id(h["id"])["status"] == "Готово"; yield idle; yield 0.2
    started_h = [x for x in RUN["started"] if x[0] == h["id"]]
    hist_g = [x for x in w._queue_history if x["batch_dir"] == batch_g]
    check("9. гонка: добавлено после выхода последнего потока → не потеряно, та же папка, NN 02",
          by_id(g["id"])["status"] == "Готово" and len(started_h) == 1 and started_h[0][2] == batch_g
          and started_h[0][1] == 2 and batch_files(batch_g) == ["01_g.mp4", "02_h.mp4"], str(started_h))
    check("9a. в истории одна партия: готово 2", len(hist_g) == 1 and hist_g[0]["ok"] == 2
          and w._queue_summary["total"] == 2, str(hist_g))
    # Папку прошлой партии использовать нельзя → «Продолжение в новой папке»
    RUN["finish_delay"] = 0.8
    g2 = new_roll("g2", 0.2)
    w.start_queue(); yield lambda: w.is_processing
    batch_g2 = w._queue_batch_dir
    yield lambda: not w._queue_accepting and w.is_processing
    shutil.rmtree(batch_g2)
    h2 = new_roll("h2", 1.0)
    w.start_queue(); yield 0.1
    RUN["finish_delay"] = 0.0
    yield lambda: w.is_processing and w._queue_batch_dir and w._queue_batch_dir != batch_g2; yield 0.3
    w._render_queue_panel()
    title = w._qp_w["title"].cget("text")
    yield lambda: by_id(h2["id"])["status"] == "Готово"; yield idle; yield 0.2
    new_dir = [x for x in RUN["started"] if x[0] == h2["id"]][0][2]
    check("9b. папки нет → новая папка, в панели «Продолжение в новой папке», нумерация с 01",
          new_dir != batch_g2 and "Продолжение в новой папке" in title and batch_files(new_dir) == ["01_h2.mp4"],
          title)
    # Добавление, когда очередь пуста, но один поток ещё работает
    i_ = new_roll("i", 1.0)
    w.start_queue(); yield lambda: w.is_processing; yield 0.3
    batch_i = w._queue_batch_dir
    j = new_roll("j", 0.2)
    w.start_queue(); yield idle; yield 0.2
    started_j = [x for x in RUN["started"] if x[0] == j["id"]]
    check("10. ждущих нет, поток ещё работает → добавленная сцена в ту же партию, NN 02",
          started_j and started_j[0][2] == batch_i and started_j[0][1] == 2, str(started_j))
    # Остановить после текущего
    ks = [new_roll(f"k{n}", 0.6) for n in range(4)]
    w.start_queue(); yield lambda: w.is_processing; yield 0.15
    w._queue_request_stop(); yield idle; yield 0.2
    done = [r for r in ks if by_id(r["id"])["status"] == "Готово"]
    back = [r for r in ks if by_id(r["id"])["status"] == "Ожидает"]
    check("11. «Остановить после текущего»: начатые доработали, остальные снова «Ожидает»",
          len(done) == 2 and len(back) == 2 and w._queue_summary["total"] == 2, f"{len(done)}/{len(back)}")
    # Перезапуск посреди очереди: один в обработке, один ждёт — закрываем окно
    RUN["dur"].clear()
    p1, p2, p3 = new_roll("p1", 30), new_roll("p2", 30), new_roll("p3", 30)
    for r in back:
        r["status"] = "Готово"
    w.start_queue(); yield lambda: w.is_processing; yield 0.5
    check("12. перед закрытием: два в обработке, один «В очереди»",
          sum(by_id(r["id"])["status"].startswith("Обработка") for r in (p1, p2, p3)) == 2
          and by_id(p3["id"])["status"] == app.ROLL_QUEUED)
    log = open(app.LOG_FILE_PATH, encoding="utf-8").read()
    check("13. исключений нет", not errors and "ИСКЛЮЧЕНИЕ" not in log, (errors[:1] or [""])[0][-300:])
    w._on_close()


def phase2():
    yield 1.0
    st = {r["voice_file"]: r["status"] for r in w.rolls}
    check("R1. после перезапуска: обрабатывавшиеся → «Прервано», ждавшая (без задачи Sync) → «Ожидает»",
          st.get("/p1.mp3") == "Прервано" and st.get("/p2.mp3") == "Прервано" and st.get("/p3.mp3") == "Ожидает",
          str({k: v for k, v in st.items() if k.startswith("/p")}))
    check("R2. исключений нет", not errors, (errors[:1] or [""])[0][-300:])
    w._on_close()


def walk(x):
    yield x
    for c in x.winfo_children():
        yield from walk(c)


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
