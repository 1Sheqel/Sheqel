"""Запуск очереди: готовые сцены пропускаются, «В очереди» и «Обработка» не берутся повторно, «Запустить заново»."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

def roll(i, status, mode="1 видео"):
    r = {"id": i, "mode": mode, "voice_file": f"/v{i}.mp3", "text": "", "video_single": f"/s{i}.mp4",
         "video_start": "", "video_end": "", "status": status, "face_mode": "default", "face_points": {}}
    return r

App = app.LipsyncTwoModeApp
fake = types.SimpleNamespace(rolls=[roll(1, "Готово"), roll(2, "Ожидает"), roll(3, "Ошибка"), roll(4, "В очереди"),
                                    roll(5, "Обработка 2"), roll(6, "Прервано"),
                                    {"id": 7, "mode": "1 видео", "voice_file": "", "text": "", "video_single": "",
                                     "video_start": "", "video_end": "", "status": "Ожидает"}])
fake.queue_candidates = types.MethodType(App.queue_candidates, fake)
valid, errors, skipped = App.queue_candidates(fake)
check("1. к запуску — «Ожидает», «Ошибка», «Прервано»; пустая сцена не считается",
      [r["id"] for _, r in valid] == [2, 3, 6] and not errors, str([r["id"] for _, r in valid]))
check("2. готовая сцена пропущена и посчитана", [r["id"] for _, r in skipped] == [1])
check("3. «В очереди» и «Обработка N» не берутся повторно", not any(r["id"] in (4, 5) for _, r in valid))
check("4. validate_rolls — те же к запуску", [r["id"] for _, r in App.validate_rolls(fake)[0]] == [2, 3, 6])
check("5. блокировка правки: «В очереди» и «Обработка N» — да, «Готово» и «Ожидает» — нет",
      app.roll_in_queue(roll(0, "В очереди")) and app.roll_in_queue(roll(0, "Обработка 3"))
      and not app.roll_in_queue(roll(0, "Готово")) and not app.roll_in_queue(roll(0, "Ожидает")))
calls = []
fake.render_scene_list = fake.render_scene_detail = fake.update_status = lambda: calls.append(1)
fake.log = lambda m: None
fake.get_roll_title = lambda i, r: f"Сцена {i + 1}"
App.rerun_roll(fake, 1)
check("6. «Запустить заново» → «Ожидает», сцена снова к запуску",
      fake.rolls[0]["status"] == "Ожидает" and [r["id"] for _, r in App.validate_rolls(fake)[0]] == [1, 2, 3, 6])
print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
