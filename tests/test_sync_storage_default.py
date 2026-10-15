"""Умолчание хранилища для Sync: Cloudinary есть/нет × явный выбор есть/нет. Без окон и сети."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

App = app.LipsyncTwoModeApp
def fake(has_cl, choice):
    s = types.SimpleNamespace(_sync_storage_choice=choice, sync_storage=None,
                              cloudinary_cloud_name="demo" if has_cl else "",
                              cloudinary_api_key="123" if has_cl else "",
                              cloudinary_api_secret="fake" if has_cl else "")
    for m in ("_has_cloudinary_keys", "_apply_sync_storage_default", "_sync_storage_hint"):
        setattr(s, m, types.MethodType(getattr(App, m), s))
    s._apply_sync_storage_default()
    return s

cases = [  # Cloudinary, явный выбор → хранилище, умолчание в подсказке
    (True,  None,         "cloudinary", "Cloudinary"),
    (False, None,         "sync",       "Sync (рекомендуется)"),
    (True,  "sync",       "sync",       "Cloudinary"),
    (False, "cloudinary", "cloudinary", "Sync (рекомендуется)"),
]
for has_cl, choice, exp, exp_default in cases:
    s = fake(has_cl, choice)
    hint = s._sync_storage_hint()
    tail = "Выбрано вами." if choice else "Сейчас действует умолчание."
    check(f"Cloudinary {'есть' if has_cl else 'нет'}, явный выбор {choice or 'нет'} → {exp}",
          s.sync_storage == exp and hint.startswith(f"По умолчанию сейчас: {exp_default}") and hint.endswith(tail), hint)

s = fake(False, None)
s.cloudinary_cloud_name, s.cloudinary_api_key, s.cloudinary_api_secret = "demo", "123", "fake"
s._apply_sync_storage_default()
check("ключи Cloudinary сохранены позже, выбора нет → Cloudinary", s.sync_storage == "cloudinary")
check("неизвестное значение в конфиге = выбора нет", app.effective_sync_storage("ftp", False) == "sync"
      and app.effective_sync_storage("ftp", True) == "cloudinary")

print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
