"""Клонирование → голос в генератор, очистка текста с «Вернуть». Подставной ответ ElevenLabs. Две фазы."""
import sys, os, json, time, shutil, threading, traceback, subprocess
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import app
import customtkinter as ctk
from tkinter import messagebox, filedialog

PHASE = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__))
SCR = os.path.join(os.path.join(__import__("tempfile").gettempdir(), "sheqel_tests"), "clgui")
if PHASE == "1":
    shutil.rmtree(SCR, ignore_errors=True); os.makedirs(SCR)
    cfg = json.load(open(app.CONFIG_PATH))
    for k in list(cfg):
        if any(x in k for x in ("key", "secret", "cloud_name")):
            cfg[k] = ""
    cfg.update(elevenlabs_api_key="-".join(("fake", "eleven", "key", "000111222")), voice_expiry_list=[], main_voice_name="Старый голос",
               main_voice_id="")
    cfg.pop("clear_text_after_clone", None)
    json.dump(cfg, open(os.path.join(SCR, "config.json"), "w"), ensure_ascii=False)
    subprocess.run([app.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=2",
                    os.path.join(SCR, "сэмпл.mp3")], check=True)
app.CONFIG_PATH = os.path.join(SCR, "config.json")
app.STATE_PATH = os.path.join(SCR, "data", "state.json")
app.VOICE_DATA_DIR = os.path.join(SCR, "data", "voices")
app.LOG_FILE_PATH = os.path.join(SCR, "app.log")
app.FACE_POINTS_PATH = os.path.join(SCR, "faces.json")
app.fetch_elevenlabs_balance = lambda key: None
threading.excepthook = app._thread_excepthook
msgs = []
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, (lambda n: lambda title, text, **k: msgs.append((n, title, text)))(name))
filedialog.askopenfilename = lambda **k: os.path.join(SCR, "сэмпл.mp3")

w = app.LipsyncTwoModeApp()
errors = []
w.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
CLONE = {"fail": False, "n": 0}
def fake_clone(path, name, description=""):
    time.sleep(1.0)
    if CLONE["fail"]:
        raise RuntimeError("ElevenLabs: voice_limit_reached (тест)")
    CLONE["n"] += 1
    return f"cloneVoiceId{CLONE['n']:08d}"
w.create_voice_from_sample = fake_clone
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
def walk(x):
    yield x
    for c in x.winfo_children():
        yield from walk(c)
def vg_text():
    return w._vg["text_box"].get("1.0", "end").strip()
def vg_voice():
    return w._vg["voice_box"].get("1.0", "end").strip()
def notice_shown():
    rows = [x for x in walk(w._panels["voice_gen"]) if isinstance(x, ctk.CTkButton) and x.cget("text") == "Вернуть"]
    return bool(rows) and bool(rows[0].master.winfo_manager())
def restore_btn():
    return next(x for x in walk(w._panels["voice_gen"]) if isinstance(x, ctk.CTkButton) and x.cget("text") == "Вернуть")
def state():
    return w._panel_state.get("voice_gen", {})
def set_gen_text(text):
    w.show_voice_gen_panel()
    w._vg["text_box"].delete("1.0", "end")
    if text:
        w._vg["text_box"].insert("1.0", text)
    w._vg["save_fields"]()
def start_clone(name, stay=True):
    w.show_voice_clone_panel()
    panel = w._panels["voice_clone"]
    entry = next(x for x in walk(panel) if isinstance(x, ctk.CTkEntry))
    entry.delete(0, "end"); entry.insert(0, name)
    next(x for x in walk(panel) if isinstance(x, ctk.CTkButton) and x.cget("text") == "Выбрать файл").invoke()
    next(x for x in walk(panel) if isinstance(x, ctk.CTkButton) and x.cget("text") == "🎙 Клонировать").invoke()
def clone_done():
    return not w.background_tasks.active(kind="clone_upload") and not w._clone_pending

def phase1():
    yield 1.2
    # карточка A/B, которую трогать нельзя
    ab = w.background_tasks.create("voice_gen", "voice_gen", "Голос, вариант A", meta={"slot": "A", "batch": "b-old"},
                                   status="done")
    w.background_tasks.update(ab, result={"path": "/tmp/ab.mp3", "duration": 1.0})
    w._panel_state_set("voice_gen", batch="b-old")
    set_gen_text("старый текст"); yield 0.3
    gen_panel = w._panels["voice_gen"]
    # A. Успех на вкладке клонирования
    msgs.clear(); start_clone("Клон А"); yield 0.2
    clone_panel = w._panels["voice_clone"]
    yield clone_done
    yield 0.8
    check("A1. успех на вкладке клонирования: сообщение, переход в генерацию, голос подставлен, текст очищен",
          [m[0] for m in msgs] == ["showinfo"] and w._active_panel == "voice_gen" and vg_voice() == "Клон А"
          and vg_text() == "" and notice_shown(), f"голос «{vg_voice()}», текст «{vg_text()}»")
    cfg = json.load(open(app.CONFIG_PATH))
    check("A2. голос записан в данные: состояние вкладки, main_voice_name/id в конфиге, резерв текста",
          state().get("voice") == "Клон А" and state().get("voice_id") == "cloneVoiceId00000001"
          and cfg.get("main_voice_name") == "Клон А" and cfg.get("main_voice_id") == "cloneVoiceId00000001"
          and state().get("text_backup") == "старый текст")
    restore_btn().invoke(); yield 0.3
    check("A3. «Вернуть» возвращает текст, строка исчезает", vg_text() == "старый текст" and not notice_shown()
          and "text_backup" not in state() and state().get("text") == "старый текст")
    # B. Начало печати скрывает «Вернуть»
    start_clone("Клон Б"); yield clone_done; yield 0.8
    check("B1. снова очищено, строка «Вернуть» видна", vg_text() == "" and notice_shown())
    box = w._vg["text_box"]._textbox
    box.focus_force(); w.update(); box.insert("insert", "х"); box.event_generate("<KeyRelease>", keysym="x"); yield 0.3
    check("B2. пользователь начал печатать — строка исчезла, резерв больше не предлагается",
          not notice_shown() and "text_backup" not in state())
    # C. Успех, пока пользователь на другой вкладке
    set_gen_text("текст для В"); yield 0.2
    start_clone("Клон В"); yield 0.2
    w.show_compress_panel(); yield 0.2
    msgs.clear()
    yield clone_done; yield 0.6
    bar = w._notice_w["bar"]
    check("C1. на другой вкладке: перехода нет, модального сообщения нет, уведомление «Голос «Клон В» готов»",
          w._active_panel == "compress" and not msgs and bar.winfo_manager()
          and w._notice_w["label"].cget("text") == "Голос «Клон В» готов")
    check("C2. данные уже обновлены: голос «Клон В», текст пуст, резерв сохранён",
          state().get("voice") == "Клон В" and state().get("text") == "" and state().get("text_backup") == "текст для В")
    w._notice_w["action"].invoke(); yield 0.5
    check("C3. «Открыть генерацию»: вкладка та же (не пересоздана), голос «Клон В», текст пуст, «Вернуть» видна, уведомление скрыто",
          w._active_panel == "voice_gen" and w._panels["voice_gen"] is gen_panel and vg_voice() == "Клон В"
          and vg_text() == "" and notice_shown() and not bar.winfo_manager())
    # D. Ошибка — ничего не меняется
    set_gen_text("мой текст"); yield 0.2
    CLONE["fail"] = True; msgs.clear()
    start_clone("Клон ошибка"); yield clone_done; yield 0.6
    CLONE["fail"] = False
    w.show_voice_gen_panel(); yield 0.2
    check("D. ошибка клонирования: голос и текст как были, сообщение об ошибке",
          vg_voice() == "Клон В" and vg_text() == "мой текст" and state().get("voice") == "Клон В"
          and [m[0] for m in msgs] == ["showerror"] and json.load(open(app.CONFIG_PATH))["main_voice_name"] == "Клон В")
    # E. Пустой текст — нечего очищать
    set_gen_text(""); yield 0.2
    start_clone("Клон Г"); yield clone_done; yield 0.8
    check("E. пустой текст: голос подставлен, строки «Вернуть» нет", vg_voice() == "Клон Г" and not notice_shown()
          and "text_backup" not in state())
    # F. Переключатель выключен — только голос
    w.show_settings_panel(); yield 0.3
    sw = [x for x in walk(w._panels["settings"]) if isinstance(x, ctk.CTkSwitch)]
    check("F0. в «Настройках → Приложение» переключатель «После клонирования очищать текст генерации», включён",
          w.clear_text_after_clone_var.get() is True and any("очищать текст генерации" in str(x.cget("text"))
          for x in walk(w._panels["settings"]) if isinstance(x, ctk.CTkLabel)))
    w.clear_text_after_clone_var.set(False); w._on_clear_after_clone_toggle()
    set_gen_text("не трогать"); yield 0.2
    start_clone("Клон Д"); yield clone_done; yield 0.8
    check("F. выключено: голос «Клон Д» подставлен, текст не тронут", vg_voice() == "Клон Д" and vg_text() == "не трогать"
          and not notice_shown() and json.load(open(app.CONFIG_PATH)).get("clear_text_after_clone") is False)
    w.clear_text_after_clone_var.set(True); w._on_clear_after_clone_toggle()
    # G. A/B не тронуты, автоудаление, подсветка
    t_ab = w.background_tasks.get(ab)
    check("G1. карточки A/B прошлых генераций не тронуты", t_ab and t_ab["status"] == "done"
          and t_ab["result"]["path"] == "/tmp/ab.mp3" and state().get("batch") == "b-old")
    exp = w._expiry_snapshot()
    check("G2. автоудаление: по записи на каждый успешный клон, срок ≈ 10 ч", len(exp) == 5
          and all(9.9 * 3600 < e["expires_at"] - time.time() <= 10 * 3600 for e in exp), f"{len(exp)} записей")
    w.fetch_all_voices = lambda: [
        {"name": "Клон Д", "voice_id": "cloneVoiceId00000005", "category": "cloned", "preview_url": "", "created_at": 0},
        {"name": "Чужой", "voice_id": "otherVoiceId0000000", "category": "cloned", "preview_url": "", "created_at": 0}]
    w.show_voice_gen_panel(); yield 0.2
    next(x for x in walk(w._panels["voice_gen"]) if isinstance(x, ctk.CTkButton)
         and x.cget("text").startswith("📋 Выбрать из списка")).invoke()
    yield 1.5
    pick = [c for c in w.winfo_children() if isinstance(c, ctk.CTkToplevel) and c.winfo_exists()][-1]
    labels = [x.cget("text") for x in walk(pick) if isinstance(x, ctk.CTkLabel)]
    check("G3. в «Мои клоны» новый голос выделен («✓ выбран в генераторе»), таймер автоудаления на месте",
          labels.count("✓ выбран в генераторе") == 1 and any(l.startswith("⏳ удалится через 9 ч") for l in labels))
    pick.destroy(); yield 0.2
    # Для перезапуска: последний клон с очисткой
    set_gen_text("перед перезапуском"); yield 0.2
    start_clone("Клон Е"); yield clone_done; yield 0.8
    check("H. панели не пересоздаются", w._panels["voice_gen"] is gen_panel and w._panels["voice_clone"] is clone_panel)
    log = open(app.LOG_FILE_PATH, encoding="utf-8").read()
    check("I. исключений нет", not errors and "ИСКЛЮЧЕНИЕ" not in log, (errors[:1] or [""])[0][-300:])
    w._on_close()

def phase2():
    yield 1.2
    w.show_voice_gen_panel(); yield 0.4
    check("R1. после перезапуска: голос «Клон Е», текст пуст, «Вернуть» на месте",
          vg_voice() == "Клон Е" and vg_text() == "" and notice_shown() and w.main_voice_id == "cloneVoiceId00000006")
    restore_btn().invoke(); yield 0.3
    check("R2. «Вернуть» после перезапуска возвращает прежний текст", vg_text() == "перед перезапуском")
    check("R3. записи автоудаления пережили перезапуск", len(w._expiry_snapshot()) == 6)
    log = open(app.LOG_FILE_PATH, encoding="utf-8").read()
    check("R4. исключений нет", not errors and "ИСКЛЮЧЕНИЕ" not in log, (errors[:1] or [""])[0][-300:])
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
            else: w.after(100, poll)
        w.after(100, poll)
    else:
        w.after(int(r * 1000), tick)
w.after(300, tick)
try:
    w.mainloop()
except Exception:
    traceback.print_exc()
print(f"Итого phase {PHASE}: {sum(results)}/{len(results)}", flush=True)
os._exit(0 if results and all(results) else 1)
