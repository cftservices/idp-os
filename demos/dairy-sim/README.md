# dairy-sim demo — runnable DairyPlant + live plant-mimic dashboard

Demo tool for showing the data layer during a talk. Simulates a continuous milk
line (Tank -> Separator -> Pasteuriser -> Homogeniser -> Bottler) and lets you
trigger three failure scenarios live, each telling a different data-layer story.

> Separate from the existing OPC-UA `dairy-sim/` container (which has no dashboard).
> This is a standalone visual demo. See [`SPEC.md`](SPEC.md) for the model.

## Fastest path — zero dependencies

```bash
cd sub-os/idp-os/demos/dairy-sim
python sim.py
# open http://localhost:8078
```

No Docker, no broker, no database. The dashboard is an **ISA-101 high-performance
HMI**: a calm steel plant mimic that stays grey until something goes wrong, then
the affected equipment, pipe and instrument light up.

### The three scenarios (operator station on the dashboard)

| Button | What happens | Data-layer point |
|--------|--------------|------------------|
| **Cooling fault** | Tank refrigeration weakens, milk temperature climbs toward the 6 C limit | Predictive: a warning fires *before* the cold-chain breach, so you act early |
| **Line leak** | A hidden loss is injected; every single tag still looks normal | Cross-signal: only the **mass-balance** readout (in vs out) reveals it. The layer makes the invisible visible |
| **Machine fault** | A chosen machine trips to ERROR | Event frame opens, the line cascade is visible (upstream backs up, downstream starves). Honours slide 14 "event frames" |

**Heal all** returns the plant to baseline. **Acknowledge all** clears the event log.

### Knobs

| Env var | Default | What |
|---------|---------|------|
| `PORT` | 8078 | dashboard port |
| `COOL_RATE` | 4 | cooling-scenario fast-forward (higher = temp climbs sooner). Mechanism is real; only the clock is sped up for the demo |
| `SIM_STEP` | 0.2 | seconds advanced per tick |

### Verify

```bash
python sim.py --selftest
# headless: triggers all 3 scenarios, asserts cooling fires the predictive alarm
# before the 6 C breach, leak trips the mass-balance alarm, and a fault opens an
# event frame with a downstream cascade.
```

## Canon Connect paths (optional)

### MQTT -> MonsterMQ

```bash
MQTT_HOST=localhost python sim.py
mosquitto_sub -t 'DairyPlant/#' -v
```

Publishes `DairyPlant/Receiving/Tank01/...`, `DairyPlant/Process/Pasteurizer/HTST_temp_C`,
`DairyPlant/Process/Pasteurizer/divert_valve_status`, `DairyPlant/Packaging/Bottler/...`,
matching the existing dairy topic shape. Graceful if no broker.

### OPC-UA (mirrors the existing dairy contract)

```bash
pip install asyncua
python opcua_bridge.py serve     # opc.tcp://0.0.0.0:4842/  DairyPlant.* nodes
python opcua_bridge.py read --mqtt
```

See [`OPCUA.md`](OPCUA.md). Port 4842 so it runs alongside the existing dairy-sim (4841).

## Docker

```bash
docker build -t dairy-sim-demo .
docker run -p 8078:8078 dairy-sim-demo
```

Compose entry (`dairy-sim-demo`) is in `idp-os/docker-compose.v3.yml`.

## Files

| File | What |
|------|------|
| `dashboard.html` | the ISA-101 plant-mimic HMI (SVG P&ID, scenarios, alarms, event log) |
| `sim.py` | loop + zero-dep server + scenario commands + optional MQTT + `--selftest` |
| `machines.py` | equipment classes + continuous-flow model + 3-scenario engine |
| `opcua_bridge.py` | OPC-UA serve/read on the dairy contract |
| `SPEC.md` · `OPCUA.md` | data model + OPC-UA integration |
