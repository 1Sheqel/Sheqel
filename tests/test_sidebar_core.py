"""Помощники сайдбара без окна: показать файл в папке (Mac, Windows подменой), подписи, баланс, иконки."""
import sys, os, tempfile
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import app
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
d = tempfile.mkdtemp(prefix="лог с пробелами #1 ")
f = os.path.join(d, "SheqelMotion.log"); open(f, "w").write("x")
calls = []
app.reveal_in_file_manager(f, "darwin", calls.append)
check("1. Mac: open -R с путём одним аргументом", calls[-1] == ["open", "-R", f])
app.reveal_in_file_manager(f, "win32", calls.append)
check("2. Windows: explorer /select, путь в кавычках", calls[-1] == f'explorer /select,"{os.path.normpath(f)}"', calls[-1])
opened = []
orig = app.open_folder; app.open_folder = opened.append
r = app.reveal_in_file_manager(os.path.join(d, "нет.log"), "darwin", calls.append)
app.open_folder = orig
check("3. файла нет → открывается папка", r == "folder" and opened == [d])
check("4. баланс: пороги цвета прежние",
      app.balance_style({"remaining": 117914, "limit": 131000}) == ("117k симв.", "#86efac")
      and app.balance_style({"remaining": 20000, "limit": 131000})[1] == "#fcd34d"
      and app.balance_style({"remaining": 900, "limit": 131000}) == ("900 симв.", "#fca5a5")
      and app.balance_style(None) == ("", app.MUTED))
check("5. иконки — один словарь, без редких эмодзи из плана (🧬, 🗜)",
      set(app.NAV_ICONS) >= {"voice_gen", "voice_clone", "downloader", "compress", "transcribe", "groq_translate",
                             "new_task", "clear_all", "update", "settings"}
      and not {"🧬", "🗜"} & set(app.NAV_ICONS.values()))
print("=" * 60); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
