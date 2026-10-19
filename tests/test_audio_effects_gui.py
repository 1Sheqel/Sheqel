"""Эффекты звука в настоящем окне генератора: блок скрыт без каталога, каталог в фоне и кэш, выбор эффектов,
A/B («только B» по умолчанию), 404 на эффекте, «Проверить эффект» с кэшем, пресеты в config, предупреждения,
проверка пауз до запуска очереди. ElevenLabs подставной, сети нет. argv: фаза 1 | 2."""
import sys, os, json, time, shutil, threading, traceback, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import customtkinter as ctk
from tkinter import messagebox

PHASE = sys.argv[1]
SCR = os.path.join(__import__("tempfile").gettempdir(), "sheqel_tests", "fxgui")
if PHASE == "1":
    shutil.rmtree(SCR, ignore_errors=True)
    os.makedirs(SCR)
    json.dump({"elevenlabs_api_key": "-".join(("fake", "eleven", "key")), "main_voice_name": "Диктор",
               "spellcheck_online": False, "error_reports_notice_shown": True},
              open(os.path.join(SCR, "config.json"), "w"), ensure_ascii=False)
app.CONFIG_PATH = os.path.join(SCR, "config.json")
app.STATE_PATH = os.path.join(SCR, "data", "state.json")
app.VOICE_DATA_DIR = os.path.join(SCR, "data", "voices")
app.LOG_FILE_PATH = os.path.join(SCR, "app.log")
app.FACE_POINTS_PATH = os.path.join(SCR, "faces.json")
app.APP_DATA_DIR = os.path.join(SCR, "appdata")         # кэш каталога — во временной папке
app.ERROR_REPORT_URL = ""
app.fetch_elevenlabs_balance = lambda key: None
threading.excepthook = app._thread_excepthook
msgs = []
for name in ("showinfo", "showwarning", "showerror"):
    setattr(messagebox, name, (lambda n: lambda title, text, **k: msgs.append((n, title, text)))(name))
messagebox.askyesno = lambda *a, **k: True

CATALOG = {
    "filter_presets": [{"filter_preset_id": "old_radio", "name": "Old Radio", "description": "Lo-fi radio"},
                       {"filter_preset_id": "phone", "name": "Phone", "description": None}],
    "environments": [{"environment_id": "small_room", "name": "Small Room", "description": "Tight space"},
                     {"environment_id": "hall", "name": "Hall", "description": None}],
    "background_noises": [{"background_noise_id": "cafe", "name": "Café", "description": None}],
}
NET = {"catalog": 0, "tts": [], "other": [], "blocked_get": [], "mode": "ok"}
FAKE_MP3 = b"ID3" + b"\0" * 8192


class Resp:
    def __init__(self, code, data=None, text="", content=b""):
        self.status_code, self._data, self.text, self.content = code, data, text, content
        self.headers = {}
    def json(self):
        return self._data


def fake_get(url, headers=None, timeout=None, **kw):
    if url.endswith("/v1/audio-effects/catalog"):
        NET["catalog"] += 1
        return Resp(200, CATALOG)
    if url.endswith("/v1/voices"):
        return Resp(200, {"voices": [{"voice_id": "DiktorVoiceId0000001", "name": "Диктор", "category": "premade",
                                      "labels": {"gender": "male"}}]})
    NET["blocked_get"].append(url.split("?")[0])      # фоновые проверки при запуске: перехвачены
    raise RuntimeError("сеть в тесте запрещена")


def fake_post(url, headers=None, json=None, timeout=None, **kw):
    if "/v1/text-to-speech/" not in url:
        NET["other"].append(url.split("?")[0])
        raise RuntimeError("сеть в тесте запрещена")
    NET["tts"].append({"url": url, "json": json})
    if NET["mode"] == "404" and json.get("audio_effects"):
        return Resp(404, text='{"detail":{"status":"not_found","param":"audio_effects.environment_id"}}')
    return Resp(200, content=FAKE_MP3)


app.requests.get = fake_get
app.requests.post = fake_post
_real_duration = app.get_duration
app.get_duration = lambda p: 1.0 if str(p).endswith(".mp3") else _real_duration(p)
PLAYED = []
class FakeProc:
    def __init__(self, args, *a, **k):
        PLAYED.append(args)
    def poll(self):
        return 0
    def terminate(self):
        pass
    def wait(self):
        return 0

w = app.LipsyncTwoModeApp()
errors = []
w.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
def FX():
    return w._vg["fx"]
def shown(widget):
    return bool(widget.winfo_manager())
def gen_btn():
    return next(x for x in walk(w._panels["voice_gen"]) if isinstance(x, ctk.CTkButton)
                and x.cget("text") == "⚡ Сгенерировать A и B")
def walk(x):
    yield x
    for c in x.winfo_children():
        yield from walk(c)
def gen_idle():
    return not w.background_tasks.active(kind="voice_gen")
def bodies_since(n):
    return [b["json"] for b in NET["tts"][n:]]
def set_text(t):
    box = w._vg["text_box"]
    box.delete("1.0", "end")
    box.insert("1.0", t)
    w._vg["save_fields"]()


def phase1():
    yield 0.8
    w.show_voice_gen_panel(); yield 0.3
    check("1. каталога нет (ни кэша, ни ответа) → блок «Эффекты звука» скрыт", not shown(FX()["section"]))
    w._refresh_effects_catalog_async(force=True); yield lambda: shown(FX()["section"]); yield 0.2
    cache = os.path.join(app.APP_DATA_DIR, app.AUDIO_EFFECTS_CACHE_NAME)
    data = json.load(open(cache, encoding="utf-8"))
    check("2. каталог получен в фоне → блок виден, свёрнут; кэш с датой, права 0600",
          shown(FX()["section"]) and not shown(FX()["body"]) and data["fetched_at"] > 0
          and (os.stat(cache).st_mode & 0o777) == 0o600 and NET["catalog"] == 1)
    log = open(app.LOG_FILE_PATH, encoding="utf-8").read()
    check("2b. ключ ElevenLabs нигде в логе", "fake-eleven-key" not in log)
    FX()["toggle"](); yield 0.2
    check("3. развернул: меню из каталога в его порядке, первым — «— нет —»",
          shown(FX()["body"]) and FX()["menus"]["filter"].cget("values") == ["— нет —", "Old Radio", "Phone"]
          and FX()["menus"]["environment"].cget("values") == ["— нет —", "Small Room", "Hall"])
    FX()["menu"]("filter", "Old Radio"); FX()["menu"]("noise", "Café"); yield 0.2
    check("4. шум выбран → подсказка: громкости шума в API нет, шум может ухудшить синхронизацию губ",
          shown(FX()["noise_hint"]) and "Громкость шума в API ElevenLabs не настраивается" in
          FX()["noise_hint"].cget("text") and "синхронизацию губ" in FX()["noise_hint"].cget("text"))
    check("4b. пространство не выбрано → строки «Уровень» нет", not shown(FX()["rows"]["send_level"]))
    FX()["menu"]("environment", "Small Room"); yield 0.2
    check("5. пространство → «Уровень пространства» 50% по умолчанию",
          shown(FX()["rows"]["send_level"]) and FX()["state"]["spec"]["send_level"] == 0.5)
    FX()["slider"]("distance", 1.0, from_button=True); FX()["slider"]("pan", -0.3); yield 0.2
    check("6. «Далеко» → distance 1; панорама → подсказка про моно для Sync",
          FX()["state"]["spec"]["distance"] == 1.0 and shown(FX()["pan_hint"]))
    check("7. заголовок «(только B)» по умолчанию, сводка эффектов строкой ниже",
          FX()["header"].cget("text") == "▾ Эффекты звука (только B)" and FX()["summary"].cget("text") ==
          "Old Radio · Small Room 50% · расстояние 1 · Café · панорама Л 0.3",
          FX()["header"].cget("text") + " | " + FX()["summary"].cget("text"))
    # A/B: только B
    set_text("Привет, это проверка."); n = len(NET["tts"])
    gen_btn().invoke(); yield gen_idle; yield 0.5
    b = {("B" if "audio_effects" in j else "A"): j for j in bodies_since(n)}
    check("8. «только B»: A без audio_effects, B — с эффектами из каталога",
          len(bodies_since(n)) == 2 and set(b) == {"A", "B"} and b["B"]["audio_effects"] ==
          {"filter_preset_id": "old_radio", "environment_id": "small_room", "send_level": 0.5, "distance": 1.0,
           "background_noise_id": "cafe", "pan": -0.3})
    cards = [x.cget("text") for x in walk(w._panels["voice_gen"]) if isinstance(x, ctk.CTkLabel)
             and x.cget("text").startswith("✓ Готово")]
    check("8b. карточка B: «· с эффектами», A — без", sorted(cards) == ["✓ Готово — 1.0 сек",
                                                                        "✓ Готово — 1.0 сек · с эффектами"], str(cards))
    check("8c. счётчик символов и строка рода не пострадали",
          "21 символов" in next(x.cget("text") for x in walk(w._panels["voice_gen"]) if isinstance(x, ctk.CTkLabel)
                                  and "символов" in x.cget("text")))
    FX()["target"]("к обоим"); n = len(NET["tts"])
    gen_btn().invoke(); yield gen_idle; yield 0.5
    check("9. «к обоим» → оба запроса с эффектами", all("audio_effects" in j for j in bodies_since(n))
          and len(bodies_since(n)) == 2)
    # формат ulaw → эффекты не уходят
    app.ELEVEN_OUTPUT_FORMAT = "ulaw_8000"; n = len(NET["tts"])
    gen_btn().invoke(); yield gen_idle; yield 0.5
    app.ELEVEN_OUTPUT_FORMAT = "mp3_44100_192"
    check("10. формат ulaw_8000 → audio_effects не отправлен ни в одном запросе",
          len(bodies_since(n)) == 2 and not any("audio_effects" in j for j in bodies_since(n)))
    # 404 на эффекте
    NET["mode"] = "404"; cat_before = NET["catalog"]; text_before = w._vg["text_box"].get("1.0", "end-1c")
    gen_btn().invoke(); yield gen_idle; yield 1.0
    NET["mode"] = "ok"
    check("11. 404 на эффекте → понятная ошибка в блоке, текст не изменён, каталог перезапрошен",
          "Эффект не принят ElevenLabs: пространство (HTTP 404)" in FX()["warn"].cget("text")
          and "текст не изменён" in FX()["warn"].cget("text") and shown(FX()["warn"])
          and w._vg["text_box"].get("1.0", "end-1c") == text_before and NET["catalog"] == cat_before + 1,
          FX()["warn"].cget("text"))
    # «Проверить эффект»
    FX()["reset"](); FX()["menu"]("filter", "Phone"); yield 0.2
    check("12. подпись: сколько символов спишется", f"до {len(app.AUDIO_EFFECTS_PREVIEW_TEXT)} символов" in
          FX()["cost"].cget("text"), FX()["cost"].cget("text"))
    real_popen = app.subprocess.Popen
    app.subprocess.Popen = FakeProc
    n = len(NET["tts"])
    FX()["preview"](); yield lambda: not FX()["state"]["busy"]; yield 0.2
    first = bodies_since(n)
    FX()["preview"](); yield lambda: not FX()["state"]["busy"]; yield 0.2
    app.subprocess.Popen = real_popen
    check("13. «Проверить эффект»: одна короткая фраза с эффектом, воспроизведена",
          len(first) == 1 and first[0]["text"] == app.AUDIO_EFFECTS_PREVIEW_TEXT
          and first[0]["audio_effects"] == {"filter_preset_id": "phone"} and len(PLAYED) == 2)
    check("13b. повтор с теми же настройками — из кэша, без нового запроса",
          len(NET["tts"]) == n + 1 and "из кэша" in FX()["cost"].cget("text"))
    # пресеты
    FX()["menu"]("environment", "Hall"); FX()["preset_name"].insert(0, "Радио в зале"); FX()["save_preset"](); yield 0.2
    cfg = json.load(open(app.CONFIG_PATH, encoding="utf-8"))
    check("14. пресет сохранён в config", cfg.get("audio_effect_presets", {}).get("Радио в зале", {}).get(
        "environment") == "hall" and "Радио в зале" in FX()["preset_menu"].cget("values"))
    FX()["reset"](); yield 0.1
    reset_ok = not app.audio_effects_active(FX()["state"]["spec"])
    FX()["apply_preset"]("Радио в зале"); yield 0.1
    check("15. «Сбросить» → без эффектов; пресет → эффекты вернулись", reset_ok and
          FX()["state"]["spec"]["filter"] == "phone" and FX()["state"]["spec"]["environment"] == "hall")
    # ------ и шум → предупреждение
    FX()["menu"]("noise", "Café"); set_text("Начало ролика. ------ Конец ролика.")
    w._vg["text_box"]._textbox.focus_force(); w.update()
    w._vg["text_box"]._textbox.event_generate("<KeyRelease>", keysym="a"); yield 0.4
    check("16. «------» в тексте + шум → предупреждение о разделении (не блокирует)",
          "помешать разделению на 2 части" in FX()["warn"].cget("text") and gen_btn().cget("state") == "normal",
          FX()["warn"].cget("text"))
    # Очередь «2 видео»: паузы проверяются до Sync
    noisy = os.path.join(SCR, "noisy.wav")
    ff = app.ffmpeg_bin()
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=2", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=mono:d=3", "-f", "lavfi", "-i", "sine=f=300:d=2", "-filter_complex",
                    "[0][1][2]concat=n=3:v=0:a=1[v];anoisesrc=d=7:a=0.2:c=pink[n];[v][n]amix=inputs=2:duration=first:"
                    "normalize=0", noisy], check=True)
    estimates = []
    w._estimate_for = lambda roll, wait=False: estimates.append(roll) or {"error": "тест"}
    launched = []
    w._launch_queue = lambda *a, **k: launched.append(a)
    roll = {"id": 1, "mode": "2 видео", "text": "Начало. ------ Конец.", "voice_file": noisy,
            "video_start": "a.mp4", "video_end": "b.mp4", "video_single": ""}
    msgs.clear()
    w._confirm_queue_cost([(0, roll)]); yield lambda: w.start_btn.cget("text") == "НАЧАТЬ ОЧЕРЕДЬ"; yield 0.3
    err = next((m for m in msgs if m[0] == "showerror"), None)
    check("17. «2 видео» с шумом: ошибка до оценки и запуска, в Sync ничего не ушло",
          err and "Очередь не запущена, в Sync ничего не отправлено" in err[2]
          and "найдено паузы: 0 из 1; шум или реверберация мешают разделению, отключите эффект или разделите вручную"
          in err[2] and not estimates and not launched, err[2] if err else str(msgs))
    # Предупреждение очереди про шум в голосе (файл, сгенерированный с эффектами)
    gen_file = next(p for p in (w._panel_state.get("fx_files") or {}) if p.endswith(".mp3"))
    warnings = w._queue_fx_warnings([(0, {"voice_file": gen_file})])
    check("18. голос с шумом в очереди → предупреждение (не запрет)",
          any("фоновый шум" in x and "синхронизацию губ" in x for x in warnings), str(warnings))
    # Хвост реверберации: голос с эффектом длиннее чистого варианта той же генерации на 1.5 с
    wet = os.path.join(SCR, "wet.wav")
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=2.5", wet], check=True)
    t = w.background_tasks.create("voice_gen", "voice_gen", "Голос, вариант A", meta={"slot": "A", "batch": "tail1",
                                                                                      "fx": ""}, status="done")
    w.background_tasks.update(t, result={"path": "/нет/a.mp3", "duration": 1.0})
    w._panel_state.setdefault("fx_files", {})[os.path.abspath(wet)] = {
        "name": "wet.wav", "size": os.path.getsize(wet), "noise": False, "reverb": True, "pan": False,
        "summary": "Hall 50%", "batch": "tail1"}
    est = {"parts": [{"fps": 25.0, "cost": 2.135, "frames": 500}]}
    tail_warn = w._queue_fx_warnings([(0, {"voice_file": wet})], [est])
    check("18b. реверберация → «Хвост реверберации удлиняет аудио: ≈ +38 кадров, ≈ +$0.16 за часть»",
          tail_warn == ["Ролик 1: хвост реверберации удлиняет аудио: ≈ +38 кадров, ≈ +$0.16 за часть "
                        "(≈ 1.5 с, по сравнению с чистым вариантом)."], str(tail_warn))
    log = open(app.LOG_FILE_PATH, encoding="utf-8").read()
    check("19. в лог не попал ни ключ, ни текст", "fake-eleven-key" not in log and "Привет, это проверка" not in log)
    check("19b. POST — только в подставной ElevenLabs (фоновые GET перехвачены, наружу не ушли)",
          not NET["other"], f"перехвачено GET: {len(NET['blocked_get'])}")
    check("20. исключений нет", not errors and "ИСКЛЮЧЕНИЕ" not in log, (errors[:1] or [""])[0][-300:])
    w._on_close()


def phase2():
    yield 0.8
    w.show_voice_gen_panel(); yield 0.5
    spec = FX()["state"]["spec"]
    check("R1. после перезапуска: каталог из кэша (без запроса), блок развёрнут, эффекты и режим сохранены",
          NET["catalog"] == 0 and shown(FX()["section"]) and shown(FX()["body"]) and spec["environment"] == "hall"
          and spec["noise"] == "cafe" and FX()["state"]["target"] == "both")
    check("R2. пресет из config на месте", "Радио в зале" in FX()["preset_menu"].cget("values"))
    w.clear_saved_data(); yield 0.5
    w.show_voice_gen_panel(); yield 0.5
    check("R3. «Очистить сохранённые данные»: пресеты остались (config), эффекты поля сброшены",
          "Радио в зале" in w.audio_effect_presets and not app.audio_effects_active(FX()["state"]["spec"]))
    check("R4. исключений нет, сети нет", not errors and not NET["other"], (errors[:1] or [""])[0][-300:])
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
