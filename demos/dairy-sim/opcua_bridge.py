"""OPC-UA bridge for the dairy-sim demo.

Mirrors the EXISTING dairy OPC-UA contract (sub-os/idp-os/dairy-sim/server.py):
    namespace  urn:techflow:dairy-plant
    nodes      DairyPlant.<Area>.<Equipment>.<tag>   (string NodeIds)
    endpoint   opc.tcp://0.0.0.0:4841/   (this bridge uses 4842 to run alongside it)

Two modes (needs `asyncua`):

  serve  Run a Python OPC-UA server that exposes the demo's live values under the
         same DairyPlant.* address space, so any OPC-UA client (incl. the existing
         dairy tooling) reads it unchanged. Default endpoint opc.tcp://0.0.0.0:4842/.

  read   Run an OPC-UA client that connects to the endpoint, reads the DairyPlant.*
         nodes, and optionally republishes them to MQTT (the CONNECT -> bus hop).

Usage:
    python opcua_bridge.py serve
    python opcua_bridge.py read [--mqtt]

Env:
    SIM_STATE_URL    sim /state (default http://localhost:8078/state)
    DAIRY_OPCUA_URL  endpoint (default opc.tcp://0.0.0.0:4842/)
    MQTT_HOST/PORT   for `read --mqtt`
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

SIM_STATE_URL = os.environ.get("SIM_STATE_URL", "http://localhost:8078/state")
DAIRY_OPCUA_URL = os.environ.get("DAIRY_OPCUA_URL", "opc.tcp://0.0.0.0:4842/")
NS_URI = "urn:techflow:dairy-plant"

# (node_id string, extractor) — node ids match the existing dairy-sim server.
def tag_values(s: dict) -> dict[str, float]:
    e = s["equipment"]
    return {
        "DairyPlant.Receiving.Tank01.in_temp_C": float(e["Tank01"]["in_temp_c"]),
        "DairyPlant.Receiving.Tank01.flow_in_L_min": float(e["Tank01"]["flow_in"]),
        "DairyPlant.Receiving.Tank01.flow_out_L_min": float(e["Tank01"]["flow_out"]),
        "DairyPlant.Receiving.Tank01.level_pct": float(e["Tank01"]["level_pct"]),
        "DairyPlant.Process.Separator.RPM": float(e["Separator"]["rpm"]),
        "DairyPlant.Process.Separator.fat_pct": float(e["Separator"]["fat_pct"]),
        "DairyPlant.Process.Pasteurizer.HTST_temp_C": float(e["Pasteurizer"]["htst_temp_c"]),
        "DairyPlant.Process.Pasteurizer.divert_valve": 1.0 if e["Pasteurizer"]["divert_valve"] else 0.0,
        "DairyPlant.Process.Homogenizer.pressure_bar": float(e["Homogenizer"]["pressure_bar"]),
        "DairyPlant.Packaging.Bottler.bottles_per_min": float(e["Bottler"]["bottles_per_min"]),
        "DairyPlant.Packaging.Bottler.bottles_total": float(e["Bottler"]["bottles_total"]),
    }


TAG_IDS = list(tag_values({"equipment": {
    "Tank01": {"in_temp_c": 0, "flow_in": 0, "flow_out": 0, "level_pct": 0},
    "Separator": {"rpm": 0, "fat_pct": 0},
    "Pasteurizer": {"htst_temp_c": 0, "divert_valve": False},
    "Homogenizer": {"pressure_bar": 0},
    "Bottler": {"bottles_per_min": 0, "bottles_total": 0},
}}).keys())


def fetch_state() -> dict:
    with urllib.request.urlopen(SIM_STATE_URL, timeout=5) as r:
        return json.loads(r.read())


def mode_serve():
    try:
        import asyncio
        from asyncua import Server, ua
    except Exception:
        sys.exit("[serve] needs asyncua:  pip install asyncua")

    async def run():
        server = Server()
        await server.init()
        server.set_endpoint(DAIRY_OPCUA_URL)
        server.set_server_name("DairyPlant demo OPC-UA (mirror)")
        idx = await server.register_namespace(NS_URI)
        objs = server.nodes.objects

        # build the DairyPlant.<Area>.<Equipment> object tree, then variables
        folders: dict[str, object] = {}
        nodes = {}
        for tag in TAG_IDS:
            parts = tag.split(".")          # [DairyPlant, Area, Equipment, tag]
            for i in range(1, len(parts)):  # ensure each folder level exists
                path = ".".join(parts[:i])
                if path not in folders:
                    parent = folders.get(".".join(parts[:i - 1]), objs)
                    folders[path] = await parent.add_object(
                        ua.NodeId(path, idx, ua.NodeIdType.String),
                        ua.QualifiedName(parts[i - 1], idx))
            equip_path = ".".join(parts[:-1])
            var = await folders[equip_path].add_variable(
                ua.NodeId(tag, idx, ua.NodeIdType.String),
                ua.QualifiedName(parts[-1], idx),
                ua.Variant(0.0, ua.VariantType.Float))
            await var.set_writable()
            nodes[tag] = var

        print(f"[serve] DairyPlant OPC-UA up at {DAIRY_OPCUA_URL}  (ns={idx})")
        print(f"[serve] {len(nodes)} nodes under DairyPlant.* — read with any OPC-UA client")
        async with server:
            while True:
                try:
                    for tag, val in tag_values(fetch_state()).items():
                        await nodes[tag].write_value(ua.Variant(float(val), ua.VariantType.Float))
                except Exception as ex:
                    print(f"[serve] sim read failed ({ex}); retrying")
                await asyncio.sleep(1.0)

    asyncio.run(run())


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
            print(f"[read] republishing to MQTT {host}:{port} (DairyPlant/.../OPCUA)")
        except Exception as ex:
            print(f"[read] MQTT unavailable ({ex}); printing only")
            mqtt = None

    endpoint = DAIRY_OPCUA_URL.replace("0.0.0.0", "localhost")

    async def run():
        print(f"[read] connecting OPC-UA client to {endpoint}")
        async with Client(url=endpoint) as client:
            ns = await client.get_namespace_index(NS_URI)
            print(f"[read] connected (ns={ns}); reading DairyPlant.* every second")
            while True:
                line = []
                for tag in TAG_IDS:
                    node = client.get_node(ua.NodeId(tag, ns, ua.NodeIdType.String))
                    try:
                        val = await node.read_value()
                    except Exception:
                        continue
                    short = tag.split(".")[-1]
                    line.append(f"{short}={val:.1f}")
                    if mqtt:
                        mqtt.publish("DairyPlant/" + tag.replace("DairyPlant.", "").replace(".", "/") + "/OPCUA", str(val))
                print("  " + " ".join(line[:6]) + " ...")
                await asyncio.sleep(1.0)

    asyncio.run(run())


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("serve", "read"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "serve":
        mode_serve()
    else:
        mode_read(to_mqtt="--mqtt" in sys.argv)


if __name__ == "__main__":
    main()
