LIPSYNC STUDIO — приложение
============================

ПОТОК
-----
1. "Добавить API"   → ключ ElevenLabs → имя голоса (или voice_id)
2. "Добавить видео" → одно видео
3. "Текст"          → сплошной текст для озвучки, Ctrl+Enter сохраняет
4. "НАЧАТЬ"         → запускает пайплайн.

После нажатия "НАЧАТЬ":
  - открывается окно с логом
  - поля "Видео" и "Текст" сразу обнуляются — можно сразу готовить
    следующий ролик, пока текущий считается
  - API и голос остаются — задавать каждый раз не нужно

Результат:
  - на Рабочем столе создаётся папка с именем видео
  - внутри final_video.mp4 (готовый lipsync) и voice.mp3 (озвучка)


ПЕРЕД ПЕРВЫМ ЗАПУСКОМ
---------------------
1. Открой app.py и впиши SYNC_API_KEY вместо "ВСТАВЬ_СЮДА_SYNC_KEY"
2. Установи зависимости:
   pip install requests
3. Установи ffmpeg:
   - Windows: скачай ffmpeg.exe и ffprobe.exe с https://www.gyan.dev/ffmpeg/builds/
              положи их рядом с app.py
   - Mac:     brew install ffmpeg
   - Linux:   sudo apt install ffmpeg


ЗАПУСК БЕЗ СБОРКИ
-----------------
python app.py


СБОРКА В .EXE (Windows)
-----------------------
pip install pyinstaller
pyinstaller --onefile --windowed --name LipsyncStudio app.py

После сборки готовый файл: dist/LipsyncStudio.exe

ВАЖНО при передаче приложения:
  Структура папки:
    LipsyncStudio/
      LipsyncStudio.exe
      ffmpeg.exe
      ffprobe.exe


СБОРКА НА MAC
-------------
pyinstaller --onefile --windowed --name LipsyncStudio app.py
Положи рядом бинарь ffmpeg.


КАЧЕСТВО
--------
Финальное видео перекодируется в x264:
  - CRF 13, preset slow
  - sharpening (unsharp 3:3:0.6)
  - аудио AAC 256k
  - format yuv420p, faststart


ОШИБКИ
------
- "Не найден ffmpeg/ffprobe"
  → положи бинари рядом с app.py / .exe или добавь в PATH

- "Sync failed" / 401
  → проверь SYNC_API_KEY в исходнике

- ElevenLabs ругается на голос
  → вставь voice_id напрямую в окне "Имя голоса" (20 символов)
