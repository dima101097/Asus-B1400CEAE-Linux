#!/usr/bin/env bash
# laptop-fixes: встановлення / видалення.
#   ./install.sh              встановити програму і (за підтвердженням) увімкнути фікси
#   ./install.sh --uninstall  вимкнути всі фікси, відкотити зміни й видалити програму
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
SHARE="$HOME/.local/share/laptop-fixes"
BIN="$HOME/.local/bin/laptop-fixes"
DESKTOP="$HOME/.local/share/applications/laptop-fixes.desktop"
ICONS="$HOME/.local/share/icons/hicolor/scalable/status"

ask() { read -r -p "$1 [y/N] " a; [[ "$a" =~ ^([yYтТ]|yes|так)$ ]]; }

if [[ "${1:-}" == "--uninstall" ]]; then
    if [[ -x "$BIN" ]]; then
        "$BIN" disable all
    fi
    if [[ -d /usr/local/lib/laptop-fixes ]]; then
        echo "Root-частини фіксів не вимкнено (sudo скасовано?). Програму НЕ видалено,"
        echo "щоб не лишити служби без файлів. Запустіть ./install.sh --uninstall ще раз."
        exit 1
    fi
    rm -rf "$SHARE" "$BIN" "$DESKTOP" "$HOME/.config/laptop-fixes"
    rm -f "$ICONS"/laptop-fixes-*.svg
    echo "laptop-fixes видалено."
    exit 0
fi

vendor=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || true)
product=$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)
echo "Ноутбук: $vendor $product"
if [[ "$product" != *B1400CEAE* ]]; then
    echo "Увага: фікси писалися для ASUS ExpertBook B1400CEAE і на іншому залізі можуть не працювати."
    ask "Все одно встановити?" || exit 1
fi

install -D -m 755 "$SRC/laptop_fixes.py" "$SHARE/laptop_fixes.py"
mkdir -p "$ICONS"
install -m 644 "$SRC"/icons/*.svg "$ICONS/"
mkdir -p "$(dirname "$BIN")" "$(dirname "$DESKTOP")"
ln -sf "$SHARE/laptop_fixes.py" "$BIN"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Laptop fixes
Name[uk]=Фікси клавіш ноутбука
Comment=Fn-lock, мікрофон, камера та інші спеціальні клавіші ASUS
Exec=$BIN gui
Icon=input-keyboard
Categories=Settings;HardwareSettings;
EOF
echo "Програму встановлено: $BIN (у меню: «Фікси клавіш ноутбука»)."
echo

# Оновлення: root-копія, з якої працюють системні служби
if [[ -f /usr/local/lib/laptop-fixes/laptop_fixes.py ]] &&
   ! cmp -s "$SHARE/laptop_fixes.py" /usr/local/lib/laptop-fixes/laptop_fixes.py; then
    echo "Системні служби використовують стару версію (/usr/local/lib/laptop-fixes)."
    if ask "Оновити її і перезапустити служби laptop-fixes (sudo)?"; then
        sudo install -m 755 "$SHARE/laptop_fixes.py" /usr/local/lib/laptop-fixes/laptop_fixes.py
        for u in laptop-fixes-fnlock laptop-fixes-micled laptop-fixes-battery; do
            if systemctl is-enabled --quiet "$u.service" 2>/dev/null; then
                sudo systemctl restart "$u.service"
            fi
        done
        echo "Оновлено."
    fi
    echo
fi

"$BIN" list
echo
if ask "Увімкнути всі фікси зараз? (для root-частин буде окремий запит)"; then
    "$BIN" enable all
else
    echo "Окремі фікси можна вмикати пізніше: laptop-fixes enable НАЗВА  або  laptop-fixes gui"
fi
