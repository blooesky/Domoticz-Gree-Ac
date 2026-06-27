cat > uninstall.sh <<'EOF'
#!/bin/bash
set -e

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"

rm -rf "$PLUGIN_DIR/venv"

echo "venv sters."
echo "Plugin and Domoticz devices were not deleted."
echo "For complete deletion:"
echo "cd /home/pi/domoticz/plugins && rm -rf GreeAC"
EOF

chmod +x uninstall.sh
