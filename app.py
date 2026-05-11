#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import queue
import shutil
import threading
import subprocess
import requests
import json
import urllib.request
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import customtkinter as ctk
from tkinter import filedialog, messagebox

SYNC_API_KEY = ""
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".version.json")
ELEVENLABS_API_KEY = ""
APP_VERSION = "1.0.1"
UPDATE_MANIFEST_URL = f"https://raw.githubusercontent.com/1Sheqel/Sheqel/main/version.json?t={int(time.time())}"



ELEVEN_BASE_URL = "https://api.elevenlabs.io"
ELEVEN_TTS_MODEL_ID = "eleven_v3"
ELEVEN_OUTPUT_FORMAT = "mp3_44100_128"

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
PAUSE_BETWEEN_ROLLS_SEC = 8
SYNC_RETRIES = 3
SILENCE_NOISE = "-25dB"
SILENCE_DURATION = "1.8"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG = "#171717"
PANEL = "#222222"
CARD = "#2b2b2b"
BTN = "#3f3f46"
BTN_HOVER = "#52525b"
BTN_OK = "#3f6212"
BTN_OK_HOVER = "#4d7c0f"
BTN_PRIMARY = "#334155"
BTN_PRIMARY_HOVER = "#475569"
BTN_DANGER = "#7f1d1d"
BTN_DANGER_HOVER = "#991b1b"
TEXT = "#f5f5f5"
MUTED = "#a3a3a3"
CARD_HOVER_DELETE = "#3a1f1f"


def app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_binary(name):
    exe = name + (".exe" if sys.platform == "win32" else "")
    local = os.path.join(app_base_dir(), exe)
    if os.path.exists(local):
        return local
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f"Не найден {exe}. Положи его рядом с приложением или добавь в PATH.")


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
            "stability": 0.85,
            "similarity_boost": 0.90,
            "style": 0.05,
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
        raise RuntimeError("Не нашёл пауз в full_voice.mp3. "
                           "Сгенерируй голос с разделителями ------.")

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

    # Берём первую паузу как конец «начала», последнюю как начало «конца»
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
    audio_duration = get_duration(audio_wav)
    cmd = [
        ffmpeg_bin(), "-y", "-i", input_video, "-t", str(round(audio_duration + END_PADDING, 3)),
        "-vf", f"scale={SYNC_INPUT_SCALE},fps=25",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", output_video,
    ]
    run(cmd)
    if not file_exists_ok(output_video):
        raise RuntimeError(f"Не удалось подготовить видео для Sync: {output_video}")
    return output_video


def apply_lipsync_sync(video_in, audio_wav, final_out, log):
    headers = {"x-api-key": SYNC_API_KEY}
    last_error = None
    for attempt in range(1, SYNC_RETRIES + 1):
        try:
            log(f"Отправляю в Sync... попытка {attempt}/{SYNC_RETRIES}")
            with open(video_in, "rb") as v, open(audio_wav, "rb") as a:
                res = requests.post(
                    SYNC_GENERATE_URL,
                    headers=headers,
                    files={"video": ("video.mp4", v, "video/mp4"), "audio": ("audio.wav", a, "audio/wav")},
                    data={"model": SYNC_MODEL},
                    timeout=300,
                )
            log(f"Sync create response: {res.status_code}")
            log(res.text)
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
                    video_url = status.get("outputUrl") or status.get("output_url")
                    if not video_url:
                        raise RuntimeError(f"Sync completed без outputUrl: {status}")
                    video_data = requests.get(video_url, timeout=300).content
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

    # 🔵 ВНЕШНЕЕ МЯГКОЕ СВЕЧЕНИЕ
    for blur, alpha in [(30, 80), (20, 120), (12, 160)]:
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.text((x, y), text, font=font, fill=(0, 60, 180, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(blur))
        img.alpha_composite(glow)

    # 🔵 ВНУТРЕННЕЕ ЯДРО (яркий неон)
    for blur, alpha in [(6, 220), (3, 255)]:
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.text((x, y), text, font=font, fill=(0, 110, 255, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(blur))
        img.alpha_composite(glow)

    # ⚡ САМ ТЕКСТ (яркий центр трубки)
    draw = ImageDraw.Draw(img)
    draw.text((x, y), text, font=font, fill=(120, 180, 255, 255))

    return img


class LipsyncTwoModeApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SheqelMotion Studio")
        set_adaptive_window(self)   
        self.resizable(True, True)
        self.configure(fg_color=BG)
            # ← ДОБАВИТЬ ЭТОТ БЛОК
        # Принудительно поднимаем окно на передний план (фикс для macOS)
        self.update_idletasks()
        self.lift()
        self.attributes('-topmost', True)
        self.after(200, lambda: self.attributes('-topmost', False))
        self.focus_force()
        # ← КОНЕЦ БЛОКА
        self.api_key = ""
        self.main_voice_id = ""
        self.main_voice_name = ""
        self.rolls = []
        self.next_roll_id = 1
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


    def _parse_version(self, v):
        #Превращает '1.2.10' в (1, 2, 10) для корректного сравнения.
        try:
            return tuple(int(x) for x in str(v).split(".") if x.isdigit())
        except Exception:
            return (0, 0, 0)

    def check_for_updates(self, silent=False):
        """Только проверяет манифест. Сам файл не качает."""
        try:
            res = requests.get(UPDATE_MANIFEST_URL, timeout=10)
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

        current_v = self._parse_version(APP_VERSION)
        latest_v = self._parse_version(latest)
        min_v = self._parse_version(min_required)

        if latest_v <= current_v:
            if not silent:
                messagebox.showinfo("Обновления",
                                    f"У тебя последняя версия: {APP_VERSION}")
            return

        # Показываем диалог обновления — БЕЗ автоскачивания
        self._show_update_dialog(latest, notes, force or (current_v < min_v))

    def _show_update_dialog(self, latest_version, notes, is_forced):
        win = ctk.CTkToplevel(self)
        win.title("Доступно обновление")
        win.geometry("560x500")
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()

        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=16)
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
                self._download_and_replace(latest_version, progress_label, win)
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

    def _download_and_replace(self, new_version, progress_label, parent_win):
        """Качает app.py, проверяет, заменяет, перезапускает."""
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

        # 2. Проверяем что это валидный Python
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

        # 3. Проверяем версию в самом коде
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

        # 4. Заменяем app.py
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

        # 5. Записываем флаг «только что обновились»
        flag_path = os.path.join(app_dir, ".just_updated")
        try:
            with open(flag_path, "w", encoding="utf-8") as f:
                f.write(new_version)
        except Exception:
            pass

        # 6. Перезапускаем приложение
        progress_label.configure(text="Перезапускаюсь...")
        parent_win.update()
        time.sleep(1)

        try:
            # Останавливаем тек. preview
            self.stop_preview()
        except Exception:
            pass

        python_exe = sys.executable
        try:
            os.execv(python_exe, [python_exe, app_path])
        except Exception:
            # Fallback на случай если execv не сработал
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
        return ctk.CTkButton(parent, text=text, command=command, fg_color=color, hover_color=hover, text_color="white", corner_radius=10, height=38, width=width, font=ctk.CTkFont(size=14, weight="bold"))

    def label(self, parent, text, size=14, weight="normal", color=TEXT):
        return ctk.CTkLabel(parent, text=text, text_color=color, font=ctk.CTkFont(size=size, weight=weight), anchor="w", justify="left")

    def build_ui(self):
        header = ctk.CTkFrame(self, fg_color=BG)
        header.pack(fill="x", padx=24, pady=(8, 8))

        logo_img = make_neon_logo("SheqelMotion", 720, 120)
        logo = ctk.CTkImage(light_image=logo_img, dark_image=logo_img, size=(520, 86))

        logo_label = ctk.CTkLabel(header, image=logo, text="")
        logo_label.pack(anchor="center", pady=(0, 2))

        self.label(
            header,
            "2 режима: один lipsync или начало+конец. Видео выбирается внутри карточки.",
            size=13,
            color=MUTED
        ).pack(anchor="center", pady=(0, 8))
        top = ctk.CTkFrame(self, fg_color=BG)
        top.pack(fill="x", padx=24, pady=(4, 12))
        self.api_btn = self.button(top, "API key", self.open_main_api_voice_dialog, color=BTN, hover=BTN_HOVER, width=150)
        self.api_btn.pack(side="left")
        self.sync_btn = self.button(top, "Sync key", self.open_sync_key_dialog, color=BTN, hover=BTN_HOVER, width=130)
        self.sync_btn.pack(side="left", padx=(10, 0))
        self.voice_btn = self.button(top, "Сгенерировать голос", self.open_voice_generator_dialog, color=BTN, hover=BTN_HOVER, width=190)
        self.voice_btn.pack(side="left", padx=(10, 0))
        self.clone_btn = self.button(top, "🎙 Клонировать голос",
                             self.open_voice_clone_dialog,
                             color=BTN, hover=BTN_HOVER, width=200)
        self.clone_btn.pack(side="left", padx=(10, 0))
        self.add_btn = self.button(top, "+ Новая задача", self.add_roll, color=BTN_PRIMARY, hover=BTN_PRIMARY_HOVER, width=170)
        self.add_btn.pack(side="left", padx=(10, 0))
        self.clear_btn = self.button(top, "Очистить всё", self.clear_rolls, color=BTN, hover=BTN_HOVER, width=150)
        self.clear_btn.pack(side="left", padx=(10, 0))
        self.update_btn = self.button(top, "🔄 Обновления",
                              lambda: self.check_for_updates(silent=False),
                              color=BTN, hover=BTN_HOVER, width=170)
        self.update_btn.pack(side="left", padx=(10, 0))
        self.rolls_area = ctk.CTkScrollableFrame(self, fg_color=BG, scrollbar_button_color="#404040", scrollbar_button_hover_color="#525252", width=890, height=540)
        self.rolls_area.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        bottom = ctk.CTkFrame(self, fg_color=BG)
        bottom.pack(fill="x", padx=24, pady=(0, 22))
        self.status_label = self.label(bottom, "", size=13, color=MUTED)
        self.status_label.pack(anchor="w", pady=(0, 10))
        self.start_btn = ctk.CTkButton(bottom, text="НАЧАТЬ ОЧЕРЕДЬ", command=self.start_queue, fg_color=BTN_DANGER, hover_color=BTN_DANGER_HOVER, text_color="white", corner_radius=12, height=50, font=ctk.CTkFont(size=17, weight="bold"))
        self.start_btn.pack(fill="x")

    def open_main_api_voice_dialog(self):
        win = ctk.CTkToplevel(self)
        win.title("ElevenLabs API key")
        win.geometry("720x420")
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()

        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=16)
        frame.pack(fill="both", expand=True, padx=24, pady=24)

        self.label(frame, "ElevenLabs API key", size=24, weight="bold").pack(anchor="w", padx=24, pady=(22, 6))
        self.label(
            frame,
            "Ключ нужен только для кнопки «Сгенерировать голос». Для обработки готового full_voice.mp3 ключ не используется.",
            size=13,
            color=MUTED,
        ).pack(anchor="w", padx=24, pady=(0, 18))

        self.label(frame, "API key", size=14, weight="bold").pack(anchor="w", padx=24)

        api_box = ctk.CTkTextbox(
            frame,
            height=86,
            fg_color="#ffffff",
            text_color="#111111",
            border_width=1,
            border_color="#737373",
            corner_radius=10,
            font=ctk.CTkFont(size=13),
        )
        api_box.pack(fill="x", padx=24, pady=(6, 18))

        if self.api_key:
            api_box.insert("1.0", self.api_key)

        result_label = self.label(frame, "", size=13, color=MUTED)
        result_label.pack(anchor="w", padx=24, pady=(0, 10))

        def save():
            api = api_box.get("1.0", "end").strip()

            if not api:
                messagebox.showerror("Ошибка", "Введите ElevenLabs API key.", parent=win)
                return

            self.api_key = api
            self.save_config()
            self.update_status()
            win.destroy()

        self.button(frame, "Сохранить ключ", save, color=BTN_OK, hover=BTN_OK_HOVER, width=190).pack(anchor="e", padx=24, pady=(10, 0))

    def open_sync_key_dialog(self):
        win = ctk.CTkToplevel(self)
        win.title("Sync.so API key")
        win.geometry("720x420")
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()

        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=16)
        frame.pack(fill="both", expand=True, padx=24, pady=24)

        self.label(frame, "Sync.so API key", size=24, weight="bold").pack(anchor="w", padx=24, pady=(22, 6))
        self.label(
            frame,
            "Ключ нужен для lipsync. Берётся в личном кабинете sync.so.",
            size=13, color=MUTED,
        ).pack(anchor="w", padx=24, pady=(0, 18))

        self.label(frame, "API key", size=14, weight="bold").pack(anchor="w", padx=24)

        sync_box = ctk.CTkTextbox(
            frame, height=86,
            fg_color="#ffffff", text_color="#111111",
            border_width=1, border_color="#737373",
            corner_radius=10, font=ctk.CTkFont(size=13),
        )
        sync_box.pack(fill="x", padx=24, pady=(6, 18))

        if self.sync_key and "ВСТАВЬ" not in self.sync_key:
            sync_box.insert("1.0", self.sync_key)

        def save():
            key = sync_box.get("1.0", "end").strip()
            if not key:
                messagebox.showerror("Ошибка", "Введите Sync API key.", parent=win)
                return
            self.sync_key = key
            global SYNC_API_KEY
            SYNC_API_KEY = key
            self.save_config()
            self.update_status()
            win.destroy()

        self.button(frame, "Сохранить ключ", save,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=190).pack(anchor="e", padx=24, pady=(10, 0))

    def fetch_voice_id_by_name(self, name):
        if not self.api_key:
            raise RuntimeError("Сначала укажи ElevenLabs API key.")
        url = f"{ELEVEN_BASE_URL}/v1/voices"
        headers = {"xi-api-key": self.api_key}
        res = requests.get(url, headers=headers, timeout=60)
        if res.status_code != 200:
            raise RuntimeError(f"{res.status_code}: {res.text[:300]}")
        for v in res.json().get("voices", []):
            if v.get("name", "").strip().lower() == name.lower():
                return v.get("voice_id")
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
            "remove_background_noise": "false",
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
    
    def open_voice_clone_dialog(self):
        """Окошко: имя + описание + файл → создаёт клон в ElevenLabs."""
        if not self.api_key:
            messagebox.showerror("Ошибка", "Сначала добавь ElevenLabs API key.")
            return

        win = ctk.CTkToplevel(self)
        win.title("Клонировать голос")
        win.geometry("620x540")
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()

        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=16)
        frame.pack(fill="both", expand=True, padx=24, pady=24)

        self.label(frame, "🎙 Клонировать голос", size=22, weight="bold").pack(anchor="w", padx=22, pady=(20, 6))
        self.label(
            frame,
            "Загрузи 1-3 минуты чистой речи без шума и музыки. "
            "Поддерживаются mp3, wav, m4a, flac.",
            size=13, color=MUTED
        ).pack(anchor="w", padx=22, pady=(0, 16))

        self.label(frame, "Имя голоса", size=14, weight="bold").pack(anchor="w", padx=22)
        name_entry = ctk.CTkEntry(
            frame, placeholder_text="например: Иван — диктор",
            fg_color="white", text_color="#111", border_color="#737373",
            font=ctk.CTkFont(size=13)
        )
        name_entry.pack(fill="x", padx=22, pady=(6, 14))

        self.label(frame, "Описание (опционально)", size=14, weight="bold").pack(anchor="w", padx=22)
        desc_entry = ctk.CTkEntry(
            frame, placeholder_text="например: тёплый низкий мужской голос",
            fg_color="white", text_color="#111", border_color="#737373",
            font=ctk.CTkFont(size=13)
        )
        desc_entry.pack(fill="x", padx=22, pady=(6, 14))

        self.label(frame, "Аудио-сэмпл", size=14, weight="bold").pack(anchor="w", padx=22)
        file_row = ctk.CTkFrame(frame, fg_color=PANEL)
        file_row.pack(fill="x", padx=22, pady=(6, 14))

        file_label = ctk.CTkLabel(
            file_row, text="  не выбран",
            fg_color="#f5f5f5", text_color="#888",
            anchor="w", corner_radius=6,
            font=ctk.CTkFont(size=12), height=32
        )
        file_label.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)

        selected = {"path": ""}

        def choose_file():
            f = filedialog.askopenfilename(
                parent=win, title="Выбери аудио-сэмпл",
                filetypes=[("Аудио", "*.mp3 *.wav *.m4a *.flac *.ogg"), ("Все файлы", "*.*")]
            )
            if f:
                selected["path"] = f
                try:
                    size_mb = os.path.getsize(f) / (1024 * 1024)
                    file_label.configure(text=f"  {os.path.basename(f)}  ({size_mb:.1f} МБ)",
                                         text_color="#111")
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
                messagebox.showerror("Ошибка", "Введи имя голоса.", parent=win)
                return
            if not path:
                messagebox.showerror("Ошибка", "Выбери аудио-сэмпл.", parent=win)
                return

            try:
                result_label.configure(text="Загружаю в ElevenLabs...", text_color=MUTED)
                win.update()
                voice_id = self.create_voice_from_sample(path, name, description)
                result_label.configure(text=f"✓ Готово! voice_id: {voice_id}",
                                       text_color="#86efac")
                self.main_voice_name = name
                self.main_voice_id = voice_id
                messagebox.showinfo(
                    "Готово",
                    f"Голос «{name}» клонирован.\n\nvoice_id: {voice_id}\n\n"
                    f"Голос уже в твоём аккаунте — нажми «🔄 Обновить» в окне «Мои голоса», "
                    f"чтобы он там появился.",
                    parent=win
                )
            except Exception as e:
                result_label.configure(text=f"Ошибка: {e}", text_color="#fca5a5")
                messagebox.showerror("Ошибка", str(e), parent=win)

        self.button(frame, "🎙 Клонировать", clone,
                    color=BTN_OK, hover=BTN_OK_HOVER, width=200).pack(anchor="e", padx=22, pady=(0, 20))
    
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
        

        import tempfile
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
        win.grab_set()

        def on_close():
            self.stop_preview()
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", on_close)

        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=16)
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
                    row = ctk.CTkFrame(list_frame, fg_color=CARD, corner_radius=8)
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
   

    def open_voice_generator_dialog(self):
        win = ctk.CTkToplevel(self)
        win.title("Сгенерировать full_voice.mp3")
        win.geometry("840x720")
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()
        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=16)
        frame.pack(fill="both", expand=True, padx=24, pady=24)
        self.label(frame, "Сгенерировать full_voice.mp3", size=24, weight="bold").pack(anchor="w", padx=22, pady=(20, 6))
        self.label(frame, "Пиши текст с ------. Приложение заменит ------ на паузу, ElevenLabs не будет читать тире.", size=13, color=MUTED).pack(anchor="w", padx=22, pady=(0, 16))
        self.label(frame, "voice_id или имя голоса", size=14, weight="bold").pack(anchor="w", padx=22)
        voice_box = ctk.CTkTextbox(frame, height=70, fg_color="#ffffff", text_color="#111111", border_width=1, border_color="#737373", corner_radius=10, font=ctk.CTkFont(size=14))
        voice_box.pack(fill="x", padx=22, pady=(6, 8))
        if self.main_voice_name:
            voice_box.insert("1.0", self.main_voice_name)

        def on_voice_picked(name, voice_id):
            voice_box.delete("1.0", "end")
            voice_box.insert("1.0", name)

        self.button(frame, "📋 Выбрать из списка моих голосов",
                    lambda: self.open_voice_picker(win, on_voice_picked),
                    color=BTN, hover=BTN_HOVER, width=320).pack(anchor="w", padx=22, pady=(0, 16))
        self.label(frame, "Полный текст", size=14, weight="bold").pack(anchor="w", padx=22)
        text_box = ctk.CTkTextbox(frame, fg_color="#ffffff", text_color="#111111", border_width=1, border_color="#737373", corner_radius=10, font=ctk.CTkFont(size=14), wrap="word")
        text_box.pack(fill="both", expand=True, padx=22, pady=(6, 16))
        result_label = self.label(frame, "", size=13, color=MUTED)
        result_label.pack(anchor="w", padx=22, pady=(0, 12))
        def generate_audio():
            if not self.api_key:
                messagebox.showerror("Ошибка", "Сначала добавь ElevenLabs API key.", parent=win)
                return
            voice_value = voice_box.get("1.0", "end").strip()
            full_text = text_box.get("1.0", "end").strip()
            if not voice_value:
                messagebox.showerror("Ошибка", "Введите voice_id или имя голоса.", parent=win)
                return
            if not full_text:
                messagebox.showerror("Ошибка", "Введите текст.", parent=win)
                return
            try:
                result_label.configure(text="Ищу голос...", text_color=MUTED)
                win.update()
                if re.fullmatch(r"[A-Za-z0-9]{20}", voice_value):
                    voice_id = voice_value
                    voice_name = voice_value
                else:
                    voice_id = self.fetch_voice_id_by_name(voice_value)
                    voice_name = voice_value
                if not voice_id:
                    result_label.configure(text="Голос с таким именем не найден.", text_color="#fca5a5")
                    return
                result_label.configure(text="Генерирую full_voice.mp3...", text_color=MUTED)
                win.update()
                path = generate_full_voice_to_desktop(self.api_key, voice_id, full_text, voice_name, self.log)
                result_label.configure(text=f"Готово: {path}", text_color="#86efac")
                messagebox.showinfo("Готово", f"Голос сохранён:\n{path}", parent=win)
            except Exception as e:
                result_label.configure(text=f"Ошибка: {e}", text_color="#fca5a5")
                messagebox.showerror("Ошибка", str(e), parent=win)
        self.button(frame, "Сгенерировать mp3", generate_audio, color=BTN_OK, hover=BTN_OK_HOVER, width=220).pack(anchor="e", padx=22, pady=(0, 20))

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
        self.render_rolls()
        self.update_status()

    def clear_rolls(self):
        if self.is_processing:
            messagebox.showwarning("Очередь", "Нельзя очищать во время обработки.")
            return
        self.rolls = []
        self.add_roll()

    def render_rolls(self):
        for w in self.rolls_area.winfo_children():
            w.destroy()
        for idx, roll in enumerate(self.rolls):
            self.render_roll_card(idx, roll)
        self._enable_mousewheel(self.rolls_area)

    def render_roll_card(self, idx, roll):
        card = ctk.CTkFrame(self.rolls_area, fg_color=CARD, corner_radius=16)
        card.pack(fill="x", padx=4, pady=(0, 14))
        top = ctk.CTkFrame(card, fg_color=CARD)
        top.pack(fill="x", padx=16, pady=(14, 8))
        if roll["mode"] == "1 видео":
            title_video = roll["video_single"]
        else:
              title_video = roll["video_start"] or roll["video_end"]

        if title_video:
                scene_title = os.path.splitext(os.path.basename(title_video))[0]
        else:
                scene_title = f"Сцена {idx + 1}"

        self.label(top, scene_title, size=18, weight="bold").pack(side="left")

        status_color = MUTED
        if roll["status"] == "Готово":
            status_color = "#86efac"
        elif roll["status"] == "Ошибка":
            status_color = "#fca5a5"
        self.label(top, roll["status"], size=13, color=status_color).pack(side="right")
        mode_row = ctk.CTkFrame(card, fg_color=CARD)
        mode_row.pack(fill="x", padx=16, pady=(0, 10))
        self.label(mode_row, "Режим:", size=13, color=MUTED).pack(side="left", padx=(0, 10))
        mode_menu = ctk.CTkOptionMenu(mode_row, values=["1 видео", "Начало + Конец"], command=lambda value, i=idx: self.set_roll_mode(i, value), fg_color=BTN, button_color=BTN, button_hover_color=BTN_HOVER, dropdown_fg_color=PANEL, dropdown_hover_color=BTN_HOVER, width=170)
        mode_menu.set(roll["mode"])
        mode_menu.pack(side="left")
        self.label(card, self.roll_details(roll), size=12, color=MUTED).pack(anchor="w", padx=16, pady=(0, 10))
        row1 = ctk.CTkFrame(card, fg_color=CARD)
        row1.pack(fill="x", padx=16, pady=(0, 10))
        voice_text = "✅ full_voice.mp3" if roll["voice_file"] else "Выбрать full_voice"
        self.button(row1, voice_text, lambda i=idx: self.choose_voice_file(i), color=BTN_OK if roll["voice_file"] else BTN_PRIMARY, hover=BTN_OK_HOVER if roll["voice_file"] else BTN_PRIMARY_HOVER, width=190).pack(side="left")
        text_btn = "✅ Текст проверки" if roll["text"] else "Текст проверки"
        self.button(row1, text_btn, lambda i=idx: self.open_roll_text_dialog(i), color=BTN_OK if roll["text"] else BTN_PRIMARY, hover=BTN_OK_HOVER if roll["text"] else BTN_PRIMARY_HOVER, width=180).pack(side="left", padx=(10, 0))
        delete_btn = self.button(
            row1,
            "Удалить",
            lambda roll_id=roll["id"]: self.remove_roll_by_id(roll_id),
            color=BTN_DANGER,
            hover=BTN_DANGER_HOVER,
            width=110
        )
        delete_btn.pack(side="right")

        hover_rows = [card, top, mode_row, row1]

        def on_delete_enter(event):
            for w in hover_rows:
                try:
                    w.configure(fg_color=CARD_HOVER_DELETE)
                except Exception:
                    pass

        def on_delete_leave(event):
            for w in hover_rows:
                try:
                    w.configure(fg_color=CARD)
                except Exception:
                    pass

        delete_btn.bind("<Enter>", on_delete_enter)
        delete_btn.bind("<Leave>", on_delete_leave)
        row2 = ctk.CTkFrame(card, fg_color=CARD)
        row2.pack(fill="x", padx=16, pady=(0, 16))
        hover_rows.append(row2)
        if roll["mode"] == "1 видео":
            video_text = "✅ Видео" if roll["video_single"] else "Выбрать видео"
            time_row = ctk.CTkFrame(card, fg_color=CARD)
            time_row.pack(fill="x", padx=16, pady=(0, 12))
            hover_rows.append(time_row)

            self.label(time_row, "Обрезать аудио (сек):", size=12, color=MUTED).pack(side="left", padx=(0, 8))

            start_entry = ctk.CTkEntry(time_row, width=90, placeholder_text="от", fg_color="white", text_color="#111", border_color="#737373")
            start_entry.pack(side="left", padx=(0, 6))
            if roll["audio_start"]:
                start_entry.insert(0, roll["audio_start"])

            end_entry = ctk.CTkEntry(time_row, width=90, placeholder_text="до", fg_color="white", text_color="#111", border_color="#737373")
            end_entry.pack(side="left")
            if roll["audio_end"]:
                end_entry.insert(0, roll["audio_end"])

            def save_times(*_):
                roll["audio_start"] = start_entry.get().strip()
                roll["audio_end"] = end_entry.get().strip()

            start_entry.bind("<FocusOut>", save_times)
            end_entry.bind("<FocusOut>", save_times)
            start_entry.bind("<Return>", save_times)
            end_entry.bind("<Return>", save_times)

            self.button(row2, video_text, lambda i=idx: self.choose_video(i, "single"), color=BTN_OK if roll["video_single"] else BTN_PRIMARY, hover=BTN_OK_HOVER if roll["video_single"] else BTN_PRIMARY_HOVER, width=170).pack(side="left")
        else:
            start_text = "✅ Видео начало" if roll["video_start"] else "Видео начало"
            end_text = "✅ Видео конец" if roll["video_end"] else "Видео конец"
            self.button(row2, start_text, lambda i=idx: self.choose_video(i, "start"), color=BTN_OK if roll["video_start"] else BTN_PRIMARY, hover=BTN_OK_HOVER if roll["video_start"] else BTN_PRIMARY_HOVER, width=170).pack(side="left")
            self.button(row2, end_text, lambda i=idx: self.choose_video(i, "end"), color=BTN_OK if roll["video_end"] else BTN_PRIMARY, hover=BTN_OK_HOVER if roll["video_end"] else BTN_PRIMARY_HOVER, width=170).pack(side="left", padx=(10, 0))

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
        self.render_rolls()
        self.update_status()

    def set_roll_mode(self, idx, value):
        self.rolls[idx]["mode"] = value
        self.rolls[idx]["status"] = "Ожидает"
        self.refresh_rolls()



    def remove_roll_by_id(self, roll_id):
        if self.is_processing:
            messagebox.showwarning("Очередь", "Нельзя удалять во время обработки.")
            return

        # запоминаем позицию скролла
        scroll_pos = self.rolls_area._parent_canvas.yview()

        self.rolls = [roll for roll in self.rolls if roll["id"] != roll_id]

        if not self.rolls:
            self.add_roll()
        else:
            self.refresh_rolls()

            # возвращаем скролл туда же
            try:
                self.rolls_area._parent_canvas.yview_moveto(scroll_pos[0])
            except Exception:
                pass



    def choose_voice_file(self, idx):
        f = filedialog.askopenfilename(parent=self, title="Выбери full_voice.mp3 / wav", filetypes=[("Аудио", "*.mp3 *.wav *.m4a *.aac"), ("Все файлы", "*.*")])
        if f:
            self.rolls[idx]["voice_file"] = f
            self.rolls[idx]["status"] = "Ожидает"
            self.refresh_rolls()

    def choose_video(self, idx, kind):
        f = filedialog.askopenfilename(parent=self, title="Выбери видео", filetypes=[("Видео", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm"), ("Все файлы", "*.*")])
        if f:
            if kind == "single":
                self.rolls[idx]["video_single"] = f
            elif kind == "start":
                self.rolls[idx]["video_start"] = f
            elif kind == "end":
                self.rolls[idx]["video_end"] = f
            self.rolls[idx]["status"] = "Ожидает"
            self.refresh_rolls()

    def open_roll_text_dialog(self, idx):
        roll = self.rolls[idx]
        win = ctk.CTkToplevel(self)
        win.title(f"Текст проверки — ролик {idx + 1}")
        win.geometry("840x620")
        win.configure(fg_color=BG)
        win.transient(self)
        win.grab_set()
        frame = ctk.CTkFrame(win, fg_color=PANEL, corner_radius=16)
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
            self.refresh_rolls()
        self.button(frame, "Сохранить", save, color=BTN_OK, hover=BTN_OK_HOVER, width=180).pack(anchor="e", padx=22, pady=(0, 20))

    def validate_rolls(self):
        valid, errors = [], []
        for i, roll in enumerate(self.rolls):
            has_any = any([roll["voice_file"], roll["text"], roll["video_single"], roll["video_start"], roll["video_end"]])
            if not has_any:
                continue
            if not roll["voice_file"]:
                errors.append(f"Ролик {i + 1}: не выбран full_voice.mp3.")
            if not roll["text"]:
                errors.append(f"Ролик {i + 1}: нет текста проверки.")
            if roll["mode"] == "1 видео":

                # если есть ------, но нет обрезки аудио
                if roll["text"] and "------" in roll["text"]:
                    if not roll.get("audio_start") and not roll.get("audio_end"):
                        errors.append(
                            f"Ролик {i + 1}: в тексте есть ------. "
                            f"Для режима '1 видео' укажи обрезку аудио (от/до)."
                        )

                if not roll["video_single"]:
                    errors.append(f"Ролик {i + 1}: не выбрано видео.")
            else:
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
                complete = bool(roll["voice_file"] and roll["text"] and roll["video_single"])
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
        

    def queue_thread(self, valid_rolls):
        try:
            failed_videos = []
            base_output = os.path.join(desktop_dir(), "Lipsync_Queue_Output")
            ensure_dir(base_output)

            batch_name = time.strftime("batch_%Y-%m-%d_%H-%M-%S")
            batch_dir = os.path.join(base_output, batch_name)
            ensure_dir(batch_dir)

            self.log("=" * 70)
            self.log(f"Папка партии: {batch_dir}")
            self.log(f"Роликов в очереди: {len(valid_rolls)}")
            self.log("=" * 70)
            for order_num, (idx, roll) in enumerate(valid_rolls, start=1):
                try:
                    self.set_roll_status(idx, f"Обработка {order_num}/{len(valid_rolls)}")
                    if roll["mode"] == "1 видео":
                        roll_video_name = os.path.splitext(os.path.basename(roll["video_single"]))[0]
                    else:
                        roll_video_name = os.path.splitext(os.path.basename(roll["video_start"]))[0]
                    roll_dir = os.path.join(batch_dir, f"{order_num:02d}_{safe_name(roll_video_name)}")
                    ensure_dir(roll_dir)
                    temp_dir = os.path.join(roll_dir, ".tmp")
                    ensure_dir(temp_dir)
                    full_voice_copy = copy_file(roll["voice_file"], os.path.join(roll_dir, "full_voice.mp3"))
                    with open(os.path.join(roll_dir, "full_text.txt"), "w", encoding="utf-8") as f:
                        f.write(roll["text"])
                    self.log("")
                    self.log("=" * 70)
                    self.log(f"Ролик {idx + 1} / очередь {order_num}")
                    self.log(f"Режим: {roll['mode']}")
                    self.log(f"Папка: {roll_dir}")
                    self.log("=" * 70)
                    if roll["mode"] == "1 видео":
                        audio_wav = os.path.join(temp_dir, "full_voice.wav")
                        convert_audio_to_wav_trimmed(
                            full_voice_copy,
                            audio_wav,
                            start_sec=roll.get("audio_start", ""),
                            end_sec=roll.get("audio_end", ""),
                        )
                        if roll.get("audio_start") or roll.get("audio_end"):
                            self.log(f"Обрезка: {roll.get('audio_start') or '0'} → {roll.get('audio_end') or 'конец'} сек")

                        video_name = safe_name(os.path.splitext(os.path.basename(roll["video_single"]))[0])
                        out = os.path.join(roll_dir, f"{video_name}.mp4")
                        process_lipsync(roll["video_single"], audio_wav, out, temp_dir, self.log)
                        self.log(f"ГОТОВО: {out}")
                    else:
                        start_text, end_text, sep_count = parse_start_end_text(roll["text"])
                        part_start, part_end = split_start_end_by_silence(
                            full_voice_copy, roll_dir, self.log,
                            expected_separators=sep_count,
                        )
                        video_name_start = safe_name(os.path.splitext(os.path.basename(roll["video_start"]))[0])
                        video_name_end = safe_name(os.path.splitext(os.path.basename(roll["video_end"]))[0])

                        out_start = os.path.join(roll_dir, f"{video_name_start}_start.mp4")
                        out_end = os.path.join(roll_dir, f"{video_name_end}_end.mp4")
                        self.log("Lipsync start...")
                        process_lipsync(roll["video_start"], part_start, out_start, temp_dir, self.log)
                        self.log("Lipsync end...")
                        process_lipsync(roll["video_end"], part_end, out_end, temp_dir, self.log)
                        self.log(f"ГОТОВО: {out_start}")
                        self.log(f"ГОТОВО: {out_end}")
                    try:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                    except Exception:
                        pass
                    self.set_roll_status(idx, "Готово")
                    if order_num < len(valid_rolls):
                        self.log(f"Пауза {PAUSE_BETWEEN_ROLLS_SEC} сек перед следующим роликом...")
                        time.sleep(PAUSE_BETWEEN_ROLLS_SEC)
                except Exception as e:
                    if roll["mode"] == "1 видео":
                        video_name = os.path.basename(roll.get("video_single", "")) or f"ролик {idx + 1}"
                    else:
                        s = os.path.basename(roll.get("video_start", ""))
                        e = os.path.basename(roll.get("video_end", ""))
                        video_name = f"{s} + {e}" if (s and e) else f"ролик {idx + 1}"
                    error_text = str(e)

                    self.log(f"❌ ОШИБКА в ролике '{video_name}': {error_text}")
                    self.set_roll_status(idx, "Ошибка")

                    failed_videos.append({
                        "video_name": video_name,
                        "error": error_text
                    })

                    continue
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
                self.refresh_rolls()
        self.after(0, update)

    def update_status(self):
        valid, _ = self.validate_rolls()
        self.status_label.configure(text=f"готовых роликов: {len(valid)} | всего роликов: {len(self.rolls)}")
        if self.api_key:
            self.api_btn.configure(fg_color=BTN_OK, hover_color=BTN_OK_HOVER)
        else:
            self.api_btn.configure(fg_color=BTN, hover_color=BTN_HOVER)
        if self.sync_key and "ВСТАВЬ" not in self.sync_key:
            self.sync_btn.configure(fg_color=BTN_OK, hover_color=BTN_OK_HOVER)
        else:
            self.sync_btn.configure(fg_color=BTN, hover_color=BTN_HOVER)


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
    #Включает скролл колесом мыши на всех виджетах внутри scrollable frame."""
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
