"""OPC-UA bridge for cornflour-sim - wires the corn-flour mill to an OPC-UA server
and client, so the workshop's CONNECT step uses a real OPC-UA hop (like a PLC)
instead of going straight to MQTT.

Designed to match the existing CIP OPC-UA server contract (Johannes' API_Project):
    endpoint   opc.tcp://localhost:4841/CipSimulation
    namespace  urn:cip:simulation  (NamespaceIndex 2)
    nodes      ns=2;s=CIP.Simulation.<TagName>   (Double, read/write, anonymous)
    mgmt REST  http://localhost:8098/tags/value?path=<TagName>  (PUT {"value": x})

Three modes:

  push   Read the running sim's /state and PUSH each machine tag into the EXISTING
         CIP C# server via its REST management API (PUT /tags/value). The C# server
         then serves the live corn-flour values on OPC-UA. Their C# client reads
         them unchanged. (Stdlib only; the corn-flour tags must be registered in the
         CIP server's appsettings.json first - see OPCUA.md for the snippet.)

  serve  Run a Python OPC-UA server (asyncua) that MIRRORS the CIP address space
         (same endpoint, namespace, node ids) and serves the corn-flour values.
         Use when the .NET server is not running. Any OPC-UA client, including the
         CIP C# test client, reads ns=2;s=CIP.Simulation.<Tag> unchanged.

  read   Run a Python OPC-UA client (asyncua) that connects to the endpoint, reads /
         subscribes the corn-flour nodes, and optionally republishes them to MQTT
         (MonsterMQ) - the CONNECT -> bus hop. Mirrors the CIP C# test client.

Usage:
    python opcua_bridge.py push  [--dry]
    python opcua_bridge.py serve
    python opcua_bridge.py read  [--mqtt]

Env:
    SIM_STATE_URL    where to read the sim from (default http://localhost:8077/state)
    CIP_MGMT_URL     CIP REST mgmt base (default http://localhost:8098)
    CIP_OPCUA_URL    OPC-UA endpoint (default opc.tcp://localhost:4841/CipSimulation)
    MQTT_HOST/PORT   for `read --mqtt` republish
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

SIM_STATE_URL = os.environ.get("SIM_STATE_URL", "http://localhost:8077/state")
CIP_MGMT_URL = os.environ.get("CIP_MGMT_URL", "http://localhost:8098").rstrip("/")
CIP_OPCUA_URL = os.environ.get("CIP_OPCUA_URL", "opc.tcp://localhost:4841/CipSimulation")
NS = 2  # CIP namespace index

# Map of OPC-UA tag name -> function extracting a float from the sim /state snapshot.
# All values are Double to match the CIP server's node DataType.
STATE_PATH = "urn:cip:simulation"

FACTORY_STATE_CODE = {"IDLE": 0.0, "RUNNING": 1.0, "COMPLETE": 2.0}


def tag_values(s: dict) -> dict[str, float]:
    m = s["machines"]
    b = s["buffers"]
    return {
        "Washer_Status": float(m["Washer"]["status"]),
        "Washer_Level": float(m["Washer"]["level_kg"]),
        "Washer_RunningHours": float(m["Washer"]["running_hours"]),
        "Dryer_Status": float(m["Dryer"]["status"]),
        "Dryer_Temperature": float(m["Dryer"]["temperature_c"]),
        "Dryer_Level": float(m["Dryer"]["level_kg"]),
        "Grinder_Status": float(m["Grinder"]["status"]),
        "Grinder_Speed": float(m["Grinder"]["speed_rpm"]),
        "Grinder_BladeWear": float(m["Grinder"]["blade_wear_pct"]),
        "Grinder_Performance": float(m["Grinder"]["performance_pct"]),
        "Grinder_Throughput": float(m["Grinder"]["throughput_kgph"]),
        "BagFiller_Status": float(m["BagFiller"]["status"]),
        "BagFiller_Bags": float(m["BagFiller"]["bags_filled"]),
        "Factory_State": FACTORY_STATE_CODE.get(s["state"], 0.0),
        "Factory_RawKg": float(b["raw_kg"] + b["washed_kg"] + b["dried_kg"]),
        "Factory_FlourKg": float(b["flour_kg"]),
    }


TAG_NAMES = list(tag_values({
    "machines": {n: {"status": 0, "level_kg": 0, "running_hours": 0, "temperature_c": 0,
                     "speed_rpm": 0, "blade_wear_pct": 0, "performance_pct": 0,
                     "throughput_kgph": 0, "bags_filled": 0}
                 for n in ("Washer", "Dryer", "Grinder", "BagFiller")},
    "buffers": {"raw_kg": 0, "washed_kg": 0, "dried_kg": 0, "flour_kg": 0},
    "state": "IDLE",
}).keys())


def fetch_state() -> dict:
    with urllib.request.urlopen(SIM_STATE_URL, timeout=5) as r:
        return json.loads(r.read())


# ----------------------------------------------------------------------------
# Mode: push  (drive the EXISTING CIP C# OPC-UA server via its REST mgmt API)
# ----------------------------------------------------------------------------
def mode_push(dry: bool):
    print(f"[push] sim={SIM_STATE_URL}  ->  CIP mgmt={CIP_MGMT_URL}/tags/value")
    if dry:
        print("[push] DRY RUN - printing mapped tags, no PUT")
    while True:
        try:
            vals = tag_values(fetch_state())
        except Exception as e:
            print(f"[push] cannot read sim ({e}); is sim.py running? retrying...")
            time.sleep(2)
            continue
        for name, value in vals.items():
            if dry:
                print(f"    {name:24} = {value:.2f}")
            else:
                _put_forced_value(name, value)
        if dry:
            print("[push] (one snapshot printed; exiting dry run)")
            return
        time.sleep(1.0)


def _put_forced_value(name: str, value: float):
    url = f"{CIP_MGMT_URL}/tags/value?path={name}"
    body = json.dumps({"value": value}).encode()
    req = urllib.request.Request(url, data=body, method="PUT",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        # tag not registered in appsettings.json, or server down
        print(f"[push] PUT {name} failed ({e}) - is it in the CIP appsettings Tags list?")


# ----------------------------------------------------------------------------
# Mode: serve  (Python OPC-UA server mirroring the CIP address space)
# ----------------------------------------------------------------------------
def mode_serve():
    try:
        import asyncio
        from asyncua import Server, ua
    except Exception:
        sys.exit("[serve] needs asyncua:  pip install asyncua")

    async def run():
        server = Server()
        await server.init()
        server.set_endpoint(CIP_OPCUA_URL)
        server.set_server_name("CornFlour OPC-UA (CIP-compatible)")
        idx = await server.register_namespace(STATE_PATH)  # -> index 2
        objects = server.nodes.objects
        cip = await objects.add_folder(ua.NodeId("CIP", idx), "CIP")
        sim = await cip.add_folder(ua.NodeId("CIP.Simulation", idx), "Simulation")
        nodes = {}
        for name in TAG_NAMES:
            node = await sim.add_variable(ua.NodeId(f"CIP.Simulation.{name}", idx), name, 0.0)
            await node.set_writable()
            nodes[name] = node
        print(f"[serve] OPC-UA up at {CIP_OPCUA_URL}  (ns={idx}, CIP.Simulation.*)")
        print(f"[serve] {len(nodes)} corn-flour tags. CIP C# client can read unchanged.")
        async with server:
            while True:
                try:
                    vals = tag_values(fetch_state())
                    for name, value in vals.items():
                        await nodes[name].write_value(float(value))
                except Exception as e:
                    print(f"[serve] sim read failed ({e}); retrying")
                await asyncio.sleep(1.0)

    asyncio.run(run())


# ----------------------------------------------------------------------------
# Mode: read  (Python OPC-UA client; optional MQTT republish = CONNECT -> bus)
# ----------------------------------------------------------------------------
def mode_read(to_mqtt: bool):
    try:
        import asyncio
        from asyncua import Client, ua
    except Exception:
        sys.exit("[read] needs asyncua:  pip install asyncua")

    mqtt = None
    if to_mqtt:
        try:
            import paho.mqtt.client as m
            host = os.environ.get("MQTT_HOST", "localhost")
            port = int(os.environ.get("MQTT_PORT", "1883"))
            mqtt = m.Client()
            mqtt.connect(host, port, 30)
            mqtt.loop_start()
            print(f"[read] republishing to MQTT {host}:{port} under TechFlow/Mill1/OPCUA/...")
        except Exception as e:
            print(f"[read] MQTT unavailable ({e}); printing only")
            mqtt = None

    async def run():
        print(f"[read] connecting OPC-UA client to {CIP_OPCUA_URL}")
        async with Client(url=CIP_OPCUA_URL) as client:
            print("[read] connected. reading CIP.Simulation.* every second (Ctrl+C to stop)")
            while True:
                line = []
                for name in TAG_NAMES:
                    node = client.get_node(ua.NodeId(f"CIP.Simulation.{name}", NS))
                    try:
                        val = await node.read_value()
                    except Exception:
                        continue
                    line.append(f"{name}={val:.1f}")
                    if mqtt:
                        mqtt.publish(f"TechFlow/Mill1/OPCUA/{name}", str(val))
                print("  " + " ".join(line[:6]) + " ...")
                await asyncio.sleep(1.0)

    asyncio.run(run())


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("push", "serve", "read"):
        print(__doc__)
        sys.exit(1)
    mode = sys.argv[1]
    if mode == "push":
        mode_push(dry="--dry" in sys.argv)
    elif mode == "serve":
        mode_serve()
    elif mode == "read":
        mode_read(to_mqtt="--mqtt" in sys.argv)


if __name__ == "__main__":
    main()
