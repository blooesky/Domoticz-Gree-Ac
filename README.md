
# Gree AC Local Plugin for Domoticz

A local LAN plugin for controlling Gree air conditioners directly from Domoticz.

No Cloud, No MQTT.

The plugin communicates directly with the indoor unit using the local Gree protocol.

## Install

```bash
cd /home/pi/domoticz/plugins/GreeAC
chmod +x install.sh update.sh uninstall.sh
./install.sh
sudo systemctl restart domoticz
