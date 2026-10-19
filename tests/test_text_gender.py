"""Род текста: анализатор на размеченном наборе, локальная замена, проверка ответа модели,
Groq на подставных ответах (без сети), отчёт об ошибке без текста."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""
F, M, X, U = app.GENDER_FEMALE, app.GENDER_MALE, app.GENDER_MIXED, app.GENDER_UNKNOWN
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

EXAMPLE = ("Прошла короткий тест, получила расчёт и запустила демонстрацию. После этого AI начал анализировать "
           "акции. Я решила посмотреть. Мне не пришлось выбирать.")
# (группа, текст, ожидаемый род, ожидаемые слова говорящего или None — не проверять)
DATASET = [
    ("женский", EXAMPLE, F, ["Прошла", "получила", "запустила", "решила"]),
    ("женский", "Я пришла домой и сразу легла спать.", F, ["пришла", "легла"]),
    ("женский", "Я была рада вас видеть.", F, ["была", "рада"]),
    ("женский", "Сегодня я поняла, что ошиблась.", F, ["поняла", "ошиблась"]),
    ("женский", "Я не смогла дозвониться и очень расстроилась.", F, ["смогла", "расстроилась"]),
    ("женский", "Рада сообщить, что я готова начать.", F, ["Рада", "готова"]),
    ("женский", "Я сама не ожидала такого результата.", F, ["сама", "ожидала"]),
    ("женский", "Купила новый ноутбук, установила программу и запустила тест.", F,
     ["Купила", "установила", "запустила"]),
    ("женский", "Вчера ходила в спортзал. Потом встретила подругу и пошла в кафе.", F,
     ["ходила", "встретила", "пошла"]),
    ("женский", "Я пришла, увидела, победила. Потом сделала всё. Я устал.", F, None),
    ("женский", "Это была ошибка, но я справилась и сделала выводы.", F, ["справилась", "сделала"]),
    ("мужской", "Я пришёл домой и сразу лёг спать.", M, ["пришёл", "лёг"]),
    ("мужской", "Я был рад вас видеть.", M, ["был", "рад"]),
    ("мужской", "Сегодня я понял, что ошибся.", M, ["понял", "ошибся"]),
    ("мужской", "Я не смог дозвониться и очень расстроился.", M, ["смог", "расстроился"]),
    ("мужской", "Рад сообщить, что я готов начать.", M, ["Рад", "готов"]),
    ("мужской", "Я сам не ожидал такого результата.", M, ["сам", "ожидал"]),
    ("мужской", "Купил новую камеру, установил программу и запустил тест.", M, ["Купил", "установил", "запустил"]),
    ("мужской", "Я должен признаться: я уверен в этом проекте.", M, ["должен", "уверен"]),
    ("мужской", "Проснулся рано, позавтракал и поехал в офис.", M, ["Проснулся", "позавтракал", "поехал"]),
    ("мужской", "Вчера ходил в спортзал. Потом встретил друга и пошёл в кафе.", M, ["ходил", "встретил", "пошёл"]),
    ("смешанный", "Я пришла на встречу. Я был уверен, что всё пройдёт хорошо.", X, ["пришла", "был", "уверен"]),
    ("смешанный", "Я решила начать. Я понял, что это ошибка.", X, ["решила", "понял"]),
    ("смешанный", "Я готова. Я рад. Я смогла. Я смог.", X, None),
    ("третье лицо", "Она решила уйти и закрыла дверь.", U, []),
    ("третье лицо", "Анна решила уйти и закрыла дверь.", U, []),
    ("третье лицо", "Мама позвонила и сказала, что приедет.", U, []),
    ("третье лицо", "Позвонила мама и попросила купить хлеб.", U, []),
    ("третье лицо", "Компания выросла, а потом закрылась.", U, []),
    ("третье лицо", "Он сказал, что я права.", U, ["права"]),
    ("множественное", "Мы решили попробовать и запустили проект.", U, []),
    ("множественное", "Я и мама пошли гулять, потом вернулись домой.", U, []),
    ("множественное", "Мы с братом купили билеты.", U, []),
    ("средний род", "Мне не пришлось выбирать, всё получилось само.", U, []),
    ("средний род", "Было холодно, и стало темно.", U, []),
    ("ловушки-существительные", "Школа была закрыта, стол стоял в углу.", U, []),
    ("ловушки-существительные", "Игла упала на пол.", U, []),
    ("ловушки-существительные", "Скала казалась огромной.", U, []),
    ("ловушки-существительные", "В зале стоял стол, у стола сидела кошка.", U, []),
    ("ловушки-существительные", "Прошла неделя, наступила осень.", U, []),
    ("ловушки-существительные", "Прошёл год, и всё изменилось.", U, []),
    ("ловушки-существительные", "Пришла мысль позвонить.", U, []),
    ("ловушки-существительные", "Был поздний вечер, шёл дождь.", U, []),
    ("ловушки-существительные", "Модель показала хороший опыт.", U, []),
    ("прилагательные", "Я очень довольна результатом и благодарна команде.", F, ["довольна", "благодарна"]),
    ("прилагательные", "Я новый сотрудник, я пока мало знаю.", U, ["новый"]),
    ("прилагательные", "Я такая уставшая сегодня.", F, ["такая", "уставшая"]),
    ("прилагательные", "Я такой счастливый сегодня.", M, ["такой", "счастливый"]),
    ("отрицания", "Я не была уверена и не стала рисковать.", F, ["была", "уверена"]),
    ("отрицания", "Я не знал и не хотел знать.", M, ["знал", "хотел"]),
    ("прямая речь", "«Я пришла», — сказала Анна.", U, []),
    ("прямая речь", "Он ответил: «Я готов», и ушёл.", U, []),
    ("прямая речь", "— Я пришла, — сказала она.", U, ["пришла"]),
]

# ── 1. Таблица ожидалось / получилось ──
print(f"{'№':>3} {'группа':<24} {'ожидалось':<12} {'получилось':<12} слова")
ok_gender = ok_words = 0
for i, (group, text, exp, exp_words) in enumerate(DATASET, 1):
    r = app.analyze_text_gender(text)
    got_words = [w["word"] for w in r["words"]]
    g_ok = r["gender"] == exp
    w_ok = exp_words is None or got_words == exp_words
    ok_gender += g_ok
    ok_words += w_ok
    mark = "✓" if g_ok and w_ok else "✗"
    print(f"{i:>3} {group:<24} {app.GENDER_TEXT_LABELS[exp]:<12} {app.GENDER_TEXT_LABELS[r['gender']]:<12} "
          f"{mark} {', '.join(got_words) or '—'}" + ("" if w_ok else f"  (ожидались: {', '.join(exp_words) or '—'})"))
n = len(DATASET)
print(f"Род: {ok_gender}/{n} ({ok_gender / n:.0%}); слова говорящего: {ok_words}/{n} ({ok_words / n:.0%})")
check(f"1. размеченный набор: {n} предложений (≥ 40)", n >= 40)
check("1b. род верен во всех предложениях набора", ok_gender == n, f"{ok_gender}/{n}")
check("1c. слова говорящего верны во всех предложениях набора", ok_words == n, f"{ok_words}/{n}")
r = app.analyze_text_gender(EXAMPLE)
check("1d. пример из задания: «начал» (AI) и «пришлось» не считаются",
      all(w["word"] not in ("начал", "пришлось") for w in r["words"]) and r["gender"] == F)
check("1e. позиции слов указывают на сами слова",
      all(EXAMPLE[w["start"]:w["end"]] == w["word"] for w in r["words"]))

# ── 1f. Контрольный набор: предложения, на которых правила не подбирались ──
# Пропуск («не определён» вместо рода) допустим — точность важнее полноты; неверный род — нет.
HOLDOUT = [
    ("Всю неделю готовилась к экзамену и в итоге сдала его на отлично.", F),
    ("Я долго думал, стоит ли менять работу, и наконец решился.", M),
    ("Когда я впервые увидела этот город, я влюбилась в него.", F),
    ("Мой брат купил машину, а я купила велосипед.", U),
    ("Признаюсь честно: я испугался, когда увидел цифры.", M),
    ("Наступила зима, выпал снег, и стало холодно.", U),
    ("Сначала я не поверила, но потом проверила всё сама.", F),
    ("Директор сказал, что проект закрыт, и я расстроилась.", U),
    ("Открыл приложение, ввёл данные и получил результат за минуту.", M),
    ("Она позвонила мне и сказала, что всё готово.", U),
    ("Я была в шоке, когда узнала цену.", F),
    ("Решил попробовать новую стратегию и не пожалел.", M),
    ("Команда работала всю ночь, а я спал.", U),
    ("Я не думала, что это так просто, но справилась за час.", F),
    ("Подруга посоветовала этот сервис, и я осталась довольна.", F),
    ("Сегодня я узнал, что мой отчёт приняли.", U),
    ("Пришёл, увидел, победил.", M),
    ("Пришла весна, и всё вокруг ожило.", U),
    ("Я сделал выбор и ни разу не пожалел об этом.", M),
    ("Когда мне было десять, я мечтала стать врачом и много читала.", F),
]
print("Контрольный набор:")
h_ok = h_wrong = h_missed = 0
for text, exp in HOLDOUT:
    r = app.analyze_text_gender(text)
    got = r["gender"]
    if got == exp:
        h_ok += 1; mark = "✓"
    elif got == U or (exp == U and got not in (F, M)):
        h_missed += 1; mark = "пропуск"
    else:
        h_wrong += 1; mark = "✗ НЕВЕРНО"
    print(f"  {mark:<9} {app.GENDER_TEXT_LABELS[exp]:<12} {app.GENDER_TEXT_LABELS[got]:<12} "
          f"{', '.join(w['word'] for w in r['words']) or '—'} | {text[:55]}")
print(f"Контрольный набор: верно {h_ok}/{len(HOLDOUT)}, пропусков {h_missed}, неверный род {h_wrong}")
check("1f. контрольный набор: ни одного неверного рода (пропуски допустимы)", h_wrong == 0,
      f"верно {h_ok}/{len(HOLDOUT)}, пропусков {h_missed}")

# ── 2. Только русский текст ──
check("2. индикатор: русский/авто с кириллицей — да; английский, авто без кириллицы, украинский — нет",
      app.text_gender_applicable("Я пришла", "Русский") and app.text_gender_applicable("Я пришла", "Авто")
      and not app.text_gender_applicable("Я пришла", "Английский")
      and not app.text_gender_applicable("I came home", "Авто")
      and not app.text_gender_applicable("Я прийшла додому, її не було", "Авто"))
check("2b. сводка слов: первые 4 и «ещё N»",
      app.gender_words_summary([{"word": w} for w in "а б в г д е".split()]) == "а, б, в, г · ещё 2")

# ── 3. Локальная замена: таблица исключений ──
PAIRS = [("ушла", "ушёл"), ("смогла", "смог"), ("нашла", "нашёл"), ("пришла", "пришёл"), ("пошла", "пошёл"),
         ("ждала", "ждал"), ("легла", "лёг"), ("вышла", "вышел"), ("привыкла", "привык"), ("принесла", "принёс"),
         ("ошиблась", "ошибся"), ("увлеклась", "увлёкся"), ("решилась", "решился"), ("рада", "рад"),
         ("должна", "должен"), ("согласна", "согласен"), ("сама", "сам"), ("довольна", "доволен"),
         ("счастливая", "счастливый"), ("такая", "такой"), ("большая", "большой"), ("маленькая", "маленький"),
         ("последняя", "последний"), ("замужем", "женат"), ("Пришла", "Пришёл"), ("сделала", "сделал")]
bad = [(f, m, app.regender_word(f, M), app.regender_word(m, F)) for f, m in PAIRS
       if app.regender_word(f, M) != m or app.regender_word(m, F) != f]
print(f"{'женский':<12} ⇄ мужской")
for f, m in PAIRS:
    print(f"  {f:<12} ⇄ {m}")
check(f"3. таблица исключений локальной замены: {len(PAIRS)} пар в обе стороны", not bad, str(bad))

# ── 4. Эталонные пары текстов ──
REF = [
    (EXAMPLE, "Прошёл короткий тест, получил расчёт и запустил демонстрацию. После этого AI начал анализировать "
              "акции. Я решил посмотреть. Мне не пришлось выбирать."),
    ("Я пришла домой и сразу легла спать. Я была рада.", "Я пришёл домой и сразу лёг спать. Я был рад."),
    ("Я сама не ожидала, но смогла. Я очень довольна!", "Я сам не ожидал, но смог. Я очень доволен!"),
    ("[смеётся] Я такая счастливая!\n\nЯ нашла работу и вышла замуж.",
     "[смеётся] Я такой счастливый!\n\nЯ нашёл работу и вышел замуж."),
]
for k, (fem, masc) in enumerate(REF, 1):
    to_m, _ = app.regender_local(fem, M)
    to_f, _ = app.regender_local(masc, F)
    check(f"4.{k} эталон: женский → мужской и обратно (локально)", to_m == masc and to_f == fem,
          f"→ {to_m!r} / {to_f!r}" if (to_m != masc or to_f != fem) else "")

# ── 5. Проверка ответа модели ──
fem, masc = REF[0]
ok, reason, ch = app.validate_regender(fem, masc, M)
check("5a. правильный ответ принят, изменено 4 слова", ok and len(ch) == 4, reason)
cases = {
    "лишнее изменённое слово (не о говорящем)": masc.replace("AI начал", "AI начала"),
    "добавлено слово": masc.replace("Я решил", "Я тогда решил"),
    "удалено слово": masc.replace("короткий ", ""),
    "изменён знак препинания": masc.replace("демонстрацию.", "демонстрацию!"),
    "изменено не только окончание": masc.replace("получил", "взял"),
}
for name, cand in cases.items():
    ok, reason, _ = app.validate_regender(fem, cand, M)
    check(f"5b. отклонено: {name}", not ok, reason)
tag_src = "[шёпотом] Я пришла.\n\nЯ была рада."
ok1, r1, _ = app.validate_regender(tag_src, "[шепотом] Я пришёл.\n\nЯ был рад.", M)
ok2, r2, _ = app.validate_regender(tag_src, "[шёпотом] Я пришёл.\nЯ был рад.", M)
ok3, r3, _ = app.validate_regender(tag_src, "[шёпотом] Я пришёл.\n\nЯ был рада.", M)
check("5c. отклонено: изменён тег [ ]; потерян абзац; результат не мужской", not ok1 and not ok2 and not ok3,
      f"{r1} | {r2} | {r3}")

# ── 6. Groq на подставных ответах ──
class Resp:
    def __init__(self, code, content=None, headers=None):
        self.status_code, self._content, self.headers = code, content, headers or {}
    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}

def fake_post(responses, calls):
    def post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout, "auth": headers.get("Authorization", "")[:7]})
        r = responses.pop(0) if responses else Resp(500)
        if isinstance(r, Exception):
            raise r
        return r(json) if callable(r) else r
    return post

sleeps = []
KEY = "-".join(("fake", "groq", "key"))
calls = []
res = app.regender_text(fem, M, KEY, post=fake_post([Resp(200, masc)], calls), sleep=sleeps.append)
body = calls[0]["json"]
check("6a. Groq: правильный ответ → источник groq, 4 изменения", res["source"] == "groq" and len(res["changes"]) == 4)
check("6b. запрос: модель переводчика, temperature 0, таймаут, правка только формы «я», только текст в ответе",
      body["model"] == app.GROQ_TRANSLATE_MODEL and body["temperature"] == 0 and calls[0]["timeout"] == 60
      and "ТОЛЬКО грамматический род" in body["messages"][0]["content"]
      and "Верни только итоговый текст" in body["messages"][0]["content"])
calls = []
res = app.regender_text(fem, M, KEY, post=fake_post([Resp(200, cases["лишнее изменённое слово (не о говорящем)"])],
                                                   calls), sleep=sleeps.append)
check("6c. Groq изменил лишнее слово → отклонено, локальная замена, результат как эталон",
      res["source"] == "local" and res["text"] == masc and "не прошёл проверку" in res["note"], res["note"])
res = app.regender_text(fem, M, KEY, post=fake_post([Resp(200, cases["добавлено слово"])], []), sleep=sleeps.append)
check("6d. Groq добавил слово → локальная замена", res["source"] == "local" and res["text"] == masc)
res = app.regender_text(tag_src, M, KEY, post=fake_post([Resp(200, "[шепотом] Я пришёл.\n\nЯ был рад.")], []),
                        sleep=sleeps.append)
check("6e. Groq изменил тег → локальная замена, тег цел", res["source"] == "local" and res["text"].startswith("[шёпотом]"))
res = app.regender_text(tag_src, M, KEY, post=fake_post([Resp(200, "[шёпотом] Я пришёл. Я был рад.")], []),
                        sleep=sleeps.append)
check("6f. Groq потерял абзац → локальная замена, абзацы целы", res["source"] == "local" and "\n\n" in res["text"])
sleeps.clear(); calls = []
res = app.regender_text(fem, M, KEY, post=fake_post([ConnectionError("нет сети")] * 3, calls), sleep=sleeps.append)
check("6g. Groq недоступен → локальная замена, текст цел, сказано почему",
      res["source"] == "local" and res["text"] == masc and res["note"] == "Groq недоступен" and len(calls) == 3)
sleeps.clear(); calls = []
r429 = Resp(429, headers={"retry-after": "120"})
res = app.regender_text(fem, M, KEY, post=fake_post([r429, r429, r429], calls), sleep=sleeps.append)
check("6h. 429 трижды → локальная замена, пауза не больше 10 с", res["source"] == "local" and len(calls) == 3
      and sleeps == [10, 10] and "429" in res["note"], f"{sleeps} {res['note']}")
calls = []
res = app.regender_text(fem, M, KEY, post=fake_post([r429, Resp(200, masc)], calls), sleep=lambda s: None)
check("6i. 429, затем успех → Groq", res["source"] == "groq" and len(calls) == 2)
calls = []
res = app.regender_text(fem, M, "", post=fake_post([], calls))
check("6j. нет ключа Groq → локально, запросов нет", res["source"] == "local" and not calls
      and res["note"] == "ключ Groq не задан")
calls = []
res = app.regender_text(fem, M, KEY, use_groq=False, post=fake_post([], calls))
check("6k. «только локально» → запросов нет, примечания нет", res["source"] == "local" and not calls and not res["note"])
res = app.regender_text(fem, M, KEY, post=fake_post([Resp(401)], []))
check("6l. 401 → локально, «ключ Groq не подошёл»", res["source"] == "local" and "ключ Groq не подошёл" in res["note"])
res = app.regender_text(fem, M, KEY, post=fake_post([Resp(200, "```\n" + masc + "\n```")], []))
check("6m. ответ в ``` → обёртка снята, принят", res["source"] == "groq" and res["text"] == masc)
res = app.regender_text(masc, M, KEY, post=fake_post([], []))
check("6n. текст уже в нужном роде → менять нечего", res["source"] == "none" and res["text"] == masc)

# ── 7. Длинный текст: части до 4000 символов по абзацам ──
para = ("Сегодня я пришла в офис рано. " + "Работа шла своим чередом, коллеги обсуждали планы. " * 18).strip()
long_text = "\n\n".join([para] * 9)
chunks = app.split_regender_chunks(long_text)
check(f"7a. {len(long_text)} символов → {len(chunks)} частей ≤ 4000, склейка = исходник, границы по абзацам",
      len(long_text) > 8000 and all(len(c) <= 4000 for c in chunks) and "".join(chunks) == long_text
      and all(c.endswith("\n\n") for c in chunks[:-1]))
calls = []
def echo_male(body):
    src = body["messages"][1]["content"]
    return Resp(200, src.replace("я пришла", "я пришёл"))
res = app.regender_text(long_text, M, KEY, post=fake_post([echo_male] * len(chunks), calls))
check("7b. Groq получает каждую часть отдельно (≤ 4000), результат собран и принят",
      len(calls) == len(chunks) and all(len(c["json"]["messages"][1]["content"]) <= 4000 for c in calls)
      and res["source"] == "groq" and res["text"] == long_text.replace("я пришла", "я пришёл"))
huge = "Я пришла. " * 600
check("7c. один абзац длиннее 4000 → части по предложениям",
      all(len(c) <= 4000 for c in app.split_regender_chunks(huge)) and "".join(app.split_regender_chunks(huge)) == huge)

# ── 8. Отчёт об ошибке: только код и этап, без текста ──
secret_text = "Я пришла к Марии Ивановой на Тверскую 15"
err = app.RegenderGroqError("http", 400)
payload = app.error_reporter.build_payload(app.regender_report_where("groq"), type(err).__name__,
                                          app.regender_report_message("groq", err))
dump = repr(payload)
check("8. отчёт по сбою правки рода: этап в where, код в message, слов текста нет",
      payload["where"] == "app.regender.groq" and "400" in payload["message"]
      and not any(w in dump for w in ("пришла", "Марии", "Тверскую")), payload["message"])
check("8b. текст ошибки Groq не содержит ответа сервиса", str(err) == "Groq ответил ошибкой (HTTP 400)")

print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
