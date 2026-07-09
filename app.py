#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys

def _ensure_deps():
    packages = [
        ("requests",       "requests==2.34.2"),
        ("customtkinter",  "customtkinter==5.2.2"),
        ("PIL",            "pillow==12.2.0"),
        ("yt_dlp",         "yt-dlp"),
        ("audio_separator","audio-separator[cpu]"),
        ("cloudinary",     "cloudinary"),
    ]
    for module, pkg in packages:
        try:
            __import__(module)
        except ImportError:
            print(f"Устанавливаю {pkg}...", flush=True)
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", pkg],
                check=True,
            )

_ensure_deps()

import os
import re
import time
import queue
import shutil
import hashlib
import tempfile
import threading
import requests
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import customtkinter as ctk
from tkinter import filedialog, messagebox

SYNC_API_KEY = ""
CLOUDINARY_CLOUD_NAME = ""
CLOUDINARY_API_KEY = ""
CLOUDINARY_API_SECRET = ""
CONFIG_PATH = str(Path.home() / ".version.json")
ELEVENLABS_API_KEY = ""
APP_VERSION = "1.1.4"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/1Sheqel/Sheqel/main/version.json"



ELEVEN_BASE_URL = "https://api.elevenlabs.io"
ELEVEN_TTS_MODEL_ID = "eleven_v3"
ELEVEN_OUTPUT_FORMAT = "mp3_44100_192"

SYNC_GENERATE_URL = "https://api.sync.so/v2/generate"
SYNC_MODEL = "sync-3"
SYNC_POLL_INTERVAL_SEC = 5
SYNC_TIMEOUT_SEC = 30 * 60

VIDEO_CRF = "13"
VIDEO_PRESET = "slow"
AUDIO_BITRATE = "256k"
SHARPEN_FILTER = "unsharp=3:3:0.6:3:3:0.0"

END_PADDING = 0.8
SYNC_INPUT_SCALE = "720:-2"
SYNC_INPUT_FPS = "25"
CONCURRENT_ROLLS = 2
PAUSE_BETWEEN_ROLLS_SEC = 3
SYNC_RETRIES = 3
SILENCE_NOISE = "-25dB"
SILENCE_DURATION = "1.8"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Arc Browser — тёмный стальной
BG = "#12131a"
PANEL = "#1a1b26"
CARD = "#20222f"
BTN = "#252736"
BTN_HOVER = "#32354a"
BTN_OK = "#0e7a60"
BTN_OK_HOVER = "#129970"
BTN_PRIMARY = "#1c3f6e"
BTN_PRIMARY_HOVER = "#234d87"
BTN_DANGER = "#b84800"
BTN_DANGER_HOVER = "#d45500"
TEXT = "#dde2f0"
MUTED = "#606880"
CARD_HOVER_DELETE = "#2a1210"


def app_base_dir():
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent)
    return str(Path(__file__).resolve().parent)


def find_binary(name):
    return shutil.which(name) or name


def ffmpeg_bin():
    for p in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if Path(p).exists():
            return p
    return find_binary("ffmpeg")


def ffprobe_bin():
    for p in ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"]:
        if Path(p).exists():
            return p
    return find_binary("ffprobe")


def run(cmd):
    subprocess.run(cmd, check=True)


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def file_exists_ok(path, min_size=1024):
    return os.path.exists(path) and os.path.getsize(path) >= min_size


def safe_name(name):
    name = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", name)
    return name.strip("_") or "roll"


def desktop_dir():
    d = Path.home() / "Desktop"
    return str(d) if d.is_dir() else str(Path.home())

def open_folder(path):
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])

def set_adaptive_window(win, width_ratio=0.72, height_ratio=0.82, min_width=900, min_height=720):
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()

    width = max(min_width, int(screen_width * width_ratio))
    height = max(min_height, int(screen_height * height_ratio))

    x = int((screen_width - width) / 2)
    y = int((screen_height - height) / 2)

    win.geometry(f"{width}x{height}+{x}+{y}")
    win.minsize(min_width, min_height)


def get_duration(path):
    result = subprocess.check_output([
        ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], encoding="utf-8", stderr=subprocess.DEVNULL)
    return float(result.strip())


def copy_file(src, dst):
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if not file_exists_ok(dst, min_size=100):
        raise RuntimeError(f"Не удалось скопировать файл: {dst}")
    return dst


def parse_start_end_text(full_text):
    """Возвращает (start_text, end_text, separator_count)."""
    separators = re.findall(r"-{3,}", full_text)
    if not separators:
        one = full_text.strip()
        if not one:
            raise RuntimeError("Текст пустой.")
        return one, None, 0
    parts = re.split(r"-{3,}", full_text)
    start_text = parts[0].strip()
    end_text = parts[-1].strip()
    if not start_text:
        raise RuntimeError("Нет текста до первого ------")
    if not end_text:
        raise RuntimeError("Нет текста после последнего ------")
    return start_text, end_text, len(separators)


def text_to_speech_mp3(text, voice_id, output_mp3, log):
    url = f"{ELEVEN_BASE_URL}/v1/text-to-speech/{voice_id}?output_format={ELEVEN_OUTPUT_FORMAT}"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    data = {
        "text": text,
        "model_id": ELEVEN_TTS_MODEL_ID,
        "voice_settings": {
            "stability": 0.55,
            "similarity_boost": 1.0,
            "style": 0.45,
            "use_speaker_boost": True,
        },
    }
    log("Генерирую аудио ElevenLabs...")
    res = requests.post(url, headers=headers, json=data, timeout=300)
    if res.status_code != 200:
        raise RuntimeError(f"ElevenLabs TTS error: {res.status_code}\n{res.text}")
    with open(output_mp3, "wb") as f:
        f.write(res.content)
    if not file_exists_ok(output_mp3):
        raise RuntimeError(f"Пустой mp3: {output_mp3}")
    log(f"Аудио готово: {get_duration(output_mp3):.2f} сек")
    return output_mp3


def generate_full_voice_to_desktop(api_key, voice_id, full_text, voice_name, log):
    global ELEVENLABS_API_KEY
    ELEVENLABS_API_KEY = api_key
    eleven_text = full_text.replace("------", '[пауза 3 сек]')
    save_dir = Path(desktop_dir()) / "ElevenLabs_Generated_Voices"
    ensure_dir(save_dir)
    safe_voice = safe_name(voice_name or voice_id)
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    output_mp3 = str(save_dir / f"{safe_voice}_{timestamp}_full_voice.mp3")
    text_to_speech_mp3(eleven_text, voice_id, output_mp3, log)
    return output_mp3


def convert_audio_to_wav(input_audio, output_wav):
    cmd = [ffmpeg_bin(), "-y", "-i", input_audio, "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", output_wav]
    run(cmd)
    if not file_exists_ok(output_wav):
        raise RuntimeError(f"Не удалось создать wav: {output_wav}")
    return output_wav

def convert_audio_to_wav_trimmed(input_audio, output_wav, start_sec="", end_sec=""):
    cmd = [ffmpeg_bin(), "-y", "-i", input_audio]
    if start_sec:
        cmd += ["-ss", str(float(start_sec))]
    if end_sec:
        cmd += ["-to", str(float(end_sec))]
    cmd += ["-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", output_wav]
    run(cmd)
    if not file_exists_ok(output_wav):
        raise RuntimeError(f"Не удалось создать wav: {output_wav}")
    return output_wav


def detect_silences(audio_path, log):
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-i", audio_path,
        "-af", f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_DURATION}",
        "-f", "null", "-"
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8", errors="replace")
    output = proc.stderr or ""
    starts, ends = [], []
    for line in output.splitlines():
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            starts.append(float(m_start.group(1)))
        m_end = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m_end:
            ends.append(float(m_end.group(1)))
    silences = []
    for i in range(min(len(starts), len(ends))):
        if ends[i] > starts[i]:
            silences.append({"start": starts[i], "end": ends[i]})
    log(f"Найдено пауз: {len(silences)}")
    for s in silences[:5]:
        log(f"Пауза: {s['start']:.2f} -> {s['end']:.2f}")
    return silences


def split_start_end_by_silence(full_voice_mp3, output_dir, log, expected_separators=None):
    silences = detect_silences(full_voice_mp3, log)
    if not silences:
        raise RuntimeError(
            "Не нашёл пауз в full_voice.mp3. "
            "Сгенерируй голос с разделителями ------."
        )

    # Отбрасываем хвостовую тишину (если она у самого конца файла)
    total_duration = get_duration(full_voice_mp3)
    silences = [
        s for s in silences
        if s["start"] > 0.5                       # не в начале файла
        and (total_duration - s["end"]) > 0.5     # не в конце файла
    ]
    log(f"После фильтра хвостовой/начальной тишины: {len(silences)} пауз")

    if not silences:
        raise RuntimeError(
            "Не нашёл внутренних пауз в full_voice.mp3. "
            "Возможно ты сгенерил голос без разделителей ------."
        )

    # Сверка с текстом
    if expected_separators is not None and expected_separators > 0:
        if len(silences) < expected_separators:
            raise RuntimeError(
                f"В тексте {expected_separators} разделителей ------, "
                f"а в аудио найдено только {len(silences)} пауз. "
                f"Похоже full_voice.mp3 сгенерирован не из этого текста."
            )
        log(f"Сверка с текстом: ожидалось {expected_separators} пауз, "
            f"в аудио найдено {len(silences)} — ок.")

    first = silences[0]
    last = silences[-1]
    start_cut = max(0.0, first["start"])
    end_cut = last["end"]

    part_start = str(Path(output_dir) / "part_start.wav")
    part_end = str(Path(output_dir) / "part_end.wav")
    run([ffmpeg_bin(), "-y", "-i", full_voice_mp3,
         "-ss", "0", "-to", str(round(start_cut, 3)),
         "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", part_start])
    run([ffmpeg_bin(), "-y", "-i", full_voice_mp3,
         "-ss", str(round(end_cut, 3)),
         "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", part_end])
    if not file_exists_ok(part_start):
        raise RuntimeError("Не удалось создать part_start.wav")
    if not file_exists_ok(part_end):
        raise RuntimeError("Не удалось создать part_end.wav")
    log(f"part_start.wav: {get_duration(part_start):.2f} сек")
    log(f"part_end.wav: {get_duration(part_end):.2f} сек")
    return part_start, part_end


def prepare_video_for_sync(input_video, audio_wav, output_video):
    """Готовит видео для отправки в Sync без потерь качества."""
    audio_duration = get_duration(audio_wav)
    target_duration = round(audio_duration + END_PADDING, 3)
    
    # Просто обрезаем видео по длине аудио, без перекодировки
    cmd = [
        ffmpeg_bin(), "-y",
        "-i", input_video,
        "-t", str(target_duration),
        "-c", "copy",                  # ← без перекодировки, оригинальное качество
        "-movflags", "+faststart",
        output_video,
    ]
    
    try:
        run(cmd)
    except subprocess.CalledProcessError:
        # Если -c copy не сработал (редкий кодек) — fallback на перекодировку
        cmd = [
            ffmpeg_bin(), "-y",
            "-i", input_video,
            "-t", str(target_duration),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_video,
        ]
        run(cmd)
    
    if not file_exists_ok(output_video):
        raise RuntimeError(f"Не удалось подготовить видео для Sync: {output_video}")
    return output_video

def _init_cloudinary():
    import cloudinary
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
    )

def upload_to_cloudinary(file_path, log):
    """Загружает файл на Cloudinary. Возвращает (url, public_id).
    Файлы > 100MB грузятся чанками через upload_large."""
    import cloudinary.uploader
    _init_cloudinary()
    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    log(f"Загружаю {Path(file_path).name} ({size_mb:.1f} MB) на Cloudinary...")
    opts = dict(resource_type="video", invalidate=True)
    if size_mb > 90:
        result = cloudinary.uploader.upload_large(file_path, **opts, chunk_size=50 * 1024 * 1024)
    else:
        result = cloudinary.uploader.upload(file_path, **opts)
    url = result["secure_url"]
    public_id = result["public_id"]
    log(f"  → {url}")
    return url, public_id

def delete_from_cloudinary(public_id, log):
    """Удаляет файл с Cloudinary по public_id."""
    import cloudinary.uploader
    _init_cloudinary()
    try:
        cloudinary.uploader.destroy(public_id, resource_type="video", invalidate=True)
        log(f"Cloudinary: удалён {public_id}")
    except Exception as e:
        log(f"Cloudinary delete error ({public_id}): {e}")

def apply_lipsync_sync(video_in, audio_wav, final_out, log):
    """Отправляет видео и аудио в Sync через URL-загрузку (без лимита 20MB)."""
    headers = {"x-api-key": SYNC_API_KEY, "Content-Type": "application/json"}
    last_error = None

    for attempt in range(1, SYNC_RETRIES + 1):
        video_pub_id = None
        audio_pub_id = None
        try:
            log(f"Отправляю в Sync... попытка {attempt}/{SYNC_RETRIES}")

            # 1. Загружаем на Cloudinary → получаем URL + public_id для удаления
            video_url, video_pub_id = upload_to_cloudinary(video_in, log)
            audio_url, audio_pub_id = upload_to_cloudinary(audio_wav, log)

            # 2. Отправляем в Sync только URL
            payload = {
                "model": SYNC_MODEL,
                "input": [
                    {"type": "video", "url": video_url},
                    {"type": "audio", "url": audio_url},
                ],
            }

            res = requests.post(
                SYNC_GENERATE_URL,
                headers=headers,
                json=payload,
                timeout=300,
            )
            log(f"Sync create response: {res.status_code}")
            log(res.text[:1500])
            if res.status_code not in [200, 201]:
                raise RuntimeError(f"Sync create error: {res.status_code}\n{res.text}")

            job_id = res.json().get("id")
            if not job_id:
                raise RuntimeError(f"Sync не вернул job id: {res.text}")
            log(f"Sync job: {job_id}")

            status_url = f"{SYNC_GENERATE_URL}/{job_id}"
            started = time.time()
            last_state = None
            while True:
                if time.time() - started > SYNC_TIMEOUT_SEC:
                    raise RuntimeError("Sync timeout.")
                status_res = requests.get(status_url, headers=headers, timeout=60)
                if status_res.status_code != 200:
                    raise RuntimeError(f"Sync status error: {status_res.status_code}\n{status_res.text}")
                status = status_res.json()
                state = status.get("status")
                if state != last_state:
                    log(f"Sync status: {state}")
                    last_state = state
                if state == "COMPLETED":
                    video_url_out = status.get("outputUrl") or status.get("output_url")
                    if not video_url_out:
                        raise RuntimeError(f"Sync completed без outputUrl: {status}")
                    video_data = requests.get(video_url_out, timeout=300).content
                    with open(final_out, "wb") as f:
                        f.write(video_data)
                    if not file_exists_ok(final_out):
                        raise RuntimeError(f"Sync скачал пустое видео: {final_out}")
                    log("Lipsync готов.")
                    return final_out
                if state in ["FAILED", "REJECTED"]:
                    raise RuntimeError(f"Sync failed: {status}")
                time.sleep(SYNC_POLL_INTERVAL_SEC)
        except Exception as e:
            last_error = e
            log(f"Sync ошибка на попытке {attempt}: {e}")
            if attempt < SYNC_RETRIES:
                log("Пауза 60 секунд перед повтором...")
                time.sleep(60)
        finally:
            # Удаляем файлы с Cloudinary всегда — и при успехе и при ошибке
            for pub_id in (video_pub_id, audio_pub_id):
                if pub_id:
                    delete_from_cloudinary(pub_id, log)

    raise RuntimeError(f"Sync не сработал после {SYNC_RETRIES} попыток: {last_error}")


def enhance_video(input_video, output_video):
    vf = f"{SHARPEN_FILTER},format=yuv420p,setpts=PTS-STARTPTS"
    cmd = [
        ffmpeg_bin(), "-y", "-i", input_video,
        "-vf", vf, "-af", "aresample=44100,asetpts=PTS-STARTPTS",
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-movflags", "+faststart", output_video,
    ]
    run(cmd)
    if not file_exists_ok(output_video):
        raise RuntimeError(f"Не создан финал: {output_video}")
    return output_video


def process_lipsync(video_path, audio_wav, output_mp4, temp_dir, log):
    stem = safe_name(Path(output_mp4).name)
    sync_video = str(Path(temp_dir) / (stem + "_sync_input.mp4"))
    raw_video = str(Path(temp_dir) / (stem + "_raw.mp4"))
    prepare_video_for_sync(video_path, audio_wav, sync_video)
    apply_lipsync_sync(sync_video, audio_wav, raw_video, log)
    enhance_video(raw_video, output_mp4)
    return output_mp4


def _ensure_ytdlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        import subprocess
        import sys
        subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"], check=True)
        import yt_dlp
        return yt_dlp


def _ytdlp_ffmpeg_opts():
    def _test_ffmpeg(path):
        try:
            result = subprocess.run(
                [path, "-version"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    candidates = []

    local = Path(app_base_dir()) / exe_name
    if local.exists():
        candidates.append(str(local.parent))

    candidates.append("/opt/homebrew/bin")
    candidates.append("/usr/local/bin")

    found = shutil.which("ffmpeg")
    if found:
        candidates.append(str(Path(found).parent))

    for directory in candidates:
        ffmpeg_path = str(Path(directory) / exe_name)
        if Path(ffmpeg_path).exists() and _test_ffmpeg(ffmpeg_path):
            return directory

    return None


def find_best_browser_with_google():
    """
    Ищет браузер где выполнен вход в Google аккаунт.
    Возвращает название браузера для yt-dlp или None.
    """
    if sys.platform == "darwin":
        candidates = ["chrome", "firefox", "safari", "edge", "brave", "chromium"]
    elif sys.platform == "win32":
        candidates = ["chrome", "firefox", "edge", "brave", "chromium"]
    else:
        candidates = ["chrome", "firefox", "chromium", "brave", "edge"]

    for browser in candidates:
        try:
            import yt_dlp
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "cookiesfrombrowser": (browser,),
                "simulate": True,
                "skip_download": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                jar = ydl.cookiejar
                for cookie in jar:
                    if "google.com" in cookie.domain or "youtube.com" in cookie.domain:
                        if cookie.name in ("SID", "HSID", "SSID", "LOGIN_INFO", "__Secure-1PSID"):
                            return browser
        except Exception:
            continue
    return None


def download_from_url(url, output_dir, mode, denoise, log, browser=None):
    """
    Скачивает видео или аудио через yt-dlp в максимальном качестве.
    mode: 'video' | 'audio'
    denoise: bool — применить голосовой денойз к аудио
    browser: str | None — браузер для cookiesfrombrowser
    """
    homebrew_dirs = ["/opt/homebrew/bin", "/usr/local/bin"]
    for d in homebrew_dirs:
        if os.path.exists(d) and d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    yt_dlp = _ensure_ytdlp()
    ensure_dir(output_dir)

    # Добавляем директорию с бандленным ffmpeg в PATH для yt-dlp
    base = str(Path(app_base_dir()))
    env_path = os.environ.get("PATH", "")
    if base not in env_path.split(os.pathsep):
        os.environ["PATH"] = base + os.pathsep + env_path

    timestamp = time.strftime("%d-%m-%Y_%H-%M-%S")
    ffmpeg_dir = _ytdlp_ffmpeg_opts()

    last_pct = {"v": ""}

    def progress_hook(d):
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "").strip()
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            line = f"  {pct}  {speed}  ETA {eta}"
            if pct != last_pct["v"]:
                last_pct["v"] = pct
                log(line)
        elif d["status"] == "finished":
            log(f"  Скачано: {Path(d.get('filename', '')).name}")

    class _YTLogger:
        def debug(self, msg):
            if msg.startswith("[debug]"):
                return
            log(msg)
        def warning(self, msg):
            log(f"[предупреждение] {msg}")
        def error(self, msg):
            log(f"[ошибка] {msg}")

    common = {
        "quiet": True,
        "no_warnings": False,
        "logger": _YTLogger(),
        "progress_hooks": [progress_hook],
    }
    if ffmpeg_dir:
        common["ffmpeg_location"] = ffmpeg_dir
    if browser:
        common["cookiesfrombrowser"] = (browser.lower(),)

    if mode == "video":
        outtmpl = str(Path(output_dir) / f"{timestamp}_%(title).80s.%(ext)s")
        opts = {
            **common,
            "format": (
                "bestvideo[ext=mp4][vcodec^=avc]+bestaudio[ext=m4a]/"
                "bestvideo[ext=mp4][vcodec^=avc]+bestaudio/"
                "bestvideo[vcodec^=avc]+bestaudio/"
                "bestvideo+bestaudio/"
                "best"
            ),
            "merge_output_format": "mp4",
            "format_sort": ["res", "ext:mp4:m4a"],
            "outtmpl": outtmpl,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "postprocessors": [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }],
            "postprocessor_args": {
                "videoconvertor": ["-vcodec", "libx264", "-acodec", "aac"],
                "merger": ["-c:v", "copy", "-c:a", "aac"],
            },
        }
        log("Получаю информацию о видео...")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                filename = str(Path(filename).with_suffix(".mp4"))
        log(f"Сохранено: {filename}")
        return filename, None

    elif mode == "audio":
        outtmpl = str(Path(output_dir) / f"{timestamp}_%(title).80s.%(ext)s")
        opts = {
            **common,
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "restrictfilenames": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }],
        }
        log("Получаю информацию об аудио...")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            base_name = ydl.prepare_filename(info)
        filename = str(Path(base_name).with_suffix(".mp3"))

        if denoise and os.path.exists(filename):
            log(f"Передаю в audio-separator: {Path(filename).name}")
            vocals_out = str(Path(filename).with_suffix("")) + "_vocals.mp3"
            _separate_vocals(filename, vocals_out, log)
            os.remove(filename)
            filename = vocals_out

        log(f"Сохранено: {filename}")
        return filename, None

    else:  # split — видео как есть + аудио отдельно
        outtmpl = str(Path(output_dir) / f"{timestamp}_%(title).80s.%(ext)s")
        opts = {
            **common,
            "format": (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[ext=mp4]+bestaudio/"
                "bestvideo+bestaudio/best"
            ),
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "restrictfilenames": True,
        }
        log("Скачиваю видео в максимальном качестве...")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                filename = str(Path(filename).with_suffix(".mp4"))

        video_out, audio_out = _split_video_audio(filename, output_dir, denoise, log)
        try:
            os.remove(filename)
        except Exception:
            pass
        return video_out, audio_out


def _separate_vocals(input_path, output_path, log):
    """Выделяет голос через audio-separator. Сохраняет mp3 в output_path."""
    import sys as _sys
    try:
        from audio_separator.separator import Separator
    except ImportError:
        log("Устанавливаю audio-separator...")
        subprocess.run(
            [_sys.executable, "-m", "pip", "install", "audio-separator[cpu]"],
            check=True,
        )
        from audio_separator.separator import Separator

    in_p = Path(input_path)
    log(f"[audio-separator] Входной файл: {in_p.name}"
        f" ({in_p.suffix.upper() or 'нет расширения'})")

    tmp_dir = tempfile.mkdtemp()
    try:
        # ШАГ 1: конвертируем в WAV — audio-separator работает лучше с WAV
        wav_path = str(Path(tmp_dir) / "input.wav")
        log("[audio-separator] ШАГ 1: конвертирую в WAV 44100 Hz...")
        run([ffmpeg_bin(), "-y", "-i", input_path, "-ar", "44100", "-ac", "2", wav_path])
        if not os.path.exists(wav_path):
            raise RuntimeError(f"ffmpeg не создал WAV: {wav_path}")
        log(f"[audio-separator] WAV готов: {Path(wav_path).name}")

        # ШАГ 2: разделяем голос и музыку
        log("[audio-separator] ШАГ 2: запускаю разделение...")
        sep = Separator(output_dir=tmp_dir)
        sep.load_model()
        files = sep.separate(wav_path)

        log(f"[audio-separator] Сгенерированные файлы: {[Path(f).name for f in files]}")

        # ШАГ 3: ищем файл с голосом (Vocals в имени)
        vocals = None
        for f in files:
            full = f if Path(f).is_absolute() else str(Path(tmp_dir) / Path(f).name)
            if "vocal" in Path(full).name.lower() and os.path.exists(full):
                vocals = full
                break

        if not vocals:
            raise RuntimeError(
                f"audio-separator: файл с голосом не найден. "
                f"Файлы: {[Path(f).name for f in files]}"
            )
        log(f"[audio-separator] ШАГ 3: голос найден: {Path(vocals).name}")

        # ШАГ 4: конвертируем результат в mp3
        run([ffmpeg_bin(), "-y", "-i", vocals, "-q:a", "0", output_path])
        log(f"Голос готов: {Path(output_path).name}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _split_video_audio(video_path, output_dir, denoise, log):
    """
    Разделяет видео на:
      - VIDEO.mp4  — оригинальное видео со всем оригинальным звуком
      - AUDIO.mp3  — только аудиодорожка, с денойзом если выбрано
    """
    ensure_dir(output_dir)
    base = safe_name(Path(video_path).stem)[:60]
    video_out = str(Path(output_dir) / (base + "_VIDEO.mp4"))
    audio_out = str(Path(output_dir) / (base + "_AUDIO.mp3"))

    safe_video = str(Path(tempfile.mkdtemp()) / "input.mp4")
    shutil.copy2(video_path, safe_video)

    log("Копирую оригинальное видео (с голосом)...")
    shutil.copy2(video_path, video_out)

    log("Извлекаю аудио в максимальном качестве...")
    try:
        subprocess.run([
            ffmpeg_bin(), "-y", "-i", safe_video,
            "-vn", "-acodec", "libmp3lame", "-q:a", "0", audio_out
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        try:
            tmp_wav = audio_out + ".wav"
            subprocess.run([
                ffmpeg_bin(), "-y", "-i", safe_video,
                "-vn", "-acodec", "pcm_s16le", "-ar", "44100", tmp_wav
            ], check=True, capture_output=True)
            subprocess.run([
                ffmpeg_bin(), "-y", "-i", tmp_wav,
                "-acodec", "libmp3lame", "-q:a", "0", audio_out
            ], check=True, capture_output=True)
            try:
                os.remove(tmp_wav)
            except Exception:
                pass
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Не удалось извлечь аудио: {e}")

    if denoise:
        log(f"Передаю в audio-separator: {Path(audio_out).name}")
        vocals_out = str(Path(output_dir) / (base + "_vocals.mp3"))
        _separate_vocals(audio_out, vocals_out, log)
        os.remove(audio_out)
        audio_out = vocals_out

    try:
        os.remove(safe_video)
    except Exception:
        pass

    if not file_exists_ok(video_out):
        raise RuntimeError(f"Не удалось сохранить видео: {video_out}")
    if not file_exists_ok(audio_out):
        raise RuntimeError(f"Не удалось создать аудиодорожку: {audio_out}")
    return video_out, audio_out


def make_neon_logo(text="SheqelMotion", width=720, height=140):

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    font = None
    font_size = 70
    candidates = []
    if sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
    ]

    elif sys.platform == "win32":
        
        candidates = [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
    ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    for path in candidates:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, font_size)
                break
            except Exception:
                continue

    if font is None:
        font = ImageFont.load_default()

    temp = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(temp)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    x = (width - text_w) // 2
    y = (height - text_h) // 2

    # Electric neon: deep blue outer → cyan mid → white-blue core
    for blur, alpha in [(32, 70), (22, 110), (14, 150)]:
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.text((x, y), text, font=font, fill=(0, 60, 200, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(blur))
        img.alpha_composite(glow)

    for blur, alpha in [(9, 180), (5, 210)]:
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.text((x, y), text, font=font, fill=(0, 160, 255, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(blur))
        img.alpha_composite(glow)

    for blur, alpha in [(3, 235), (1, 255)]:
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.text((x, y), text, font=font, fill=(80, 210, 255, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(blur))
        img.alpha_composite(glow)

    draw = ImageDraw.Draw(img)
    draw.text((x, y), text, font=font, fill=(200, 235, 255, 255))

    return img


try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

_BaseApp = TkinterDnD.Tk if HAS_DND else ctk.CTk


def delete_elevenlabs_voice(voice_id, api_key):
    url = f"{ELEVEN_BASE_URL}/v1/voices/{voice_id}"
    headers = {"xi-api-key": api_key}
    res = requests.delete(url, headers=headers, timeout=30)
    return res.status_code in [200, 204]


def fetch_elevenlabs_balance(api_key) -> dict | None:
    try:
        headers = {"xi-api-key": api_key}
        print(f"[balance] запрос с ключом {api_key[:8]}...")
        res = requests.get(
            f"{ELEVEN_BASE_URL}/v1/user/subscription",
            headers=headers, timeout=10,
        )
        print(f"[balance] статус: {res.status_code}")
        print(f"[balance] ответ: {res.text[:300]}")
        if res.status_code != 200:
            return None
        data = res.json()
        character_count = data.get("character_count", 0)
        character_limit = data.get("character_limit", 0)
        tier = data.get("tier", "")
        next_reset = data.get("next_character_count_reset_unix", 0)
        remaining = max(0, character_limit - character_count)
        return {
            "used": character_count,
            "limit": character_limit,
            "remaining": remaining,
            "tier": tier,
            "next_reset": next_reset,
        }
    except Exception:
        return None


class LipsyncTwoModeApp(_BaseApp):
    def __init__(self):
        super().__init__()
        self.title("SheqelMotion Studio")
        set_adaptive_window(self)   
        self.resizable(True, True)
        self.configure(fg_color=BG)
        # Принудительно поднимаем окно на передний план (фикс для macOS)
        self.update_idletasks()
        self.lift()
        self.attributes('-topmost', True)
        self.after(200, lambda: self.attributes('-topmost', False))
        self.focus_force()
        # Фикс: клик по иконке в Dock восстанавливает свёрнутое/скрытое окно
        try:
            self.createcommand('tk::mac::ReopenApplication', self._reopen_window)
        except Exception:
            pass
        self.api_key = ""
        self._el_balance = None
        self.main_voice_id = ""
        self.main_voice_name = ""
        self.rolls = []
        self.next_roll_id = 1
        self.selected_roll_id = None
        self._active_panel = None
        self._stop_download = False
        self._detected_browser = None
        self._browser_checked = False
        self.log_queue = queue.Queue()
        self.log_window = None
        self.log_box = None
        self.is_processing = False
        self.sync_key = SYNC_API_KEY
        self.cloudinary_cloud_name = ""
        self.cloudinary_api_key = ""
        self.cloudinary_api_secret = ""
        self.preview_process = None
        self.preview_voice_id = None
        self.voice_expiry_list = []
        self.build_ui()
        self.add_roll()
        self.poll_logs()
        self.load_config()
        self.update_status()
        self.after(3000, lambda: self.check_for_updates(silent=True))
        self.after(500, self._check_just_updated)
        self.after(2000, self._cleanup_expired_voices_on_start)
        self.after(2000, self._check_elevenlabs)
        self.after(3000, self._refresh_balance_badge)
        self.after(4000, self._check_ytdlp_update)
        threading.Thread(target=self._voice_expiry_watcher, daemon=True).start()


    def _reopen_window(self):
        """Вызывается при клике на иконку Dock — восстанавливает скрытое/свёрнутое окно."""
        self.deiconify()
        self.lift()
        for child in self.winfo_children():
            if isinstance(child, ctk.CTkToplevel) and child.winfo_exists():
                child.deiconify()
                child.lift()
                child.focus_force()
                return
        self.focus_force()

    def _parse_version(self, v):
        #Превращает '1.2.10' в (1, 2, 10) для корректного сравнения.
        try:
            return tuple(int(x) for x in str(v).split(".") if x.isdigit())
        except Exception:
            return (0, 0, 0)

    def check_for_updates(self, silent=False):
        """Только проверяет манифест. Сам файл не качает."""
        try:
            url = f"{UPDATE_MANIFEST_URL}?t={int(time.time())}"
            res = requests.get(url, timeout=10, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
            res.raise_for_status()
            manifest = res.json()
        except Exception as e:
            if not silent:
                messagebox.showerror("Ошибка проверки",
                                     f"Не удалось проверить обновления:\n{e}")
            return

        latest = manifest.get("latest_version", "0.0.0")
        notes = manifest.get("release_notes", "")
        force = manifest.get("force_update", False)
        min_required = manifest.get("min_required_version", "0.0.0")
        expected_sha256 = manifest.get("sha256", "")

        current_v = self._parse_version(APP_VERSION)
        latest_v = self._parse_version(latest)
        min_v = self._parse_version(min_required)

        if latest_v <= current_v:
            if not silent:
                messagebox.showinfo("Обновления",
                                    f"У тебя последняя версия: {APP_VERSION}")
            return

        self._show_update_dialog(latest, notes, force or (current_v < min_v), expected_sha256)

    def _show_update_dialog(self, latest_version, notes, is_forced, expected_sha256=""):
        win = ctk.CTkToplevel(self)
        win.title("Доступно обновление")
        win.geometry("560x500")
        win.configure(fg_color=BG)
        win.transient(self)
        win.after(100, win.grab_set)
        win.protocol("WM_DELETE_WINDOW", win.destroy)

        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self.label(frame, "🎉 Доступно обновление", size=22, weight="bold").pack(anchor="w", padx=20, pady=(20, 8))
        self.label(frame, f"Текущая версия:  {APP_VERSION}",
                   size=12, color=MUTED).pack(anchor="w", padx=20)
        self.label(frame, f"Новая версия:  {latest_version}",
                   size=15, weight="bold", color="#86efac").pack(anchor="w", padx=20, pady=(0, 14))

        self.label(frame, "Что нового:", size=13, weight="bold").pack(anchor="w", padx=20)
        notes_box = ctk.CTkTextbox(frame, fg_color="#1a1a1a", text_color="#e5e5e5",
                                   border_width=0, height=140,
                                   font=ctk.CTkFont(size=12), wrap="word")
        notes_box.pack(fill="both", expand=True, padx=20, pady=(6, 10))
        notes_box.insert("1.0", notes or "—")
        notes_box.configure(state="disabled")

        progress_label = self.label(frame, "", size=12, color=MUTED)
        progress_label.pack(anchor="w", padx=20, pady=(0, 6))

        btn_row = ctk.CTkFrame(frame, fg_color=PANEL)
        btn_row.pack(fill="x", padx=20, pady=(0, 16))

        def do_update():
            update_btn.configure(state="disabled")
            def run():
                try:
                    self._download_and_replace(latest_version, expected_sha256, progress_label, win)
                except Exception as e:
                    self.after(0, lambda: messagebox.showerror("Ошибка обновления", str(e), parent=win))
                    self.after(0, lambda: update_btn.configure(state="normal"))
            threading.Thread(target=run, daemon=True).start()

        if not is_forced:
            self.button(btn_row, "Позже", win.destroy,
                        color=BTN, hover=BTN_HOVER, width=120).pack(side="right", padx=(8, 0))

        update_btn = self.button(btn_row, "Скачать обновление", do_update,
                                 color=BTN_OK, hover=BTN_OK_HOVER, width=220)
        update_btn.pack(side="right")

        if is_forced:
            self.label(frame, "⚠ Это обязательное обновление.",
                       size=11, color="#fca5a5").pack(anchor="w", padx=20, pady=(0, 6))
            win.protocol("WM_DELETE_WINDOW", lambda: None)

    def _download_and_replace(self, new_version, expected_sha256, progress_label, parent_win):
        """Качает app.py, проверяет целостность и подпись, заменяет, перезапускает."""
        if getattr(sys, "frozen", False):
            messagebox.showinfo(
                "Обновление",
                f"Доступна версия {new_version}.\n\n"
                "Автообновление недоступно в .exe/.app сборке.\n"
                "Скачай новый файл вручную с GitHub и замени текущий.",
                parent=parent_win,
            )
            parent_win.destroy()
            return

        raw_url = "https://raw.githubusercontent.com/1Sheqel/Sheqel/main/app.py"

        # 1. Скачиваем во временный файл
        progress_label.configure(text="Скачиваю обновление...")
        parent_win.update()
        app_dir = Path(app_base_dir())
        temp_path = str(app_dir / "app.py.new")
        try:
            with requests.get(raw_url, timeout=60, stream=True) as r:
                r.raise_for_status()
                with open(temp_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
        except Exception as e:
            raise RuntimeError(f"Не удалось скачать: {e}")

        # 2. Проверяем SHA256 (защита от подмены/повреждения файла)
        if expected_sha256:
            progress_label.configure(text="Проверяю целостность файла...")
            parent_win.update()
            with open(temp_path, "rb") as f:
                actual_sha256 = hashlib.sha256(f.read()).hexdigest()
            if actual_sha256 != expected_sha256:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                raise RuntimeError(
                    f"SHA256 не совпадает — файл мог быть повреждён или подменён.\n"
                    f"Ожидалось: {expected_sha256[:24]}…\n"
                    f"Получено:  {actual_sha256[:24]}…"
                )

        # 3. Проверяем что это валидный Python
        progress_label.configure(text="Проверяю синтаксис...")
        parent_win.update()
        try:
            with open(temp_path, "r", encoding="utf-8") as f:
                new_code = f.read()
            compile(new_code, temp_path, "exec")
        except SyntaxError as e:
            try:
                os.remove(temp_path)
            except Exception:
                pass
            raise RuntimeError(f"Скачанный код содержит синтаксическую ошибку:\n{e}")

        # 4. Проверяем версию в самом коде
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', new_code)
        if m:
            actual = m.group(1)
            if self._parse_version(actual) <= self._parse_version(APP_VERSION):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                raise RuntimeError(
                    f"Скачанный код не новее текущего "
                    f"(в файле: {actual}, у тебя: {APP_VERSION})."
                )

        # 5. Заменяем app.py
        progress_label.configure(text="Применяю обновление...")
        parent_win.update()
        app_path = str(app_dir / "app.py")
        backup_path = str(app_dir / "app.py.bak")
        try:
            if os.path.exists(app_path):
                shutil.copy2(app_path, backup_path)
            os.replace(temp_path, app_path)
        except Exception as e:
            raise RuntimeError(f"Не удалось заменить файл: {e}")

        # 6. Записываем флаг «только что обновились»
        flag_path = str(app_dir / ".just_updated")
        try:
            with open(flag_path, "w", encoding="utf-8") as f:
                f.write(new_version)
        except Exception:
            pass

        # 7. Перезапускаем приложение
        progress_label.configure(text="Перезапускаюсь...")
        parent_win.update()
        time.sleep(1)

        try:
            # Останавливаем тек. preview
            self.stop_preview()
        except Exception:
            pass

        python_exe = sys.executable
        if sys.platform == "win32":
            subprocess.Popen([python_exe, app_path])
            self.destroy()
            sys.exit(0)
        try:
            os.execv(python_exe, [python_exe, app_path])
        except Exception:
            subprocess.Popen([python_exe, app_path])
            self.destroy()
            sys.exit(0)

    def _check_just_updated(self):
        """Если на прошлом старте было обновление — показываем toast."""
        flag_path = Path(app_base_dir()) / ".just_updated"
        if flag_path.exists():
            try:
                with open(flag_path, "r", encoding="utf-8") as f:
                    updated_to = f.read().strip()
                os.remove(flag_path)
                # Удаляем бэкап если всё ок
                backup = Path(app_base_dir()) / "app.py.bak"
                if backup.exists():
                    try:
                        os.remove(backup)
                    except Exception:
                        pass
                self.after(800, lambda: messagebox.showinfo(
                    "✓ Обновлено",
                    f"Приложение успешно обновлено до версии {updated_to}"
                ))
            except Exception:
                pass

    def _check_elevenlabs(self):
        if not self.api_key:
            return

        def run():
            headers = {"xi-api-key": self.api_key}
            try:
                res = requests.get(
                    f"{ELEVEN_BASE_URL}/v1/user",
                    headers=headers, timeout=10,
                )
            except Exception:
                return

            if res.status_code != 200:
                return

            try:
                models_res = requests.get(
                    f"{ELEVEN_BASE_URL}/v1/models",
                    headers=headers, timeout=10,
                )
                if models_res.status_code == 200:
                    ids = [m.get("model_id") for m in models_res.json()]
                    if ELEVEN_TTS_MODEL_ID not in ids:
                        self.after(0, lambda: messagebox.showwarning(
                            "ElevenLabs",
                            f"Твой план ElevenLabs не поддерживает {ELEVEN_TTS_MODEL_ID}.\n"
                            "Нужен план Creator или выше.",
                        ))
            except Exception:
                pass
            self.after(0, self._refresh_balance_badge)

        threading.Thread(target=run, daemon=True).start()

    def _check_ytdlp_update(self):
        def run():
            try:
                import yt_dlp
                current = yt_dlp.version.__version__

                res = requests.get(
                    "https://pypi.org/pypi/yt-dlp/json",
                    timeout=10
                )
                if res.status_code != 200:
                    return

                latest = res.json()["info"]["version"]

                if current == latest:
                    self.log(f"yt-dlp актуален: {current}")
                    return

                self.log(f"yt-dlp устарел: {current} → {latest}. Обновляю...")

                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install",
                     "-U", "yt-dlp", "--quiet"],
                    capture_output=True,
                    encoding="utf-8",
                    timeout=120,
                )

                if result.returncode == 0:
                    self.log(f"yt-dlp обновлён до {latest} ✓")
                    self.after(0, lambda v=latest: self.status_label.configure(
                        text=f"yt-dlp обновлён до {v}"
                    ))
                    self.after(5000, self.update_status)
                else:
                    self.log(f"Ошибка обновления yt-dlp: {result.stderr[:200]}")

            except Exception as e:
                self.log(f"yt-dlp check error: {e}")

        threading.Thread(target=run, daemon=True).start()

    def button(self, parent, text, command, color=BTN_PRIMARY, hover=BTN_PRIMARY_HOVER, width=150):
        return ctk.CTkButton(parent, text=text, command=command, fg_color=color, hover_color=hover, text_color="white", corner_radius=20, height=38, width=width, font=ctk.CTkFont(size=14, weight="bold"))

    def label(self, parent, text, size=14, weight="normal", color=TEXT):
        return ctk.CTkLabel(parent, text=text, text_color=color, font=ctk.CTkFont(size=size, weight=weight), anchor="w", justify="left")

    def _fix_paste(self, widget):
        def do_paste(event=None):
            try:
                text = widget.clipboard_get()
                widget.insert("insert", text)
            except Exception:
                pass
            return "break"
        widget.bind("<Command-v>", do_paste)
        widget.bind("<Command-V>", do_paste)
        widget.bind("<Control-v>", do_paste)
        widget.bind("<Control-V>", do_paste)

    def _add_copy_menu(self, widget, get_text_fn):
        import tkinter as tk
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Копировать", command=lambda: (
            self.clipboard_clear(),
            self.clipboard_append(get_text_fn()),
        ))
        def show_menu(event):
            menu.tk_popup(event.x_root, event.y_root)
        widget.bind("<Button-2>", show_menu)
        widget.bind("<Button-3>", show_menu)

    def _show_dnd_tooltip(self, widget, msg):
        try:
            tip = ctk.CTkToplevel(self)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tip.geometry(f"+{x}+{y}")
            tip.configure(fg_color="#2d5a27")
            ctk.CTkLabel(tip, text=f"✓ {msg}", text_color="white",
                         font=ctk.CTkFont(size=12)).pack(padx=12, pady=8)
            self.after(2000, tip.destroy)
        except Exception:
            pass

    def _apply_dnd(self, widget, roll_fn, also_select=False):
        if not HAS_DND:
            return
        def on_drop(event):
            roll = roll_fn()
            if roll is None:
                return
            if also_select:
                self.selected_roll_id = roll["id"]
            files = re.findall(r'\{[^}]+\}|\S+', event.data)
            if not files:
                return
            filepath = files[0].strip('{}')
            ext = Path(filepath).suffix.lower()
            if ext in ('.mp3', '.wav', '.m4a', '.aac'):
                roll["voice_file"] = filepath
                msg = f"Голос: {Path(filepath).name}"
            elif ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm'):
                if roll["mode"] == "1 видео":
                    roll["video_single"] = filepath
                    msg = f"Видео: {Path(filepath).name}"
                else:
                    if not roll["video_start"]:
                        roll["video_start"] = filepath
                        msg = f"Видео начало: {Path(filepath).name}"
                    else:
                        roll["video_end"] = filepath
                        msg = f"Видео конец: {Path(filepath).name}"
            else:
                return
            roll["status"] = "Ожидает"
            self.refresh_rolls()
            self._show_dnd_tooltip(widget, msg)
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind('<<Drop>>', on_drop)

    def build_ui(self):
        # Top accent strip
        strip = ctk.CTkFrame(self, height=4, fg_color="#0095ff", corner_radius=0)
        strip.pack(fill="x")
        strip.pack_propagate(False)

        # Main body: sidebar (left) + content (right)
        body = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        body.pack(fill="both", expand=True)

        # ── Sidebar ──────────────────────────────────────────────────────────
        sidebar = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=0, width=200)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Logo — render full-size, scale down via CTkImage to fit sidebar
        logo_img = make_neon_logo("SheqelMotion", 720, 120)
        logo = ctk.CTkImage(light_image=logo_img, dark_image=logo_img, size=(176, 34))
        ctk.CTkLabel(sidebar, image=logo, text="").pack(anchor="center", padx=10, pady=(20, 8))

        # Separator
        ctk.CTkFrame(sidebar, height=1, fg_color="#2a2d3e", corner_radius=0).pack(
            fill="x", padx=12, pady=(0, 4))

        def sb_header(text):
            ctk.CTkLabel(
                sidebar, text=text, text_color=MUTED,
                font=ctk.CTkFont(size=10, weight="bold"), anchor="w",
            ).pack(fill="x", padx=14, pady=(10, 2))

        def sb_btn(text, command, color="transparent", hover=BTN_HOVER):
            btn = ctk.CTkButton(
                sidebar, text=f"  {text}", command=command,
                fg_color=color, hover_color=hover,
                text_color=TEXT, anchor="w",
                corner_radius=8, height=36,
                font=ctk.CTkFont(size=13),
            )
            btn.pack(fill="x", padx=8, pady=2)
            return btn

        # ⚙️ НАСТРОЙКИ
        sb_header("⚙️  НАСТРОЙКИ")
        self.api_btn = sb_btn("API key", self.show_api_key_panel)
        self.sync_btn = sb_btn("Sync key", self.show_sync_key_panel)
        self.cloudinary_btn = sb_btn("Cloudinary key", self.show_cloudinary_panel)

        # 🎙 ГОЛОС
        sb_header("🎙  ГОЛОС")
        self.voice_btn = sb_btn("Сгенерировать голос", self.show_voice_gen_panel)
        self.clone_btn = sb_btn("Клонировать голос", self.show_voice_clone_panel)

        # 📥 МЕДИА
        sb_header("📥  МЕДИА")
        self.dl_btn = sb_btn("⬇  Скачать видео", self.show_downloader_panel)

        # ➕ ЗАДАЧИ
        sb_header("➕  ЗАДАЧИ")
        self.add_btn = sb_btn("Новая задача", self.add_roll,
                              color=BTN_PRIMARY, hover=BTN_PRIMARY_HOVER)
        self.clear_btn = sb_btn("Очистить всё", self.clear_rolls)
        self.update_btn = sb_btn("🔄  Обновить",
                                 lambda: self.check_for_updates(silent=False))

        # Thin vertical divider
        ctk.CTkFrame(body, width=1, fg_color="#2a2d3e", corner_radius=0).pack(
            side="left", fill="y")

        # ── Right section: scene list + detail + bottom bar ───────────────────
        right_section = ctk.CTkFrame(body, fg_color=BG, corner_radius=0)
        right_section.pack(side="left", fill="both", expand=True)

        # Columns row (scene list + detail)
        columns_row = ctk.CTkFrame(right_section, fg_color=BG, corner_radius=0)
        columns_row.pack(fill="both", expand=True)

        # ── Scene list (middle panel, 300px) ─────────────────────────────────
        scene_list_frame = ctk.CTkFrame(columns_row, fg_color=PANEL, corner_radius=0, width=300)
        scene_list_frame.pack(side="left", fill="y")
        scene_list_frame.pack_propagate(False)

        self.label(scene_list_frame, "Сцены", size=13, weight="bold", color=MUTED).pack(
            anchor="w", padx=14, pady=(14, 6))

        self.scene_list_scroll = ctk.CTkScrollableFrame(
            scene_list_frame, fg_color=PANEL,
            scrollbar_button_color="#3d3d6a",
            scrollbar_button_hover_color="#ff7eb6",
        )
        self.scene_list_scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # "Новая задача" button pinned at bottom of scene list
        add_scene_btn = ctk.CTkButton(
            scene_list_frame, text="＋  Новая задача", command=self.add_roll,
            fg_color=BTN_PRIMARY, hover_color=BTN_PRIMARY_HOVER,
            text_color=TEXT, corner_radius=10, height=38,
            font=ctk.CTkFont(size=13),
        )
        add_scene_btn.pack(fill="x", padx=10, pady=10)

        # Divider between scene list and detail
        ctk.CTkFrame(columns_row, width=1, fg_color="#2a2d3e", corner_radius=0).pack(
            side="left", fill="y")

        # ── Detail panel (right panel, fills rest) ────────────────────────────
        self.detail_panel = ctk.CTkFrame(columns_row, fg_color=BG, corner_radius=0)
        self.detail_panel.pack(side="left", fill="both", expand=True)

        # ── Bottom bar (spans scene list + detail, NOT sidebar) ───────────────
        bottom = ctk.CTkFrame(right_section, fg_color=BG)
        bottom.pack(fill="x", padx=20, pady=(6, 20))
        self.status_label = self.label(bottom, "", size=13, color=MUTED)
        self.status_label.pack(anchor="w", pady=(0, 8))
        self.start_btn = ctk.CTkButton(
            bottom, text="НАЧАТЬ ОЧЕРЕДЬ", command=self.start_queue,
            fg_color=BTN_DANGER, hover_color=BTN_DANGER_HOVER,
            text_color="white", corner_radius=24, height=50,
            font=ctk.CTkFont(size=17, weight="bold"),
        )
        self.start_btn.pack(fill="x")

    def _start_panel(self, name):
        self._active_panel = name
        for w in self.detail_panel.winfo_children():
            w.destroy()
        self.update_status()
        scroll = ctk.CTkScrollableFrame(
            self.detail_panel, fg_color=BG,
            scrollbar_button_color="#3d3d6a",
            scrollbar_button_hover_color="#ff7eb6",
        )
        scroll.pack(fill="both", expand=True)
        return scroll

    def show_api_key_panel(self):
        scroll = self._start_panel("api")
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        self.label(frame, "ElevenLabs API key", size=22, weight="bold").pack(
            anchor="w", padx=24, pady=(22, 6))
        self.label(
            frame,
            "Нужен только для «Сгенерировать голос». Для готового full_voice.mp3 — не используется.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=24, pady=(0, 18))
        self.label(frame, "API key", size=14, weight="bold").pack(anchor="w", padx=24)
        api_box = ctk.CTkTextbox(
            frame, height=80, fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=13),
        )
        api_box.pack(fill="x", padx=24, pady=(6, 14))
        if self.api_key:
            api_box.insert("1.0", self.api_key)
        self._fix_paste(api_box)
        result_label = self.label(frame, "", size=13, color=MUTED)
        result_label.pack(anchor="w", padx=24, pady=(0, 6))

        def save():
            api = api_box.get("1.0", "end").strip()
            if not api:
                messagebox.showerror("Ошибка", "Введите ElevenLabs API key.", parent=self)
                return
            self.api_key = api
            self.save_config()
            self.update_status()
            self._refresh_balance_badge()
            result_label.configure(text="✓ Ключ сохранён", text_color="#86efac")

        self.button(frame, "Сохранить ключ", save,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=190).pack(
            anchor="e", padx=24, pady=(0, 20))

    def show_sync_key_panel(self):
        scroll = self._start_panel("sync")
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        self.label(frame, "Sync.so API key", size=22, weight="bold").pack(
            anchor="w", padx=24, pady=(22, 6))
        self.label(
            frame,
            "Нужен для lipsync. Берётся в личном кабинете sync.so.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=24, pady=(0, 18))
        self.label(frame, "API key", size=14, weight="bold").pack(anchor="w", padx=24)
        sync_box = ctk.CTkTextbox(
            frame, height=80, fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=13),
        )
        sync_box.pack(fill="x", padx=24, pady=(6, 14))
        if self.sync_key and "ВСТАВЬ" not in self.sync_key:
            sync_box.insert("1.0", self.sync_key)
        self._fix_paste(sync_box)
        result_label = self.label(frame, "", size=13, color=MUTED)
        result_label.pack(anchor="w", padx=24, pady=(0, 6))

        def save():
            key = sync_box.get("1.0", "end").strip()
            if not key:
                messagebox.showerror("Ошибка", "Введите Sync API key.", parent=self)
                return
            self.sync_key = key
            global SYNC_API_KEY
            SYNC_API_KEY = key
            self.save_config()
            self.update_status()
            result_label.configure(text="✓ Ключ сохранён", text_color="#86efac")

        self.button(frame, "Сохранить ключ", save,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=190).pack(
            anchor="e", padx=24, pady=(0, 20))

    def show_cloudinary_panel(self):
        scroll = self._start_panel("cloudinary")
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        self.label(frame, "Cloudinary", size=22, weight="bold").pack(
            anchor="w", padx=24, pady=(22, 6))
        self.label(
            frame,
            "Нужен для загрузки файлов в Sync. Берётся в личном кабинете cloudinary.com.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=24, pady=(0, 18))

        def _field(label_text, value):
            self.label(frame, label_text, size=13, weight="bold").pack(anchor="w", padx=24)
            box = ctk.CTkTextbox(
                frame, height=44, fg_color="#ffffff", text_color="#111111",
                border_width=1, border_color="#737373",
                corner_radius=10, font=ctk.CTkFont(size=13),
            )
            box.pack(fill="x", padx=24, pady=(4, 14))
            if value:
                box.insert("1.0", value)
            return box

        cloud_box  = _field("Cloud Name",  self.cloudinary_cloud_name)
        key_box    = _field("API Key",      self.cloudinary_api_key)
        secret_box = _field("API Secret",   self.cloudinary_api_secret)
        self._fix_paste(cloud_box)
        self._fix_paste(key_box)
        self._fix_paste(secret_box)

        result_label = self.label(frame, "", size=13, color=MUTED)
        result_label.pack(anchor="w", padx=24, pady=(0, 6))

        def save():
            cloud  = cloud_box.get("1.0", "end").strip()
            key    = key_box.get("1.0", "end").strip()
            secret = secret_box.get("1.0", "end").strip()
            if not cloud or not key or not secret:
                messagebox.showerror("Ошибка", "Заполните все три поля.", parent=self)
                return
            self.cloudinary_cloud_name = cloud
            self.cloudinary_api_key    = key
            self.cloudinary_api_secret = secret
            global CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
            CLOUDINARY_CLOUD_NAME  = cloud
            CLOUDINARY_API_KEY     = key
            CLOUDINARY_API_SECRET  = secret
            self.save_config()
            self.update_status()
            result_label.configure(text="✓ Ключи сохранены", text_color="#86efac")

        self.button(frame, "Сохранить ключи", save,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=200).pack(
            anchor="e", padx=24, pady=(0, 20))

    def fetch_voice_id_by_name(self, name):
        for v in self.fetch_all_voices():
            if v["name"].lower() == name.lower():
                return v["voice_id"]
        return None
    
    def fetch_all_voices(self):
        """Возвращает список словарей {name, voice_id, category}."""
        if not self.api_key:
            raise RuntimeError("Сначала укажи ElevenLabs API key.")
        url = f"{ELEVEN_BASE_URL}/v1/voices"
        headers = {"xi-api-key": self.api_key}
        res = requests.get(url, headers=headers, timeout=60)
        if res.status_code != 200:
            raise RuntimeError(f"{res.status_code}: {res.text[:300]}")
        voices = res.json().get("voices", [])
        result = []
        for v in voices:
            name = v.get("name", "").strip()
            vid = v.get("voice_id", "")
            category = v.get("category", "")
            result.append({
                "name": name,
                "voice_id": vid,
                "category": category,
                "preview_url": v.get("preview_url", ""),
                "created_at": v.get("created_at_unix", 0),
            })
        return result
    
    def create_voice_from_sample(self, audio_path, voice_name, description=""):
        """Загружает аудио в ElevenLabs и создаёт клон. Возвращает voice_id."""
        if not self.api_key:
            raise RuntimeError("Сначала укажи ElevenLabs API key.")
        if not os.path.exists(audio_path):
            raise RuntimeError(f"Файл не найден: {audio_path}")

        url = f"{ELEVEN_BASE_URL}/v1/voices/add"
        headers = {"xi-api-key": self.api_key}
        data = {
            "name": voice_name,
            "description": description or "Клонирован через SheqelMotion Studio",
            "remove_background_noise": "true",
        }

        with open(audio_path, "rb") as f:
            files = [("files", (Path(audio_path).name, f, "audio/mpeg"))]
            res = requests.post(url, headers=headers, data=data, files=files, timeout=300)

        if res.status_code not in [200, 201]:
            try:
                err = res.json()
                detail = err.get("detail", {})
                status = detail.get("status", "")
                message = detail.get("message", res.text)
                if status == "missing_permissions":
                    raise RuntimeError(
                        "Нет прав на клонирование. В API ключе поставь Voices: Write. "
                        "Если стоит — проверь что план Creator или выше."
                    )
                raise RuntimeError(f"{res.status_code}: {message}")
            except ValueError:
                raise RuntimeError(f"{res.status_code}: {res.text[:300]}")

        payload = res.json()
        voice_id = payload.get("voice_id") or payload.get("voiceId")
        if not voice_id:
            raise RuntimeError(f"ElevenLabs не вернул voice_id: {payload}")
        return voice_id
    
    def show_voice_clone_panel(self):
        if not self.api_key:
            messagebox.showerror("Ошибка", "Сначала добавь ElevenLabs API key (⚙️ Настройки → API key).", parent=self)
            self.show_api_key_panel()
            return
        scroll = self._start_panel("voice_clone")
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        self.label(frame, "🎙 Клонировать голос", size=22, weight="bold").pack(
            anchor="w", padx=22, pady=(20, 6))
        self.label(
            frame,
            "Загрузи 1-3 минуты чистой речи без шума и музыки. "
            "Поддерживаются mp3, wav, m4a, flac.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=22, pady=(0, 16))

        # ── Переключатель источника ────────────────────────────────────────
        source_var = ctk.StringVar(value="audio")
        source_row = ctk.CTkFrame(frame, fg_color=CARD, corner_radius=12)
        source_row.pack(fill="x", padx=22, pady=(0, 16))

        audio_btn = ctk.CTkButton(
            source_row, text="🎵 Аудио файл",
            fg_color=BTN_OK, hover_color=BTN_OK_HOVER,
            font=ctk.CTkFont(size=13), height=36, corner_radius=10,
        )
        audio_btn.pack(side="left", padx=(8, 4), pady=8, expand=True, fill="x")

        video_btn = ctk.CTkButton(
            source_row, text="🎬 Видео файл",
            fg_color=BTN, hover_color=BTN_HOVER,
            font=ctk.CTkFont(size=13), height=36, corner_radius=10,
        )
        video_btn.pack(side="left", padx=(4, 8), pady=8, expand=True, fill="x")

        # ── Имя и описание ─────────────────────────────────────────────────
        self.label(frame, "Имя голоса", size=14, weight="bold").pack(anchor="w", padx=22)
        name_entry = ctk.CTkEntry(
            frame, placeholder_text="например: Иван — диктор",
            fg_color="white", text_color="#111", border_color="#737373",
            font=ctk.CTkFont(size=13),
        )
        name_entry.pack(fill="x", padx=22, pady=(6, 14))
        self._fix_paste(name_entry)

        self.label(frame, "Описание (опционально)", size=14, weight="bold").pack(anchor="w", padx=22)
        desc_entry = ctk.CTkEntry(
            frame, placeholder_text="например: тёплый низкий мужской голос",
            fg_color="white", text_color="#111", border_color="#737373",
            font=ctk.CTkFont(size=13),
        )
        desc_entry.pack(fill="x", padx=22, pady=(6, 14))
        self._fix_paste(desc_entry)

        selected = {"path": ""}
        extracted = {"tmp_dir": None}
        extracting = {"active": False}

        # ── Аудио блок ─────────────────────────────────────────────────────
        audio_block = ctk.CTkFrame(frame, fg_color="transparent")

        self.label(audio_block, "Аудио-сэмпл", size=14, weight="bold").pack(anchor="w")
        audio_file_row = ctk.CTkFrame(audio_block, fg_color=PANEL)
        audio_file_row.pack(fill="x", pady=(6, 0))

        audio_file_label = ctk.CTkLabel(
            audio_file_row, text="  не выбран",
            fg_color="#f5f5f5", text_color="#888",
            anchor="w", corner_radius=6,
            font=ctk.CTkFont(size=12), height=32,
        )
        audio_file_label.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)

        def choose_audio():
            f = filedialog.askopenfilename(
                parent=self, title="Выбери аудио-сэмпл",
                filetypes=[("Аудио", "*.mp3 *.wav *.m4a *.flac *.ogg"), ("Все файлы", "*.*")],
            )
            if f:
                selected["path"] = f
                try:
                    size_mb = os.path.getsize(f) / (1024 * 1024)
                    audio_file_label.configure(
                        text=f"  {Path(f).name}  ({size_mb:.1f} МБ)", text_color="#111")
                except Exception:
                    audio_file_label.configure(text=f"  {Path(f).name}", text_color="#111")

        self.button(audio_file_row, "Выбрать файл", choose_audio,
                    color=BTN, hover=BTN_HOVER, width=130).pack(side="right")

        # ── Видео блок ─────────────────────────────────────────────────────
        video_block = ctk.CTkFrame(frame, fg_color="transparent")

        self.label(video_block, "Видео файл", size=14, weight="bold").pack(anchor="w")
        video_file_row = ctk.CTkFrame(video_block, fg_color=PANEL)
        video_file_row.pack(fill="x", pady=(6, 0))

        video_file_label = ctk.CTkLabel(
            video_file_row, text="  не выбран",
            fg_color="#f5f5f5", text_color="#888",
            anchor="w", corner_radius=6,
            font=ctk.CTkFont(size=12), height=32,
        )
        video_file_label.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)

        extract_status_label = self.label(
            video_block,
            "Выбери видео — аудио будет извлечено автоматически",
            size=12, color=MUTED,
        )
        extract_status_label.pack(anchor="w", pady=(8, 0))

        def _cleanup_extracted():
            if extracted["tmp_dir"]:
                shutil.rmtree(extracted["tmp_dir"], ignore_errors=True)
                extracted["tmp_dir"] = None

        def extract_audio_from_video(video_path):
            tmp_dir = tempfile.mkdtemp()
            extracted["tmp_dir"] = tmp_dir
            raw_audio = str(Path(tmp_dir) / "extracted.mp3")
            run([ffmpeg_bin(), "-y", "-i", video_path,
                 "-vn", "-ar", "44100", "-ac", "1", "-q:a", "0", raw_audio])
            return raw_audio

        def choose_video():
            f = filedialog.askopenfilename(
                parent=self, title="Выбери видео файл",
                filetypes=[("Видео", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm"), ("Все файлы", "*.*")],
            )
            if not f:
                return
            selected["path"] = ""
            video_file_label.configure(text=f"  {Path(f).name}", text_color="#111")
            extract_status_label.configure(text="Извлекаю аудио...", text_color=MUTED)
            clone_btn_ref[0].configure(state="disabled")
            extracting["active"] = True

            def worker():
                try:
                    out_path = extract_audio_from_video(f)
                    dur = get_duration(out_path)
                    size_mb = os.path.getsize(out_path) / (1024 * 1024)
                    selected["path"] = out_path
                    self.after(0, lambda: extract_status_label.configure(
                        text=f"✓ Аудио извлечено: {dur:.1f} сек · {size_mb:.1f} МБ",
                        text_color="#86efac",
                    ))
                except Exception as e:
                    self.after(0, lambda err=e: extract_status_label.configure(
                        text=f"Ошибка извлечения: {err}",
                        text_color="#fca5a5",
                    ))
                finally:
                    extracting["active"] = False
                    self.after(0, lambda: clone_btn_ref[0].configure(state="normal"))

            threading.Thread(target=worker, daemon=True).start()

        self.button(video_file_row, "Выбрать файл", choose_video,
                    color=BTN, hover=BTN_HOVER, width=130).pack(side="right")

        # ── Переключение блоков ────────────────────────────────────────────
        def switch_source(mode):
            source_var.set(mode)
            if mode == "audio":
                video_block.pack_forget()
                audio_block.pack(fill="x", padx=22, pady=(0, 14))
                audio_btn.configure(fg_color=BTN_OK, hover_color=BTN_OK_HOVER)
                video_btn.configure(fg_color=BTN, hover_color=BTN_HOVER)
            else:
                audio_block.pack_forget()
                video_block.pack(fill="x", padx=22, pady=(0, 14))
                video_btn.configure(fg_color=BTN_OK, hover_color=BTN_OK_HOVER)
                audio_btn.configure(fg_color=BTN, hover_color=BTN_HOVER)
            selected["path"] = ""

        audio_btn.configure(command=lambda: switch_source("audio"))
        video_btn.configure(command=lambda: switch_source("video"))

        # Показываем аудио блок по умолчанию
        audio_block.pack(fill="x", padx=22, pady=(0, 14))

        result_label = self.label(frame, "", size=12, color=MUTED)
        result_label.pack(anchor="w", padx=22, pady=(0, 8))
        self._add_copy_menu(result_label, lambda lbl=result_label: lbl.cget("text"))

        def clone():
            name = name_entry.get().strip()
            description = desc_entry.get().strip()
            path = selected["path"]
            if not name:
                messagebox.showerror("Ошибка", "Введи имя голоса.", parent=self)
                return
            if not path:
                messagebox.showerror("Ошибка", "Выбери аудио-сэмпл.", parent=self)
                return
            try:
                result_label.configure(text="Загружаю в ElevenLabs...", text_color=MUTED)
                self.update_idletasks()
                voice_id = self.create_voice_from_sample(path, name, description)
                _cleanup_extracted()
                expires_at = time.time() + (10 * 60 * 60)
                self.voice_expiry_list.append({
                    "voice_id": voice_id,
                    "name": name,
                    "expires_at": expires_at,
                })
                self.save_config()
                result_label.configure(
                    text=f"✓ Голос «{name}» будет автоудалён через 10 часов",
                    text_color="#fcd34d",
                )
                self.main_voice_name = name
                self.main_voice_id = voice_id
                messagebox.showinfo(
                    "Готово",
                    f"Голос «{name}» клонирован.\n\nvoice_id: {voice_id}",
                    parent=self,
                )
                self.after(500, lambda n=name: self.show_voice_gen_panel(initial_voice=n))
            except Exception as e:
                result_label.configure(text=f"Ошибка: {e}", text_color="#fca5a5")
                messagebox.showerror("Ошибка", str(e), parent=self)

        clone_btn = self.button(frame, "🎙 Клонировать", clone,
                                color=BTN_OK, hover=BTN_OK_HOVER, width=200)
        clone_btn.pack(anchor="e", padx=22, pady=(0, 20))
        clone_btn_ref = [clone_btn]

        frame.bind("<Destroy>", lambda e: _cleanup_extracted())

    def show_downloader_panel(self):
        try:
            _ensure_ytdlp()
        except RuntimeError as e:
            messagebox.showerror("yt-dlp не найден", str(e), parent=self)
            return

        outer = self._start_panel("downloader")

        # ── Заголовок ──────────────────────────────────────────────────────
        self.label(outer, "⬇ Скачать видео / аудио", size=20, weight="bold").pack(
            anchor="w", padx=20, pady=(16, 2))
        self.label(
            outer,
            "TikTok · YouTube · Instagram · Twitter · почем и тысячи других сайтов.",
            size=12, color=MUTED,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        # ── Поле для ссылок ────────────────────────────────────────────────
        self.label(outer, "Ссылки (по одной на строке):", size=13, weight="bold").pack(
            anchor="w", padx=20)
        url_box = ctk.CTkTextbox(
            outer, height=80,
            fg_color="white", text_color="#111",
            border_color="#737373", border_width=1,
            font=ctk.CTkFont(size=12),
        )
        url_box.pack(fill="x", padx=20, pady=(4, 10))

        # ── Cookies браузера ───────────────────────────────────────────────
        browser_status_label = self.label(outer, "🔍 Определяю браузер...", size=12, color=MUTED)
        browser_status_label.pack(anchor="w", padx=20, pady=(0, 4))

        cookies_row = ctk.CTkFrame(outer, fg_color="transparent")
        cookies_row.pack(fill="x", padx=20, pady=(0, 4))

        use_cookies_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            cookies_row,
            text="Использовать cookies браузера (для видео с авторизацией)",
            variable=use_cookies_var,
            text_color=TEXT, fg_color=BTN_PRIMARY,
            font=ctk.CTkFont(size=12),
        ).pack(side="left")

        browser_override_var = ctk.StringVar(value="Авто")
        ctk.CTkOptionMenu(
            cookies_row,
            values=["Авто", "Chrome", "Firefox", "Safari", "Edge", "Brave", "Chromium"],
            variable=browser_override_var,
            fg_color=BTN, button_color=BTN,
            button_hover_color=BTN_HOVER,
            width=120,
        ).pack(side="right")
        self.label(cookies_row, "Браузер:", size=12, color=MUTED).pack(side="right", padx=(0, 6))

        def _auto_detect_browser():
            if not self._browser_checked:
                self._browser_checked = True
                browser = find_best_browser_with_google()
                self._detected_browser = browser
                if browser:
                    self.after(0, lambda b=browser: browser_status_label.configure(
                        text=f"✓ Найден браузер с Google аккаунтом: {b.capitalize()}",
                        text_color="#86efac",
                    ))
                else:
                    self.after(0, lambda: browser_status_label.configure(
                        text="⚠ Браузер с Google аккаунтом не найден — некоторые видео могут не скачаться",
                        text_color="#fcd34d",
                    ))

        threading.Thread(target=_auto_detect_browser, daemon=True).start()

        def recheck():
            self._browser_checked = False
            self._detected_browser = None
            browser_status_label.configure(text="🔍 Определяю браузер...", text_color=MUTED)
            threading.Thread(target=_auto_detect_browser, daemon=True).start()

        self.button(outer, "🔄 Перепроверить", recheck,
                    color=BTN, hover=BTN_HOVER, width=140).pack(anchor="w", padx=20, pady=(0, 8))

        # ── Режим ─────────────────────────────────────────────────────────
        mode_var = ctk.StringVar(value="video")
        mode_row = ctk.CTkFrame(outer, fg_color="transparent")
        mode_row.pack(fill="x", padx=20, pady=(0, 6))
        self.label(mode_row, "Режим:", size=13, weight="bold").pack(side="left", padx=(0, 10))
        for txt, val in [
            ("Видео (mp4)", "video"),
            ("Аудио (mp3)", "audio"),
            ("Видео + Аудио отдельно", "split"),
        ]:
            ctk.CTkRadioButton(
                mode_row, text=txt, variable=mode_var, value=val,
                text_color=TEXT, fg_color=BTN_PRIMARY,
                font=ctk.CTkFont(size=12),
            ).pack(side="left", padx=8)

        # ── Опции ─────────────────────────────────────────────────────────
        opts_row = ctk.CTkFrame(outer, fg_color="transparent")
        opts_row.pack(fill="x", padx=20, pady=(0, 8))
        denoise_var = ctk.BooleanVar(value=False)
        denoise_cb = ctk.CTkCheckBox(
            opts_row, text="Убрать музыку (audio-separator)",
            variable=denoise_var, text_color=TEXT, fg_color=BTN_PRIMARY,
            font=ctk.CTkFont(size=12),
        )

        def _update_denoise_vis(*_):
            if mode_var.get() == "video":
                denoise_cb.pack_forget()
            else:
                denoise_cb.pack(side="left", padx=(0, 24))

        mode_var.trace_add("write", _update_denoise_vis)
        _update_denoise_vis()

        # ── Папка ─────────────────────────────────────────────────────────
        folder_row = ctk.CTkFrame(outer, fg_color="transparent")
        folder_row.pack(fill="x", padx=20, pady=(0, 10))
        self.label(folder_row, "Папка:", size=12, weight="bold").pack(side="left", padx=(0, 6))
        folder_var = ctk.StringVar(value=str(Path(desktop_dir()) / "SheqelMotion_Downloads"))
        ctk.CTkEntry(
            folder_row, textvariable=folder_var,
            fg_color="white", text_color="#111",
            border_color="#737373", font=ctk.CTkFont(size=11),
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        def choose_folder():
            d = filedialog.askdirectory(title="Выбери папку для сохранения", parent=self)
            if d:
                folder_var.set(d)

        self.button(folder_row, "Выбрать...", choose_folder,
                    color=BTN, hover=BTN_HOVER, width=90).pack(side="left")

        # ── Кнопки ────────────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(0, 8))

        def do_download():
            raw = url_box.get("1.0", "end")
            urls = [u.strip() for u in raw.splitlines() if u.strip().startswith("http")]
            if not urls:
                messagebox.showerror(
                    "Ошибка", "Вставь хотя бы одну ссылку (начинается с http).", parent=self)
                return

            self._stop_download = False
            mode = mode_var.get()
            denoise = denoise_var.get()
            output_dir = folder_var.get()

            def get_browser_for_download():
                if not use_cookies_var.get():
                    return None
                override = browser_override_var.get()
                if override != "Авто":
                    return override.lower()
                return self._detected_browser

            dl_btn.configure(state="disabled", text="Скачиваю...")
            stop_btn.configure(state="normal")
            status_lbl.configure(text="")

            items = [(url, _add_history_row(url)) for url in urls]

            def process_one(url, item):
                def log(msg):
                    if self._active_panel != "downloader":
                        return
                    m = re.search(r'(\d+(?:\.\d+)?)\s*%', msg)
                    if m:
                        pct = min(float(m.group(1)) / 100.0, 1.0)
                        self.after(0, lambda p=pct: item["pbar"].set(p))
                    short = msg.strip()[:70]
                    if short:
                        self.after(0, lambda s=short: item["name"].configure(text=s))

                try:
                    main_file, extra_file = download_from_url(
                        url, output_dir, mode, denoise, log,
                        browser=get_browser_for_download())
                    show_name = Path(main_file).name
                    if extra_file:
                        show_name += " + " + Path(extra_file).name

                    def mark_done(f=main_file, n=show_name):
                        if self._active_panel != "downloader":
                            return
                        item["status"].configure(text="✓", text_color="#86efac")
                        item["name"].configure(
                            text=(n[:70] + "…") if len(n) > 70 else n,
                            text_color=TEXT,
                        )
                        item["pbar"].set(1.0)
                        item["pbar"].configure(progress_color="#86efac")
                        item["open"].configure(
                            state="normal",
                            command=lambda path=f: open_folder(str(Path(path).parent)),
                        )
                    self.after(0, mark_done)
                except Exception as e:
                    err = str(e)[:80]
                    def mark_err(e=err):
                        if self._active_panel != "downloader":
                            return
                        item["status"].configure(text="✗", text_color="#fca5a5")
                        item["name"].configure(text=f"Ошибка: {e}", text_color="#fca5a5")
                        item["pbar"].configure(progress_color="#fca5a5")
                    self.after(0, mark_err)

            def run_queue():
                total = len(items)
                downloaded = 0
                for i, (url, item) in enumerate(items):
                    if self._stop_download:
                        for _, it in items[i:]:
                            self.after(0, lambda it=it: it["status"].configure(
                                text="✕", text_color="#fca5a5"))
                        break
                    self.after(0, lambda n=i + 1, t=total: status_lbl.configure(
                        text=f"Скачивается {n} из {t}..."))
                    process_one(url, item)
                    downloaded += 1

                if self._stop_download:
                    msg = f"Остановлено. Скачано {downloaded} из {total}"
                else:
                    msg = f"Готово! Скачано {total} из {total}"

                self._stop_download = False

                if self._active_panel == "downloader":
                    self.after(0, lambda m=msg: status_lbl.configure(text=m))
                    self.after(0, lambda: dl_btn.configure(state="normal", text="⬇ Скачать"))
                    self.after(0, lambda: stop_btn.configure(state="disabled"))

            threading.Thread(target=run_queue, daemon=True).start()

        dl_btn = self.button(btn_row, "⬇ Скачать", do_download,
                             color=BTN_OK, hover=BTN_OK_HOVER, width=140)
        dl_btn.pack(side="left")

        stop_btn = self.button(btn_row, "✕ Стоп",
                               lambda: setattr(self, "_stop_download", True),
                               color=BTN_DANGER, hover=BTN_DANGER_HOVER, width=100)
        stop_btn.pack(side="left", padx=8)
        stop_btn.configure(state="disabled")

        status_lbl = self.label(btn_row, "", size=12, color=MUTED)
        status_lbl.pack(side="left", padx=12)

        self.button(btn_row, "Открыть папку",
                    lambda: open_folder(folder_var.get()),
                    color=BTN, hover=BTN_HOVER, width=130).pack(side="right")

        # ── История загрузок ───────────────────────────────────────────────
        self.label(outer, "История загрузок", size=13, weight="bold", color=MUTED).pack(
            anchor="w", padx=20, pady=(4, 4))

        history_scroll = ctk.CTkScrollableFrame(outer, fg_color=BG, corner_radius=10)
        history_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        def _add_history_row(url_text):
            row = ctk.CTkFrame(history_scroll, fg_color=PANEL, corner_radius=8)
            row.pack(fill="x", padx=4, pady=3)
            row.columnconfigure(1, weight=1)

            status_lbl = self.label(row, "⏳", size=14)
            status_lbl.grid(row=0, column=0, padx=(10, 6), pady=8, sticky="w")

            short = (url_text[:66] + "…") if len(url_text) > 66 else url_text
            name_lbl = self.label(row, short, size=11, color=MUTED)
            name_lbl.grid(row=0, column=1, padx=4, pady=8, sticky="w")

            pbar = ctk.CTkProgressBar(row, width=130, height=6)
            pbar.set(0)
            pbar.grid(row=0, column=2, padx=8, pady=8, sticky="ew")

            open_btn_h = self.button(row, "Открыть", lambda: None,
                                     color=BTN, hover=BTN_HOVER, width=80)
            open_btn_h.grid(row=0, column=3, padx=(4, 10), pady=6)
            open_btn_h.configure(state="disabled")

            return {"status": status_lbl, "name": name_lbl, "pbar": pbar, "open": open_btn_h}

    def stop_preview(self):
        """Останавливает текущий preview если играет."""
        if self.preview_process and self.preview_process.poll() is None:
            try:
                self.preview_process.terminate()
            except Exception:
                pass
        self.preview_process = None
        self.preview_voice_id = None

    def play_voice_preview(self, voice_id, url):
        """Скачивает (если надо) и играет preview через ffplay/afplay."""
        self.stop_preview()

        cache_dir = Path(tempfile.gettempdir()) / "sheqelmotion_previews"
        ensure_dir(cache_dir)
        cache_path = str(cache_dir / f"{voice_id}.mp3")

        if not os.path.exists(cache_path):
            if url:
                try:
                    r = requests.get(url, timeout=30)
                    r.raise_for_status()
                    with open(cache_path, "wb") as f:
                        f.write(r.content)
                except Exception as e:
                    self.log(f"Не удалось скачать preview: {e}")
                    return
            else:
                # У голоса нет preview_url — генерируем сэмпл через TTS
                self.log(f"Генерирую сэмпл для {voice_id}...")
                sample_text = "Привет, это пример моего голоса. Тестовая фраза для прослушивания."
                try:
                    global ELEVENLABS_API_KEY
                    ELEVENLABS_API_KEY = self.api_key
                    text_to_speech_mp3(sample_text, voice_id, cache_path, self.log)
                except Exception as e:
                    self.log(f"Не удалось сгенерировать сэмпл: {e}")
                    return

        try:
            ffplay = shutil.which("ffplay")
            if not ffplay:
                local = Path(app_base_dir()) / ("ffplay" + (".exe" if sys.platform == "win32" else ""))
                if local.exists():
                    ffplay = str(local)

            if ffplay:
                self.preview_process = subprocess.Popen(
                    [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(cache_path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "darwin":
                self.preview_process = subprocess.Popen(["afplay", str(cache_path)])
            elif sys.platform == "win32":
                os.startfile(cache_path)
            self.preview_voice_id = voice_id
        except Exception as e:
            self.log(f"Не удалось воспроизвести: {e}")

    def open_voice_picker(self, parent_win, on_pick):
        """Окошко с выбором голоса. on_pick(name, voice_id) при выборе."""
        win = ctk.CTkToplevel(parent_win)
        win.title("Мои голоса ElevenLabs")
        win.geometry("620x680")
        win.configure(fg_color=BG)
        win.transient(parent_win)
        win.after(100, win.grab_set)

        def on_close():
            self.stop_preview()
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", on_close)

        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="both", expand=True, padx=16, pady=16)

        self.label(frame, "Мои голоса", size=18, weight="bold").pack(anchor="w", padx=16, pady=(14, 6))

        search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(frame, placeholder_text="🔍 Поиск по имени...",
                                    textvariable=search_var,
                                    fg_color="white", text_color="#111",
                                    border_color="#737373",
                                    font=ctk.CTkFont(size=13))
        search_entry.pack(fill="x", padx=16, pady=(0, 10))
        self._fix_paste(search_entry)

        list_frame = ctk.CTkScrollableFrame(frame, fg_color=BG)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        status_label = self.label(frame, "Загружаю...", size=12, color=MUTED)
        status_label.pack(anchor="w", padx=16, pady=(0, 4))

        voices_data = []

        def _time_ago(ts):
            if not ts:
                return ""
            import time as _time
            diff = int(_time.time()) - ts
            if diff < 0:
                return ""
            minutes = diff // 60
            if minutes < 1:
                return "только что"
            if minutes < 60:
                return f"{minutes} мин назад"
            hours = diff // 3600
            if hours < 24:
                return f"{hours} ч назад"
            days = diff // 86400
            if days < 30:
                return f"{days} дн назад"
            months = days // 30
            return f"{months} мес назад"

        def render_list(filter_text=""):
            for w in list_frame.winfo_children():
                w.destroy()
            filter_lower = filter_text.lower().strip()

            groups = {"cloned": [], "professional": [], "other": []}
            for v in voices_data:
                if filter_lower and filter_lower not in v["name"].lower():
                    continue
                cat = v["category"]
                if cat == "cloned":
                    groups["cloned"].append(v)
                elif cat == "professional":
                    groups["professional"].append(v)
                else:
                    groups["other"].append(v)

            sections = [
                ("🎤 Мои клоны", groups["cloned"], "#86efac"),
                ("⭐ Профи голоса", groups["professional"], "#fbbf24"),
            ]
            if groups["other"]:
                sections.append(("📚 Другие", groups["other"], "#a3a3a3"))

            total_shown = 0
            for section_title, section_voices, section_color in sections:
                if not section_voices:
                    continue
                header = ctk.CTkFrame(list_frame, fg_color=BG)
                header.pack(fill="x", pady=(10, 4))
                ctk.CTkLabel(header, text=f"{section_title}  ·  {len(section_voices)}",
                             font=ctk.CTkFont(size=14, weight="bold"),
                             text_color=section_color
                             ).pack(side="left", padx=4)

                for v in section_voices:
                    row = ctk.CTkFrame(list_frame, fg_color=CARD, corner_radius=14)
                    row.pack(fill="x", padx=2, pady=3)

                    name_col = ctk.CTkFrame(row, fg_color="transparent")
                    name_col.pack(side="left", fill="x", expand=True, padx=12, pady=6)
                    ctk.CTkLabel(name_col, text=v["name"],
                                 font=ctk.CTkFont(size=14, weight="bold"),
                                 text_color=TEXT, anchor="w"
                                 ).pack(anchor="w")
                    ago = _time_ago(v.get("created_at", 0))
                    if ago:
                        ctk.CTkLabel(name_col, text=ago,
                                     font=ctk.CTkFont(size=10),
                                     text_color=MUTED, anchor="w"
                                     ).pack(anchor="w", padx=0)
                    expiry_entry = next(
                        (e for e in self.voice_expiry_list if e["voice_id"] == v["voice_id"]),
                        None,
                    )
                    if expiry_entry:
                        secs_left = max(0, int(expiry_entry["expires_at"] - time.time()))
                        h_left = secs_left // 3600
                        m_left = (secs_left % 3600) // 60
                        timer_text = f"⏳ удалится через {h_left} ч {m_left} мин"
                        ctk.CTkLabel(name_col, text=timer_text,
                                     font=ctk.CTkFont(size=10),
                                     text_color="#fcd34d", anchor="w"
                                     ).pack(anchor="w", padx=0)

                    
                    def make_play(voice=v):
                        return lambda: self.play_voice_preview(
                            voice["voice_id"], voice.get("preview_url", ""))
                    ctk.CTkButton(row, text="▶", command=make_play(),
                                      fg_color=BTN, hover_color=BTN_HOVER,
                                      width=40, height=28,
                                      font=ctk.CTkFont(size=14)
                                      ).pack(side="right", padx=4)

                    def make_pick(voice=v):
                        def pick():
                            self.stop_preview()
                            on_pick(voice["name"], voice["voice_id"])
                            win.destroy()
                        return pick
                    ctk.CTkButton(row, text="Выбрать", command=make_pick(),
                                  fg_color=BTN_OK, hover_color=BTN_OK_HOVER,
                                  width=90, height=28
                                  ).pack(side="right", padx=4)
                    total_shown += 1

            if total_shown == 0 and voices_data:
                ctk.CTkLabel(list_frame, text="Ничего не найдено",
                             text_color=MUTED).pack(pady=20)
            status_label.configure(text=f"Показано: {total_shown} из {len(voices_data)}",
                                   text_color=MUTED)

        search_var.trace_add("write", lambda *_: render_list(search_var.get()))

        def load():
            try:
                status_label.configure(text="Загружаю список голосов...", text_color=MUTED)
                win.update()
                voices_data.clear()
                voices_data.extend(self.fetch_all_voices())
                if not voices_data:
                    status_label.configure(text="В аккаунте нет голосов",
                                           text_color="#fca5a5")
                    return
                render_list()
            except Exception as e:
                status_label.configure(text=f"Ошибка: {e}", text_color="#fca5a5")

        bottom = ctk.CTkFrame(frame, fg_color=PANEL)
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(bottom, text="🔄 Обновить", command=load,
                      fg_color=BTN, hover_color=BTN_HOVER,
                      width=120, height=32).pack(side="right")

        win.after(100, load)
   

    def show_voice_gen_panel(self, initial_voice=""):
        outer = self._start_panel("voice_gen")
        frame = ctk.CTkFrame(outer, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="x", padx=20, pady=20)

        # ── Header ────────────────────────────────────────────────────────
        self.label(frame, "Сгенерировать full_voice.mp3", size=22, weight="bold").pack(
            anchor="w", padx=22, pady=(20, 4))
        self.label(
            frame,
            "Один текст — два варианта генерации параллельно. Прослушай оба, скачай лучший.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=22, pady=(0, 14))

        # ── Voice picker ──────────────────────────────────────────────────
        self.label(frame, "voice_id или имя голоса", size=14, weight="bold").pack(
            anchor="w", padx=22)
        voice_box = ctk.CTkTextbox(
            frame, height=56, fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=14),
        )
        voice_box.pack(fill="x", padx=22, pady=(6, 8))
        if self.main_voice_name:
            voice_box.insert("1.0", self.main_voice_name)
        self._fix_paste(voice_box)
        if initial_voice:
            voice_box.delete("1.0", "end")
            voice_box.insert("1.0", initial_voice)

        def on_voice_picked(name, voice_id):
            voice_box.delete("1.0", "end")
            voice_box.insert("1.0", name)

        self.button(frame, "📋 Выбрать из списка моих голосов",
                    lambda: self.open_voice_picker(self, on_voice_picked),
                    color=BTN, hover=BTN_HOVER, width=320).pack(
            anchor="w", padx=22, pady=(0, 14))

        # ── Text input ────────────────────────────────────────────────────
        self.label(frame, "Текст (один для обоих вариантов)", size=14, weight="bold").pack(
            anchor="w", padx=22)
        text_box = ctk.CTkTextbox(
            frame, height=180,
            fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=14), wrap="word",
        )
        text_box.pack(fill="x", padx=22, pady=(6, 4))
        self._fix_paste(text_box)
        tk_text = text_box._textbox  # внутренний tk.Text для тегов/биндингов

        # ── Счётчик символов ──────────────────────────────────────────────
        status_text_label = ctk.CTkLabel(
            frame, text="0 символов",
            font=ctk.CTkFont(size=12), text_color=MUTED, anchor="w"
        )
        status_text_label.pack(anchor="w", padx=22, pady=(0, 8))

        # ── Кнопка запуска ────────────────────────────────────────────────
        gen_row = ctk.CTkFrame(frame, fg_color="transparent")
        gen_row.pack(fill="x", padx=22, pady=(0, 16))

        gen_btn = self.button(gen_row, "⚡ Сгенерировать A и B",
                              lambda: None,
                              color=BTN_OK, hover=BTN_OK_HOVER, width=240)
        gen_btn.pack(side="left")

        gen_status = self.label(gen_row, "", size=12, color=MUTED)
        gen_status.pack(side="left", padx=14)

        # ── A/B карточки ──────────────────────────────────────────────────
        ab_row = ctk.CTkFrame(frame, fg_color="transparent")
        ab_row.pack(fill="x", padx=22, pady=(0, 20))
        ab_row.columnconfigure(0, weight=1)
        ab_row.columnconfigure(1, weight=1)

        state = {"temp_path": {"A": None, "B": None}}
        slot_widgets = {}

        for col, slot in enumerate(["A", "B"]):
            color_accent = "#1c7ed6" if slot == "A" else "#7950f2"
            card = ctk.CTkFrame(ab_row, fg_color=CARD, corner_radius=16)
            card.grid(row=0, column=col, padx=(0 if col == 0 else 8, 0), sticky="nsew")

            hdr = ctk.CTkFrame(card, fg_color=color_accent, corner_radius=10, height=36)
            hdr.pack(fill="x", padx=10, pady=(10, 8))
            hdr.pack_propagate(False)
            ctk.CTkLabel(hdr, text=f"Вариант {slot}",
                         font=ctk.CTkFont(size=15, weight="bold"),
                         text_color="white").pack(expand=True)

            status_lbl = self.label(card, "Ожидает генерации", size=12, color=MUTED)
            status_lbl.pack(anchor="w", padx=12, pady=(0, 8))

            pbar = ctk.CTkProgressBar(card, height=6, progress_color=color_accent)
            pbar.set(0)
            pbar.pack(fill="x", padx=12, pady=(0, 10))

            btn_row = ctk.CTkFrame(card, fg_color="transparent")
            btn_row.pack(fill="x", padx=10, pady=(0, 10))

            play_btn = self.button(btn_row, "▶ Слушать", lambda s=slot: _play(s),
                                   color=BTN, hover=BTN_HOVER, width=110)
            play_btn.pack(side="left", padx=(0, 6))
            play_btn.configure(state="disabled")

            save_btn = self.button(btn_row, "💾 Скачать", lambda s=slot: _save(s),
                                   color=BTN_OK, hover=BTN_OK_HOVER, width=110)
            save_btn.pack(side="left")
            save_btn.configure(state="disabled")

            stop_btn = self.button(btn_row, "⏹", lambda s=slot: _stop(s),
                                   color=BTN_DANGER, hover=BTN_DANGER_HOVER, width=44)
            stop_btn.pack(side="right")
            stop_btn.configure(state="disabled")

            slot_widgets[slot] = {
                "status": status_lbl,
                "pbar": pbar,
                "play": play_btn,
                "save": save_btn,
                "stop": stop_btn,
            }

        # ── Логика ────────────────────────────────────────────────────────
        procs = {"A": None, "B": None}

        def _stop(slot):
            p = procs[slot]
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
            procs[slot] = None
            slot_widgets[slot]["play"].configure(state="normal")
            slot_widgets[slot]["stop"].configure(state="disabled")

        def _play(slot):
            p = state["temp_path"][slot]
            if not p or not os.path.exists(p):
                return
            _stop(slot)
            try:
                if sys.platform == "darwin":
                    proc = subprocess.Popen(["afplay", p])
                elif sys.platform == "win32":
                    os.startfile(p)
                    return
                else:
                    proc = subprocess.Popen(["xdg-open", p])
                procs[slot] = proc
                slot_widgets[slot]["play"].configure(state="disabled")
                slot_widgets[slot]["stop"].configure(state="normal")
                def watch(s=slot, pr=proc):
                    pr.wait()
                    self.after(0, lambda: slot_widgets[s]["play"].configure(state="normal"))
                    self.after(0, lambda: slot_widgets[s]["stop"].configure(state="disabled"))
                threading.Thread(target=watch, daemon=True).start()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось воспроизвести: {e}", parent=self)

        def _save(slot):
            p = state["temp_path"][slot]
            if not p or not os.path.exists(p):
                return
            chosen = filedialog.askdirectory(title=f"Сохранить вариант {slot}", parent=self)
            if not chosen:
                return
            dst = str(Path(chosen) / Path(p).name)
            try:
                shutil.copy2(p, dst)
                slot_widgets[slot]["status"].configure(
                    text=f"✓ Сохранено: {Path(dst).name}", text_color="#86efac")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить: {e}", parent=self)

        def _set_slot_generating(slot):
            w = slot_widgets[slot]
            w["status"].configure(text="Генерирую...", text_color=MUTED)
            w["pbar"].set(0)
            w["play"].configure(state="disabled")
            w["save"].configure(state="disabled")
            w["stop"].configure(state="disabled")

        def _set_slot_done(slot, tmp_path):
            state["temp_path"][slot] = tmp_path
            w = slot_widgets[slot]
            try:
                dur = get_duration(tmp_path)
                w["status"].configure(text=f"✓ Готово — {dur:.1f} сек", text_color="#86efac")
            except Exception:
                w["status"].configure(text="✓ Готово", text_color="#86efac")
            w["pbar"].set(1.0)
            w["play"].configure(state="normal")
            w["save"].configure(state="normal")
            _check_both_done()

        def _set_slot_error(slot, err):
            w = slot_widgets[slot]
            w["status"].configure(text=f"Ошибка: {str(err)[:60]}", text_color="#fca5a5")
            w["pbar"].set(0)
            _check_both_done()

        def _check_both_done():
            a_done = state["temp_path"]["A"] is not None or \
                     "Ошибка" in slot_widgets["A"]["status"].cget("text")
            b_done = state["temp_path"]["B"] is not None or \
                     "Ошибка" in slot_widgets["B"]["status"].cget("text")
            if a_done and b_done:
                gen_btn.configure(state="normal", text="⚡ Сгенерировать A и B")
                gen_status.configure(text="")

        def _generate_slot(slot):
            voice_value = voice_box.get("1.0", "end").strip()
            full_text = text_box.get("1.0", "end").strip()
            self.after(0, lambda s=slot: _set_slot_generating(s))

            def run():
                try:
                    global ELEVENLABS_API_KEY
                    ELEVENLABS_API_KEY = self.api_key
                    if re.fullmatch(r"[A-Za-z0-9]{20}", voice_value):
                        voice_id = voice_value
                        vname = voice_value
                    else:
                        voice_id = self.fetch_voice_id_by_name(voice_value)
                        vname = voice_value
                    if not voice_id:
                        self.after(0, lambda s=slot: _set_slot_error(s, "Голос не найден"))
                        return
                    eleven_text = full_text.replace("------", '[пауза 3 сек]')
                    safe_vname = safe_name(vname or voice_id)
                    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
                    tmp_dir = tempfile.mkdtemp()
                    tmp_path = str(Path(tmp_dir) / f"{safe_vname}_{timestamp}_variant_{slot}.mp3")
                    text_to_speech_mp3(eleven_text, voice_id, tmp_path, self.log)
                    self.after(0, lambda s=slot, p=tmp_path: _set_slot_done(s, p))
                except Exception as e:
                    err = str(e)
                    self.after(0, lambda s=slot, er=err: _set_slot_error(s, er))

            threading.Thread(target=run, daemon=True).start()

        def generate_both():
            if not self.api_key:
                messagebox.showerror("Ошибка", "Сначала добавь ElevenLabs API key.", parent=self)
                return
            if not voice_box.get("1.0", "end").strip():
                messagebox.showerror("Ошибка", "Введите voice_id или имя голоса.", parent=self)
                return
            if not text_box.get("1.0", "end").strip():
                messagebox.showerror("Ошибка", "Введите текст.", parent=self)
                return
            for s in ["A", "B"]:
                state["temp_path"][s] = None
            gen_btn.configure(state="disabled", text="Генерирую...")
            gen_status.configure(text="Запускаю A и B параллельно...")
            for slot in ["A", "B"]:
                _generate_slot(slot)

        gen_btn.configure(command=generate_both)

        def _update_status(*args):
            try:
                tk_text.edit_modified(False)
            except Exception:
                pass
            text = text_box.get("1.0", "end").strip()
            chars = len(text)

            tk_text.tag_remove("error", "1.0", "end")
            tk_text.tag_remove("separator_ok", "1.0", "end")
            tk_text.tag_remove("separator_bad", "1.0", "end")

            tk_text.tag_config("error", underline=True, foreground="#ff4444")
            tk_text.tag_config("separator_ok", foreground="#fcd34d", font=ctk.CTkFont(size=14, weight="bold"))
            tk_text.tag_config("separator_bad", foreground="#ff4444", underline=True)

            errors = 0
            lines = text_box.get("1.0", "end").split("\n")

            for line_idx, line in enumerate(lines):
                line_num = line_idx + 1

                for m in re.finditer(r'  +', line):
                    tk_text.tag_add("error", f"{line_num}.{m.start()}", f"{line_num}.{m.end()}")
                    errors += 1

                for m in re.finditer(r' [,\.!?]', line):
                    tk_text.tag_add("error", f"{line_num}.{m.start()}", f"{line_num}.{m.end()}")
                    errors += 1

                for m in re.finditer(r'[!?]{2,}|\.{4,}|,{2,}', line):
                    tk_text.tag_add("error", f"{line_num}.{m.start()}", f"{line_num}.{m.end()}")
                    errors += 1

                for m in re.finditer(r'-{2,}', line):
                    matched = m.group()
                    if matched == "------":
                        tk_text.tag_add("separator_ok", f"{line_num}.{m.start()}", f"{line_num}.{m.end()}")
                    else:
                        tk_text.tag_add("separator_bad", f"{line_num}.{m.start()}", f"{line_num}.{m.end()}")
                        errors += 1

            if errors == 0:
                status_text_label.configure(
                    text=f"✓ {chars} символов — текст готов к озвучке",
                    text_color="#86efac")
            else:
                status_text_label.configure(
                    text=f"⚠ {chars} символов · {errors} ошибок",
                    text_color="#fcd34d")

        def check_spelling(text):
            try:
                res = requests.post(
                    "https://api.languagetool.org/v2/check",
                    data={"text": text, "language": "auto"},
                    timeout=5,
                )
                if res.status_code != 200:
                    return []
                matches = res.json().get("matches", [])
                return [
                    {
                        "offset": m["offset"],
                        "length": m["length"],
                        "message": m["message"],
                        "type": m["rule"]["issueType"],
                    }
                    for m in matches
                    if m["rule"]["issueType"] in ("misspelling", "grammar")
                ]
            except Exception:
                return []

        spell_timer = {"id": None}

        def _schedule_spell_check(*args):
            if spell_timer["id"]:
                frame.after_cancel(spell_timer["id"])
            spell_timer["id"] = frame.after(800, _run_spell_check)

        def _run_spell_check():
            text = text_box.get("1.0", "end")
            def run():
                matches = check_spelling(text)
                self.after(0, lambda m=matches: _apply_spell_tags(m))
            threading.Thread(target=run, daemon=True).start()

        def _apply_spell_tags(matches):
            tk_text.tag_remove("spell_error", "1.0", "end")
            tk_text.tag_config("spell_error", underline=True, foreground="#ff6b6b")
            for m in matches:
                start_idx = tk_text.index(f"1.0 + {m['offset']} chars")
                end_idx = tk_text.index(f"1.0 + {m['offset'] + m['length']} chars")
                tk_text.tag_add("spell_error", start_idx, end_idx)

        tk_text.bind("<KeyRelease>", lambda e: (
            self.after(100, _update_status),
            _schedule_spell_check(),
        ))
        tk_text.bind("<<Modified>>", lambda e: self.after(100, _update_status))
        tk_text.bind("<ButtonRelease>", lambda e: self.after(100, _update_status))
        frame.bind("<Destroy>", lambda e: [
            state["temp_path"].update({"A": None, "B": None})
        ])
    def add_roll(self):
        roll = {
            "id": self.next_roll_id,
            "mode": "1 видео",
            "voice_file": "",
            "text": "",
            "video_single": "",
            "video_start": "",
            "video_end": "",
            "audio_start": "",
            "audio_end": "",
            "status": "Ожидает",
        }
        self.next_roll_id += 1
        self.rolls.append(roll)
        self.selected_roll_id = roll["id"]
        self.render_scene_list()
        self.render_scene_detail()
        self.update_status()

    def clear_rolls(self):
        if self.is_processing:
            messagebox.showwarning("Очередь", "Нельзя очищать во время обработки.")
            return
        self.rolls = []
        self.add_roll()

    def render_rolls(self):
        self.render_scene_list()
        self.render_scene_detail()

    def render_scene_list(self):
        for w in self.scene_list_scroll.winfo_children():
            w.destroy()
        for idx, roll in enumerate(self.rolls):
            self._render_scene_item(idx, roll)
        self._enable_mousewheel(self.scene_list_scroll)

    def _render_scene_item(self, idx, roll):
        is_active = (roll["id"] == self.selected_roll_id)
        bg = BTN_PRIMARY if is_active else CARD

        item = ctk.CTkFrame(
            self.scene_list_scroll, fg_color=bg, corner_radius=10, cursor="hand2")
        item.pack(fill="x", padx=8, pady=(0, 5))

        top_row = ctk.CTkFrame(item, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=(8, 2))

        title = self.get_roll_title(idx, roll)
        title_lbl = ctk.CTkLabel(
            top_row, text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT, anchor="w",
        )
        title_lbl.pack(side="left", fill="x", expand=True)

        status_color = MUTED
        if roll["status"] == "Готово":
            status_color = "#86efac"
        elif roll["status"] == "Ошибка":
            status_color = "#fca5a5"
        elif roll["status"] == "В очереди" or roll["status"] == "Обработка":
            status_color = "#fcd34d"

        ctk.CTkLabel(
            top_row, text=roll["status"],
            font=ctk.CTkFont(size=10),
            text_color=status_color, anchor="e",
        ).pack(side="right")

        mode_lbl = ctk.CTkLabel(
            item, text=roll["mode"],
            font=ctk.CTkFont(size=11),
            text_color=MUTED, anchor="w",
        )
        mode_lbl.pack(anchor="w", padx=10, pady=(0, 8))

        def select(event=None, rid=roll["id"]):
            self.selected_roll_id = rid
            self.render_scene_list()
            self.render_scene_detail()

        def on_enter(event, w=item, rid=roll["id"]):
            if rid != self.selected_roll_id:
                w.configure(fg_color=BTN_HOVER)

        def on_leave(event, w=item, rid=roll["id"]):
            if rid != self.selected_roll_id:
                w.configure(fg_color=CARD)

        for widget in (item, top_row, title_lbl, mode_lbl):
            widget.bind("<Button-1>", select)
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)

        self._add_copy_menu(title_lbl, lambda lbl=title_lbl: lbl.cget("text"))
        self._apply_dnd(item, lambda r=roll: r, also_select=True)

    def render_scene_detail(self):
        self._active_panel = "scene"
        for w in self.detail_panel.winfo_children():
            w.destroy()

        roll = next((r for r in self.rolls if r["id"] == self.selected_roll_id), None)
        if roll is None:
            self.label(self.detail_panel, "Выбери сцену из списка слева",
                       size=15, color=MUTED).pack(expand=True, anchor="center", pady=60)
            return

        idx = self.rolls.index(roll)

        # Scrollable container for detail content
        detail_scroll = ctk.CTkScrollableFrame(
            self.detail_panel, fg_color=BG,
            scrollbar_button_color="#3d3d6a",
            scrollbar_button_hover_color="#ff7eb6",
        )
        detail_scroll.pack(fill="both", expand=True)
        self._enable_mousewheel(detail_scroll)

        # ── Header ────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(detail_scroll, fg_color=CARD, corner_radius=16)
        hdr.pack(fill="x", padx=16, pady=(16, 10))

        title_row = ctk.CTkFrame(hdr, fg_color="transparent")
        title_row.pack(fill="x", padx=16, pady=(14, 6))

        scene_title = self.get_roll_title(idx, roll)
        self.label(title_row, scene_title, size=20, weight="bold").pack(side="left")

        status_color = MUTED
        if roll["status"] == "Готово":
            status_color = "#86efac"
        elif roll["status"] == "Ошибка":
            status_color = "#fca5a5"
        elif roll["status"] in ("В очереди", "Обработка"):
            status_color = "#fcd34d"

        self.label(title_row, roll["status"], size=13, color=status_color).pack(
            side="right", padx=(0, 8))

        self.button(title_row, "Удалить",
                    lambda rid=roll["id"]: self.remove_roll_by_id(rid),
                    color=BTN_DANGER, hover=BTN_DANGER_HOVER, width=100).pack(
            side="right", padx=(0, 10))

        # ── Mode ──────────────────────────────────────────────────────────
        mode_row = ctk.CTkFrame(hdr, fg_color="transparent")
        mode_row.pack(fill="x", padx=16, pady=(0, 14))
        self.label(mode_row, "Режим:", size=13, color=MUTED).pack(side="left", padx=(0, 10))
        mode_menu = ctk.CTkOptionMenu(
            mode_row, values=["1 видео", "Начало + Конец"],
            command=lambda value, i=idx: self.set_roll_mode(i, value),
            fg_color=BTN, button_color=BTN, button_hover_color=BTN_HOVER,
            dropdown_fg_color=PANEL, dropdown_hover_color=BTN_HOVER, width=170,
        )
        mode_menu.set(roll["mode"])
        mode_menu.pack(side="left")

        # ── Summary ───────────────────────────────────────────────────────
        self.label(detail_scroll, self.roll_details(roll),
                   size=12, color=MUTED).pack(anchor="w", padx=20, pady=(0, 10))

        # ── Actions ───────────────────────────────────────────────────────
        actions = ctk.CTkFrame(detail_scroll, fg_color=CARD, corner_radius=16)
        actions.pack(fill="x", padx=16, pady=(0, 10))

        row1 = ctk.CTkFrame(actions, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=(14, 10))

        self._apply_dnd(row1, lambda r=roll: r)

        self.button(row1, "📁 Выбрать папку",
                    lambda i=idx: self.choose_folder_auto(i),
                    color=BTN, hover=BTN_HOVER, width=180).pack(side="left", padx=(0, 10))

        voice_text = "✅ full_voice.mp3" if roll["voice_file"] else "Выбрать full_voice"
        self.button(row1, voice_text, lambda i=idx: self.choose_voice_file(i),
                    color=BTN_OK if roll["voice_file"] else BTN_PRIMARY,
                    hover=BTN_OK_HOVER if roll["voice_file"] else BTN_PRIMARY_HOVER,
                    width=190).pack(side="left")

        if roll["mode"] == "Начало + Конец":
            text_btn = "✅ Текст проверки" if roll["text"] else "Текст проверки"
            self.button(row1, text_btn, lambda i=idx: self.open_roll_text_dialog(i),
                        color=BTN_OK if roll["text"] else BTN_PRIMARY,
                        hover=BTN_OK_HOVER if roll["text"] else BTN_PRIMARY_HOVER,
                        width=180).pack(side="left", padx=(10, 0))

        # ── Video / audio trim ────────────────────────────────────────────
        row2 = ctk.CTkFrame(actions, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(0, 14))
        self._apply_dnd(row2, lambda r=roll: r)

        if roll["mode"] == "1 видео":
            time_row = ctk.CTkFrame(actions, fg_color="transparent")
            time_row.pack(fill="x", padx=16, pady=(0, 10))

            self.label(time_row, "Обрезать аудио (сек):",
                       size=12, color=MUTED).pack(side="left", padx=(0, 8))

            start_entry = ctk.CTkEntry(
                time_row, width=90, placeholder_text="от",
                fg_color="white", text_color="#111", border_color="#737373")
            start_entry.pack(side="left", padx=(0, 6))
            if roll["audio_start"]:
                start_entry.insert(0, roll["audio_start"])

            end_entry = ctk.CTkEntry(
                time_row, width=90, placeholder_text="до",
                fg_color="white", text_color="#111", border_color="#737373")
            end_entry.pack(side="left")
            if roll["audio_end"]:
                end_entry.insert(0, roll["audio_end"])

            def save_times(*_, r=roll):
                for entry, key in [(start_entry, "audio_start"), (end_entry, "audio_end")]:
                    val = entry.get().strip()
                    if val:
                        try:
                            float(val)
                            r[key] = val
                        except ValueError:
                            entry.delete(0, "end")
                            r[key] = ""
                    else:
                        r[key] = ""

            for e in (start_entry, end_entry):
                e.bind("<FocusOut>", save_times)
                e.bind("<Return>", save_times)

            video_text = "✅ Видео" if roll["video_single"] else "Выбрать видео"
            self.button(row2, video_text, lambda i=idx: self.choose_video(i, "single"),
                        color=BTN_OK if roll["video_single"] else BTN_PRIMARY,
                        hover=BTN_OK_HOVER if roll["video_single"] else BTN_PRIMARY_HOVER,
                        width=170).pack(side="left")
        else:
            start_text = "✅ Видео начало" if roll["video_start"] else "Видео начало"
            end_text = "✅ Видео конец" if roll["video_end"] else "Видео конец"
            self.button(row2, start_text, lambda i=idx: self.choose_video(i, "start"),
                        color=BTN_OK if roll["video_start"] else BTN_PRIMARY,
                        hover=BTN_OK_HOVER if roll["video_start"] else BTN_PRIMARY_HOVER,
                        width=170).pack(side="left")
            self.button(row2, end_text, lambda i=idx: self.choose_video(i, "end"),
                        color=BTN_OK if roll["video_end"] else BTN_PRIMARY,
                        hover=BTN_OK_HOVER if roll["video_end"] else BTN_PRIMARY_HOVER,
                        width=170).pack(side="left", padx=(10, 0))


    def roll_details(self, roll):
        parts = []
        parts.append(f"Голос: {Path(roll['voice_file']).name}" if roll["voice_file"] else "Голос: не выбран")
        if roll["text"]:
            preview = roll["text"].replace("\n", " ")
            if len(preview) > 95:
                preview = preview[:95] + "..."
            parts.append(f"Текст: {preview}")
        else:
            parts.append("Текст проверки: пусто")
        if roll["mode"] == "1 видео":
            parts.append(f"Видео: {Path(roll['video_single']).name if roll['video_single'] else 'не выбрано'}")
        else:
            parts.append(f"Начало: {Path(roll['video_start']).name if roll['video_start'] else 'не выбрано'}")
            parts.append(f"Конец: {Path(roll['video_end']).name if roll['video_end'] else 'не выбрано'}")
        return " | ".join(parts)
    
    def get_roll_title(self, idx, roll):
        if roll["mode"] == "1 видео":
            title_video = roll["video_single"]
        else:
            title_video = roll["video_start"] or roll["video_end"]

        if title_video:
            name = Path(title_video).stem
            return name[:40] + "..." if len(name) > 40 else name

        return f"Сцена {idx + 1}"

    def refresh_rolls(self):
        self.render_scene_list()
        self.render_scene_detail()
        self.update_status()

    def set_roll_mode(self, idx, value):
        self.rolls[idx]["mode"] = value
        self.rolls[idx]["status"] = "Ожидает"
        self.refresh_rolls()



    def remove_roll_by_id(self, roll_id):
        if self.is_processing:
            messagebox.showwarning("Очередь", "Нельзя удалять во время обработки.")
            return

        removed_idx = next(
            (i for i, r in enumerate(self.rolls) if r["id"] == roll_id), None)
        self.rolls = [r for r in self.rolls if r["id"] != roll_id]

        if not self.rolls:
            self.add_roll()
            return

        if self.selected_roll_id == roll_id:
            new_idx = min(removed_idx, len(self.rolls) - 1)
            self.selected_roll_id = self.rolls[new_idx]["id"]

        self.render_scene_list()
        self.render_scene_detail()
        self.update_status()



    def choose_folder_auto(self, idx):
        folder = filedialog.askdirectory(
            parent=self, title="Выбери папку с аудио и видео"
        )
        if not folder:
            return

        audio_exts = {".mp3", ".wav", ".m4a", ".aac"}
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".MOV"}

        audio_found = None
        video_found = None

        for f in Path(folder).iterdir():
            if f.is_file():
                if f.suffix.lower() in audio_exts and not audio_found:
                    audio_found = str(f)
                if f.suffix.lower() in video_exts and not video_found:
                    video_found = str(f)

        messages = []
        if audio_found:
            self.rolls[idx]["voice_file"] = audio_found
            messages.append(f"Аудио: {Path(audio_found).name}")
        else:
            messages.append("⚠ Аудио не найдено")

        if video_found:
            self.rolls[idx]["video_single"] = video_found
            messages.append(f"Видео: {Path(video_found).name}")
        else:
            messages.append("⚠ Видео не найдено")

        self.rolls[idx]["status"] = "Ожидает"
        self.refresh_rolls()

        if audio_found or video_found:
            messagebox.showinfo("Найдено", "\n".join(messages), parent=self)
        else:
            messagebox.showwarning("Ничего не найдено",
                "В папке нет аудио или видео файлов.", parent=self)

    def choose_voice_file(self, idx):
        f = filedialog.askopenfilename(
            parent=self, title="Выбери full_voice.mp3 / wav",
            filetypes=[("Аудио", "*.mp3 *.wav *.m4a *.aac"), ("Все файлы", "*.*")])
        if f:
            self.rolls[idx]["voice_file"] = f
            self.rolls[idx]["status"] = "Ожидает"
            self.render_scene_list()
            self.render_scene_detail()
            self.update_status()

    def choose_video(self, idx, kind):
        f = filedialog.askopenfilename(
            parent=self, title="Выбери видео",
            filetypes=[("Видео", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm"), ("Все файлы", "*.*")])
        if f:
            if kind == "single":
                self.rolls[idx]["video_single"] = f
            elif kind == "start":
                self.rolls[idx]["video_start"] = f
            elif kind == "end":
                self.rolls[idx]["video_end"] = f
            self.rolls[idx]["status"] = "Ожидает"
            self.render_scene_list()
            self.render_scene_detail()
            self.update_status()

    def open_roll_text_dialog(self, idx):
        roll = self.rolls[idx]
        win = ctk.CTkToplevel(self)
        win.title(f"Текст проверки — ролик {idx + 1}")
        win.geometry("840x620")
        win.configure(fg_color=BG)
        win.transient(self)
        win.after(100, win.grab_set)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="both", expand=True, padx=24, pady=24)
        roll_title = self.get_roll_title(idx, roll)
        self.label(frame,f"Текст — {roll_title}",size=24,weight="bold").pack(anchor="w", padx=22, pady=(20, 6))
        self.label(frame, "Для режима 'Начало + Конец': до первого ------ = начало, после последнего ------ = конец. Середина игнорируется.", size=13, color=MUTED).pack(anchor="w", padx=22, pady=(0, 14))
        text_box = ctk.CTkTextbox(frame, fg_color="#ffffff", text_color="#111111", border_width=1, border_color="#737373", corner_radius=10, font=ctk.CTkFont(size=14), wrap="word")
        text_box.pack(fill="both", expand=True, padx=22, pady=(0, 18))
        if roll["text"]:
            text_box.insert("1.0", roll["text"])
        self._fix_paste(text_box)
        def save():
            text_value = text_box.get("1.0", "end").strip()
            if not text_value:
                messagebox.showerror("Ошибка", "Текст пустой.", parent=win)
                return
            if roll["mode"] == "Начало + Конец":
                try:
                    parse_start_end_text(text_value)
                except Exception as e:
                    messagebox.showerror("Ошибка в тексте", str(e), parent=win)
                    return
            roll["text"] = text_value
            roll["status"] = "Ожидает"
            win.destroy()
            self.render_scene_list()
            self.render_scene_detail()
            self.update_status()
        self.button(frame, "Сохранить", save, color=BTN_OK, hover=BTN_OK_HOVER, width=180).pack(anchor="e", padx=22, pady=(0, 20))

    def validate_rolls(self):
        valid, errors = [], []
        for i, roll in enumerate(self.rolls):
            has_any = any([roll["voice_file"], roll["text"], roll["video_single"], roll["video_start"], roll["video_end"]])
            if not has_any:
                continue
            if not roll["voice_file"]:
                errors.append(f"Ролик {i + 1}: не выбран full_voice.mp3.")
            if roll["mode"] == "1 видео":
                if not roll["video_single"]:
                    errors.append(f"Ролик {i + 1}: не выбрано видео.")
            else:
                if not roll["text"]:
                    errors.append(f"Ролик {i + 1}: нет текста проверки.")
                if not roll["video_start"]:
                    errors.append(f"Ролик {i + 1}: не выбрано видео начала.")
                if not roll["video_end"]:
                    errors.append(f"Ролик {i + 1}: не выбрано видео конца.")
                if roll["text"]:
                    try:
                        parse_start_end_text(roll["text"])
                    except Exception as e:
                        errors.append(f"Ролик {i + 1}: {e}")
            complete = False
            if roll["mode"] == "1 видео":
                complete = bool(roll["voice_file"] and roll["video_single"])
            else:
                complete = bool(roll["voice_file"] and roll["text"] and roll["video_start"] and roll["video_end"])
            if complete:
                valid.append((i, roll))
        return valid, errors

    def start_queue(self):
        if self.is_processing:
            messagebox.showinfo("Очередь", "Очередь уже обрабатывается.")
            return
        valid, errors = self.validate_rolls()
        if errors:
            messagebox.showerror("Ошибка", "\n".join(errors))
            return
        if not valid:
            messagebox.showerror("Ошибка", "Нет роликов для обработки.")
            return
        if not self.sync_key or "ВСТАВЬ" in self.sync_key:
            messagebox.showerror(
                "Ошибка",
                "Не задан Sync API key. Нажми кнопку «Sync key» в верхней панели и введи ключ."
            )
            return
        if not self.cloudinary_cloud_name or not self.cloudinary_api_key or not self.cloudinary_api_secret:
            messagebox.showerror(
                "Ошибка",
                "Добавьте Cloudinary ключи в Настройки.\nНажми кнопку «Cloudinary key» в боковой панели."
            )
            return
        global SYNC_API_KEY
        SYNC_API_KEY = self.sync_key
        self.open_log_window()
        self.is_processing = True
        self.start_btn.configure(state="disabled", text="ОБРАБАТЫВАЮ ОЧЕРЕДЬ...")
        thread = threading.Thread(target=self.queue_thread, args=(valid,), daemon=True)
        thread.start()


    def _process_single_roll(self, order_num, idx, roll, batch_dir, log):
        """Обрабатывает один ролик (используется и в последовательном, и в параллельном режиме)."""
        if roll["mode"] == "1 видео":
            roll_video_name = Path(roll["video_single"]).stem
        else:
            roll_video_name = Path(roll["video_start"]).stem

        temp_dir = str(Path(batch_dir) / f".tmp_{order_num:02d}_{safe_name(roll_video_name)}")
        ensure_dir(temp_dir)

        full_voice_copy = copy_file(roll["voice_file"], str(Path(temp_dir) / "full_voice.mp3"))
        if roll["text"]:
            with open(Path(temp_dir) / "full_text.txt", "w", encoding="utf-8") as f:
                f.write(roll["text"])

        log("=" * 60)
        log(f"Ролик {idx + 1} / очередь {order_num}")
        log(f"Режим: {roll['mode']}")
        log("=" * 60)

        if roll["mode"] == "1 видео":
            audio_wav = str(Path(temp_dir) / "full_voice.wav")
            convert_audio_to_wav_trimmed(
                full_voice_copy,
                audio_wav,
                start_sec=roll.get("audio_start", ""),
                end_sec=roll.get("audio_end", ""),
            )
            if roll.get("audio_start") or roll.get("audio_end"):
                log(f"Обрезка: {roll.get('audio_start') or '0'} → {roll.get('audio_end') or 'конец'} сек")

            video_name = safe_name(Path(roll["video_single"]).stem)
            out = str(Path(batch_dir) / f"{order_num:02d}_{video_name}.mp4")
            process_lipsync(roll["video_single"], audio_wav, out, temp_dir, log)
            log(f"ГОТОВО: {out}")
        else:
            start_text, end_text, sep_count = parse_start_end_text(roll["text"])
            part_start, part_end = split_start_end_by_silence(
                full_voice_copy, temp_dir, log,
                expected_separators=sep_count,
            )
            video_name_start = safe_name(Path(roll["video_start"]).stem)
            video_name_end = safe_name(Path(roll["video_end"]).stem)

            out_start = str(Path(batch_dir) / f"{order_num:02d}_{video_name_start}_start.mp4")
            out_end = str(Path(batch_dir) / f"{video_name_end}_end.mp4")
            log("Lipsync start...")
            process_lipsync(roll["video_start"], part_start, out_start, temp_dir, log)
            log("Lipsync end...")
            process_lipsync(roll["video_end"], part_end, out_end, temp_dir, log)
            log(f"ГОТОВО: {out_start}")
            log(f"ГОТОВО: {out_end}")

        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass    

    def queue_thread(self, valid_rolls):
        try:
            failed_videos = []
            failed_lock = threading.Lock()
            base_output = str(Path(desktop_dir()) / "Lipsync_Queue_Output")
            ensure_dir(base_output)

            batch_name = time.strftime("%d-%m-%Y_%H-%M")
            batch_dir = str(Path(base_output) / batch_name)
            ensure_dir(batch_dir)

            total = len(valid_rolls)
            self.log("=" * 70)
            self.log(f"Папка партии: {batch_dir}")
            self.log(f"Роликов в очереди: {total}")
            self.log(f"Параллельность: {CONCURRENT_ROLLS} ролика одновременно")
            self.log(f"Стартовая задержка между запусками: {PAUSE_BETWEEN_ROLLS_SEC} сек")
            self.log("=" * 70)

            def process_one(order_num, idx, roll):
                prefix = f"[#{order_num:02d}]"

                def prefixed_log(msg):
                    self.log(f"{prefix} {msg}")

                # Стагер старта: тред N стартует через N*PAUSE сек — предотвращает
                # одновременный удар по Sync API несколькими потоками
                stagger = (order_num - 1) * PAUSE_BETWEEN_ROLLS_SEC
                if stagger > 0:
                    prefixed_log(f"Жду {stagger} сек перед стартом...")
                    time.sleep(stagger)

                try:
                    self.set_roll_status(idx, f"Обработка {order_num}/{total}")
                    self._process_single_roll(order_num, idx, roll, batch_dir, prefixed_log)
                    self.set_roll_status(idx, "Готово")
                except Exception as e:
                    if roll["mode"] == "1 видео":
                        video_name = Path(roll.get("video_single", "")).name or f"ролик {idx + 1}"
                    else:
                        s = Path(roll.get("video_start", "")).name
                        en = Path(roll.get("video_end", "")).name
                        video_name = f"{s} + {en}" if (s and en) else f"ролик {idx + 1}"

                    error_text = str(e)
                    prefixed_log(f"❌ ОШИБКА в '{video_name}': {error_text}")
                    self.set_roll_status(idx, "Ошибка")

                    with failed_lock:
                        failed_videos.append({
                            "video_name": video_name,
                            "error": error_text
                        })

            with ThreadPoolExecutor(max_workers=CONCURRENT_ROLLS) as executor:
                futures = [
                    executor.submit(process_one, order_num, idx, roll)
                    for order_num, (idx, roll) in enumerate(valid_rolls, start=1)
                ]
                for f in futures:
                    f.result()

            if failed_videos:
                error_message = "Очередь завершена, но были ошибки:\n\n"
                for item in failed_videos:
                    error_message += f"❌ {item['video_name']}\nПричина: {item['error']}\n\n"

                def done_with_errors():
                    messagebox.showwarning("Готово с ошибками", error_message)
                    open_folder(batch_dir)

                self.after(0, done_with_errors)
            else:
                def done_ok():
                    messagebox.showinfo(
                        "Готово",
                        f"✅ Все ролики обработаны успешно.\n\nПапка:\n{batch_dir}"
                    )
                    open_folder(batch_dir)

                self.after(0, done_ok)

        except Exception as e:
            err = str(e)
            self.log(f"ОБЩАЯ ОШИБКА: {err}")
            self.after(0, lambda: messagebox.showerror("Ошибка", err))
        finally:
            self.is_processing = False
            self.after(0, lambda: self.start_btn.configure(state="normal", text="НАЧАТЬ ОЧЕРЕДЬ"))

    def set_roll_status(self, idx, status):
        def update():
            if 0 <= idx < len(self.rolls):
                self.rolls[idx]["status"] = status
                self.render_scene_list()
                if self.rolls[idx]["id"] == self.selected_roll_id:
                    self.render_scene_detail()
                self.update_status()
        self.after(0, update)

    def _refresh_balance_badge(self):
        if not self.api_key:
            return

        def run():
            balance = fetch_elevenlabs_balance(self.api_key)
            self.log(f"Balance fetch result: {balance}")
            if balance:
                self._el_balance = balance
                remaining = balance["remaining"]
                label = f"{remaining // 1000}k" if remaining >= 1000 else str(remaining)
                pct = balance["remaining"] / max(balance["limit"], 1)
                if pct > 0.3:
                    badge_color = "#0a5c47"
                    badge_text_color = "#86efac"
                elif pct > 0.1:
                    badge_color = "#7a4f00"
                    badge_text_color = "#fcd34d"
                else:
                    badge_color = "#7a1500"
                    badge_text_color = "#fca5a5"
                self.after(500, lambda l=label, bc=badge_color, tc=badge_text_color:
                    self._update_api_btn_badge(l, bc, tc))

        threading.Thread(target=run, daemon=True).start()

    def _update_api_btn_badge(self, label, badge_color, text_color):
        try:
            if not self.api_btn.winfo_exists():
                return
            self.api_btn.configure(text=f"API key  ·  {label} симв.")
        except Exception:
            pass

    def update_status(self):
        valid, _ = self.validate_rolls()
        self.status_label.configure(text=f"готовых роликов: {len(valid)} | всего роликов: {len(self.rolls)}")
        # API key button: green if key set, primary if panel active, transparent otherwise
        def _api_btn_text():
            if self._el_balance:
                remaining = self._el_balance["remaining"]
                label = f"{remaining // 1000}k" if remaining >= 1000 else str(remaining)
                return f"API key  ·  {label} симв."
            return "API key"

        if self.api_key:
            self.api_btn.configure(fg_color=BTN_OK, hover_color=BTN_OK_HOVER)
            self.api_btn.configure(text=_api_btn_text())
        elif self._active_panel == "api":
            self.api_btn.configure(fg_color=BTN_PRIMARY, hover_color=BTN_PRIMARY_HOVER)
            self.api_btn.configure(text=_api_btn_text())
        else:
            self.api_btn.configure(fg_color="transparent", hover_color=BTN_HOVER)
            self.api_btn.configure(text=_api_btn_text())

        if self.sync_key and "ВСТАВЬ" not in self.sync_key:
            self.sync_btn.configure(fg_color=BTN_OK, hover_color=BTN_OK_HOVER)
        elif self._active_panel == "sync":
            self.sync_btn.configure(fg_color=BTN_PRIMARY, hover_color=BTN_PRIMARY_HOVER)
        else:
            self.sync_btn.configure(fg_color="transparent", hover_color=BTN_HOVER)

        for btn, panel in [
            (self.voice_btn, "voice_gen"),
            (self.clone_btn, "voice_clone"),
            (self.dl_btn, "downloader"),
        ]:
            if self._active_panel == panel:
                btn.configure(fg_color=BTN_PRIMARY, hover_color=BTN_PRIMARY_HOVER)
            else:
                btn.configure(fg_color="transparent", hover_color=BTN_HOVER)


    def open_log_window(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.lift()
            return
        self.log_window = ctk.CTkToplevel(self)
        self.log_window.title("Прогресс очереди")
        self.log_window.geometry("940x640")
        self.log_window.configure(fg_color=BG)
        self.log_box = ctk.CTkTextbox(self.log_window, fg_color="#111111", text_color="#e5e5e5", font=ctk.CTkFont(family="Courier New", size=12), corner_radius=10)
        self.log_box.pack(fill="both", expand=True, padx=18, pady=18)
        self.log_box.bind("<Command-a>", lambda e: self.log_box.tag_add("sel", "1.0", "end"))
        self.log_box.bind("<Command-c>", lambda e: None)

    def log(self, msg):
        self.log_queue.put(str(msg))

    def poll_logs(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if self.log_box is not None and self.log_box.winfo_exists():
                    self.log_box.insert("end", msg + "\n")
                    self.log_box.see("end")
        except queue.Empty:
            pass
        self.after(120, self.poll_logs)

    def _voice_expiry_watcher(self):
        while True:
            time.sleep(60)
            now = time.time()
            expired = [v for v in self.voice_expiry_list if v["expires_at"] <= now]
            for v in expired:
                try:
                    if not self.api_key:
                        continue
                    deleted = delete_elevenlabs_voice(v["voice_id"], self.api_key)
                    if deleted:
                        self.log(f"Голос «{v['name']}» автоудалён (истёк срок)")
                        self.voice_expiry_list.remove(v)
                        self.save_config()
                except Exception as e:
                    self.log(f"Ошибка удаления голоса {v['name']}: {e}")

    def _cleanup_expired_voices_on_start(self):
        if not self.api_key or not self.voice_expiry_list:
            return
        now = time.time()
        expired = [v for v in self.voice_expiry_list if v["expires_at"] <= now]
        for v in expired:
            try:
                delete_elevenlabs_voice(v["voice_id"], self.api_key)
                self.voice_expiry_list.remove(v)
                self.log(f"Голос «{v['name']}» удалён при старте (просрочен)")
            except Exception:
                pass
        if expired:
            self.save_config()

    def load_config(self):
        """Загружает API ключи и настройки из ~/.sheqelmotion.json."""
        try:
            if not os.path.exists(CONFIG_PATH):
                return
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.api_key = data.get("elevenlabs_api_key", "") or self.api_key
            sync_key = data.get("sync_api_key", "")
            if sync_key:
                self.sync_key = sync_key
                global SYNC_API_KEY
                SYNC_API_KEY = sync_key
            self.cloudinary_cloud_name = data.get("cloudinary_cloud_name", "") or self.cloudinary_cloud_name
            self.cloudinary_api_key    = data.get("cloudinary_api_key", "")    or self.cloudinary_api_key
            self.cloudinary_api_secret = data.get("cloudinary_api_secret", "") or self.cloudinary_api_secret
            global CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
            CLOUDINARY_CLOUD_NAME  = self.cloudinary_cloud_name
            CLOUDINARY_API_KEY     = self.cloudinary_api_key
            CLOUDINARY_API_SECRET  = self.cloudinary_api_secret
            self.main_voice_name = data.get("main_voice_name", "") or self.main_voice_name
            self.voice_expiry_list = data.get("voice_expiry_list", [])
        except Exception as e:
            self.log(f"Не удалось загрузить конфиг: {e}")

    def save_config(self):
        """Сохраняет API ключи и настройки в ~/.sheqelmotion.json."""
        try:
            data = {
                "elevenlabs_api_key":    self.api_key,
                "sync_api_key":          self.sync_key,
                "cloudinary_cloud_name": self.cloudinary_cloud_name,
                "cloudinary_api_key":    self.cloudinary_api_key,
                "cloudinary_api_secret": self.cloudinary_api_secret,
                "main_voice_name":       self.main_voice_name,
                "voice_expiry_list":     self.voice_expiry_list,
            }
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"Не удалось сохранить конфиг: {e}")

    def _enable_mousewheel(self, scrollable):
    #Включает скролл колесом мыши на всех виджетах внутри scrollable frame.
        canvas = scrollable._parent_canvas

        def on_wheel(event):
            if sys.platform == "darwin":
                delta = -1 * event.delta
            else:
                delta = int(-1 * (event.delta / 120))

            canvas.yview_scroll(delta, "units")

        def on_b4(event):
            canvas.yview_scroll(-1, "units")

        def on_b5(event):
            canvas.yview_scroll(1, "units")

        def bind_recursive(w):
            w.bind("<MouseWheel>", on_wheel, add="+")
            w.bind("<Button-4>", on_b4, add="+")
            w.bind("<Button-5>", on_b5, add="+")
            for child in w.winfo_children():
                bind_recursive(child)

        bind_recursive(scrollable)


def main():
    app = LipsyncTwoModeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
