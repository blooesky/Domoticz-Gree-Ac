# Gree AC Local Plugin for Domoticz

A local LAN plugin for controlling Gree air conditioners directly from Domoticz.

**No cloud. No MQTT.**

The plugin communicates directly with the indoor unit using the local Gree protocol.

---

## Features

* Local LAN communication
* No cloud dependency
* One hardware instance per air conditioner
* Power ON/OFF
* Operation Modes
* Target Temperature
* Room Temperature
* Fan Speed
* Vertical Swing
* Turbo Mode
* Display Light Control
* Raw Status (Debug)
* Automatic Reconnect
* Virtual Environment Installer

---

## Requirements

* Domoticz 2024 or newer
* Python 3.11 or newer
* Raspberry Pi or any Linux system

---

## Automatic Installation (GitHub)

```bash
cd /home/pi/domoticz/plugins
git clone https://github.com/blooesky/Domoticz-Gree-Ac.git GreeAC

cd GreeAC

chmod +x install.sh update.sh uninstall.sh

./install.sh

sudo systemctl restart domoticz
```

---

## Manual Installation (Copy Files)

Copy the **GreeAC** folder into:

```text
/home/pi/domoticz/plugins/
```

Then run:

```bash
cd /home/pi/domoticz/plugins/GreeAC

chmod +x install.sh update.sh uninstall.sh

./install.sh

sudo systemctl restart domoticz
```

---

## Updating

```bash
cd /home/pi/domoticz/plugins/GreeAC

git pull

./update.sh

sudo systemctl restart domoticz
```

---

## Uninstall

```bash
cd /home/pi/domoticz/plugins/GreeAC

./uninstall.sh

sudo systemctl restart domoticz
```

---

## Hardware Configuration

Create a new **Gree AC Local** hardware instance in Domoticz and configure:

* IP Address
* MAC Address
* Poll Interval (Power ON)
* Poll Interval (Power OFF)
* Debug Mode

> Create one hardware instance for each Gree air conditioner.

---

## License

MIT License
