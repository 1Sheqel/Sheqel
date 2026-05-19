#!/bin/bash
cd "$(dirname "$0")"

# Найти доступный Python 3 (3.10+)
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.10+ не найден. Установи Python с https://www.python.org/"
    read -p "Нажми Enter для выхода..."
    exit 1
fi

echo "Использую: $($PYTHON --version)"

# Обновляем зависимости
"$PYTHON" -m pip install -r requirements.txt --quiet

# Запускаем приложение
"$PYTHON" app.py
