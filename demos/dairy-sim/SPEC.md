# dairy-sim demo — SPEC

Standalone visual demo of a continuous milk line, mirroring the equipment in the
existing OPC-UA `dairy-sim/` server but adding a live HMI dashboard and a 3-scenario
failure engine. Zero-dependency dashboard; optional MQTT + OPC-UA. NO Node-RED.

## The line (continuous flow)

```
 Raw milk
    │  ~1000 L/min
    ▼
 ┌────────┐   ┌───────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────┐
 │ Tank01 │──▶│ Separator │──▶│ Pasteuriser  │──▶│ Homogeniser │──▶│ Bottler  │──▶ 1 L bottles
 │ 4-6 C  │   │ 6000 rpm  │   │ HTST 72 C    │   │ 180 bar     │   │ 120/min  │
 │ cold   │   │ fat 3.5%  │   │ +divert valve│   │             │   │          │
 └────────┘   └───────────┘   └──────────────┘   └─────────────┘   └──────────┘
```

## Equipment (classes in `machines.py`)

| Equipment | Tags | Notes |
|-----------|------|-------|
| Tank01 (Receiving) | `in_temp_c`, `flow_in`, `flow_out`, `level_pct`, `cooling_health_pct`, `status` | cold store; `flow_in` vs `flow_out` is the leak signal |
| Separator | `rpm`, `fat_pct`, `status` | centrifuge |
| Pasteurizer | `htst_temp_c`, `hold_sec`, `divert_valve`, `diverted_l`, `status` | HTST; divert valve trips < 71.5 C |
| Homogenizer | `pressure_bar`, `status` | high-pressure pump |
| Bottler (Packaging) | `bottles_per_min`, `bottles_total`, `fill_volume_ml`, `reject_count`, `status` | starves on any upstream ERROR or divert |

Status: 0 Stopped, 1 Running, 2 Error.

## The 3 scenarios (engine in `machines.py`)

**1. Cooling weakens (predictive).** `cooling_health_pct` decays; `in_temp_c` rises
toward the 6 C cold-chain limit. Predictive alarm at 5.5 C ("service tank cooling")
before the 6 C breach. Story: act early.

**2. Leak (cross-signal / mass balance).** A hidden `leak_L_min` reduces `flow_out`.
Every single tag still looks plausible; only `|flow_in - flow_out|` past tolerance
(25 L/min) trips "leak detected". The dashboard shows the in/out/Δ maths so the point
lands: tags in context reveal what no single tag can. Story: why you need the layer.

**3. Machine fault (event frame).** A chosen machine -> status ERROR. An event frame
opens `{asset, start, end, duration, acknowledged}`; the cascade is simulated and
shown (faulted-and-upstream backs up the tank level, downstream rpm/pressure/bottling
starves). Clearing closes the frame into the event log. Mirrors slide 14 of the source
PowerPoint. Story: real-time awareness + event capture.

Each scenario is independently triggerable/clearable via `/command`; `heal` resets all.

## MQTT topics (Step 1 Connect)

Matches the existing dairy topic shape:

```
DairyPlant/Receiving/Tank01/in_temp_C | flow_in_L_min | flow_out_L_min
DairyPlant/Process/Separator/RPM | fat_pct
DairyPlant/Process/Pasteurizer/HTST_temp_C | divert_valve_status
DairyPlant/Process/Homogenizer/pressure_bar
DairyPlant/Packaging/Bottler/bottles_per_min | bottles_total
DairyPlant/Alarms/<id>            (cooling | leak | fault | divert)
```

## OPC-UA (Step 1 Connect, alt)

`opcua_bridge.py` mirrors the existing dairy address space: namespace
`urn:techflow:dairy-plant`, string node ids `DairyPlant.<Area>.<Equipment>.<tag>`,
endpoint `opc.tcp://0.0.0.0:4842/` (4842 to run alongside the existing 4841). See OPCUA.md.

## Dashboard (`dashboard.html`) — ISA-101 high-performance HMI

Calm steel canvas; colour reserved for abnormal states. SVG P&ID mimic with flowing
pipes, a divert valve that swings, vessel fills, and per-equipment alarm illumination.
Instrument strip: HTST temperature trend (72 C setpoint + 71.5 C divert lines), cold-chain
bar with the 6 C limit, mass-balance readout. Operator station (scenario switches) +
event-frame table. Typeface: Bahnschrift (DIN) for labels, Consolas mono for telemetry.

## API

- `GET /` dashboard · `GET /state` JSON snapshot
- `GET /command?action=start|stop|heal|ack`
- `GET /command?action=scenario&type=cooling|leak|fault[&asset=Separator]`
- `GET /command?action=clear&type=cooling|leak|fault`
