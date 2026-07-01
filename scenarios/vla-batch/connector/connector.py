"""opcua-uns-connector — the bridge between the vla-factory (OPC-UA server) and
the MonsterMQ UNS bus.

MonsterMQ has NO native OPC-UA client, so this connector plays the role of
"MonsterMQ's OPC-UA ingest":

  READ  : asyncua Client connects to the factory, polls every Status-tag from the
          ISA-95 model (via the fixed node-ids
          ns=2;s=DairyWorks.Vla.{Area}.{Equipment}.{tag}) and publishes each as an
          MQTT message on  DairyWorks/Vla/{Area}/{Equipment}/Status/{tag}
          (JSON payload {value,unit,ts,quality}).

  WRITE : subscribes to  DairyWorks/Vla/+/+/Command/#  and
          DairyWorks/Vla/Batch/Command/#  → translates each into the right OPC-UA
          method call (StartBatch/Stop/SetSetpoint/TakeSample/InjectFault/ClearFault)
          or a node write (setpoints / writable tags) on the factory.

Robust against (re)connecting: the factory or broker may not be up yet — both the
OPC-UA client loop and the MQTT client retry with backoff.

Contract: vla-build-contract.md §OPC-UA (node-ids), §UNS topics, §ISA-95 tags.
All names are the anonymised DairyWorks / generic set — no real vendor/company names.

Env:
    OPCUA_URL   default opc.tcp://vla-factory:4840/DairyWorks
    MQTT_HOST   default monstermq
    MQTT_PORT   default 1883
    POLL_SEC    default 1.0
    UNS_ROOT    default DairyWorks/Vla
    LOG_LEVEL   default INFO
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from asyncua import Client, ua

log = logging.getLogger("opcua-uns-connector")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

OPCUA_URL = os.environ.get("OPCUA_URL", "opc.tcp://vla-factory:4840/DairyWorks")
MQTT_HOST = os.environ.get("MQTT_HOST", "monstermq")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USERNAME") or None
MQTT_PASS = os.environ.get("MQTT_PASSWORD") or None
POLL_SEC = float(os.environ.get("POLL_SEC", "1.0"))
UNS_ROOT = os.environ.get("UNS_ROOT", "DairyWorks/Vla").strip("/")

NS = 2  # urn:dairyworks
SITE = "DairyWorks"
LINE = "Vla"

# ---------------------------------------------------------------------------
# Tag -> OPC-UA node-id mapping (LOCK — exact copy of the ISA-95 table in the
# build contract). Each entry: (Area, Equipment, tag, unit).
# The node-id is always  ns=2;s=DairyWorks.Vla.{Area}.{Equipment}.{tag}.
# The UNS Status topic is  {UNS_ROOT}/{Area}/{Equipment}/Status/{tag}.
# ---------------------------------------------------------------------------
STATUS_TAGS: list[tuple[str, str, str, str]] = [
    # Receiving
    ("Receiving", "receiving-tank-01", "level_L", "L"),
    ("Receiving", "receiving-tank-01", "temp_C", "C"),
    ("Receiving", "receiving-tank-01", "fat_setpoint_pct", "%"),
    # Mixing
    ("Mixing", "process-tank-01", "level_L", "L"),
    ("Mixing", "process-tank-01", "temp_C", "C"),
    ("Mixing", "process-tank-01", "agitator_rpm", "rpm"),
    ("Mixing", "process-tank-01", "dose_milk_actual_kg", "kg"),
    ("Mixing", "process-tank-01", "dose_sugar_actual_kg", "kg"),
    ("Mixing", "process-tank-01", "dose_starch_actual_kg", "kg"),
    ("Mixing", "process-tank-01", "dose_cocoa_actual_kg", "kg"),
    ("Mixing", "process-tank-01", "phase", ""),
    # Cook
    ("Cook", "cook-unit-01", "temp_C", "C"),
    ("Cook", "cook-unit-01", "setpoint_C", "C"),
    ("Cook", "cook-unit-01", "hold_sec", "s"),
    ("Cook", "cook-unit-01", "hold_elapsed_sec", "s"),
    ("Cook", "cook-unit-01", "viscosity_cP", "cP"),
    # Cooling
    ("Cooling", "cooler-01", "temp_C", "C"),
    ("Cooling", "cooler-01", "target_C", "C"),
    # Filling
    ("Filling", "filler-01", "packs_total", ""),
    ("Filling", "filler-01", "reject_count", ""),
    ("Filling", "filler-01", "pack_size_L", "L"),
]

# Line-level Batch status tags. UNS topic: {UNS_ROOT}/Batch/Status/{tag}.
# Node-id: ns=2;s=DairyWorks.Vla.Batch.{tag}
BATCH_TAGS: list[tuple[str, str]] = [
    ("state", ""),
    ("batch_id", ""),
    ("active_recipe", ""),
]


def node_id_for(area: str, equipment: str, tag: str) -> str:
    return f"{SITE}.{LINE}.{area}.{equipment}.{tag}"


def batch_node_id_for(tag: str) -> str:
    return f"{SITE}.{LINE}.Batch.{tag}"


def status_topic(area: str, equipment: str, tag: str) -> str:
    return f"{UNS_ROOT}/{area}/{equipment}/Status/{tag}"


def batch_status_topic(tag: str) -> str:
    return f"{UNS_ROOT}/Batch/Status/{tag}"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Command routing: which OPC-UA method a Command topic maps to.
#
# Line-level (topic {UNS_ROOT}/Batch/Command/{cmd}):
#   StartBatch  -> StartBatch(recipeId:String)
#   Stop        -> Stop()
#   InjectFault -> InjectFault(faultId:String, magnitude:Double)
#   ClearFault  -> ClearFault()
#   TakeSample  -> TakeSample(sampleType:String)
#
# Equipment-level (topic {UNS_ROOT}/{Area}/{Equipment}/Command/{cmd}) maps to
# either the line-object SetSetpoint(target,value) method, or a direct node-write
# on a writable tag. The build contract's SetSetpoint targets:
#   "cook.setpoint_C" | "cook.hold_sec" | "cooler.target_C" |
#   "mixing.agitator_rpm" | "dose.milk" | "dose.sugar" | "dose.starch" |
#   "dose.cocoa" | "receiving.fat"
# ---------------------------------------------------------------------------

# (Area, Equipment, cmd) -> SetSetpoint target string.
EQUIP_SETPOINT_TARGET: dict[tuple[str, str, str], str] = {
    ("Receiving", "receiving-tank-01", "fat_setpoint_pct"): "receiving.fat",
    ("Mixing", "process-tank-01", "agitator_rpm"): "mixing.agitator_rpm",
    ("Mixing", "process-tank-01", "dose_milk_setpoint_kg"): "dose.milk",
    ("Mixing", "process-tank-01", "dose_sugar_setpoint_kg"): "dose.sugar",
    ("Mixing", "process-tank-01", "dose_starch_setpoint_kg"): "dose.starch",
    ("Mixing", "process-tank-01", "dose_cocoa_setpoint_kg"): "dose.cocoa",
    ("Cook", "cook-unit-01", "setpoint_C"): "cook.setpoint_C",
    ("Cook", "cook-unit-01", "hold_sec"): "cook.hold_sec",
    ("Cooling", "cooler-01", "target_C"): "cooler.target_C",
}


class CommandJob:
    """A decoded MQTT Command, queued for execution on the OPC-UA thread."""

    __slots__ = ("kind", "args")

    def __init__(self, kind: str, args: dict):
        self.kind = kind      # "method:StartBatch" | "setpoint" | ...
        self.args = args


# ---------------------------------------------------------------------------
# MQTT side — runs in its own paho thread, decodes Commands into CommandJobs.
# ---------------------------------------------------------------------------
class MqttBus:
    def __init__(self, cmd_queue: "queue.Queue[CommandJob]"):
        self.cmd_queue = cmd_queue
        try:
            # paho-mqtt 2.x
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2, client_id="opcua-uns-connector"
            )
        except (AttributeError, TypeError):
            # paho-mqtt 1.x fallback
            self.client = mqtt.Client(client_id="opcua-uns-connector")
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.connected = threading.Event()

    def start(self):
        # connect_async + loop_start → paho auto-reconnects with backoff forever.
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        while True:
            try:
                self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
                self.client.loop_start()
                log.info("MQTT loop started (target %s:%s)", MQTT_HOST, MQTT_PORT)
                return
            except Exception as e:  # DNS / socket not ready yet
                log.warning("MQTT start failed (%s) — retry in 3s", e)
                threading.Event().wait(3)

    def _on_connect(self, client, userdata, flags, rc, *args):
        self.connected.set()
        client.subscribe(f"{UNS_ROOT}/+/+/Command/#", qos=1)
        client.subscribe(f"{UNS_ROOT}/Batch/Command/#", qos=1)
        log.info(
            "MQTT connected %s:%s — subscribed %s/+/+/Command/# and %s/Batch/Command/#",
            MQTT_HOST, MQTT_PORT, UNS_ROOT, UNS_ROOT,
        )

    def publish_status(self, topic: str, value, unit: str, quality: str = "GOOD"):
        payload = json.dumps(
            {"value": value, "unit": unit, "ts": iso_now(), "quality": quality}
        )
        self.client.publish(topic, payload, qos=0, retain=False)

    def _on_message(self, client, userdata, msg):
        try:
            self._decode(msg.topic, msg.payload.decode("utf-8", "replace"))
        except Exception as e:
            log.warning("bad command on %s (%s): %r", msg.topic, e, msg.payload[:200])

    def _decode(self, topic: str, raw: str):
        parts = topic.split("/")
        root_parts = UNS_ROOT.split("/")
        n = len(root_parts)
        if parts[:n] != root_parts:
            return
        rest = parts[n:]  # after DairyWorks/Vla

        # --- payload → value (accept scalar, JSON scalar, or {"value":..} obj) ---
        val = self._parse_value(raw)

        # Line-level: Batch/Command/{cmd}
        if len(rest) >= 3 and rest[0] == "Batch" and rest[1] == "Command":
            cmd = rest[2]
            self._route_line_command(cmd, val, raw)
            return

        # Equipment-level: {Area}/{Equipment}/Command/{cmd...}
        if len(rest) >= 4 and rest[2] == "Command":
            area, equipment, cmd = rest[0], rest[1], rest[3]
            self._route_equipment_command(area, equipment, cmd, val, raw)
            return

        log.debug("ignored command topic %s", topic)

    @staticmethod
    def _parse_value(raw: str):
        raw = raw.strip()
        try:
            obj = json.loads(raw)
        except Exception:
            return raw  # plain string / bare scalar text
        if isinstance(obj, dict):
            return obj.get("value", obj)
        return obj

    def _route_line_command(self, cmd: str, val, raw: str):
        cl = cmd.lower()
        if cl == "startbatch":
            recipe = val if isinstance(val, str) else (
                val.get("recipeId") if isinstance(val, dict) else str(val)
            )
            self.cmd_queue.put(CommandJob("method:StartBatch", {"recipeId": str(recipe)}))
        elif cl == "stop":
            self.cmd_queue.put(CommandJob("method:Stop", {}))
        elif cl == "clearfault":
            self.cmd_queue.put(CommandJob("method:ClearFault", {}))
        elif cl == "takesample":
            sample_type = val if isinstance(val, str) else str(val)
            self.cmd_queue.put(
                CommandJob("method:TakeSample", {"sampleType": sample_type})
            )
        elif cl == "injectfault":
            fault_id, magnitude = self._parse_fault(val, raw)
            self.cmd_queue.put(
                CommandJob(
                    "method:InjectFault",
                    {"faultId": fault_id, "magnitude": magnitude},
                )
            )
        elif cl == "setsetpoint":
            # generic line-level SetSetpoint with explicit target+value in payload
            obj = self._as_dict(raw)
            target = obj.get("target")
            value = obj.get("value")
            if target is not None and value is not None:
                self.cmd_queue.put(
                    CommandJob(
                        "method:SetSetpoint",
                        {"target": str(target), "value": float(value)},
                    )
                )
            else:
                log.warning("SetSetpoint missing target/value: %s", raw)
        else:
            log.warning("unknown line command '%s'", cmd)

    def _route_equipment_command(self, area, equipment, cmd, val, raw: str):
        key = (area, equipment, cmd)
        target = EQUIP_SETPOINT_TARGET.get(key)
        if target is not None:
            try:
                value = float(val)
            except (TypeError, ValueError):
                log.warning("non-numeric setpoint for %s/%s/%s: %r",
                            area, equipment, cmd, val)
                return
            self.cmd_queue.put(
                CommandJob("method:SetSetpoint", {"target": target, "value": value})
            )
            return
        log.warning("no mapping for equipment command %s/%s/Command/%s",
                    area, equipment, cmd)

    def _parse_fault(self, val, raw: str):
        obj = self._as_dict(raw)
        fault_id = obj.get("faultId") or obj.get("value")
        if fault_id is None and isinstance(val, str):
            fault_id = val
        magnitude = obj.get("magnitude", 1.0)
        try:
            magnitude = float(magnitude)
        except (TypeError, ValueError):
            magnitude = 1.0
        return str(fault_id), magnitude

    @staticmethod
    def _as_dict(raw: str) -> dict:
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# OPC-UA side — asyncua client: poll Status nodes, execute queued Commands.
# ---------------------------------------------------------------------------
class OpcuaClientLoop:
    def __init__(self, bus: MqttBus, cmd_queue: "queue.Queue[CommandJob]"):
        self.bus = bus
        self.cmd_queue = cmd_queue

    async def run(self):
        backoff = 1.0
        while True:
            try:
                async with Client(url=OPCUA_URL) as client:
                    log.info("OPC-UA connected to %s (ns=%d)", OPCUA_URL, NS)
                    backoff = 1.0
                    await self._serve(client)
            except (OSError, asyncio.TimeoutError, ua.UaError) as e:
                log.warning("OPC-UA connection lost/failed (%s) — retry in %.0fs",
                            e, backoff)
            except Exception as e:  # noqa: BLE001 — keep the loop alive
                log.warning("OPC-UA loop error (%s) — retry in %.0fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _serve(self, client: Client):
        # Resolve nodes once per connection.
        status_nodes = [
            (area, eq, tag, unit, client.get_node(ua.NodeId(node_id_for(area, eq, tag), NS)))
            for (area, eq, tag, unit) in STATUS_TAGS
        ]
        batch_nodes = [
            (tag, unit, client.get_node(ua.NodeId(batch_node_id_for(tag), NS)))
            for (tag, unit) in BATCH_TAGS
        ]
        # Line-object holds the methods (StartBatch/Stop/SetSetpoint/...).
        line_obj = client.get_node(ua.NodeId(f"{SITE}.{LINE}.Batch", NS))

        while True:
            await self._poll_and_publish(status_nodes, batch_nodes)
            await self._drain_commands(client, line_obj)
            await asyncio.sleep(POLL_SEC)

    async def _poll_and_publish(self, status_nodes, batch_nodes):
        for area, eq, tag, unit, node in status_nodes:
            try:
                dv = await node.read_data_value()
            except Exception as e:
                log.debug("read failed %s/%s/%s (%s)", area, eq, tag, e)
                continue
            value = dv.Value.Value
            quality = "GOOD" if (dv.StatusCode is None or dv.StatusCode.is_good()) else "BAD"
            self.bus.publish_status(status_topic(area, eq, tag), value, unit, quality)

        for tag, unit, node in batch_nodes:
            try:
                dv = await node.read_data_value()
            except Exception as e:
                log.debug("read failed Batch/%s (%s)", tag, e)
                continue
            value = dv.Value.Value
            quality = "GOOD" if (dv.StatusCode is None or dv.StatusCode.is_good()) else "BAD"
            self.bus.publish_status(batch_status_topic(tag), value, unit, quality)

    async def _drain_commands(self, client: Client, line_obj):
        while True:
            try:
                job = self.cmd_queue.get_nowait()
            except queue.Empty:
                return
            try:
                await self._execute(client, line_obj, job)
            except Exception as e:  # noqa: BLE001
                log.warning("command %s failed (%s)", job.kind, e)

    async def _execute(self, client: Client, line_obj, job: CommandJob):
        kind = job.kind
        a = job.args
        if kind == "method:StartBatch":
            rc = await line_obj.call_method(
                ua.NodeId(f"{SITE}.{LINE}.Batch.StartBatch", NS), a["recipeId"]
            )
            log.info("StartBatch(%s) -> %s", a["recipeId"], rc)
        elif kind == "method:Stop":
            rc = await line_obj.call_method(ua.NodeId(f"{SITE}.{LINE}.Batch.Stop", NS))
            log.info("Stop() -> %s", rc)
        elif kind == "method:SetSetpoint":
            rc = await line_obj.call_method(
                ua.NodeId(f"{SITE}.{LINE}.Batch.SetSetpoint", NS),
                a["target"], float(a["value"]),
            )
            log.info("SetSetpoint(%s, %s) -> %s", a["target"], a["value"], rc)
        elif kind == "method:TakeSample":
            rc = await line_obj.call_method(
                ua.NodeId(f"{SITE}.{LINE}.Batch.TakeSample", NS), a["sampleType"]
            )
            log.info("TakeSample(%s) -> %s", a["sampleType"], rc)
        elif kind == "method:InjectFault":
            rc = await line_obj.call_method(
                ua.NodeId(f"{SITE}.{LINE}.Batch.InjectFault", NS),
                a["faultId"], float(a["magnitude"]),
            )
            log.info("InjectFault(%s, %s) -> %s", a["faultId"], a["magnitude"], rc)
        elif kind == "method:ClearFault":
            rc = await line_obj.call_method(ua.NodeId(f"{SITE}.{LINE}.Batch.ClearFault", NS))
            log.info("ClearFault() -> %s", rc)
        else:
            log.warning("unknown command job kind %s", kind)


async def main():
    log.info(
        "opcua-uns-connector starting — OPCUA_URL=%s MQTT=%s:%s POLL_SEC=%s UNS_ROOT=%s",
        OPCUA_URL, MQTT_HOST, MQTT_PORT, POLL_SEC, UNS_ROOT,
    )
    log.info(
        "%d status tags + %d batch tags mapped to ns=%d node-ids",
        len(STATUS_TAGS), len(BATCH_TAGS), NS,
    )

    cmd_queue: "queue.Queue[CommandJob]" = queue.Queue()
    bus = MqttBus(cmd_queue)
    bus.start()  # paho loop in its own thread, auto-reconnects

    opc = OpcuaClientLoop(bus, cmd_queue)
    await opc.run()  # never returns; reconnects internally


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("shutting down")
