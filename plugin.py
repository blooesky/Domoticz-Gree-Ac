# Gree AC Domoticz Plugin
# Version 1.0.8

"""
<plugin key="GreeAC" name="Gree AC Local" author="4D" version="1.0.8"
        wikilink="https://github.com/blooesky/Domoticz-Gree-Ac"
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

import sys
import os
import json
import time
import threading
import queue
import subprocess

PLUGIN_VERSION = "1.0.8"
PLUGIN_DIR = os.path.dirname(os.path.realpath(__file__))
VENV_SITE = os.path.join(PLUGIN_DIR, "venv", "lib")
VENV_PYTHON = os.path.join(PLUGIN_DIR, "venv", "bin", "python")
WORKER_FLAG = "--gree-worker"
WORKER_MODE = WORKER_FLAG in sys.argv

# Add the plugin venv to sys.path both in Domoticz and worker mode.
for root, dirs, files in os.walk(VENV_SITE):
    if root.endswith("site-packages"):
        if root not in sys.path:
            sys.path.insert(0, root)
        break


# =====================================================================
# Standalone worker process
# =====================================================================
#
# The worker is deliberately a SEPARATE Python process, not a thread.
# Domoticz embeds Python and multiple plugin instances can otherwise end
# up with asyncio loop state crossing boundaries.  The worker process has
# one normal asyncio loop and keeps the Gree Device bound between calls.
#
# Stdout is reserved for one-JSON-object-per-line IPC with Domoticz.
# Library/debug logging goes to stderr.
# =====================================================================

if WORKER_MODE:
    import asyncio
    import logging

    from greeclimate.discovery import Discovery, Listener
    from greeclimate.device import Device, Props

    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(name)s - %(levelname)s - %(message)s",
    )

    WORKER_STATUS_SETTLE = 0.30
    WORKER_DISCOVERY_WAIT = 5.0
    WORKER_DISCOVERY_RETRIES = 3
    WORKER_DISCOVERY_RETRY_DELAY = 1.0

    def worker_emit(payload):
        try:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass


    class WorkerDiscoveryListener(Listener):
        def __init__(self, target_ip, target_mac):
            self.target_ip = target_ip
            self.target_mac = target_mac.lower()
            self.found_device = None
            self.found_event = asyncio.Event()

        async def device_found(self, device_info):
            mac = str(device_info.mac).lower()
            if device_info.ip == self.target_ip or mac == self.target_mac:
                self.found_device = device_info
                self.found_event.set()


    class StandaloneGreeClient:
        def __init__(self, ip, mac):
            self.ip = ip
            self.mac = mac.lower()
            self.device = None
            self.device_info = None

        async def close(self):
            if self.device is not None:
                try:
                    self.device.close()
                except Exception:
                    pass
            self.device = None
            self.device_info = None

        async def discover(self):
            # Discovery is the only fragile part during Domoticz startup.
            # Both Gree plugin instances normally start at almost the same
            # moment, so a single short broadcast window can occasionally
            # miss an otherwise healthy unit.
            #
            # Keep the event-based discovery because it returns immediately
            # when the target replies, but allow several complete retries.

            try:
                mac_bytes = bytes.fromhex(self.mac)
                initial_stagger = (sum(mac_bytes) % 7) * 0.10
            except Exception:
                initial_stagger = 0.0

            if initial_stagger:
                await asyncio.sleep(initial_stagger)

            for attempt in range(1, WORKER_DISCOVERY_RETRIES + 1):
                listener = WorkerDiscoveryListener(self.ip, self.mac)

                # Do NOT pass a loop explicitly. This object is created while
                # the worker's one asyncio loop is already running.
                discovery = Discovery(timeout=WORKER_DISCOVERY_WAIT)
                discovery.add_listener(listener)

                try:
                    await discovery.search_devices()

                    try:
                        await asyncio.wait_for(
                            listener.found_event.wait(),
                            timeout=WORKER_DISCOVERY_WAIT,
                        )
                    except asyncio.TimeoutError:
                        pass

                    if listener.found_device is not None:
                        self.device_info = listener.found_device
                        return self.device_info

                finally:
                    try:
                        discovery.close()
                    except Exception:
                        pass

                if attempt < WORKER_DISCOVERY_RETRIES:
                    await asyncio.sleep(
                        WORKER_DISCOVERY_RETRY_DELAY * attempt
                    )

            raise RuntimeError(
                f"Gree device not found after "
                f"{WORKER_DISCOVERY_RETRIES} discovery attempts: "
                f"{self.ip} / {self.mac}"
            )

        async def ensure_device(self):
            if self.device is not None:
                return self.device

            device_info = await self.discover()

            # Again, do not pass loop=.  Device and all asyncio primitives
            # are created on the same running loop in this process.
            device = Device(
                device_info,
                timeout=10,
                bind_timeout=3,
            )

            try:
                await device.bind()

                # bind() requests state too, but explicitly request it once
                # so command-dependent fields are populated before use.
                await device.update_state()

                deadline = asyncio.get_running_loop().time() + 1.2
                while asyncio.get_running_loop().time() < deadline:
                    if device.has_valid_state and device.raw_properties:
                        break
                    await asyncio.sleep(0.05)

                self.device = device
                return self.device

            except Exception:
                try:
                    device.close()
                except Exception:
                    pass
                raise

        def snapshot(self):
            if self.device is None:
                return None

            raw = dict(self.device.raw_properties or {})
            if not self.device.has_valid_state or not raw:
                return None

            return {
                "raw": raw,
                "target_temperature": self.device.target_temperature,
                "current_temperature": self.device.current_temperature,
            }

        async def poll(self):
            device = await self.ensure_device()
            await device.update_state()
            await asyncio.sleep(WORKER_STATUS_SETTLE)
            return self.snapshot()

        async def command(self, commands):
            device = await self.ensure_device()

            for prop_name, value in commands:
                device.set_property(Props(prop_name), value)

            # This sends immediately.  Verification is performed by a normal
            # poll queued by the Domoticz side after command success.
            await device.push_state_update()
            return True


    async def worker_main(ip, mac):
        client = StandaloneGreeClient(ip, mac)
        worker_emit({"type": "worker_ready"})

        try:
            while True:
                # stdin is blocking, therefore read it in the default executor.
                # This is safe here because this is a standalone interpreter.
                line = await asyncio.to_thread(sys.stdin.readline)

                if line == "":
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                except Exception as e:
                    worker_emit({
                        "type": "worker_error",
                        "context": "decode",
                        "message": f"{type(e).__name__}: {e}",
                    })
                    continue

                request_id = request.get("id")
                request_type = request.get("type")

                if request_type == "stop":
                    break

                try:
                    if request_type == "poll":
                        snapshot = await client.poll()
                        worker_emit({
                            "type": "poll_result",
                            "id": request_id,
                            "snapshot": snapshot,
                        })

                    elif request_type == "command":
                        commands = request.get("commands", [])
                        await client.command(commands)
                        worker_emit({
                            "type": "command_result",
                            "id": request_id,
                            "commands": commands,
                        })

                    else:
                        worker_emit({
                            "type": "worker_error",
                            "id": request_id,
                            "context": "request",
                            "message": f"Unknown request type: {request_type}",
                        })

                except Exception as e:
                    # Drop the bound object after a network/protocol error.
                    # The next operation performs fresh discovery + bind.
                    await client.close()
                    worker_emit({
                        "type": "worker_error",
                        "id": request_id,
                        "context": request_type or "unknown",
                        "message": f"{type(e).__name__}: {e}",
                    })

        finally:
            await client.close()


    def worker_entry():
        try:
            idx = sys.argv.index(WORKER_FLAG)
            ip = sys.argv[idx + 1]
            mac = sys.argv[idx + 2]
        except Exception:
            worker_emit({
                "type": "worker_fatal",
                "message": "Missing worker IP/MAC arguments",
            })
            return 2

        try:
            asyncio.run(worker_main(ip, mac))
            return 0
        except BaseException as e:
            worker_emit({
                "type": "worker_fatal",
                "message": f"{type(e).__name__}: {e}",
            })
            return 1


    sys.exit(worker_entry())


# =====================================================================
# Domoticz plugin process
# =====================================================================

import Domoticz

from greeclimate.device import Props


UNIT_POWER = 1
UNIT_MODE = 2
UNIT_SET_TEMP = 3
UNIT_ROOM_TEMP = 4
UNIT_FAN = 5
UNIT_SWING = 6
UNIT_LIGHT = 7
UNIT_TURBO = 8
UNIT_RAW = 9

MODE_OPTIONS = {
    "LevelActions": "|||||",
    "LevelNames": "Off|Auto|Cool|Dry|Fan|Heat",
    "LevelOffHidden": "false",
    "SelectorStyle": "0",
}

FAN_OPTIONS = {
    "LevelActions": "|||||",
    "LevelNames": "Auto|Low|Medium Low|Medium|Medium High|High",
    "LevelOffHidden": "false",
    "SelectorStyle": "0",
}

CONFIRMATION_READS = 2
CONFIRMATION_POLL_SECONDS = 2
WORKER_RESTART_DELAY = 2
COMMAND_VERIFY_DELAY = 0.5


class BasePlugin:
    def __init__(self):
        self.ip = ""
        self.mac = ""
        self.poll_on = 5
        self.poll_off = 30

        self.last_poll = 0.0
        self.power_on = False
        self.confirmed = {}
        self.pending = {}

        self.process = None
        self.stdout_thread = None
        self.stderr_thread = None
        self.result_queue = queue.Queue()
        self.log_queue = queue.Queue()
        self.process_lock = threading.RLock()

        self.request_id = 0
        self.poll_in_flight = False
        self.worker_ready = False
        self.next_restart_at = 0.0
        self.verify_after = 0.0
        self.stopping = False
        self.debug_enabled = False

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    def onStart(self):
        self.ip = Parameters["Address"].strip()
        self.mac = Parameters["Mode1"].strip().lower()

        try:
            self.poll_on = max(2, int(Parameters["Mode2"]))
        except Exception:
            self.poll_on = 5

        try:
            self.poll_off = max(5, int(Parameters["Mode3"]))
        except Exception:
            self.poll_off = 30

        self.debug_enabled = Parameters["Mode6"] == "True"
        if self.debug_enabled:
            Domoticz.Debugging(1)

        Domoticz.Log(f"Gree AC Local plugin v{PLUGIN_VERSION} started")
        Domoticz.Log(f"Target: {self.ip} / {self.mac}")

        self.create_devices()
        self.start_worker()
        Domoticz.Heartbeat(1)

    def onStop(self):
        self.stopping = True
        self.stop_worker()
        Domoticz.Log("Gree AC Local plugin stopped")

    # -----------------------------------------------------------------
    # Worker process management
    # -----------------------------------------------------------------

    def get_worker_python(self):
        if os.path.isfile(VENV_PYTHON) and os.access(VENV_PYTHON, os.X_OK):
            return VENV_PYTHON
        return "python3"

    def start_worker(self):
        with self.process_lock:
            if self.process is not None and self.process.poll() is None:
                return True

            self.stop_worker(clean=False)

            python_bin = self.get_worker_python()
            command = [
                python_bin,
                os.path.realpath(__file__),
                WORKER_FLAG,
                self.ip,
                self.mac,
            ]

            try:
                self.process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    close_fds=True,
                )
            except Exception as e:
                self.process = None
                Domoticz.Error(f"Unable to start Gree worker process: {e}")
                self.next_restart_at = time.monotonic() + WORKER_RESTART_DELAY
                return False

            self.worker_ready = False
            self.poll_in_flight = False

            self.stdout_thread = threading.Thread(
                target=self._stdout_reader,
                args=(self.process,),
                name=f"GreeAC-stdout-{self.mac}",
                daemon=True,
            )
            self.stdout_thread.start()

            self.stderr_thread = threading.Thread(
                target=self._stderr_reader,
                args=(self.process,),
                name=f"GreeAC-stderr-{self.mac}",
                daemon=True,
            )
            self.stderr_thread.start()

            return True

    def stop_worker(self, clean=True):
        proc = self.process
        if proc is None:
            return

        try:
            if proc.poll() is None and clean and proc.stdin:
                proc.stdin.write(json.dumps({"type": "stop"}) + "\n")
                proc.stdin.flush()
        except Exception:
            pass

        if clean:
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

        self.process = None
        self.worker_ready = False
        self.poll_in_flight = False

    def _stdout_reader(self, proc):
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    self.log_queue.put(
                        f"Worker sent invalid JSON: {line[:300]}"
                    )
                    continue
                self.result_queue.put(item)
        except Exception as e:
            self.log_queue.put(f"Worker stdout reader stopped: {e}")

    def _stderr_reader(self, proc):
        try:
            for line in proc.stderr:
                line = line.strip()
                if line:
                    self.log_queue.put(line)
        except Exception:
            pass

    def ensure_worker(self):
        if self.stopping:
            return False

        if self.process is not None and self.process.poll() is None:
            return True

        if time.monotonic() < self.next_restart_at:
            return False

        if self.process is not None:
            rc = self.process.poll()
            Domoticz.Error(
                f"Gree worker process stopped (exit code {rc}); restarting"
            )

        self.process = None
        self.next_restart_at = time.monotonic() + WORKER_RESTART_DELAY
        return self.start_worker()

    def send_worker_request(self, request):
        if not self.ensure_worker():
            return False

        with self.process_lock:
            proc = self.process
            if proc is None or proc.poll() is not None or proc.stdin is None:
                return False

            self.request_id += 1
            request["id"] = self.request_id

            try:
                proc.stdin.write(json.dumps(request) + "\n")
                proc.stdin.flush()
                return True
            except Exception as e:
                Domoticz.Error(f"Unable to send request to Gree worker: {e}")
                try:
                    proc.terminate()
                except Exception:
                    pass
                return False

    # -----------------------------------------------------------------
    # Domoticz device creation
    # -----------------------------------------------------------------

    def create_devices(self):
        if UNIT_POWER not in Devices:
            Domoticz.Device(
                Name="Gree Power", Unit=UNIT_POWER, TypeName="Switch"
            ).Create()

        if UNIT_MODE not in Devices:
            Domoticz.Device(
                Name="Gree Mode",
                Unit=UNIT_MODE,
                TypeName="Selector Switch",
                Options=MODE_OPTIONS,
            ).Create()

        if UNIT_SET_TEMP not in Devices:
            Domoticz.Device(
                Name="Gree Set Temp", Unit=UNIT_SET_TEMP, Type=242, Subtype=1
            ).Create()

        if UNIT_ROOM_TEMP not in Devices:
            Domoticz.Device(
                Name="Gree Room Temp", Unit=UNIT_ROOM_TEMP, TypeName="Temperature"
            ).Create()

        if UNIT_FAN not in Devices:
            Domoticz.Device(
                Name="Gree Fan",
                Unit=UNIT_FAN,
                TypeName="Selector Switch",
                Options=FAN_OPTIONS,
            ).Create()
        else:
            self.upgrade_fan_selector()

        if UNIT_SWING not in Devices:
            Domoticz.Device(
                Name="Gree Swing Vertical", Unit=UNIT_SWING, TypeName="Switch"
            ).Create()

        if UNIT_LIGHT not in Devices:
            Domoticz.Device(
                Name="Gree Light", Unit=UNIT_LIGHT, TypeName="Switch"
            ).Create()

        if UNIT_TURBO not in Devices:
            Domoticz.Device(
                Name="Gree Turbo", Unit=UNIT_TURBO, TypeName="Switch"
            ).Create()

        if UNIT_RAW not in Devices:
            Domoticz.Device(
                Name="Gree Raw Status", Unit=UNIT_RAW, TypeName="Text"
            ).Create()

    def upgrade_fan_selector(self):
        try:
            current = Devices[UNIT_FAN].Options or {}
            if current.get("LevelNames", "") == FAN_OPTIONS["LevelNames"]:
                return

            Domoticz.Log("Upgrading Gree Fan selector to 6 speeds")
            Devices[UNIT_FAN].Update(
                nValue=Devices[UNIT_FAN].nValue,
                sValue=Devices[UNIT_FAN].sValue,
                Options=FAN_OPTIONS,
            )
        except Exception as e:
            Domoticz.Error(f"Could not upgrade Gree Fan selector: {e}")
            Domoticz.Error(
                "If needed, delete only 'Gree Fan' and restart Domoticz."
            )

    # -----------------------------------------------------------------
    # Heartbeat / worker results
    # -----------------------------------------------------------------

    def onHeartbeat(self):
        self.process_worker_logs()
        self.process_worker_results()

        if not self.ensure_worker():
            return

        if not self.worker_ready:
            return

        now_mono = time.monotonic()

        if self.verify_after and now_mono >= self.verify_after:
            self.verify_after = 0.0
            self.enqueue_poll(force=True)
            return

        interval = (
            CONFIRMATION_POLL_SECONDS
            if self.pending
            else (self.poll_on if self.power_on else self.poll_off)
        )

        if (
            not self.poll_in_flight
            and time.time() - self.last_poll >= interval
        ):
            self.enqueue_poll()

    def process_worker_logs(self):
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break

            if self.debug_enabled:
                Domoticz.Debug(f"[GREE WORKER] {line}")

    def process_worker_results(self):
        while True:
            try:
                item = self.result_queue.get_nowait()
            except queue.Empty:
                break

            item_type = item.get("type")

            if item_type == "worker_ready":
                self.worker_ready = True
                self.last_poll = 0.0
                self.enqueue_poll(force=True)

            elif item_type == "poll_result":
                self.poll_in_flight = False
                self.last_poll = time.time()
                snapshot = item.get("snapshot")
                if snapshot:
                    self.process_snapshot(snapshot)

            elif item_type == "command_result":
                commands = item.get("commands", [])
                for prop_name, value in commands:
                    self.apply_command_state(prop_name, value)

                # Verify real device state shortly afterwards, without delaying
                # the command itself.
                self.verify_after = time.monotonic() + COMMAND_VERIFY_DELAY

            elif item_type == "worker_error":
                context = item.get("context", "unknown")
                message = item.get("message", "unknown error")

                if context == "poll":
                    self.poll_in_flight = False
                    self.last_poll = time.time()
                    Domoticz.Error(f"Gree poll failed: {message}")
                elif context == "command":
                    Domoticz.Error(f"Gree command failed: {message}")
                else:
                    Domoticz.Error(f"Gree worker error ({context}): {message}")

            elif item_type == "worker_fatal":
                Domoticz.Error(
                    f"Gree worker fatal error: {item.get('message', 'unknown')}"
                )
                if self.process is not None:
                    try:
                        self.process.terminate()
                    except Exception:
                        pass

    def enqueue_poll(self, force=False):
        # Never queue two polls at the same time.  ``force`` only means
        # "poll as soon as possible" from the caller's point of view.
        if self.poll_in_flight:
            return False

        if self.send_worker_request({"type": "poll"}):
            self.poll_in_flight = True
            return True

        return False

    # -----------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Debug(
            f"[GREE] Command Unit={Unit} Command={Command} Level={Level}"
        )

        commands = []

        if Unit == UNIT_POWER:
            commands.append((Props.POWER.value, 1 if Command == "On" else 0))

        elif Unit == UNIT_MODE:
            mode_map = {
                10: 0,  # Auto
                20: 1,  # Cool
                30: 2,  # Dry
                40: 3,  # Fan
                50: 4,  # Heat
            }

            if Level == 0:
                commands.append((Props.POWER.value, 0))
            elif Level in mode_map:
                commands.append((Props.MODE.value, mode_map[Level]))
                commands.append((Props.POWER.value, 1))

        elif Unit == UNIT_SET_TEMP:
            try:
                temp = int(float(Command))
            except Exception:
                try:
                    temp = int(float(Level))
                except Exception:
                    Domoticz.Error(
                        f"Invalid temperature command: {Command} / {Level}"
                    )
                    return

            if not 16 <= temp <= 30:
                Domoticz.Error(f"Temperature out of range: {temp}")
                return

            commands.append((Props.TEMP_SET.value, temp))

        elif Unit == UNIT_FAN:
            fan_map = {
                0: 0,   # Auto
                10: 1,  # Low
                20: 2,  # Medium Low
                30: 3,  # Medium
                40: 4,  # Medium High
                50: 5,  # High
            }
            if Level in fan_map:
                commands.append((Props.FAN_SPEED.value, fan_map[Level]))

        elif Unit == UNIT_SWING:
            commands.append(
                (Props.SWING_VERT.value, 1 if Command == "On" else 0)
            )

        elif Unit == UNIT_LIGHT:
            commands.append(
                (Props.LIGHT.value, 1 if Command == "On" else 0)
            )

        elif Unit == UNIT_TURBO:
            commands.append(
                (Props.TURBO.value, 1 if Command == "On" else 0)
            )

        if not commands:
            return

        if not self.send_worker_request({
            "type": "command",
            "commands": commands,
        }):
            Domoticz.Error("Unable to queue Gree command")
            return

        # Optimistic local update gives immediate Domoticz feedback.  The
        # worker result + verification poll will reconcile the real state.
        for prop_name, value in commands:
            self.apply_command_state(prop_name, value)

    # -----------------------------------------------------------------
    # Snapshot filtering
    # -----------------------------------------------------------------

    def process_snapshot(self, snapshot):
        raw = snapshot.get("raw") or {}
        if not raw:
            return

        Domoticz.Debug(
            "[GREE] Raw state: "
            + json.dumps(raw, ensure_ascii=False, sort_keys=True)
        )

        self._consider_raw_state(raw, Props.POWER, {0, 1})
        self._consider_raw_state(raw, Props.MODE, {0, 1, 2, 3, 4})
        self._consider_raw_state(raw, Props.FAN_SPEED, {0, 1, 2, 3, 4, 5})
        self._consider_raw_state(raw, Props.SWING_VERT, set(range(0, 12)))
        self._consider_raw_state(raw, Props.LIGHT, {0, 1})
        self._consider_raw_state(raw, Props.TURBO, {0, 1})

        self.render_confirmed_state()

        target = snapshot.get("target_temperature")
        if target is not None:
            self.update_value(UNIT_SET_TEMP, 0, str(target))

        current = snapshot.get("current_temperature")
        if current is not None:
            self.update_value(UNIT_ROOM_TEMP, 0, str(current))

        self.update_value(
            UNIT_RAW,
            0,
            json.dumps(raw, ensure_ascii=False, sort_keys=True),
        )

    def _consider_raw_state(self, raw, prop, valid_values):
        key = prop.value
        if key not in raw:
            Domoticz.Debug(f"[GREE] Missing {key}; keeping previous state")
            return

        self.consider_state(key, raw[key], valid_values)

    def consider_state(self, key, value, valid_values):
        if value is None:
            Domoticz.Debug(f"[GREE] Ignoring {key}=None")
            return

        try:
            value = int(value)
        except Exception:
            Domoticz.Debug(f"[GREE] Invalid {key}={value}")
            return

        if value not in valid_values:
            Domoticz.Debug(f"[GREE] Unsupported {key}={value}")
            return

        # First valid state after startup is trusted immediately.
        if key not in self.confirmed:
            self.confirmed[key] = value
            self.pending.pop(key, None)
            return

        # No change.
        if self.confirmed[key] == value:
            self.pending.pop(key, None)
            return

        # External change: require two consecutive valid readings.
        candidate = self.pending.get(key)
        if candidate and candidate["value"] == value:
            candidate["count"] += 1
        else:
            candidate = {"value": value, "count": 1}
            self.pending[key] = candidate

        Domoticz.Debug(
            f"[GREE] Candidate {key}={value} "
            f"{candidate['count']}/{CONFIRMATION_READS}"
        )

        if candidate["count"] >= CONFIRMATION_READS:
            old = self.confirmed.get(key)
            self.confirmed[key] = value
            self.pending.pop(key, None)
            Domoticz.Debug(f"[GREE] Confirmed {key}: {old} -> {value}")

    def apply_command_state(self, prop_name, value):
        try:
            value = int(value)
        except Exception:
            return

        if prop_name == Props.TEMP_SET.value:
            self.update_value(UNIT_SET_TEMP, 0, str(value))
            return

        self.confirmed[prop_name] = value
        self.pending.pop(prop_name, None)
        self.render_confirmed_state()

    # -----------------------------------------------------------------
    # Render confirmed state
    # -----------------------------------------------------------------

    def render_confirmed_state(self):
        if Props.POWER.value in self.confirmed:
            power = self.confirmed[Props.POWER.value] == 1
            self.power_on = power
            self.update_switch(UNIT_POWER, power)

        if Props.POWER.value in self.confirmed:
            power_raw = self.confirmed[Props.POWER.value]

            if power_raw == 0:
                self.update_selector(UNIT_MODE, 0, active=False)
            elif Props.MODE.value in self.confirmed:
                mode_to_level = {
                    0: 10,
                    1: 20,
                    2: 30,
                    3: 40,
                    4: 50,
                }
                mode = self.confirmed[Props.MODE.value]
                if mode in mode_to_level:
                    self.update_selector(
                        UNIT_MODE, mode_to_level[mode], active=True
                    )

        if Props.FAN_SPEED.value in self.confirmed:
            fan_to_level = {
                0: 0,
                1: 10,
                2: 20,
                3: 30,
                4: 40,
                5: 50,
            }
            fan = self.confirmed[Props.FAN_SPEED.value]
            if fan in fan_to_level:
                self.update_selector(
                    UNIT_FAN, fan_to_level[fan], active=True
                )

        if Props.SWING_VERT.value in self.confirmed:
            self.update_switch(
                UNIT_SWING,
                self.confirmed[Props.SWING_VERT.value] != 0,
            )

        if Props.LIGHT.value in self.confirmed:
            self.update_switch(
                UNIT_LIGHT,
                self.confirmed[Props.LIGHT.value] == 1,
            )

        if Props.TURBO.value in self.confirmed:
            self.update_switch(
                UNIT_TURBO,
                self.confirmed[Props.TURBO.value] == 1,
            )

    # -----------------------------------------------------------------
    # Safe Domoticz updates
    # -----------------------------------------------------------------

    def update_switch(self, unit, state):
        if unit not in Devices or state is None:
            return

        nvalue = 1 if bool(state) else 0
        svalue = "On" if bool(state) else "Off"

        if (
            Devices[unit].nValue != nvalue
            or Devices[unit].sValue != svalue
        ):
            Devices[unit].Update(nValue=nvalue, sValue=svalue)

    def update_selector(self, unit, level, active=True):
        if unit not in Devices:
            return

        nvalue = 1 if active else 0
        svalue = str(int(level))

        if (
            Devices[unit].nValue != nvalue
            or Devices[unit].sValue != svalue
        ):
            Devices[unit].Update(nValue=nvalue, sValue=svalue)

    def update_value(self, unit, nvalue, svalue):
        if unit not in Devices:
            return

        svalue = str(svalue)
        if (
            Devices[unit].nValue != nvalue
            or Devices[unit].sValue != svalue
        ):
            Devices[unit].Update(nValue=nvalue, sValue=svalue)


# =====================================================================
# Domoticz callbacks
# =====================================================================

global _plugin
_plugin = BasePlugin()


def onStart():
    _plugin.onStart()


def onStop():
    _plugin.onStop()


def onHeartbeat():
    _plugin.onHeartbeat()


def onCommand(Unit, Command, Level, Hue):
    _plugin.onCommand(Unit, Command, Level, Hue)
