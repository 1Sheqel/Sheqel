# SheqelMotion — Инструкция для Windows

## Требования

- Windows 10 / 11 (64-bit)
- Python 3.10 или новее
- ffmpeg (обязательно!)

---

## Шаг 1 — Установить Python

1. Открой [python.org/downloads](https://www.python.org/downloads/)
2. Скачай Python 3.11 или 3.12 для Windows
3. При установке **обязательно поставь галочку "Add Python to PATH"**
4. Проверь в командной строке:
   ```
   python --version
   ```

---

## Шаг 2 — Установить ffmpeg

**Вариант A — через winget (рекомендуется):**
```
winget install Gyan.FFmpeg
```

**Вариант B — через Chocolatey:**
```
choco install ffmpeg
```

**Вариант C — вручную:**
1. Скачай с [ffmpeg.org/download.html](https://ffmpeg.org/download.html) → Windows builds by Gyan
2. Распакуй архив, например в `C:\ffmpeg`
3. Добавь `C:\ffmpeg\bin` в системную переменную `PATH`:
   - Пуск → "переменные среды" → "Изменить системные переменные среды"
   - "Переменные среды" → Path → Изменить → Создать → вставь путь

Проверь что ffmpeg работает:
```
ffmpeg -version
```

---

## Шаг 3 — Запустить из исходников

```cmd
# Клонируй или распакуй проект
cd SheqelM

# Установи зависимости
pip install -r requirements.txt

# Запусти
python app.py
```

---

## Шаг 4 — Собрать .exe через PyInstaller

```cmd
# Установи PyInstaller
pip install pyinstaller

# Запусти скрипт сборки
build_windows.bat
```

Готовый файл будет в `dist\SheqelMotion.exe`.

> **Примечание:** .exe НЕ включает ffmpeg внутри себя.  
> ffmpeg должен быть установлен в системе (шаг 2).

---

## Часто встречаемые проблемы

### "python не является внутренней или внешней командой"
→ Python не добавлен в PATH. Переустанови Python с галочкой "Add to PATH".

### "ffmpeg не найден" / приложение зависает на обработке
→ Установи ffmpeg (шаг 2) и убедись что `ffmpeg -version` работает в cmd.

### Окно мигает или сразу закрывается
→ Запусти через cmd чтобы увидеть ошибку:
```
python app.py
```

### Ошибка при установке customtkinter
```
pip install customtkinter==5.2.2 --force-reinstall
```

### Антивирус блокирует .exe
→ Это ложное срабатывание на PyInstaller-пакованные файлы. Добавь в исключения.

---

## Отличия Windows-версии от macOS

| Функция | macOS | Windows |
|---------|-------|---------|
| ffmpeg | встроен в .app | устанавливается отдельно |
| Аудио превью | afplay / ffplay | ffplay (требует ffmpeg) |
| Открытие папки | open | os.startfile |
| Автообновление | скачивает app.py | показывает ссылку для ручного скачивания |
