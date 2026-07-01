"""DairyWorks OPC-UA server — PRIMARY external facade over the MQTT sim bus.

The packml-sim units (libremfg PackML, MQTT-only) publish Status and accept
Command over MQTT via MonsterMQ (the internal bus). This facade makes the
whole factory available over OPC-UA — both directions:

  READ  : subscribe MonsterMQ  DairyWorks/#/Status/#  ->  mirror each value
          into an OPC-UA read node  ns=2;s=DairyWorks.<Area>.<Equipment>.<tag>

  WRITE : each equipment exposes OPC-UA methods
             Start(), Stop(), Reset(), Hold(), Unhold(),
             SetMachSpeed(Double), InjectFault(String,Double), ClearFault()
          -> the method publishes the matching MQTT Command on MonsterMQ
             (DairyWorks/Plant/<Area>/<Equipment>/Command/<cmd>)  -> sim reacts.

So the whole demo is driven and observed over OPC-UA, while MQTT stays the
internal transport. Address space is built from the factory-model.json.

Env:
    FACTORY_MODEL   path to isa95-dairyworks.json (default ./factory-model/isa95-dairyworks.json)
    OPCUA_ENDPOINT  default opc.tcp://0.0.0.0:4840/DairyWorks
    MQTT_HOST       default monstermq
    MQTT_PORT       default 1883
    MQTT_USERNAME / MQTT_PASSWORD  optional
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading

import paho.mqtt.client as mqtt
from asyncua import Server, ua
from asyncua.common.methods import uamethod

log = logging.getLogger("opcua-facade")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s", datefmt="%H:%M:%S")

NS_URI = "urn:dairyworks"
SITE = "DairyWorks"
LINE = "Plant"

FACTORY_MODEL = os.environ.get("FACTORY_MODEL", "./factory-model/isa95-dairyworks.json")
ENDPOINT = os.environ.get("OPCUA_ENDPOINT", "opc.tcp://0.0.0.0:4840/DairyWorks")
MQTT_HOST = os.environ.get("MQTT_HOST", "monstermq")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USERNAME") or None
MQTT_PASS = os.environ.get("MQTT_PASSWORD") or None


def load_units(model_path: str):
    """Return list of (area, equipment, [tag,...]) from the factory model."""
    with open(model_path, encoding="utf-8") as f:
        model = json.load(f)
    units = []
    for area in model["enterprise"]["sites"][0]["areas"]:
        an = area["name"]
        for wc in area["work_centers"]:
            eq = wc["equipment_id"]
            tags = [t["mqtt_topic"].split("/Status/", 1)[1] for t in wc.get("tags", [])]
            units.append((an, eq, tags))
    return units


class MqttBus:
    """Thin MQTT wrapper: caches latest Status values, publishes Commands."""

    def __init__(self):
        self.cache: dict[str, str] = {}          # "Area/eq/tag" -> value string
        self.lock = threading.Lock()
        self.client = mqtt.Client(client_id="opcua-facade")
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.connected = False

    def start(self):
        try:
            self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
            self.client.loop_start()
        except Exception as e:
            log.warning("MQTT connect failed (%s) — running without live bus", e)

    def _on_connect(self, client, userdata, flags, rc, *args):
        self.connected = True
        client.subscribe(f"{SITE}/#", qos=0)
        log.info("MQTT connected %s:%s — subscribed %s/#", MQTT_HOST, MQTT_PORT, SITE)

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        # DairyWorks/Plant/<Area>/<eq>/Status/<tag...>
        if len(parts) >= 6 and parts[0] == SITE and parts[4] == "Status":
            area, eq = parts[2], parts[3]
            tag = "/".join(parts[5:])
            with self.lock:
                self.cache[f"{area}/{eq}/{tag}"] = msg.payload.decode("utf-8", "replace")

    def get(self, area, eq, tag):
        with self.lock:
            return self.cache.get(f"{area}/{eq}/{tag}")

    def publish_command(self, area, eq, cmd, payload):
        topic = f"{SITE}/{LINE}/{area}/{eq}/Command/{cmd}"
        self.client.publish(topic, payload, qos=1)
        log.info("CMD -> %s = %s", topic, payload)


async def build(server: Server, idx: int, bus: MqttBus, units):
    objects = server.nodes.objects
    root = await objects.add_folder(ua.NodeId(f"{SITE}", idx), SITE)
    read_nodes = {}   # (area,eq,tag) -> node
    areas = {}
    for area, eq, tags in units:
        if area not in areas:
            areas[area] = await root.add_folder(ua.NodeId(f"{SITE}.{area}", idx), area)
        eqfolder = await areas[area].add_folder(ua.NodeId(f"{SITE}.{area}.{eq}", idx), eq)

        for tag in tags:
            nid = ua.NodeId(f"{SITE}.{area}.{eq}.{tag}", idx)
            node = await eqfolder.add_variable(nid, tag, "")
            read_nodes[(area, eq, tag)] = node

        # --- command methods (publish MQTT Command) ---
        def make(area, eq):
            @uamethod
            def _start(parent): bus.publish_command(area, eq, "Start", "1")
            @uamethod
            def _stop(parent): bus.publish_command(area, eq, "Stop", "1")
            @uamethod
            def _reset(parent): bus.publish_command(area, eq, "Reset", "1")
            @uamethod
            def _hold(parent): bus.publish_command(area, eq, "Hold", "1")
            @uamethod
            def _unhold(parent): bus.publish_command(area, eq, "Unhold", "1")
            @uamethod
            def _speed(parent, speed): bus.publish_command(area, eq, "MachSpeed", str(float(speed)))
            @uamethod
            def _inject(parent, fault, magnitude):
                bus.publish_command(area, eq, "Fault/Inject",
                                    json.dumps({"fault": str(fault), "magnitude": float(magnitude)}))
            @uamethod
            def _clear(parent): bus.publish_command(area, eq, "Fault/Clear", "1")
            return _start, _stop, _reset, _hold, _unhold, _speed, _inject, _clear

        s, st, rs, hd, uh, sp, inj, clr = make(area, eq)
        base = f"{SITE}.{area}.{eq}"
        await eqfolder.add_method(ua.NodeId(base + ".Start", idx), "Start", s, [], [])
        await eqfolder.add_method(ua.NodeId(base + ".Stop", idx), "Stop", st, [], [])
        await eqfolder.add_method(ua.NodeId(base + ".Reset", idx), "Reset", rs, [], [])
        await eqfolder.add_method(ua.NodeId(base + ".Hold", idx), "Hold", hd, [], [])
        await eqfolder.add_method(ua.NodeId(base + ".Unhold", idx), "Unhold", uh, [], [])
        await eqfolder.add_method(ua.NodeId(base + ".SetMachSpeed", idx), "SetMachSpeed", sp,
                                  [ua.VariantType.Double], [])
        await eqfolder.add_method(ua.NodeId(base + ".InjectFault", idx), "InjectFault", inj,
                                  [ua.VariantType.String, ua.VariantType.Double], [])
        await eqfolder.add_method(ua.NodeId(base + ".ClearFault", idx), "ClearFault", clr, [], [])
    return read_nodes


async def main():
    units = load_units(FACTORY_MODEL)
    log.info("Loaded %d units from %s", len(units), FACTORY_MODEL)

    bus = MqttBus()
    bus.start()

    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)
    server.set_server_name("DairyWorks OPC-UA Facade")
    idx = await server.register_namespace(NS_URI)
    read_nodes = await build(server, idx, bus, units)

    log.info("OPC-UA facade up at %s (ns=%d) — %d read-nodes, methods per equipment",
             ENDPOINT, idx, len(read_nodes))
    async with server:
        while True:
            for (area, eq, tag), node in read_nodes.items():
                val = bus.get(area, eq, tag)
                if val is not None:
                    try:
                        await node.write_value(val)
                    except Exception:
                        pass
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
