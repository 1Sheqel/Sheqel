#!/bin/bash
# Собирает SheqelMotion.app для macOS
set -e

echo "=== SheqelMotion Mac Build ==="

# Проверяем зависимости
python3 -m pip install --quiet -r requirements.txt
python3 -m pip install --quiet pyinstaller

# Удаляем старую сборку
rm -rf build dist

# Собираем
pyinstaller SheqelMotion.spec --noconfirm

echo ""
echo "=== Готово ==="
echo "Приложение: dist/SheqelMotion.app"

# Создаём DMG
if command -v hdiutil &>/dev/null; then
    echo "Создаю DMG..."
    hdiutil create -volname "SheqelMotion" \
        -srcfolder dist/SheqelMotion.app \
        -ov -format UDZO \
        dist/SheqelMotion.dmg
    echo "DMG: dist/SheqelMotion.dmg"
fi
