"""«Точный размер» и «Не больше 95 МБ»: минимум с подсказкой, мало бит на пиксель с рекомендуемым размером,
«сжатие не нужно», повтор с меньшим битрейтом, если результат вышел за лимит. Без кодирования (ffmpeg подменён)."""
import os, sys, tempfile
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

MB = app.MB
def info(minutes=10, w=1920, h=1080, fps=30.0, size_mb=500):
    return {"path": "/x.mp4", "duration": minutes * 60.0, "width": w, "height": h, "fps": fps, "vfr": False,
            "codec": "h264", "size": int(size_mb * MB), "bitrate_kbps": size_mb * MB * 8 / (minutes * 60) / 1000,
            "has_audio": True, "audio_codec": "aac", "audio_kbps": 128, "hdr": None, "color_transfer": None}
EX, LIM = app.COMPRESS_EXACT_PRESET, app.COMPRESS_LIMIT_PRESET

i10 = info()
p = app.compress_plan(i10, {"preset": EX, "exact_mb": 5})
kb_at_min = app.compress_target_kbps(i10, p.get("min_mb", 0), 128, False)
check("1. слишком мало → ошибка с минимумом; минимум даёт ≥150 кбит/с на видео",
      p.get("error") and p.get("min_mb") and f"Минимум — {p['min_mb']:g} МБ" in p["error"]
      and 150 <= kb_at_min < 152, f"{p.get('min_mb')} МБ → {kb_at_min:.1f} кбит/с")
pl = app.compress_plan(info(minutes=120), {"preset": LIM})
check("1b. «Не больше 95 МБ» для 2 часов → понятная ошибка: 720p или «Точный размер» от N МБ",
      pl.get("error", "").startswith("95 МБ мало для 120.0 мин") and "720p" in pl["error"]
      and "«Точный размер» от" in pl["error"], pl.get("error"))
p = app.compress_plan(i10, {"preset": EX, "exact_mb": 60})
bpp_rec = app.compress_target_kbps(i10, p.get("rec_mb", 0), 128, False) * 1000 / (1920 * 1080 * 30)
check("2. мало бит на пиксель → предупреждение и рекомендуемый размер (≈0.05 бит/пикс.)",
      not p.get("error") and p.get("rec_mb") and any("Рекомендуемый размер — от" in w for w in p["warnings"])
      and 0.0495 <= bpp_rec <= 0.0505, f"{p.get('rec_mb')} МБ, {bpp_rec:.4f}")
p = app.compress_plan(i10, {"preset": EX, "exact_mb": 600})
check("3. размер больше исходника → «сжатие не нужно» (не ошибка)", p.get("not_needed") and not p.get("error")
      and any("сжатие не нужно" in w for w in p["warnings"]))
check("3b. нормальный размер — без ошибок и предупреждений о размере",
      not app.compress_plan(i10, {"preset": EX, "exact_mb": 250}).get("error"))

# ── Повтор с меньшим битрейтом, если «Не больше 95 МБ» вышел больше лимита ──
D = tempfile.mkdtemp()
src = os.path.join(D, "src.mp4"); open(src, "wb").write(b"\0" * 1024)
i_small = info(minutes=2, size_mb=300)
plan = app.compress_plan(i_small, {"preset": LIM}, encoders={"libx264"}, works=lambda e: True)
sizes = []
def fake_run(cmd, duration, report=None, cancel=None, cwd=None, register=None):
    out = cmd[-1]
    if out == "-":
        return 0                               # первый проход 2-проходного кодирования
    size = SIZES.pop(0)
    sizes.append(size)
    with open(out, "wb") as f:
        f.truncate(int(size * MB))
    return 0
def verify(info, path):
    return {"duration": info["duration"], "width": 1920, "height": 1080}
logs = []
SIZES = [101.0, 92.0]
with patch.object(app, "run_ffmpeg_progress", fake_run), patch.object(app, "verify_compressed", verify):
    res = app.compress_video_file(src, i_small, plan, os.path.join(D, "out.mp4"), logs.append)
check("4. вышло 101 МБ > 95 → повтор с меньшим битрейтом → 92 МБ, принято",
      sizes == [101.0, 92.0] and res["size"] == int(92 * MB) and any("повтор 1/2" in l for l in logs), str(sizes))
SIZES = [101.0, 99.0, 97.0]
sizes.clear()
with patch.object(app, "run_ffmpeg_progress", fake_run), patch.object(app, "verify_compressed", verify):
    try:
        app.compress_video_file(src, i_small, plan, os.path.join(D, "out2.mp4"), logs.append)
        err = ""
    except RuntimeError as e:
        err = str(e)
check("4b. не уложились и после двух повторов → понятная ошибка, временный файл удалён",
      "не удалось уложиться в 95 МБ" in err and not any(f.endswith(".compressing.mp4") for f in os.listdir(D)), err)

print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
