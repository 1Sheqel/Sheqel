"""Проверка качества SSIM после сжатия: одинаковые файлы ≈ 1, сильное сжатие ниже, отрезки, отмена, пороги.
Тестовые видео делает ffmpeg, сети нет."""
import os, sys, time, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

FF = app.ffmpeg_bin()
D = tempfile.mkdtemp(prefix="cmp_ssim_")
src = os.path.join(D, "src.mp4")
subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=12",
                "-vf", "noise=alls=10:allf=t", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10",
                "-pix_fmt", "yuv420p", src], check=True)
info = app.probe_video_info(src)

check("1. отрезки: 12 с — целиком; 40 с — 3 по 10 с", app.ssim_segments(12) == [(0.0, 12)]
      and len(app.ssim_segments(40)) == 3 and all(l == 10 for _, l in app.ssim_segments(40)))
same = app.measure_ssim(src, src, info["duration"], 640, 360)
check("2. одинаковые файлы → SSIM ≈ 1.0, «отлично»", same > 0.999 and app.ssim_rating(same) == "отлично", str(same))
out = {}
for name, preset in (("hq", app.COMPRESS_HQ_PRESET), ("small", app.COMPRESS_SMALL_PRESET)):
    plan = app.compress_plan(info, {"preset": preset})
    res = app.compress_video_file(src, info, plan, os.path.join(D, f"{name}.mp4"), lambda *_: None)
    out[name] = app.measure_ssim(src, res["output"], info["duration"], res["width"], res["height"])
check("3. «Высокое качество» выше «Маленького файла»", out["hq"] > out["small"], str(out))
small_720 = os.path.join(D, "half.mp4")
subprocess.run([FF, "-v", "error", "-y", "-i", src, "-vf", "scale=320:180", "-c:v", "libx264", "-crf", "20", small_720],
               check=True)
v = app.measure_ssim(src, small_720, info["duration"], 320, 180)
check("4. другое разрешение результата — сравнение приводит к одному размеру", 0.5 < v <= 1.0, str(v))
t0 = time.time()
try:
    app.measure_ssim(src, src, info["duration"], 640, 360, cancel=lambda: True)
    cancelled = False
except app.CompressCancelled:
    cancelled = True
check("5. отмена останавливает проверку сразу", cancelled and time.time() - t0 < 3)
check("6. пороги: 0.985 отлично, 0.96 хорошо, 0.93 заметные потери",
      [app.ssim_rating(x) for x in (0.985, 0.96, 0.93)] == ["отлично", "хорошо", "заметные потери"])

print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
