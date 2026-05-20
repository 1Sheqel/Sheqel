@echo off
REM Собирает SheqelMotion.exe для Windows
REM Запускай в Command Prompt или PowerShell

echo === SheqelMotion Windows Build ===

REM Проверяем Python
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo ОШИБКА: Python не найден. Установи Python 3.10+ с python.org
    pause
    exit /b 1
)

REM Проверяем ffmpeg
where ffmpeg >nul 2>&1
IF ERRORLEVEL 1 (
    echo ПРЕДУПРЕЖДЕНИЕ: ffmpeg не найден в PATH.
    echo Программа будет работать только если пользователь установит ffmpeg.
    echo Установи: winget install Gyan.FFmpeg
)

REM Устанавливаем зависимости
python -m pip install --quiet -r requirements.txt
python -m pip install --quiet pyinstaller

REM Удаляем старую сборку
IF EXIST build rmdir /s /q build
IF EXIST dist rmdir /s /q dist

REM Собираем onefile .exe
pyinstaller SheqelMotion_windows.spec --noconfirm

echo.
echo === Готово ===
echo EXE файл: dist\SheqelMotion.exe
echo.
echo ВАЖНО: пользователь должен установить ffmpeg отдельно!
echo        winget install Gyan.FFmpeg
pause
