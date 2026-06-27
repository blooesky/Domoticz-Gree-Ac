
# Gree AC Local Plugin for Domoticz

A local LAN plugin for controlling Gree air conditioners directly from Domoticz.

No Cloud, No MQTT.

The plugin communicates directly with the indoor unit using the local Gree protocol.

## Features

- Local LAN communication
- No cloud dependency
- One hardware instance per air conditioner
- Power ON/OFF
- Operation Mode
- Target Temperature
- Room Temperature
- Fan Speed
- Vertical Swing
- Turbo Mode
- Display Light
- Raw Status (Debug)
- Automatic reconnect
- Virtual Environment installer

 ## Requirements

- Domoticz 2024+
- Python 3.11+
- Raspberry Pi / Linux

## Install

```bash
cd /home/pi/domoticz/plugins/GreeAC
chmod +x install.sh update.sh uninstall.sh
./install.sh
sudo systemctl restart domoticz
