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
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import customtkinter as ctk
from tkinter import filedialog, messagebox

SYNC_API_KEY = ""
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".version.json")
ELEVENLABS_API_KEY = ""
APP_VERSION = "1.0.5"
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
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_binary(name):
    return shutil.which(name) or name


def ffmpeg_bin():
    return find_binary("ffmpeg")


def ffprobe_bin():
    return find_binary("ffprobe")


def run(cmd):
    subprocess.run(cmd, check=True)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def file_exists_ok(path, min_size=1024):
    return os.path.exists(path) and os.path.getsize(path) >= min_size


def safe_name(name):
    name = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", name)
    return name.strip("_") or "roll"


def desktop_dir():
    d = os.path.join(os.path.expanduser("~"), "Desktop")
    return d if os.path.isdir(d) else os.path.expanduser("~")

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
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ])
    return float(result.decode().strip())


def copy_file(src, dst):
    ensure_dir(os.path.dirname(dst))
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
    save_dir = os.path.join(desktop_dir(), "ElevenLabs_Generated_Voices")
    ensure_dir(save_dir)
    safe_voice = safe_name(voice_name or voice_id)
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    output_mp3 = os.path.join(save_dir, f"{safe_voice}_{timestamp}_full_voice.mp3")
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
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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

    part_start = os.path.join(output_dir, "part_start.wav")
    part_end = os.path.join(output_dir, "part_end.wav")
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

def upload_to_fileio(file_path, log):
    """Загружает файл на tmpfiles.org и возвращает прямой URL."""
    log(f"Загружаю {os.path.basename(file_path)} на tmpfiles.org...")
    with open(file_path, "rb") as f:
        res = requests.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": f},
            timeout=600,
        )
    if res.status_code != 200:
        raise RuntimeError(f"tmpfiles.org upload error: {res.status_code}\n{res.text}")
    data = res.json()
    public_url = data["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/")
    if not public_url.startswith("http"):
        raise RuntimeError(f"tmpfiles.org вернул не URL: {data}")
    log(f"  → {public_url}")
    return public_url

def apply_lipsync_sync(video_in, audio_wav, final_out, log):
    """Отправляет видео и аудио в Sync через URL-загрузку (без лимита 20MB)."""
    headers = {"x-api-key": SYNC_API_KEY, "Content-Type": "application/json"}
    last_error = None

    for attempt in range(1, SYNC_RETRIES + 1):
        try:
            log(f"Отправляю в Sync... попытка {attempt}/{SYNC_RETRIES}")

            # 1. Заливаем файлы на временный хостинг → получаем URL
            video_url = upload_to_fileio(video_in, log)
            audio_url = upload_to_fileio(audio_wav, log)

            # 2. Отправляем в Sync только URL (никакого multipart)
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
    sync_video = os.path.join(temp_dir, safe_name(os.path.basename(output_mp4)) + "_sync_input.mp4")
    raw_video = os.path.join(temp_dir, safe_name(os.path.basename(output_mp4)) + "_raw.mp4")
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
    """Возвращает путь к директории с ffmpeg для передачи yt-dlp."""
    bin_path = ffmpeg_bin()
    if os.path.isabs(bin_path):
        return os.path.dirname(bin_path)
    return None


def download_from_url(url, output_dir, mode, denoise, log):
    """
    Скачивает видео или аудио через yt-dlp в максимальном качестве.
    mode: 'video' | 'audio'
    denoise: bool — применить голосовой денойз к аудио
    """
    yt_dlp = _ensure_ytdlp()
    ensure_dir(output_dir)

    # Добавляем директорию с бандленным ffmpeg в PATH для yt-dlp
    base = app_base_dir()
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
            log(f"  Скачано: {os.path.basename(d.get('filename', ''))}")

    common = {
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
    }
    if ffmpeg_dir:
        common["ffmpeg_location"] = ffmpeg_dir

    if mode == "video":
        outtmpl = os.path.join(output_dir, f"{timestamp}_%(title).80s.%(ext)s")
        opts = {
            **common,
            "format": (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[ext=mp4]+bestaudio/"
                "bestvideo+bestaudio/best"
            ),
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
        }
        log("Получаю информацию о видео...")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                filename = os.path.splitext(filename)[0] + ".mp4"
        log(f"Сохранено: {filename}")
        return filename, None

    elif mode == "audio":
        outtmpl = os.path.join(output_dir, f"{timestamp}_%(title).80s.%(ext)s")
        opts = {
            **common,
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
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
        filename = os.path.splitext(base_name)[0] + ".mp3"

        if denoise and os.path.exists(filename):
            log(f"Передаю в audio-separator: {os.path.basename(filename)}")
            vocals_out = os.path.splitext(filename)[0] + "_vocals.mp3"
            _separate_vocals(filename, vocals_out, log)
            os.remove(filename)
            filename = vocals_out

        log(f"Сохранено: {filename}")
        return filename, None

    else:  # split — видео как есть + аудио отдельно
        outtmpl = os.path.join(output_dir, f"{timestamp}_%(title).80s.%(ext)s")
        opts = {
            **common,
            "format": (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[ext=mp4]+bestaudio/"
                "bestvideo+bestaudio/best"
            ),
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
        }
        log("Скачиваю видео в максимальном качестве...")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                filename = os.path.splitext(filename)[0] + ".mp4"

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

    log(f"[audio-separator] Входной файл: {os.path.basename(input_path)}"
        f" ({os.path.splitext(input_path)[1].upper() or 'нет расширения'})")

    tmp_dir = tempfile.mkdtemp()
    try:
        # ШАГ 1: конвертируем в WAV — audio-separator работает лучше с WAV
        wav_path = os.path.join(tmp_dir, "input.wav")
        log("[audio-separator] ШАГ 1: конвертирую в WAV 44100 Hz...")
        run([ffmpeg_bin(), "-y", "-i", input_path, "-ar", "44100", "-ac", "2", wav_path])
        if not os.path.exists(wav_path):
            raise RuntimeError(f"ffmpeg не создал WAV: {wav_path}")
        log(f"[audio-separator] WAV готов: {os.path.basename(wav_path)}")

        # ШАГ 2: разделяем голос и музыку
        log("[audio-separator] ШАГ 2: запускаю разделение...")
        sep = Separator(output_dir=tmp_dir)
        sep.load_model()
        files = sep.separate(wav_path)

        log(f"[audio-separator] Сгенерированные файлы: {[os.path.basename(f) for f in files]}")

        # ШАГ 3: ищем файл с голосом (Vocals в имени)
        vocals = None
        for f in files:
            full = f if os.path.isabs(f) else os.path.join(tmp_dir, os.path.basename(f))
            if "vocal" in os.path.basename(full).lower() and os.path.exists(full):
                vocals = full
                break

        if not vocals:
            raise RuntimeError(
                f"audio-separator: файл с голосом не найден. "
                f"Файлы: {[os.path.basename(f) for f in files]}"
            )
        log(f"[audio-separator] ШАГ 3: голос найден: {os.path.basename(vocals)}")

        # ШАГ 4: конвертируем результат в mp3
        run([ffmpeg_bin(), "-y", "-i", vocals, "-q:a", "0", output_path])
        log(f"Голос готов: {os.path.basename(output_path)}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _split_video_audio(video_path, output_dir, denoise, log):
    """
    Разделяет видео на:
      - VIDEO.mp4  — оригинальное видео со всем оригинальным звуком
      - AUDIO.mp3  — только аудиодорожка, с денойзом если выбрано
    """
    ensure_dir(output_dir)
    base = os.path.splitext(os.path.basename(video_path))[0]
    video_out = os.path.join(output_dir, base + "_VIDEO.mp4")
    audio_out = os.path.join(output_dir, base + "_AUDIO.mp3")

    log("Копирую оригинальное видео (с голосом)...")
    shutil.copy2(video_path, video_out)

    log("Извлекаю аудио в максимальном качестве...")
    run([ffmpeg_bin(), "-y", "-i", video_path, "-vn", "-q:a", "0", audio_out])

    if denoise:
        log(f"Передаю в audio-separator: {os.path.basename(audio_out)}")
        vocals_out = os.path.join(output_dir, base + "_vocals.mp3")
        _separate_vocals(audio_out, vocals_out, log)
        os.remove(audio_out)
        audio_out = vocals_out

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


class LipsyncTwoModeApp(ctk.CTk):
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
        self.main_voice_id = ""
        self.main_voice_name = ""
        self.rolls = []
        self.next_roll_id = 1
        self.selected_roll_id = None
        self._active_panel = None
        self._stop_download = False
        self.log_queue = queue.Queue()
        self.log_window = None
        self.log_box = None
        self.is_processing = False
        self.sync_key = SYNC_API_KEY
        self.preview_process = None
        self.preview_voice_id = None
        self.build_ui()
        self.add_roll()
        self.poll_logs()
        self.load_config()
        self.update_status()
        self.after(3000, lambda: self.check_for_updates(silent=True))
        self.after(500, self._check_just_updated)


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
            try:
                self._download_and_replace(latest_version, expected_sha256, progress_label, win)
            except Exception as e:
                messagebox.showerror("Ошибка обновления", str(e), parent=win)

        if not is_forced:
            self.button(btn_row, "Позже", win.destroy,
                        color=BTN, hover=BTN_HOVER, width=120).pack(side="right", padx=(8, 0))

        self.button(btn_row, "Скачать обновление", do_update,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=220).pack(side="right")

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
        app_dir = app_base_dir()
        temp_path = os.path.join(app_dir, "app.py.new")
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
        app_path = os.path.join(app_dir, "app.py")
        backup_path = os.path.join(app_dir, "app.py.bak")
        try:
            if os.path.exists(app_path):
                shutil.copy2(app_path, backup_path)
            os.replace(temp_path, app_path)
        except Exception as e:
            raise RuntimeError(f"Не удалось заменить файл: {e}")

        # 6. Записываем флаг «только что обновились»
        flag_path = os.path.join(app_dir, ".just_updated")
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
        flag_path = os.path.join(app_base_dir(), ".just_updated")
        if os.path.exists(flag_path):
            try:
                with open(flag_path, "r", encoding="utf-8") as f:
                    updated_to = f.read().strip()
                os.remove(flag_path)
                # Удаляем бэкап если всё ок
                backup = os.path.join(app_base_dir(), "app.py.bak")
                if os.path.exists(backup):
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

    def button(self, parent, text, command, color=BTN_PRIMARY, hover=BTN_PRIMARY_HOVER, width=150):
        return ctk.CTkButton(parent, text=text, command=command, fg_color=color, hover_color=hover, text_color="white", corner_radius=20, height=38, width=width, font=ctk.CTkFont(size=14, weight="bold"))

    def label(self, parent, text, size=14, weight="normal", color=TEXT):
        return ctk.CTkLabel(parent, text=text, text_color=color, font=ctk.CTkFont(size=size, weight=weight), anchor="w", justify="left")

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
            files = [("files", (os.path.basename(audio_path), f, "audio/mpeg"))]
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

        self.label(frame, "Имя голоса", size=14, weight="bold").pack(anchor="w", padx=22)
        name_entry = ctk.CTkEntry(
            frame, placeholder_text="например: Иван — диктор",
            fg_color="white", text_color="#111", border_color="#737373",
            font=ctk.CTkFont(size=13),
        )
        name_entry.pack(fill="x", padx=22, pady=(6, 14))

        self.label(frame, "Описание (опционально)", size=14, weight="bold").pack(anchor="w", padx=22)
        desc_entry = ctk.CTkEntry(
            frame, placeholder_text="например: тёплый низкий мужской голос",
            fg_color="white", text_color="#111", border_color="#737373",
            font=ctk.CTkFont(size=13),
        )
        desc_entry.pack(fill="x", padx=22, pady=(6, 14))

        self.label(frame, "Аудио-сэмпл", size=14, weight="bold").pack(anchor="w", padx=22)
        file_row = ctk.CTkFrame(frame, fg_color=PANEL)
        file_row.pack(fill="x", padx=22, pady=(6, 14))

        file_label = ctk.CTkLabel(
            file_row, text="  не выбран",
            fg_color="#f5f5f5", text_color="#888",
            anchor="w", corner_radius=6,
            font=ctk.CTkFont(size=12), height=32,
        )
        file_label.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)

        selected = {"path": ""}

        def choose_file():
            f = filedialog.askopenfilename(
                parent=self, title="Выбери аудио-сэмпл",
                filetypes=[("Аудио", "*.mp3 *.wav *.m4a *.flac *.ogg"), ("Все файлы", "*.*")],
            )
            if f:
                selected["path"] = f
                try:
                    size_mb = os.path.getsize(f) / (1024 * 1024)
                    file_label.configure(
                        text=f"  {os.path.basename(f)}  ({size_mb:.1f} МБ)", text_color="#111")
                except Exception:
                    file_label.configure(text=f"  {os.path.basename(f)}", text_color="#111")

        self.button(file_row, "Выбрать файл", choose_file,
                    color=BTN, hover=BTN_HOVER, width=130).pack(side="right")

        result_label = self.label(frame, "", size=12, color=MUTED)
        result_label.pack(anchor="w", padx=22, pady=(0, 8))

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
                result_label.configure(text=f"✓ Готово! voice_id: {voice_id}",
                                       text_color="#86efac")
                self.main_voice_name = name
                self.main_voice_id = voice_id
                messagebox.showinfo(
                    "Готово",
                    f"Голос «{name}» клонирован.\n\nvoice_id: {voice_id}",
                    parent=self,
                )
            except Exception as e:
                result_label.configure(text=f"Ошибка: {e}", text_color="#fca5a5")
                messagebox.showerror("Ошибка", str(e), parent=self)

        self.button(frame, "🎙 Клонировать", clone,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=200).pack(
            anchor="e", padx=22, pady=(0, 20))

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
        folder_var = ctk.StringVar(value=os.path.join(desktop_dir(), "SheqelMotion_Downloads"))
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
                        url, output_dir, mode, denoise, log)
                    show_name = os.path.basename(main_file)
                    if extra_file:
                        show_name += " + " + os.path.basename(extra_file)

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
                            command=lambda path=f: open_folder(os.path.dirname(path)),
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

        cache_dir = os.path.join(tempfile.gettempdir(), "sheqelmotion_previews")
        ensure_dir(cache_dir)
        cache_path = os.path.join(cache_dir, f"{voice_id}.mp3")

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
                local = os.path.join(app_base_dir(),
                                     "ffplay" + (".exe" if sys.platform == "win32" else ""))
                if os.path.exists(local):
                    ffplay = local

            if ffplay:
                self.preview_process = subprocess.Popen(
                    [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", cache_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "darwin":
                self.preview_process = subprocess.Popen(["afplay", cache_path])
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

        list_frame = ctk.CTkScrollableFrame(frame, fg_color=BG)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        status_label = self.label(frame, "Загружаю...", size=12, color=MUTED)
        status_label.pack(anchor="w", padx=16, pady=(0, 4))

        voices_data = []

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

                    ctk.CTkLabel(row, text=v["name"],
                                 font=ctk.CTkFont(size=14, weight="bold"),
                                 text_color=TEXT, anchor="w"
                                 ).pack(side="left", fill="x", expand=True, padx=12, pady=8)

                    
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
   

    def show_voice_gen_panel(self):
        scroll = self._start_panel("voice_gen")
        frame = ctk.CTkFrame(scroll, fg_color=PANEL, corner_radius=20)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        self.label(frame, "Сгенерировать full_voice.mp3", size=22, weight="bold").pack(
            anchor="w", padx=22, pady=(20, 6))
        self.label(
            frame,
            "Пиши текст с ------. Приложение заменит ------ на паузу, ElevenLabs не будет читать тире.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=22, pady=(0, 16))

        self.label(frame, "voice_id или имя голоса", size=14, weight="bold").pack(
            anchor="w", padx=22)
        voice_box = ctk.CTkTextbox(
            frame, height=60, fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=14),
        )
        voice_box.pack(fill="x", padx=22, pady=(6, 8))
        if self.main_voice_name:
            voice_box.insert("1.0", self.main_voice_name)

        def on_voice_picked(name, voice_id):
            voice_box.delete("1.0", "end")
            voice_box.insert("1.0", name)

        self.button(frame, "📋 Выбрать из списка моих голосов",
                    lambda: self.open_voice_picker(self, on_voice_picked),
                    color=BTN, hover=BTN_HOVER, width=320).pack(
            anchor="w", padx=22, pady=(0, 16))

        self.label(frame, "Полный текст", size=14, weight="bold").pack(anchor="w", padx=22)
        text_box = ctk.CTkTextbox(
            frame, height=220,
            fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=14), wrap="word",
        )
        text_box.pack(fill="x", padx=22, pady=(6, 16))

        result_label = self.label(frame, "", size=13, color=MUTED)
        result_label.pack(anchor="w", padx=22, pady=(0, 12))

        def generate_audio():
            if not self.api_key:
                messagebox.showerror("Ошибка", "Сначала добавь ElevenLabs API key.", parent=self)
                return
            voice_value = voice_box.get("1.0", "end").strip()
            full_text = text_box.get("1.0", "end").strip()
            if not voice_value:
                messagebox.showerror("Ошибка", "Введите voice_id или имя голоса.", parent=self)
                return
            if not full_text:
                messagebox.showerror("Ошибка", "Введите текст.", parent=self)
                return
            try:
                result_label.configure(text="Ищу голос...", text_color=MUTED)
                self.update_idletasks()
                if re.fullmatch(r"[A-Za-z0-9]{20}", voice_value):
                    voice_id = voice_value
                    voice_name = voice_value
                else:
                    voice_id = self.fetch_voice_id_by_name(voice_value)
                    voice_name = voice_value
                if not voice_id:
                    result_label.configure(text="Голос с таким именем не найден.",
                                           text_color="#fca5a5")
                    return
                result_label.configure(text="Генерирую full_voice.mp3...", text_color=MUTED)
                self.update_idletasks()
                path = generate_full_voice_to_desktop(
                    self.api_key, voice_id, full_text, voice_name, self.log)
                result_label.configure(text=f"✓ Готово: {path}", text_color="#86efac")
                messagebox.showinfo("Готово", f"Голос сохранён:\n{path}", parent=self)
            except Exception as e:
                result_label.configure(text=f"Ошибка: {e}", text_color="#fca5a5")
                messagebox.showerror("Ошибка", str(e), parent=self)

        self.button(frame, "Сгенерировать mp3", generate_audio,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=220).pack(
            anchor="e", padx=22, pady=(0, 20))

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
        parts.append(f"Голос: {os.path.basename(roll['voice_file'])}" if roll["voice_file"] else "Голос: не выбран")
        if roll["text"]:
            preview = roll["text"].replace("\n", " ")
            if len(preview) > 95:
                preview = preview[:95] + "..."
            parts.append(f"Текст: {preview}")
        else:
            parts.append("Текст проверки: пусто")
        if roll["mode"] == "1 видео":
            parts.append(f"Видео: {os.path.basename(roll['video_single']) if roll['video_single'] else 'не выбрано'}")
        else:
            parts.append(f"Начало: {os.path.basename(roll['video_start']) if roll['video_start'] else 'не выбрано'}")
            parts.append(f"Конец: {os.path.basename(roll['video_end']) if roll['video_end'] else 'не выбрано'}")
        return " | ".join(parts)
    
    def get_roll_title(self, idx, roll):
        if roll["mode"] == "1 видео":
            title_video = roll["video_single"]
        else:
            title_video = roll["video_start"] or roll["video_end"]

        if title_video:
            name = os.path.splitext(os.path.basename(title_video))[0]
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
            roll_video_name = os.path.splitext(os.path.basename(roll["video_single"]))[0]
        else:
            roll_video_name = os.path.splitext(os.path.basename(roll["video_start"]))[0]

        roll_dir = os.path.join(batch_dir, f"{order_num:02d}_{safe_name(roll_video_name)}")
        ensure_dir(roll_dir)
        temp_dir = os.path.join(roll_dir, ".tmp")
        ensure_dir(temp_dir)

        full_voice_copy = copy_file(roll["voice_file"], os.path.join(roll_dir, "full_voice.mp3"))
        if roll["text"]:
            with open(os.path.join(roll_dir, "full_text.txt"), "w", encoding="utf-8") as f:
                f.write(roll["text"])

        log("=" * 60)
        log(f"Ролик {idx + 1} / очередь {order_num}")
        log(f"Режим: {roll['mode']}")
        log(f"Папка: {roll_dir}")
        log("=" * 60)

        if roll["mode"] == "1 видео":
            audio_wav = os.path.join(temp_dir, "full_voice.wav")
            convert_audio_to_wav_trimmed(
                full_voice_copy,
                audio_wav,
                start_sec=roll.get("audio_start", ""),
                end_sec=roll.get("audio_end", ""),
            )
            if roll.get("audio_start") or roll.get("audio_end"):
                log(f"Обрезка: {roll.get('audio_start') or '0'} → {roll.get('audio_end') or 'конец'} сек")

            video_name = safe_name(os.path.splitext(os.path.basename(roll["video_single"]))[0])
            out = os.path.join(roll_dir, f"{video_name}.mp4")
            process_lipsync(roll["video_single"], audio_wav, out, temp_dir, log)
            log(f"ГОТОВО: {out}")
        else:
            start_text, end_text, sep_count = parse_start_end_text(roll["text"])
            part_start, part_end = split_start_end_by_silence(
                full_voice_copy, roll_dir, log,
                expected_separators=sep_count,
            )
            video_name_start = safe_name(os.path.splitext(os.path.basename(roll["video_start"]))[0])
            video_name_end = safe_name(os.path.splitext(os.path.basename(roll["video_end"]))[0])

            out_start = os.path.join(roll_dir, f"{video_name_start}_start.mp4")
            out_end = os.path.join(roll_dir, f"{video_name_end}_end.mp4")
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
            base_output = os.path.join(desktop_dir(), "Lipsync_Queue_Output")
            ensure_dir(base_output)

            batch_name = time.strftime("%d-%m-%Y_%H-%M")
            batch_dir = os.path.join(base_output, batch_name)
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
                        video_name = os.path.basename(roll.get("video_single", "")) or f"ролик {idx + 1}"
                    else:
                        s = os.path.basename(roll.get("video_start", ""))
                        en = os.path.basename(roll.get("video_end", ""))
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

    def update_status(self):
        valid, _ = self.validate_rolls()
        self.status_label.configure(text=f"готовых роликов: {len(valid)} | всего роликов: {len(self.rolls)}")
        # API key button: green if key set, primary if panel active, transparent otherwise
        if self.api_key:
            self.api_btn.configure(fg_color=BTN_OK, hover_color=BTN_OK_HOVER)
        elif self._active_panel == "api":
            self.api_btn.configure(fg_color=BTN_PRIMARY, hover_color=BTN_PRIMARY_HOVER)
        else:
            self.api_btn.configure(fg_color="transparent", hover_color=BTN_HOVER)

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
            self.main_voice_name = data.get("main_voice_name", "") or self.main_voice_name
        except Exception as e:
            self.log(f"Не удалось загрузить конфиг: {e}")

    def save_config(self):
        """Сохраняет API ключи и настройки в ~/.sheqelmotion.json."""
        try:
            data = {
                "elevenlabs_api_key": self.api_key,
                "sync_api_key": self.sync_key,
                "main_voice_name": self.main_voice_name,
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
