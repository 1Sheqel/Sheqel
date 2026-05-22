#!/bin/bash
# ============================================================
#  SheqelMotion — встановлення в /Applications
#  Запускати подвійним кліком або з терміналу
# ============================================================

set -euo pipefail

APP_NAME="SheqelMotion.app"
# Директорія, де лежить цей скрипт (незалежно від того, звідки запущено)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/$APP_NAME"
DEST="/Applications/$APP_NAME"

echo ""
echo "  ╔═══════════════════════════════╗"
echo "  ║   Встановлення SheqelMotion   ║"
echo "  ╚═══════════════════════════════╝"
echo ""

# --- Перевірка наявності .app поруч зі скриптом ---
if [ ! -d "$SRC" ]; then
    echo "❌  Не знайдено $APP_NAME поруч зі скриптом."
    echo "    Переконайтеся, що install.command та SheqelMotion.app"
    echo "    знаходяться в одній папці."
    echo ""
    read -r -p "Натисніть Enter для виходу..." _
    exit 1
fi

# --- Видалити стару версію якщо є ---
if [ -d "$DEST" ]; then
    echo "🔄  Видаляю попередню версію з /Applications..."
    rm -rf "$DEST"
fi

# --- Копіювання ---
echo "📦  Копіюю SheqelMotion.app → /Applications..."
cp -R "$SRC" "$DEST"

# --- Зняти карантин (щоб Gatekeeper не блокував) ---
echo "🔓  Знімаю карантин..."
xattr -rd com.apple.quarantine "$DEST" 2>/dev/null || true

# --- Зробити launcher виконуваним ---
chmod +x "$DEST/Contents/MacOS/SheqelMotion"

# --- Перепідписати ad-hoc після копіювання ---
echo "✍️   Підписую ad-hoc..."
codesign --force --deep --sign - "$DEST" 2>/dev/null || true

# --- Очистити кеш іконок Finder ---
echo "🎨  Оновлюю кеш іконок Finder..."
touch "$DEST"
/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister \
    -f "$DEST" 2>/dev/null || true

echo ""
echo "✅  Встановлено успішно!"
echo ""
echo "    Запуск: відкрийте /Applications → SheqelMotion"
echo "    або двічі клікніть на іконку."
echo ""

# --- Запропонувати одразу відкрити ---
osascript -e 'display dialog "SheqelMotion встановлено в /Applications." buttons {"Закрити", "Відкрити зараз"} default button "Відкрити зараз" with title "Встановлення завершено"' 2>/dev/null | grep -q "Відкрити зараз" && open "$DEST" || true

read -r -p "Натисніть Enter для закриття..." _
