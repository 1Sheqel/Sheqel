"""Род текста в настоящем окне генератора: индикатор, подсветка, пол голоса, предупреждение, приведение к роду
с предпросмотром и «Вернуть» (в том числе после перезапуска). Groq и ElevenLabs — подставные, сети нет.
argv: фаза 1 | 2."""
import sys, os, json, time, shutil, threading, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import customtkinter as ctk
from tkinter import messagebox

PHASE = sys.argv[1]
SCR = os.path.join(__import__("tempfile").gettempdir(), "sheqel_tests", "gendergui")
if PHASE == "1":
    shutil.rmtree(SCR, ignore_errors=True)
    os.makedirs(SCR)
    cfg = {"elevenlabs_api_key": "-".join(("fake", "eleven", "key")), "groq_api_key": "-".join(("fake", "groq", "key")),
           "main_voice_name": "Клон Маша", "main_voice_id": "", "spellcheck_online": False,
           "error_reports_notice_shown": True}
    json.dump(cfg, open(os.path.join(SCR, "config.json"), "w"), ensure_ascii=False)
app.CONFIG_PATH = os.path.join(SCR, "config.json")
app.STATE_PATH = os.path.join(SCR, "data", "state.json")
app.VOICE_DATA_DIR = os.path.join(SCR, "data", "voices")
app.LOG_FILE_PATH = os.path.join(SCR, "app.log")
app.FACE_POINTS_PATH = os.path.join(SCR, "faces.json")
app.ERROR_REPORT_URL = ""
app.fetch_elevenlabs_balance = lambda key: None
threading.excepthook = app._thread_excepthook
msgs = []
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, (lambda n: lambda title, text, **k: msgs.append((n, title, text)))(name))

FEM = ("Прошла короткий тест, получила расчёт и запустила демонстрацию. После этого AI начал анализировать "
       "акции. Я решила посмотреть. Мне не пришлось выбирать.")
MASC = FEM.replace("Прошла", "Прошёл").replace("получила", "получил").replace("запустила", "запустил") \
          .replace("решила", "решил")


class Resp:
    def __init__(self, code, content=None, headers=None):
        self.status_code, self._content, self.headers = code, content, headers or {}
        self.text = ""
    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


NET = {"groq": [], "other": [], "blocked_get": [], "mode": "ok"}
def fake_post(url, headers=None, json=None, timeout=None, **kw):
    if "api.groq.com" not in url:
        NET["other"].append(url)
        raise RuntimeError("сеть в тесте запрещена")
    NET["groq"].append(json)
    if NET["mode"] == "429":
        return Resp(429, headers={"retry-after": "1"})
    src = json["messages"][1]["content"]
    return Resp(200, src.replace("Прошла", "Прошёл").replace("получила", "получил")
                .replace("запустила", "запустил").replace("решила", "решил"))
app.requests.post = fake_post

class GetResp:
    status_code = 200
    def json(self):
        return {"voices": [{"voice_id": "LabelVoiceId00000001", "name": "Анна (диктор)", "category": "premade",
                            "labels": {"gender": "female", "accent": "russian"}},
                           {"voice_id": "cloneNoLabel00000002", "name": "Клон Маша", "category": "cloned",
                            "labels": {}}]}
def fake_get(url, headers=None, timeout=None, **kw):
    if url.endswith("/v1/voices"):
        return GetResp()
    NET["blocked_get"].append(url.split("?")[0])      # фоновые проверки при запуске (обновление и т. п.)
    raise RuntimeError("сеть в тесте запрещена")
app.requests.get = fake_get

w = app.LipsyncTwoModeApp()
errors = []
w.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
def G():
    return w._vg["gender"]
def text():
    return w._vg["text_box"].get("1.0", "end-1c")
def set_text(t):
    box = w._vg["text_box"]
    box.delete("1.0", "end")
    box.insert("1.0", t)
    box._textbox.event_generate("<KeyRelease>", keysym="a")
def tag_words(tag):
    tk_text = w._vg["text_box"]._textbox
    r = tk_text.tag_ranges(tag)
    return [tk_text.get(r[i], r[i + 1]) for i in range(0, len(r), 2)]
def shown(widget):
    return bool(widget.winfo_manager())
def visible_buttons():
    return {t: b for t, b in G()["buttons"].items() if shown(b)}
def idle():
    return not G()["state"]["busy"]


def phase1():
    yield 1.2
    w.show_voice_gen_panel(); yield 0.3
    set_text(FEM)
    t0 = time.time(); w.update(); ui_ms = (time.time() - t0) * 1000
    yield 0.15
    early = G()["label"].cget("text")
    yield 0.9
    check("1. индикатор пересчитан после паузы (400 мс), ввод не блокируется",
          "Род текста" not in early and ui_ms < 200, f"{ui_ms:.0f} мс")
    check("1b. «Род текста: женский · Прошла, получила, запустила, решила»",
          G()["label"].cget("text") == "Род текста: женский · Прошла, получила, запустила, решила",
          G()["label"].cget("text"))
    check("1c. подсветка: розовые — 4 слова говорящего, голубых нет",
          tag_words("gender_f") == ["Прошла", "получила", "запустила", "решила"] and not tag_words("gender_m"))
    check("1d. цвета подсветки — только фон (подчёркивание орфографии не трогается)",
          w._vg["text_box"]._textbox.tag_cget("gender_f", "underline") in ("", "0", 0, False)
          and w._vg["text_box"]._textbox.tag_cget("gender_f", "background") == "#fce7f3")
    check("2. клон без метки: «Пол голоса: не задан», выбор виден, кнопки — обе, «к женскому» неактивна",
          shown(G()["vgender_row"]) and shown(G()["vgender_menu"]) and set(visible_buttons()) == {"male", "female"}
          and G()["buttons"]["female"].cget("state") == "disabled" and not shown(G()["warn"]))
    G()["pick_gender"]("мужской"); yield 0.2
    check("3. пол «мужской» → предупреждение под полем голоса со словами",
          shown(G()["warn"]) and G()["warn"].cget("text") ==
          "Голос «Клон Маша» мужской, а текст в женском роде: Прошла, получила, запустила, решила",
          G()["warn"].cget("text"))
    check("3b. одна кнопка «Привести к мужскому роду», подсвечена", set(visible_buttons()) == {"male"}
          and G()["buttons"]["male"].cget("fg_color") == app.BTN_OK)
    gen_btn = next(x for x in walk(w._panels["voice_gen"]) if isinstance(x, ctk.CTkButton)
                   and x.cget("text") == "⚡ Сгенерировать A и B")
    check("3c. генерация не блокируется: кнопка «Сгенерировать» активна", gen_btn.cget("state") == "normal")
    w.gender_highlight_var.set(False); w._on_gender_highlight_toggle(); yield 0.1
    off = tag_words("gender_f")
    w.gender_highlight_var.set(True); w._on_gender_highlight_toggle(); yield 0.1
    check("4. «Подсвечивать род текста» выкл → подсветки нет, вкл → снова есть",
          off == [] and len(tag_words("gender_f")) == 4)
    G()["buttons"]["male"].invoke(); yield 0.2
    check("5. первое использование Groq: уведомление внутри окна, Groq ещё не вызывался",
          shown(G()["notice"]) and not NET["groq"] and not shown(G()["preview"]))
    G()["notice_choice"](True); yield idle; yield 0.2
    pv = G()["preview"]
    before = G()["boxes"]["before"]._textbox
    r = before.tag_ranges("changed")
    check("5b. «Отправить в Groq» → предпросмотр «Было → Стало» в главном окне, 4 слова выделены, текст не тронут",
          shown(pv) and len(r) == 8 and text() == FEM and "Groq привёл к мужскому роду слов: 4" in G()["head"].cget("text"),
          G()["head"].cget("text"))
    check("5c. в Groq ушёл запрос с temperature 0", NET["groq"] and NET["groq"][-1]["temperature"] == 0)
    check("5d. окно не Toplevel: предпросмотр — часть вкладки", pv.winfo_toplevel() is w)
    next(b for b in pv.winfo_children()[-1].winfo_children() if b.cget("text") == "Отмена").invoke(); yield 0.2
    check("6. «Отмена» → предпросмотр закрыт, текст не изменён", not shown(pv) and text() == FEM)
    n_before = len(NET["groq"])
    G()["buttons"]["male"].invoke(); yield 0.2
    check("6b. уведомление о Groq второй раз не показывается", not shown(G()["notice"]))
    yield idle; yield 0.2
    G()["apply"](); yield 1.0
    st = w._panel_state["voice_gen"]
    check("7. «Применить» → текст в мужском роде, над полем «Текст изменён (род). Вернуть»",
          text() == MASC and st.get("text_backup") == FEM and st.get("text_backup_kind") == "regender"
          and shown(G()["restore_label"].master) and G()["restore_label"].cget("text") == "Текст изменён (род).")
    check("7b. индикатор: мужской, предупреждения нет", G()["label"].cget("text").startswith("Род текста: мужской")
          and not shown(G()["warn"]) and not visible_buttons(), G()["label"].cget("text"))
    box = w._vg["text_box"]._textbox
    box.insert("end", "!"); box.event_generate("<KeyRelease>", keysym="exclam"); yield 0.3
    check("7c. набор после правки не стирает резерв «Вернуть»", st.get("text_backup") == FEM)
    box.delete("end-2c", "end-1c"); box.event_generate("<KeyRelease>", keysym="BackSpace"); yield 0.6
    G()["restore"](); yield 0.8
    check("8. «Вернуть» → прежний текст, резерв убран", text() == FEM and "text_backup" not in st)
    # 429 от Groq → локальная замена, текст цел до «Применить»
    NET["mode"] = "429"
    G()["pick_gender"]("не задан"); yield 0.2
    G()["buttons"]["male"].invoke(); yield idle; yield 0.2
    check("9. Groq 429 → предпросмотр с локальной заменой, сказано почему, текст не тронут",
          G()["head"].cget("text").startswith("Groq не помог (Groq: превышен лимит запросов (429)) — использована "
                                              "локальная замена") and text() == FEM,
          G()["head"].cget("text"))
    G()["apply"](); yield 1.0
    check("9b. после «Применить» — мужской род, как у Groq", text() == MASC)
    G()["pick_gender"]("женский"); yield 0.2
    check("9c. пол голоса женский, текст мужской → кнопка «к женскому», предупреждение",
          set(visible_buttons()) == {"female"} and "текст в мужском роде" in G()["warn"].cget("text"))
    # Только русский
    G()["lang"].set("Английский"); yield 0.8
    check("10. язык «Английский» → строка рода, кнопки и подсветка скрыты",
          not shown(G()["row"]) and not tag_words("gender_m") and not shown(G()["warn"]))
    G()["lang"].set("Авто"); yield 0.8
    check("10b. «Авто» с кириллицей → снова видно", shown(G()["row"]))
    log = open(app.LOG_FILE_PATH, encoding="utf-8").read()
    check("11. в лог не попали слова текста", not any(x in log for x in ("демонстрацию", "Прошла", "получил ")))
    check("11b. POST — только в подставной Groq (фоновые GET при запуске перехвачены, наружу не ушли)",
          not NET["other"], f"перехвачено GET: {len(NET['blocked_get'])}")
    check("12. исключений нет", not errors and "ИСКЛЮЧЕНИЕ" not in log, (errors[:1] or [""])[0][-300:])
    w._on_close()


def walk(x):
    yield x
    for c in x.winfo_children():
        yield from walk(c)


def phase2():
    yield 1.2
    w.show_voice_gen_panel(); yield 1.0
    st = w._panel_state["voice_gen"]
    check("R1. после перезапуска: «Текст изменён (род). Вернуть» на месте",
          text() == MASC and st.get("text_backup") == FEM and shown(G()["restore_label"].master)
          and G()["restore_label"].cget("text") == "Текст изменён (род).")
    check("R2. пол голоса, выбранный пользователем, сохранён по голосу", G()["vgender_menu"].get() == "женский"
          and shown(G()["warn"]))
    G()["restore"](); yield 0.8
    check("R3. «Вернуть» после перезапуска → текст до правки", text() == FEM)
    # Метка пола из ElevenLabs
    voices = w.fetch_all_voices()
    w._vg["voice_box"].delete("1.0", "end"); w._vg["voice_box"].insert("1.0", "Анна (диктор)")
    w._panel_state["voice_gen"]["voice_id"] = ""
    w._vg["voice_box"]._textbox.focus_force(); w.update()
    w._vg["voice_box"]._textbox.event_generate("<KeyRelease>", keysym="a"); yield 0.3
    check("R4. labels.gender из списка голосов → «женский (метка ElevenLabs)», выбора нет",
          voices[0]["gender"] == "female" and shown(G()["vgender_info"]) and not shown(G()["vgender_menu"])
          and G()["vgender_info"].cget("text") == "женский (метка ElevenLabs)",
          f"{G()['vgender_info'].cget('text')!r} info={shown(G()['vgender_info'])} menu={shown(G()['vgender_menu'])} "
          f"sel={w._selected_generator_voice()} for={w._voice_gender_for(*w._selected_generator_voice())}")
    check("R5. клон без метки → пол не задан (метка пустая)", w._voice_gender_for("cloneNoLabel00000002", "")[0] is None)
    # Пол, выбранный при клонировании
    w._on_clone_finished({"status": "done", "meta": {"name": "Клон Н", "gender": "male"},
                          "result": {"voice_id": "cloneVoiceId00000009", "name": "Клон Н", "gender": "male"}})
    yield 0.8
    check("R6. при клонировании пол подставляется для нового voice_id",
          w._voice_gender_for("cloneVoiceId00000009", "Клон Н")[:2] == ("male", "user"))
    w.show_settings_panel(); yield 0.5
    texts = [x.cget("text") for x in walk(w._panels["settings"]) if isinstance(x, (ctk.CTkLabel, ctk.CTkSwitch))]
    menus = [x for x in walk(w._panels["settings"]) if isinstance(x, ctk.CTkOptionMenu)
             and set(x.cget("values")) == set(app.REGENDER_MODES)]
    check("R8. «Настройки → Приложение»: «Подсвечивать род текста» и «Править род текста: Groq / Только локально»",
          any("Подсвечивать род текста" in t for t in texts) and menus
          and menus[0].get() == "Groq (текст уходит в Groq)" and any(t.startswith("По умолчанию: Groq") for t in texts))
    w.show_voice_clone_panel(); yield 0.4
    cl = [x for x in walk(w._panels["voice_clone"]) if isinstance(x, ctk.CTkOptionMenu)
          and set(x.cget("values")) == set(app.VOICE_GENDER_CHOICES)]
    check("R9. «Клонировать»: выбор «Пол голоса», по умолчанию «не задан»", cl and cl[0].get() == "не задан")
    check("R7. исключений нет, в сеть ничего лишнего", not errors and not NET["other"], (errors[:1] or [""])[0][-300:])
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
