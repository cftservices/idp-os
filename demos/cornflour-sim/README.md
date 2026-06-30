# cornflour-sim — runnable corn-flour mill + live dashboard

Demo data source for the AGS-007 two-host YouTube workshop. Simulates a 4-machine
corn-flour mill (Washer → Dryer → Grinder → BagFiller), produces 10 kg bags per
100 kg batch, and **wears down the grinder blades** so the predictive-maintenance
Solve test is real signal. See [`SPEC.md`](SPEC.md) for the full data model.

## Fastest path — zero dependencies

```bash
cd sub-os/idp-os/demos/cornflour-sim
python sim.py
# open http://localhost:8077
```

That is it. No Docker, no broker, no database. The dashboard polls `/state` and
shows the factory running live: the 4 machines, batch production, the grinder
performance trend with the 80% spec line, the batch history, and the maintenance
alarm when the blades wear out. Buttons: **Start factory**, **Stop**, **Replace
blades** (resets wear, clears the alarm).

### Knobs

| Env var | Default | What |
|---------|---------|------|
| `PORT` | 8077 | dashboard port |
| `WEAR_RATE` | 600 | grinder wear fast-forward. Higher = alarm fires sooner. The wear *mechanism* is real; only the clock is sped up so it fits on camera. Lower it (e.g. 150) for a slower, more realistic droop. |
| `AUTOSTART` | 1 | start the factory immediately |
| `SIM_STEP` | 0.2 | seconds advanced per tick |

```bash
WEAR_RATE=1200 python sim.py     # alarm in ~1 min (snappy demo)
WEAR_RATE=150  python sim.py     # slow, realistic droop
```

### Verify it works

```bash
python sim.py --selftest
# accelerated headless run; asserts batches complete AND the Solve alarm fires
```

## Canon Connect paths (for the real 7-step demo)

The same sim can feed the canon stack so the workshop's Connect → Distribute steps
are authentic. Both are optional and degrade gracefully.

### MQTT → MonsterMQ

```bash
MQTT_HOST=localhost MQTT_PORT=1883 python sim.py
```

Publishes ISA-95-shaped topics under `TechFlow/Mill1/...` (see SPEC.md). If
`paho-mqtt` is missing or the broker is unreachable, the sim logs it and keeps the
dashboard running.

### OPC-UA server + client

`opcua_bridge.py` exposes the machine tags through an OPC-UA server (so the workshop
can show OPC-UA as the Connect method, like a real PLC) and read them back with an
OPC-UA client into the MQTT/Mongo pipeline. Requires `asyncua`. See the file header
for endpoint + usage. (Wired to mirror Johannes' existing OPC-UA API_Project — see
that project's interface.)

## Docker

```bash
docker build -t cornflour-sim .
docker run -p 8077:8077 cornflour-sim
# or add the compose snippet below to idp-os/docker-compose.v3.yml
```

```yaml
  cornflour-sim:
    build: ./demos/cornflour-sim
    container_name: cornflour-sim
    ports: ["8077:8077"]
    environment:
      - WEAR_RATE=600
      # - MQTT_HOST=monstermq      # uncomment for the MQTT Connect path
    restart: unless-stopped
```

## Files

| File | What |
|------|------|
| `machines.py` | the 4 machine classes + batch state machine + grinder wear model (pure Python) |
| `sim.py` | loop + zero-dep dashboard server + optional MQTT publish + `--selftest` |
| `dashboard.html` | the live visual (dark theme, polls `/state`) |
| `opcua_bridge.py` | optional OPC-UA server/client Connect path (asyncua) |
| `SPEC.md` | full data model, topics, Mongo schema, Grafana panels |
