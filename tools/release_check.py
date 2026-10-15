#!/usr/bin/env python3
"""Проверка выпуска: sha256 app.py на main совпадает с sha256 в version.json на main.

Опрашивает raw.githubusercontent.com (с параметром против кэша), пока совпадение не появится или не
выйдет время (по умолчанию 10 минут). Выводит OK или причину. Только стандартная библиотека.

    python tools/release_check.py
    python tools/release_check.py --timeout 300 --interval 15
    python tools/release_check.py --base-url http://127.0.0.1:8765   # локальная проверка
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_REPO = "1Sheqel/Sheqel"


def fetch(url, timeout=30):
    req = urllib.request.Request(f"{url}{'&' if '?' in url else '?'}t={time.time_ns()}",
                                 headers={"Cache-Control": "no-cache", "Pragma": "no-cache",
                                          "User-Agent": "sheqel-release-check"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check_once(base):
    """(ok, причина, подробности)."""
    try:
        manifest = json.loads(fetch(f"{base}/version.json").decode("utf-8"))
    except (urllib.error.URLError, OSError) as e:
        return False, f"version.json недоступен: {e}", {}
    except ValueError as e:
        return False, f"version.json не JSON: {e}", {}
    expected = str(manifest.get("sha256", "")).strip().lower()
    version = manifest.get("latest_version", "?")
    if len(expected) != 64:
        return False, "в version.json нет sha256 — новое обновление откажется ставиться", {"version": version}
    try:
        body = fetch(f"{base}/app.py")
    except (urllib.error.URLError, OSError) as e:
        return False, f"app.py недоступен: {e}", {"version": version}
    actual = hashlib.sha256(body).hexdigest()
    import re
    m = re.search(rb'^APP_VERSION = "([^"]+)"', body, re.M)
    file_version = m.group(1).decode() if m else "?"
    info = {"version": version, "file_version": file_version, "expected": expected[:16], "actual": actual[:16]}
    if actual != expected:
        return False, "sha256 app.py не совпадает с version.json (кэш GitHub ещё отдаёт старый файл?)", info
    if file_version != version:
        return False, f"версия в app.py ({file_version}) не равна latest_version ({version})", info
    return True, "OK", info


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--interval", type=float, default=20)
    a = ap.parse_args()
    base = a.base_url.rstrip("/") or f"https://raw.githubusercontent.com/{a.repo}/{a.branch}"
    deadline = time.time() + a.timeout
    attempt = 0
    while True:
        attempt += 1
        ok, reason, info = check_once(base)
        stamp = time.strftime("%H:%M:%S")
        detail = " · ".join(f"{k}={v}" for k, v in info.items())
        print(f"[{stamp}] попытка {attempt}: {reason}" + (f" ({detail})" if detail else ""), flush=True)
        if ok:
            print("OK: sha256 и версия app.py на main совпадают с version.json")
            return 0
        if time.time() + a.interval > deadline:
            print(f"НЕ ГОТОВО за {a.timeout:g} с: {reason}")
            return 1
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
