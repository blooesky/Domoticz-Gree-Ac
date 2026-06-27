#!/bin/bash
set -e

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PLUGIN_DIR/venv"

echo "=== Gree AC Local - Installer ==="

cd "$PLUGIN_DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 missing."
    exit 1
fi

if ! dpkg -s python3-venv >/dev/null 2>&1; then
    sudo apt update
    sudo apt install -y python3-venv python3-pip
fi

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python - <<'PY'
import greeclimate
import netifaces
from Crypto.Cipher import AES
print("Check OK: greeclimate, netifaces, pycryptodome")
PY

deactivate

chmod +x install.sh update.sh uninstall.sh 2>/dev/null || true

echo ""
echo "Install completed."
echo "Run: sudo systemctl restart domoticz"
