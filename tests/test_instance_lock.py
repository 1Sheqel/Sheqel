"""Замок одного экземпляра: устаревший замок, сбой pid_alive, замок старше суток, «запустить всё равно».
Временная папка вместо ~/.sheqelmotion; окно проверяется нажатием кнопки из кода."""
import os, sys, json, time, tempfile
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

D = tempfile.mkdtemp()
LOCK = os.path.join(D, "instance.lock")
LIVE = os.getppid()                         # живой чужой процесс

def put(pid, age=0.0):
    with open(LOCK, "w") as f:
        json.dump({"pid": pid, "time": time.time() - age, "version": "1.3.0"}, f)
def owner():
    with open(LOCK) as f:
        return json.load(f)["pid"]
def acquire():
    with patch.object(app, "instance_lock_path", lambda: LOCK):
        return app.acquire_instance_lock(wait_sec=0)

put(LIVE)
ok, info = acquire()
check("1. живая копия, свежий замок → второй экземпляр не запускается", not ok and info["pid"] == LIVE)

def boom(pid): raise RuntimeError("ctypes сломан")
put(LIVE)
with patch.object(app, "pid_alive", boom):
    ok, info = acquire()
check("2. pid_alive выбросил исключение → замок устаревший, забираем", ok and owner() == os.getpid())

put(LIVE, age=25 * 3600)
ok, info = acquire()
check("3. замок старше 24 часов игнорируется", ok and owner() == os.getpid())

put(LIVE, age=23 * 3600)
ok, info = acquire()
check("4. замок моложе 24 часов с живым процессом — по-прежнему держит", not ok)

put(999999)
ok, info = acquire()
check("5. процесса нет → замок забран", ok and owner() == os.getpid())

with open(LOCK, "w") as f: f.write("{сломан")
ok, info = acquire()
check("6. повреждённый файл замка → забран", ok and owner() == os.getpid())

texts = []
check("7. «Это ошибка, запустить всё равно» → True",
      app.notify_already_running({"pid": LIVE}, ask=lambda t: texts.append(t) or True) is True
      and app.RUN_ANYWAY_LABEL in texts[0])
check("8. «Закрыть» → False", app.notify_already_running({"pid": LIVE}, ask=lambda t: False) is False)
def broken(t): raise RuntimeError("нет дисплея")
check("9. окно не открылось → False, без падения", app.notify_already_running(None, ask=broken) is False)

put(LIVE)
with patch.object(app, "instance_lock_path", lambda: LOCK):
    app.take_instance_lock()
check("10. take_instance_lock переписывает замок на нас, права 0600",
      owner() == os.getpid() and (os.stat(LOCK).st_mode & 0o777) == 0o600)

# 11. настоящее окно: находим кнопку по тексту и нажимаем
import tkinter as tk
def press(label):
    orig = tk.Tk.mainloop
    def auto(root, *a):
        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, tk.Button) and c.cget("text") == label:
                    return c
                found = walk(c)
                if found: return found
        root.after(300, lambda: walk(root).invoke())
        orig(root, *a)
    with patch.object(tk.Tk, "mainloop", auto):
        return app._ask_run_anyway("SheqelMotion уже открыт.")
check("11. окно: кнопка «Это ошибка, запустить всё равно» → True, «Закрыть» → False",
      press(app.RUN_ANYWAY_LABEL) is True and press("Закрыть") is False)

print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
