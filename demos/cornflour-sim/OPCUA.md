# OPC-UA Connect path — corn-flour mill through an OPC-UA server + client

The workshop's CONNECT step is more convincing when the data takes a real OPC-UA
hop (like a PLC) before hitting the bus, instead of going straight to MQTT. This
demo reuses Johannes' existing **CIP OPC-UA server + client** (the .NET
`API_Project`) so the Connect step uses production-grade tooling he already built.

> **Publishing note (STRICT anonymisation):** the CIP server is employer / client
> work (ICT Group · Circle Infra Partners). For the public video, refer to it
> generically as "an OPC-UA server I built" — no client name, no `CipSimulation`
> endpoint on screen, no internal hostnames. The local demo can use the real thing;
> the published artefacts stay clean. Use the Python `serve` mode on camera if you
> want a fully neutral endpoint.

## The CIP server contract (what we match)

| Thing | Value |
|-------|-------|
| Endpoint | `opc.tcp://localhost:4841/CipSimulation` |
| Namespace | `urn:cip:simulation` (NamespaceIndex **2**) |
| Node ids | `ns=2;s=CIP.Simulation.<TagName>` |
| Data type | `Double`, scalar, read/write, **anonymous** (SecurityMode.None) |
| Mgmt REST | `http://localhost:8098/tags/value?path=<TagName>` (PUT `{"value": x}`), `GET /tags` |
| Tag config | `tagwriter/CipOpcTagWriter/appsettings.json` -> `OpcUaServer:Tags[]` |

## Three ways to wire it (`opcua_bridge.py`)

All three pull from the running sim's single source of truth (`/state`).

### 1. `push` — drive the real CIP C# server (recommended, uses your server AND client)

The sim's live machine values are PUT into the CIP server via its REST management
API. The C# server then serves those values on OPC-UA, and your C# test client
reads them unchanged.

```bash
# terminal 1: the mill
python sim.py
# terminal 2: the CIP .NET server (from API_Project)
#   cd API_Project/tagwriter/CipOpcTagWriter ; dotnet run
# terminal 3: push corn-flour values into the CIP server
python opcua_bridge.py push
```

**One-time:** the CIP server only creates nodes for tags listed in its
`appsettings.json`. Add the corn-flour tags below to `OpcUaServer:Tags` (the
`Signal` is ignored once we force values via REST, but a field is required):

```jsonc
// API_Project/tagwriter/CipOpcTagWriter/appsettings.json -> OpcUaServer.Tags
{ "Name": "Washer_Status",        "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Washer_Level",         "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Washer_RunningHours",  "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Dryer_Status",         "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Dryer_Temperature",    "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Dryer_Level",          "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Grinder_Status",       "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Grinder_Speed",        "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Grinder_BladeWear",    "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Grinder_Performance",  "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Grinder_Throughput",   "Signal": "Constant", "Amplitude": 0 },
{ "Name": "BagFiller_Status",     "Signal": "Constant", "Amplitude": 0 },
{ "Name": "BagFiller_Bags",       "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Factory_State",        "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Factory_RawKg",        "Signal": "Constant", "Amplitude": 0 },
{ "Name": "Factory_FlourKg",      "Signal": "Constant", "Amplitude": 0 }
```

Preview the mapping without touching the server:

```bash
python opcua_bridge.py push --dry
```

### 2. `serve` — Python OPC-UA server, same contract (no .NET needed / neutral endpoint)

Runs an `asyncua` server that mirrors the CIP address space exactly, so your CIP C#
test client (or any OPC-UA client) reads `ns=2;s=CIP.Simulation.*` unchanged. Use
this when the .NET server is not running, or for an anonymous endpoint on camera.

```bash
pip install asyncua
python sim.py            # terminal 1
python opcua_bridge.py serve   # terminal 2  -> opc.tcp://localhost:4841/CipSimulation
```

### 3. `read` — Python OPC-UA client -> MQTT (the CONNECT -> bus hop)

An `asyncua` client connects to the endpoint, reads/subscribes the corn-flour nodes,
and (optionally) republishes them to MonsterMQ. This is the on-camera "data goes
from OPC-UA onto the semantic bus" moment, and it mirrors your CIP C# test client.

```bash
pip install asyncua paho-mqtt
python opcua_bridge.py read --mqtt     # republishes to TechFlow/Mill1/OPCUA/<tag>
python opcua_bridge.py read            # print-only
```

## Tag map (sim -> OPC-UA)

| OPC-UA node (`CIP.Simulation.*`) | sim source | unit |
|---|---|---|
| `Washer_Status` / `Dryer_Status` / `Grinder_Status` / `BagFiller_Status` | machine status | 0 stop / 1 run / 2 error |
| `Washer_Level` / `Dryer_Level` | machine level | kg |
| `*_RunningHours` | running hours | h |
| `Dryer_Temperature` | dryer temperature | °C |
| `Grinder_Speed` | grinder speed | rpm |
| `Grinder_BladeWear` | blade wear | % |
| `Grinder_Performance` | grinder performance | % (the Solve signal) |
| `Grinder_Throughput` | grinder throughput | kg/h |
| `BagFiller_Bags` | lifetime bags | count |
| `Factory_State` | batch state | 0 idle / 1 running / 2 complete |
| `Factory_RawKg` / `Factory_FlourKg` | line buffers | kg |

## How it fits the 7 steps on camera

```
cornflour-sim  --(push REST / asyncua write)-->  OPC-UA server (CIP or Python)
                                                       |
        OPC-UA client (your CIP C# client, or `read`) reads ns=2;s=CIP.Simulation.*
                                                       |
                              --(republish)-->  MQTT (MonsterMQ)  = CONNECT done
                                                       |
                         Condition -> Model -> Store -> ... -> Solve (grinder alarm)
```

This is exactly the CONNECT decision in canon: OPC-UA is one of the three input
methods (MQTT / OPC-UA / REST). Here the mill speaks OPC-UA like a real PLC, an
OPC-UA client lands it on the bus, and the rest of the layer is built on top.
