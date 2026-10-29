"""JS-рантайм для yt-dlp: js_runtimes в опциях, deno из pip-пакета, node — запасной, без них — как раньше.
Без сети, без настоящих загрузок и без pip."""
import sys, os, tempfile, types, subprocess
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""  # тесты не шлют настоящих отчётов на воркер
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

TMP = tempfile.mkdtemp(prefix="js rt ")
EXE = ".exe" if sys.platform == "win32" else ""
fake_deno = os.path.join(TMP, "deno" + EXE); open(fake_deno, "w").write("x")
fake_deno_mod = types.SimpleNamespace(find_deno_bin=lambda: fake_deno)
def missing_deno():
    raise FileNotFoundError("deno")
broken_deno_mod = types.SimpleNamespace(find_deno_bin=missing_deno)

# ── 1. поиск рантаймов ──
with patch.dict(sys.modules, {"deno": fake_deno_mod}), patch.object(app.shutil, "which", lambda n: None), \
     patch.object(app, "YTDLP_NODE_DIRS", []):
    check("1a. deno из pip-пакета найден по find_deno_bin", app.find_deno_binary() == fake_deno)
    check("1b. только deno → {'deno': {'path': …}}", app.ytdlp_js_runtimes() == {"deno": {"path": fake_deno}})
which = {"node": "/usr/bin/node", "deno": None}
with patch.dict(sys.modules, {"deno": broken_deno_mod}), patch.object(app.shutil, "which", which.get):
    check("1c. pip-пакет без бинарника и deno нет в PATH → node запасной",
          app.ytdlp_js_runtimes() == {"node": {"path": "/usr/bin/node"}}, str(app.ytdlp_js_runtimes()))
with patch.dict(sys.modules, {"deno": fake_deno_mod}), patch.object(app.shutil, "which", which.get):
    rt = app.ytdlp_js_runtimes()
    check("1d. deno и node → deno первым", list(rt) == ["deno", "node"], str(rt))
with patch.dict(sys.modules, {"deno": None}), patch.object(app.shutil, "which", lambda n: None), \
     patch.object(app, "YTDLP_NODE_DIRS", [TMP]):
    check("1e. пакета deno нет (ImportError), node нет → пусто, без исключений", app.ytdlp_js_runtimes() == {})
if sys.platform != "win32":
    node = os.path.join(TMP, "node"); open(node, "w").write("x"); os.chmod(node, 0o755)
    with patch.dict(sys.modules, {"deno": None}), patch.object(app.shutil, "which", lambda n: None), \
         patch.object(app, "YTDLP_NODE_DIRS", [TMP]):
        check("1f. Mac: node из Homebrew-папки, даже если её нет в PATH", app.find_node_binary() == node)

# ── 2. опции yt-dlp ──
class FakeYDL:
    opts_seen = []
    def __init__(self, opts): self.opts = opts; FakeYDL.opts_seen.append(opts)
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def extract_info(self, url, download=True): return {"title": "t"}
    def prepare_filename(self, info):
        p = os.path.join(OUT, "v.mp4"); open(p, "w").write("x"); return p
fake_mod = types.SimpleNamespace(YoutubeDL=FakeYDL)
OUT = tempfile.mkdtemp(prefix="out ")
LOG = os.path.join(OUT, "app.log")
def run(runtimes):
    FakeYDL.opts_seen = []
    with patch.object(app, "_ensure_ytdlp", lambda: fake_mod), patch.object(app, "_ytdlp_ffmpeg_opts", lambda: None), \
         patch.object(app, "LOG_FILE_PATH", LOG), patch.object(app, "ytdlp_js_runtimes", lambda: runtimes):
        res = app.download_from_url("https://youtu.be/x", OUT, "video", False, lambda m: None, browser=None)
    return res, FakeYDL.opts_seen[-1]
res, opts = run({"deno": {"path": fake_deno}})
check("2a. js_runtimes с путём к deno попадает в опции YoutubeDL", opts.get("js_runtimes") == {"deno": {"path": fake_deno}})
check("2b. remote_components не включается (ejs ставится pip)", "remote_components" not in opts)
res, opts = run({})
check("2c. рантайма нет → опции без js_runtimes (поведение yt-dlp по умолчанию), скачивание работает",
      "js_runtimes" not in opts and res and res[0].endswith("v.mp4"))
check("2d. отсутствие рантайма записано в лог", "JS-рантайм (deno/node) не найден" in open(LOG, encoding="utf-8").read())

# ── 3. установка необязательных пакетов не роняет запуск ──
calls = []
def pip_fail(*a, **k):
    calls.append(a[0]); raise subprocess.TimeoutExpired(a[0], 1)
with patch.dict(sys.modules, {"yt_dlp_ejs": None, "deno": None}), patch.object(app.subprocess, "run", pip_fail):
    try:
        app._ensure_optional_deps(); ok = True
    except Exception as e:
        ok = False
check("3a. pip не ответил (нет сети) → запуск продолжается", ok and calls and app.YTDLP_PIP_SPEC in calls[0])
calls.clear()
with patch.dict(sys.modules, {"yt_dlp_ejs": None, "deno": None}), patch.object(app.subprocess, "run",
        lambda *a, **k: calls.append(a[0]) or types.SimpleNamespace(returncode=1)):
    app._ensure_optional_deps()
check("3b. pip вернул ошибку → запуск продолжается", len(calls) == 1)
calls.clear()
with patch.dict(sys.modules, {"yt_dlp_ejs": None}), patch.object(app.sys, "frozen", True, create=True), \
     patch.object(app.subprocess, "run", lambda *a, **k: calls.append(a[0])):
    app._ensure_optional_deps()
check("3c. сборка PyInstaller → pip не запускается", calls == [])
calls.clear()
with patch.dict(sys.modules, {"yt_dlp_ejs": types.ModuleType("yt_dlp_ejs"), "deno": fake_deno_mod}), \
     patch.object(app.subprocess, "run", lambda *a, **k: calls.append(a[0])):
    app._ensure_optional_deps()
check("3d. всё установлено → pip не запускается", calls == [])
check("3e. группа пакетов yt-dlp: default (yt-dlp-ejs той же версии) и deno", app.YTDLP_PIP_SPEC == "yt-dlp[default,deno]")
print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
