cat > update.sh <<'EOF'
#!/bin/bash
set -e

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PLUGIN_DIR/venv"

cd "$PLUGIN_DIR"

if [ ! -d "$VENV_DIR" ]; then
    echo "venv missing. Runing install.sh..."
    ./install.sh
    exit 0
fi

source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
deactivate

echo "Update completed."
echo "Run: sudo systemctl restart domoticz"
EOF

chmod +x update.sh
