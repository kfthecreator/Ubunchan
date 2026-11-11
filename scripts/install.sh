#!/bin/sh
# One-time setup for Ubunchan on Ubuntu:  ./scripts/install.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Checking system packages"
MISSING=""
python3 -c "import venv, ensurepip" 2>/dev/null || MISSING="$MISSING python3-venv"
command -v parecord >/dev/null 2>&1 || MISSING="$MISSING pulseaudio-utils"
ldconfig -p | grep -q libxcb-cursor.so.0 || MISSING="$MISSING libxcb-cursor0"
if [ -n "$MISSING" ]; then
    echo "    Installing:$MISSING (needs your password)"
    sudo apt-get install -y $MISSING
fi

echo "==> Creating Python environment in .venv"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

if [ ! -f models/piper/en_US-hfc_female-medium.onnx ]; then
    echo "==> Downloading the voice"
    .venv/bin/python -m piper.download_voices --download-dir models/piper en_US-hfc_female-medium
fi

echo "==> Adding Ubunchan to your app menu"
./scripts/install-desktop-entry.sh

echo
if command -v ollama >/dev/null 2>&1 || [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "Done! Start Ubunchan from your app menu or with: ./scripts/ubunchan"
else
    echo "Done! One more thing: Ubunchan needs a brain. Either"
    echo "  - install Ollama (free, runs locally):  curl -fsSL https://ollama.com/install.sh | sh"
    echo "    then:  ollama pull llama3.2:3b"
    echo "  - or set ANTHROPIC_API_KEY to use Claude."
    echo "Then start her with: ./scripts/ubunchan"
fi
