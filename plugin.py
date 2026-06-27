# Gree AC Domoticz Plugin

"""
<plugin key="GreeAC" name="Gree AC Local" author="4D" version="1.0.0" wikilink="https://github.com/blooesky/Domoticz-Gree-Ac"
        externallink="https://github.com/blooesky/Domoticz-Gree-Ac">
    <params>
        <param field="Address" label="Gree AC IP" width="200px" required="true" default="192.168.0.200"/>
        <param field="Mode1" label="Gree AC MAC" width="200px" required="true" default="502c111111"/>
        <param field="Mode2" label="Poll ON seconds" width="80px" required="true" default="5"/>
        <param field="Mode3" label="Poll OFF seconds" width="80px" required="true" default="30"/>
        <param field="Mode6" label="Debug" width="80px">
            <options>
                <option label="False" value="False" default="true"/>
                <option label="True" value="True"/>
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
import sys
import os
import asyncio
import json
import time


PLUGIN_DIR = os.path.dirname(os.path.realpath(__file__))
VENV_SITE = os.path.join(PLUGIN_DIR, "venv", "lib")

for root, dirs, files in os.walk(VENV_SITE):
    if root.endswith("site-packages"):
        sys.path.insert(0, root)
        break

from greeclimate.discovery import Discovery, Listener
from greeclimate.device import Device, Props


UNIT_POWER = 1
UNIT_MODE = 2
UNIT_SET_TEMP = 3
UNIT_ROOM_TEMP = 4
UNIT_FAN = 5
UNIT_SWING = 6
UNIT_LIGHT = 7
UNIT_TURBO = 8
UNIT_RAW = 9


class GreeListener(Listener):
    def __init__(self, target_ip, target_mac):
        self.target_ip = target_ip
        self.target_mac = target_mac.lower()
        self.found_device = None

    async def device_found(self, device_info):
        Domoticz.Debug(f"Found Gree: {device_info.ip} {device_info.mac}")

        if device_info.ip == self.target_ip or device_info.mac.lower() == self.target_mac:
            self.found_device = device_info


class BasePlugin:
    def __init__(self):
        self.ip = ""
        self.mac = ""
        self.poll_on = 5
        self.poll_off = 30
        self.last_poll = 0
        self.power_on = False
        self.device_info = None

    def onStart(self):
        self.ip = Parameters["Address"].strip()
        self.mac = Parameters["Mode1"].strip().lower()
        self.poll_on = int(Parameters["Mode2"])
        self.poll_off = int(Parameters["Mode3"])

        if Parameters["Mode6"] == "True":
            Domoticz.Debugging(1)

        Domoticz.Log("Gree AC plugin started")

        self.create_devices()
        Domoticz.Heartbeat(1)

        self.refresh_status()

    def create_devices(self):
        if UNIT_POWER not in Devices:
            Domoticz.Device(Name="Gree Power", Unit=UNIT_POWER, TypeName="Switch").Create()

        if UNIT_MODE not in Devices:
            options = {
                "LevelActions": "||||",
                "LevelNames": "Off|Auto|Cool|Dry|Fan|Heat",
                "LevelOffHidden": "false",
                "SelectorStyle": "0"
            }
            Domoticz.Device(Name="Gree Mode", Unit=UNIT_MODE, TypeName="Selector Switch", Options=options).Create()

        if UNIT_SET_TEMP not in Devices:
            Domoticz.Device(Name="Gree Set Temp", Unit=UNIT_SET_TEMP, Type=242, Subtype=1).Create()

        if UNIT_ROOM_TEMP not in Devices:
            Domoticz.Device(Name="Gree Room Temp", Unit=UNIT_ROOM_TEMP, TypeName="Temperature").Create()

        if UNIT_FAN not in Devices:
            options = {
                "LevelActions": "|||",
                "LevelNames": "Auto|Low|Medium|High",
                "LevelOffHidden": "false",
                "SelectorStyle": "0"
            }
            Domoticz.Device(Name="Gree Fan", Unit=UNIT_FAN, TypeName="Selector Switch", Options=options).Create()

        if UNIT_SWING not in Devices:
            Domoticz.Device(Name="Gree Swing Vertical", Unit=UNIT_SWING, TypeName="Switch").Create()

        if UNIT_LIGHT not in Devices:
            Domoticz.Device(Name="Gree Light", Unit=UNIT_LIGHT, TypeName="Switch").Create()

        if UNIT_TURBO not in Devices:
            Domoticz.Device(Name="Gree Turbo", Unit=UNIT_TURBO, TypeName="Switch").Create()

        if UNIT_RAW not in Devices:
            Domoticz.Device(Name="Gree Raw Status", Unit=UNIT_RAW, TypeName="Text").Create()

    def onHeartbeat(self):
        interval = self.poll_on if self.power_on else self.poll_off

        if time.time() - self.last_poll >= interval:
            self.refresh_status()

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Debug(f"onCommand Unit={Unit} Command={Command} Level={Level}")

        if Unit == UNIT_POWER:
            self.send_command(Props.POWER, 1 if Command == "On" else 0)

        elif Unit == UNIT_MODE:
            mode_map = {
                0: None,
                10: 0,
                20: 1,
                30: 2,
                40: 3,
                50: 4,
            }

            if Level == 0:
                self.send_command(Props.POWER, 0)
            elif Level in mode_map:
                self.send_command(Props.MODE, mode_map[Level])
                self.send_command(Props.POWER, 1)

        elif Unit == UNIT_SET_TEMP:
            try:
                temp = int(float(Command))
            except Exception:
                try:
                    temp = int(float(Level))
                except Exception:
                    Domoticz.Error(f"Invalid temp command: {Command} / {Level}")
                    return

            if 16 <= temp <= 30:
                self.send_command(Props.TEMP_SET, temp)

        elif Unit == UNIT_FAN:
            fan_map = {
                0: 0,
                10: 1,
                20: 2,
                30: 3,
            }
            if Level in fan_map:
                self.send_command(Props.FAN_SPEED, fan_map[Level])

        elif Unit == UNIT_SWING:
            self.send_command(Props.SWING_VERT, 1 if Command == "On" else 0)

        elif Unit == UNIT_LIGHT:
            self.send_command(Props.LIGHT, 1 if Command == "On" else 0)

        elif Unit == UNIT_TURBO:
            self.send_command(Props.TURBO, 1 if Command == "On" else 0)

        self.refresh_status()

    def run_async(self, coro):
        return asyncio.run(coro)

    async def discover_device(self):
        listener = GreeListener(self.ip, self.mac)
        discovery = Discovery()
        discovery.add_listener(listener)

        await discovery.scan(wait_for=10)
        return listener.found_device

    async def get_device(self):
        if self.device_info is None:
            self.device_info = await self.discover_device()

        if self.device_info is None:
            raise Exception(f"Gree device not found: {self.ip} {self.mac}")

        device = Device(self.device_info)
        await device.bind()
        await asyncio.sleep(1)
        return device

    def refresh_status(self):
        try:
            self.run_async(self._refresh_status())
        except Exception as e:
            Domoticz.Error(f"Refresh failed: {e}")

    async def _refresh_status(self):
        device = await self.get_device()

        try:
            await device.update_state()
            await asyncio.sleep(1)

            self.power_on = bool(device.power)
            raw = device.raw_properties or {}

            self.update_switch(UNIT_POWER, device.power)
            self.update_mode(device.power, device.mode)

            if device.target_temperature is not None:
                Devices[UNIT_SET_TEMP].Update(nValue=0, sValue=str(device.target_temperature))

            if device.current_temperature is not None:
                Devices[UNIT_ROOM_TEMP].Update(nValue=0, sValue=str(device.current_temperature))

            self.update_fan(device.fan_speed)
            self.update_switch(UNIT_SWING, bool(device.vertical_swing))
            self.update_switch(UNIT_LIGHT, bool(device.light))
            self.update_switch(UNIT_TURBO, bool(device.turbo))

            Devices[UNIT_RAW].Update(nValue=0, sValue=json.dumps(raw, ensure_ascii=False))

            self.last_poll = time.time()

        finally:
            device.close()

    def send_command(self, prop, value):
        try:
            self.run_async(self._send_command(prop, value))
        except Exception as e:
            Domoticz.Error(f"Command failed {prop}={value}: {e}")

    async def _send_command(self, prop, value):
        device = await self.get_device()

        try:
            device.set_property(prop, value)
            await device.push_state_update()
            await asyncio.sleep(1)
        finally:
            device.close()

    def update_switch(self, unit, state):
        if unit not in Devices:
            return

        nvalue = 1 if state else 0
        svalue = "On" if state else "Off"

        if Devices[unit].nValue != nvalue:
            Devices[unit].Update(nValue=nvalue, sValue=svalue)

    def update_mode(self, power, mode):
        if UNIT_MODE not in Devices:
            return

        if not power:
            level = 0
        else:
            mode_to_level = {
                0: 10,
                1: 20,
                2: 30,
                3: 40,
                4: 50,
            }
            level = mode_to_level.get(mode, 0)

        Devices[UNIT_MODE].Update(nValue=1 if power else 0, sValue=str(level))

    def update_fan(self, fan):
        if UNIT_FAN not in Devices:
            return

        fan_to_level = {
            0: 0,
            1: 10,
            2: 20,
            3: 30,
        }

        level = fan_to_level.get(fan, 0)
        Devices[UNIT_FAN].Update(nValue=1, sValue=str(level))


global _plugin
_plugin = BasePlugin()


def onStart():
    _plugin.onStart()


def onHeartbeat():
    _plugin.onHeartbeat()


def onCommand(Unit, Command, Level, Hue):
    _plugin.onCommand(Unit, Command, Level, Hue)
