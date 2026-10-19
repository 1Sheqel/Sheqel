"""Лимит одновременных генераций Sync на подставном сервере (без сети и оплаты): 429 concurrency_limit_reached
не тратит попытки и не удаляет ассеты, повтор — тем же Idempotency-Key, пауза max(retryAfterSeconds, 20);
узнанный лимит N=1 при 3 роликах — в Sync одновременно не больше одной генерации; оценка времени с лимитом."""
import os, sys, json, time, threading, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class Resp:
    def __init__(self, code, data=None, headers=None, content=b""):
        self.status_code, self._data, self.headers, self.content = code, data, headers or {}, content
        self.text = json.dumps(data) if data is not None else ""
    def json(self):
        return self._data
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSync:
    """Sync с лимитом одновременных генераций. Задача завершается после polls_to_complete опросов."""
    def __init__(self, limit, retry_after=30, polls_to_complete=3, forced_429=0):
        self.limit, self.retry_after, self.polls = limit, retry_after, polls_to_complete
        self.forced_429 = forced_429           # первые N запросов — 429, даже если место есть
        self.lock = threading.Lock()
        self.jobs, self.active, self.max_active = {}, 0, 0
        self.posts, self.count_429 = [], 0

    def post(self, url, headers=None, json=None, timeout=None):
        with self.lock:
            self.posts.append(headers.get("Idempotency-Key"))
            if self.forced_429 > 0 or self.active >= self.limit:
                self.forced_429 = max(0, self.forced_429 - 1)
                self.count_429 += 1
                return Resp(429, {"errorCode": "concurrency_limit_reached",
                                  "message": f"You have reached your concurrency limit of {self.limit}.",
                                  "retryAfterSeconds": self.retry_after})
            job = f"job-{len(self.jobs) + 1}"
            self.jobs[job] = 0
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            return Resp(201, {"id": job})

    def get(self, url, headers=None, timeout=None, params=None):
        if url.startswith("https://out.example/"):
            return Resp(200, content=b"\0" * 4096)
        job = url.rsplit("/", 1)[-1]
        with self.lock:
            self.jobs[job] += 1
            if self.jobs[job] == self.polls:
                self.active -= 1
            done = self.jobs[job] >= self.polls
        return Resp(200, {"id": job, "status": "COMPLETED" if done else "PROCESSING",
                          "outputUrl": f"https://out.example/{job}.mp4" if done else None})


class FakeSession:
    server = None
    def __init__(self):
        pass
    def mount(self, *a):
        pass
    def close(self):
        pass
    def post(self, *a, **k):
        return FakeSession.server.post(*a, **k)
    def get(self, *a, **k):
        return FakeSession.server.get(*a, **k)


deleted, waits = [], []
app.requests.Session = FakeSession
app.upload_inputs_to_sync = lambda v, a, key, log: {"video": f"asset-v-{os.path.basename(v)}",
                                                    "audio": f"asset-a-{os.path.basename(a)}"}
app.delete_sync_asset = lambda asset, key, log: deleted.append(asset)
app.log_actual_sync_cost = lambda *a, **k: None
app.SYNC_POLL_INTERVAL_SEC = 0.02
app.SYNC_API_KEY = "-".join(("fake", "sync"))
def fake_wait(sec):
    waits.append(sec)
    time.sleep(0.05)
app._sync_sleep = fake_wait
learned = []
app.sync_concurrency_hook = learned.append
D = tempfile.mkdtemp()


def run(name, logs):
    out = os.path.join(D, f"{name}.mp4")
    app.apply_lipsync_sync(f"/in/{name}.mp4", f"/in/{name}.wav", out, logs.append, storage="sync")
    return out


# ── 1. Разбор ответа 429 ──
is_c, ra, n = app.parse_sync_concurrency_429(Resp(429, {"errorCode": "concurrency_limit_reached",
                                                        "message": "concurrency limit of 3", "retryAfterSeconds": 12}))
check("1a. 429 concurrency: распознан, retryAfterSeconds 12, лимит 3", (is_c, ra, n) == (True, 12.0, 3))
is_c, ra, n = app.parse_sync_concurrency_429(Resp(429, {"errorCode": "rate_limit_exceeded",
                                                        "message": "Rate limit exceeded."}, {"Retry-After": "7"}))
check("1b. 429 rate_limit — не лимит одновременных", not is_c and ra == 7.0 and n is None)

# ── 2. Два отказа по лимиту → третий запрос успешен, попытка одна ──
app.sync_gen_gate.configure()
FakeSession.server = srv = FakeSync(limit=5, retry_after=30, forced_429=2)
logs = []
out = run("a", logs)
check("2a. результат сохранён", os.path.exists(out))
check("2b. 429 не тратит попытки: «попытка 1/3» одна, «попытка 2/3» нет",
      sum("попытка 1/3" in l for l in logs) == 1 and not any("попытка 2/3" in l for l in logs))
check("2c. повтор — с тем же Idempotency-Key (3 запроса, один ключ)", len(srv.posts) == 3 and len(set(srv.posts)) == 1)
check("2d. пауза max(retryAfterSeconds, 20): 30 и 30 с", waits == [30, 30], str(waits))
check("2e. ассеты удалены один раз — после готового результата, не после 429",
      deleted == ["asset-v-a.mp4", "asset-a-a.wav"], str(deleted))
check("2f. узнан лимит 5 (из текста ответа)", learned == [5, 5] and app.sync_gen_gate.learned == 5)
waits.clear(); deleted.clear(); learned.clear()
FakeSession.server = srv = FakeSync(limit=5, retry_after=5, forced_429=1)
run("b", [])
check("2g. retryAfterSeconds 5 → пауза 20 с (не меньше)", waits == [20], str(waits))

# ── 3. Лимит N=1 при 3 роликах ──
app.sync_gen_gate.configure()             # авто, лимит ещё не известен
waits.clear(); learned.clear()
FakeSession.server = srv = FakeSync(limit=1, retry_after=30, polls_to_complete=8)
errs = []
def worker(i):
    try:
        run(f"r{i}", [])
    except Exception as e:
        errs.append(e)
threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join(30)
check("3a. все 3 ролика готовы, ошибок нет", not errs and all(os.path.exists(os.path.join(D, f"r{i}.mp4"))
                                                              for i in range(3)), str(errs))
check("3b. в Sync одновременно не больше 1 генерации", srv.max_active == 1)
check("3c. лимит 1 узнан и сохранён; ворота дальше не пускают второй запрос",
      app.sync_gen_gate.learned == 1 and 1 in learned and srv.count_429 <= 2, f"429: {srv.count_429}")
check("3d. ворота пусты после работы", app.sync_gen_gate.active == 0)

# ── 4. Оценка времени очереди учитывает лимит ──
times = [(100, 100)] * 3
app.sync_gen_gate.configure(manual=1)
one = app.estimate_queue_time(times, stagger=0)
app.sync_gen_gate.configure()
two = app.estimate_queue_time(times, stagger=0)
check("4a. лимит 1 → 3 ролика по 100 с ≈ 300 с; без лимита (2 потока) ≈ 200 с", one == (300, 300) and two == (200, 200),
      f"{one} / {two}")
check("4b. задержка старта — от предыдущего запуска, не по номеру ролика",
      app.estimate_queue_time([(10, 10)] * 4, workers=4, stagger=3) == (19, 19))

print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
