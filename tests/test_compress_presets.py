"""«Сжать видео»: названия пресетов и синонимы старых, умолчания, пресет «Для рекламы» (команда и итоговый файл),
«Не больше 95 МБ» и «Точный размер» (≈50% файла). Тестовые видео делает ffmpeg, сети нет."""
import os, sys, json, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
app.ERROR_REPORT_URL = ""
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

FF = app.ffmpeg_bin()
D = tempfile.mkdtemp(prefix="cmp_presets_")
def make(name, audio="aac", size="640x360", rate=30, dur=6, ext="mp4"):
    path = os.path.join(D, f"{name}.{ext}")
    a = {"aac": ["-c:a", "aac", "-b:a", "96k"], "flac": ["-c:a", "flac"]}[audio]
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "16", "-pix_fmt", "yuv420p", *a, "-shortest", path], check=True)
    return path

# ── 1. Названия и синонимы ──
P = app
olds = {"Под лимит": P.COMPRESS_LIMIT_PRESET, "Под лимит (для Cloudinary)": P.COMPRESS_LIMIT_PRESET,
        "Высокое качество (CRF 20)": P.COMPRESS_HQ_PRESET, "Баланс (CRF 23)": P.COMPRESS_BALANCED_PRESET,
        "Маленький (CRF 27)": P.COMPRESS_SMALL_PRESET, "Свой размер, МБ": P.COMPRESS_EXACT_PRESET}
check("1. старые имена из прошлых версий → новые", all(P.normalize_compress_settings({"preset": o})["preset"] == n
                                                     for o, n in olds.items()))
check("1b. «Высокое качество — для монтажа и Sync» в списке", P.COMPRESS_HQ_PRESET in P.COMPRESS_PRESETS
      and P.COMPRESS_HQ_PRESET == "Высокое качество — для монтажа и Sync")
check("1c. у каждого пресета есть понятное описание", set(P.COMPRESS_PRESET_DETAILS) == set(P.COMPRESS_PRESETS))
check("2. новый пользователь (выбор не сохранён) → «Для рекламы»",
      P.normalize_compress_settings(None)["preset"] == P.COMPRESS_AD_PRESET
      and P.normalize_compress_settings({"resolution": "720"})["preset"] == P.COMPRESS_AD_PRESET)
check("2b. сохранённый выбор не меняется (даже прежнее умолчание)",
      P.normalize_compress_settings({"preset": "Высокое качество (CRF 20)"})["preset"] == P.COMPRESS_HQ_PRESET
      and P.normalize_compress_settings({"preset": P.COMPRESS_SMALL_PRESET})["preset"] == P.COMPRESS_SMALL_PRESET)
m = P.normalize_compress_settings({"preset": "Свой размер, МБ", "target_mb": 40})
check("2c. размер из «Свой размер, МБ» переносится в «Точный размер»", m["preset"] == P.COMPRESS_EXACT_PRESET
      and m["exact_mb"] == 40.0)

# ── 2. «Для рекламы» ──
v_aac = make("ad_aac")
v_flac = make("ad_flac", audio="flac", ext="mkv")
info = app.probe_video_info(v_flac)
plan = app.compress_plan(info, {"preset": P.COMPRESS_AD_PRESET, "resolution": "720", "fast": True, "codec": "hevc"})
cmd = app.build_compress_cmd(v_flac, os.path.join(D, "x.mp4"), plan)
joined = " ".join(cmd)
check("3. «Для рекламы»: x264 slow, High, CRF 18 — даже если выбраны «быстрее», HEVC и 720p",
      plan["encoder"] == "libx264" and "-preset slow" in joined and "-profile:v high" in joined
      and "-crf 18" in joined and (plan["out_w"], plan["out_h"]) == (640, 360), joined[:200])
check("3b. ключевой кадр каждые 2 с, maxrate и bufsize, BT.709, yuv420p, +faststart",
      "expr:gte(t,n_forced*2)" in joined and "-maxrate" in joined and "-bufsize" in joined
      and "-color_primaries bt709" in joined and "format=yuv420p" in joined and "+faststart" in joined)
check("3c. звук не AAC → AAC 192k / 48 кГц", "-c:a aac -b:a 192k -ar 48000" in joined)
plan_aac = app.compress_plan(app.probe_video_info(v_aac), {"preset": P.COMPRESS_AD_PRESET})
check("3d. звук уже AAC → копия", plan_aac["audio_args"] == ["-c:a", "copy"])
vfr_info = dict(info, vfr=True, fps=27.3)
check("3e. переменный fps → постоянный", any(f.startswith("fps=") for f in
                                           app.compress_plan(vfr_info, {"preset": P.COMPRESS_AD_PRESET})["filters"]))
out = os.path.join(D, "ad_out.mp4")
res = app.compress_video_file(v_flac, info, plan, out, lambda *_: None)
probe = json.loads(subprocess.run([FF.replace("ffmpeg", "ffprobe") if os.path.exists(FF.replace("ffmpeg", "ffprobe"))
                                   else "ffprobe", "-v", "error", "-show_entries",
                                   "stream=codec_name,profile,pix_fmt,color_primaries,color_transfer,sample_rate,r_frame_rate",
                                   "-of", "json", res["output"]], capture_output=True, text=True).stdout)["streams"]
vs = next(s for s in probe if s["codec_name"] == "h264")
au = next(s for s in probe if s["codec_name"] == "aac")
check("4. итоговый файл: H.264 High, yuv420p, BT.709, 30 fps как в исходнике, AAC 48 кГц",
      vs["profile"] == "High" and vs["pix_fmt"] == "yuv420p" and vs.get("color_primaries") == "bt709"
      and vs.get("color_transfer") == "bt709"
      and vs["r_frame_rate"] == "30/1" and au["sample_rate"] == "48000", str(vs))
keys = subprocess.run([FF.replace("ffmpeg", "ffprobe") if os.path.exists(FF.replace("ffmpeg", "ffprobe")) else "ffprobe",
                       "-v", "error", "-select_streams", "v", "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
                       "-of", "csv=p=0", res["output"]], capture_output=True, text=True).stdout.split()
keys = [round(float(k.strip(",")), 1) for k in keys if k.strip(",")]
check("4b. ключевые кадры через 2 с", keys[:3] == [0.0, 2.0, 4.0], str(keys))

# ── 3. Размерные пресеты ──
pl = app.compress_plan(info, {"preset": P.COMPRESS_LIMIT_PRESET, "exact_mb": 3})
check("5. «Не больше 95 МБ»: всегда 95, строго", pl["target_mb"] == 95 and pl["strict"])
pe = app.compress_plan(info, {"preset": P.COMPRESS_EXACT_PRESET})
check("5b. «Точный размер» без числа → ≈50% размера файла", abs(pe["target_mb"] - info["size"] / app.MB * 0.5) < 0.06,
      f"{pe['target_mb']} из {info['size'] / app.MB:.2f}")
check("5c. «Точный размер» с числом", app.compress_plan(info, {"preset": P.COMPRESS_EXACT_PRESET,
                                                              "exact_mb": 1.5})["target_mb"] == 1.5)

print("=" * 50); print(f"Итого: {sum(results)}/{len(results)}"); sys.exit(0 if all(results) else 1)
