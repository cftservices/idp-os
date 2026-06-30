# OPC-UA Connect path — dairy-sim demo

The demo can take a real OPC-UA hop (like a PLC) before the bus. `opcua_bridge.py`
mirrors the **existing dairy OPC-UA contract** ([`../../dairy-sim/server.py`](../../dairy-sim/server.py)),
so it stays consistent with the dairy tooling already in the stack.

## The contract (matched)

| Thing | Value |
|-------|-------|
| Namespace | `urn:techflow:dairy-plant` |
| Node ids | string, `DairyPlant.<Area>.<Equipment>.<tag>` (e.g. `DairyPlant.Process.Pasteurizer.HTST_temp_C`) |
| Endpoint | existing server: `opc.tcp://0.0.0.0:4841/` · this bridge: `opc.tcp://0.0.0.0:4842/` (alongside) |
| Security | anonymous, none |

## Two modes (need `asyncua`)

```bash
pip install asyncua
python sim.py                      # terminal 1: the demo + dashboard
```

### serve — Python OPC-UA server, dairy address space

```bash
python opcua_bridge.py serve       # opc.tcp://0.0.0.0:4842/  DairyPlant.* nodes
```

Exposes the demo's live values under `DairyPlant.<Area>.<Equipment>.<tag>`, so any
OPC-UA client (including the existing dairy tooling) reads them unchanged.

### read — Python OPC-UA client -> MQTT (the CONNECT -> bus hop)

```bash
python opcua_bridge.py read --mqtt   # republish to DairyPlant/.../OPCUA
python opcua_bridge.py read          # print-only
```

Connects to the endpoint, reads the `DairyPlant.*` nodes, and optionally republishes
to MonsterMQ. This is the on-camera "OPC-UA onto the semantic bus" moment.

## Node map (sim -> OPC-UA)

| Node (`DairyPlant.*`) | sim source |
|---|---|
| `Receiving.Tank01.in_temp_C` / `flow_in_L_min` / `flow_out_L_min` / `level_pct` | tank |
| `Process.Separator.RPM` / `fat_pct` | separator |
| `Process.Pasteurizer.HTST_temp_C` / `divert_valve` (1/0) | pasteuriser |
| `Process.Homogenizer.pressure_bar` | homogeniser |
| `Packaging.Bottler.bottles_per_min` / `bottles_total` | bottler |

All `Float`, matching the existing server's variable types.

## Run alongside the existing dairy-sim

The existing OPC-UA `dairy-sim` server binds 4841 and generates its own signals. This
bridge binds 4842 and serves the demo's *scenario-driven* values. Run the one you want
to read from; the node paths are identical, so a client only changes the port.
