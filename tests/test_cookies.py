"""Куки для загрузчика: какие браузеры есть, порядок, перебор, понятные ошибки. Без сети и настоящих загрузок."""
import sys, os, tempfile, types, json
from unittest.mock import patch
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""  # тесты не шлют настоящих отчётов на воркер
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

def make_profile(root, browser, with_db=True):
    if browser == "safari":
        os.makedirs(root, exist_ok=True); return
    sub = os.path.join(root, "abc.default-release" if browser == "firefox" else "Default")
    os.makedirs(sub, exist_ok=True)
    if with_db:
        open(os.path.join(sub, "cookies.sqlite" if browser == "firefox" else "Cookies"), "w").write("x")
def fake_home(browsers, empty=()):
    h = tempfile.mkdtemp(prefix="home ")
    for b in browsers:
        make_profile(app.browser_profile_paths(b, "darwin", home=h)[0], b)
    for b in empty:                       # папка браузера есть, базы куки нет
        make_profile(app.browser_profile_paths(b, "darwin", home=h)[0], b, with_db=False)
    return h

# ── 1. профили и порядок ──
h = fake_home(["chrome", "safari", "firefox", "brave", "edge"])
check("1a. Mac: порядок Firefox, Chrome, Brave, Edge, Safari; Chromium без профиля пропущен",
      app.cookie_browser_candidates("darwin", home=h) == ["firefox", "chrome", "brave", "edge", "safari"])
h3 = fake_home(["chrome", "safari"], empty=["chromium", "firefox"])
check("1a2. как на этом Mac: папки Chromium и Firefox есть, но без базы куки → пропущены",
      app.cookie_browser_candidates("darwin", home=h3) == ["chrome", "safari"])
h2 = fake_home(["chrome"])
check("1b. Mac: есть только Chrome → только Chrome", app.cookie_browser_candidates("darwin", home=h2) == ["chrome"])
win = tempfile.mkdtemp(prefix="win ")
env = {"LOCALAPPDATA": os.path.join(win, "Local"), "APPDATA": os.path.join(win, "Roaming")}
make_profile(os.path.join(env["APPDATA"], "Mozilla", "Firefox", "Profiles"), "firefox")
make_profile(os.path.join(env["LOCALAPPDATA"], "Microsoft", "Edge", "User Data"), "edge")
make_profile(os.path.join(env["LOCALAPPDATA"], "Chromium", "User Data"), "chromium", with_db=False)
check("1c. Windows: Firefox (Roaming) и Edge (Local), без Safari", app.cookie_browser_candidates("win32", env=env) == ["firefox", "edge"])
check("1d. Windows без переменных окружения — ничего не найдено, без ошибок", app.cookie_browser_candidates("win32", env={}) == [])

# ── 2. разбор ошибок ──
raw = {
    "bot": "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. Use --cookies-from-browser",
    "bot_curly": "ERROR: [youtube] abc: Sign in to confirm you’re not a bot",
    "nodb": "could not find chromium cookies database in \"/Users/x/Library/Application Support/Chromium\"",
    "safari": "[Errno 1] Operation not permitted: '/Users/x/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies'",
    "dpapi": "Failed to decrypt with DPAPI",
    "video": "ERROR: [youtube] abc: Video unavailable. This video is private",
}
kinds = {k: app.classify_download_error(v) for k, v in raw.items()}
check("2. виды ошибок распознаются", kinds == {"bot": "bot_check", "bot_curly": "bot_check", "nodb": "no_cookie_db",
      "safari": "safari_permission", "dpapi": "dpapi", "video": "video"}, str(kinds))
check("2b. тексты сообщений как в задании",
      app.friendly_download_error("bot_check").startswith("YouTube просит войти. Откройте youtube.com в выбранном браузере")
      and "Если включён VPN, отключите его" in app.friendly_download_error("bot_check")
      and app.friendly_download_error("no_cookie_db", "chromium") == "В браузере Chromium не найден профиль. Выберите другой браузер."
      and app.friendly_download_error("safari_permission").startswith("Нужен Полный доступ к диску для приложения, из которого запущена программа"))

# ── 3. перебор в download_from_url с подставным yt-dlp ──
class FakeYDL:
    plan = {}          # браузер (или "file"/"none") → текст ошибки или None (успех)
    seen = []
    def __init__(self, opts):
        self.opts = opts
        if "cookiefile" in opts: self.key = "file"
        elif "cookiesfrombrowser" in opts: self.key = opts["cookiesfrombrowser"][0]
        else: self.key = "none"
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def extract_info(self, url, download=True):
        FakeYDL.seen.append(self.key)
        err = FakeYDL.plan.get(self.key)
        if err:
            raise Exception(err)
        return {"title": "t"}
    def prepare_filename(self, info):
        p = os.path.join(OUT, f"{self.key}.mp4"); open(p, "w").write("x"); return p
fake_mod = types.SimpleNamespace(YoutubeDL=FakeYDL)
OUT = tempfile.mkdtemp()
LOG = os.path.join(OUT, "app.log")
def run(home, plan, browser=app.AUTO_BROWSER, cookies_file=None):
    FakeYDL.plan, FakeYDL.seen = plan, []
    used, lines = [], []
    with patch.dict(os.environ, {"HOME": home}), patch.object(app, "_ensure_ytdlp", lambda: fake_mod), \
         patch.object(app, "_ytdlp_ffmpeg_opts", lambda: None), patch.object(app, "LOG_FILE_PATH", LOG), \
         patch.object(app.sys, "platform", "darwin"):
        try:
            res = app.download_from_url("https://youtu.be/x", OUT, "video", False, lines.append, browser=browser,
                                        cookies_file=cookies_file, on_cookies=used.append)
            return res, None, used, list(FakeYDL.seen), lines
        except Exception as e:
            return None, str(e), used, list(FakeYDL.seen), lines

h = fake_home(["chrome", "safari"])            # как на этом Mac: Chromium и Firefox нет
res, err, used, seen, lines = run(h, {"chrome": raw["bot"], "safari": None})
check("3a. «Авто»: только установленные (Chrome → Safari), Chromium не трогается; Chrome «войдите» → Safari успех",
      seen == ["chrome", "safari"] and res and used == ["Safari"] and "chromium" not in seen, f"{seen} → {used}")
res, err, used, seen, lines = run(h, {"chrome": raw["nodb"].replace("chromium", "chrome"), "safari": raw["safari"]})
check("3b. все браузеры с ошибкой куки → понятная сводка: Chrome — профиль, Safari — Полный доступ к диску",
      err and "Chrome — профиль не найден" in err and "Safari — нужен Полный доступ к диску" in err
      and "Системные настройки" in err and "Errno" not in err, err)
check("3c. сырой текст ошибок yt-dlp — в логе", all(s in open(LOG, encoding="utf-8").read() for s in ("Operation not permitted", "cookies database")))
res, err, used, seen, lines = run(h, {"chrome": raw["bot"], "safari": raw["bot_curly"]})
check("3d. все «войдите» → сообщение про вход в YouTube и VPN", err and err.startswith("YouTube просит войти"), err)
res, err, used, seen, lines = run(h, {"chrome": raw["video"]})
check("3e. видео недоступно → перебор останавливается (Safari не пробуется)", seen == ["chrome"] and "Video unavailable" in err, err)
res, err, used, seen, lines = run(fake_home([]), {"none": None})
check("3f. браузеров нет → загрузка без куки, «без куки»", seen == ["none"] and used == ["без куки"])
res, err, used, seen, lines = run(h, {"chromium": raw["nodb"]}, browser="Chromium")
check("3g. вручную выбран Chromium без профиля → «В браузере Chromium не найден профиль»",
      err == "В браузере Chromium не найден профиль. Выберите другой браузер.", err)
res, err, used, seen, lines = run(h, {"safari": raw["safari"]}, browser="Safari")
check("3h. вручную Safari без доступа → «Нужен Полный доступ к диску…»", err and err.startswith("Нужен Полный доступ к диску"))
res, err, used, seen, lines = run(h, {"file": None}, browser=None, cookies_file="/tmp/cookies.txt")
check("3i. файл cookies.txt: --cookies (cookiefile), использовано «cookies.txt»", seen == ["file"] and used == ["cookies.txt"])
res, err, used, seen, lines = run(h, {"file": raw["bot"]}, browser=None, cookies_file="/tmp/cookies.txt")
check("3j. cookies.txt устарел (YouTube просит войти) → понятное сообщение", err and err.startswith("YouTube просит войти"))
check("3k. в журнале попыток — какой браузер пробуется", any("Пробую куки из браузера: Chrome (1/2)" in l for l in run(h, {"chrome": None})[4]))
print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
