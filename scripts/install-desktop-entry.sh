#!/bin/sh
# Adds Ubunchan to your application menu (~/.local/share/applications).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/ubunchan.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Ubunchan
Comment=Voice assistant that takes care of your Ubuntu system
Exec=$ROOT/scripts/ubunchan
Icon=$ROOT/waifu/gui/web/icon.png
Terminal=false
Categories=Utility;System;
StartupWMClass=ubunchan
DESKTOP
echo "Installed: $HOME/.local/share/applications/ubunchan.desktop"
