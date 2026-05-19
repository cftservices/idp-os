# packml-sim — Combined Process Simulator

> PackML ISA-88 state machine + scenario-specific physics modules, publishing
> directly to MQTT. One Docker image, many configurations.

Replaces the original "PackML + Sim3Tanks" plan from
[`docker-compose.v3.yml:431-475`](../docker-compose.v3.yml). Sim3Tanks'
3-tank physics is replaced by bakery + dairy physics modules — same
PackML state machine, same MQTT topic shape (libremfg-compatible),
but actually relevant to the demo scenarios.

## Architecture

```
┌─────────────────────────┐    SIM_STEP=0.2s    ┌──────────────────────┐
│ packml.state_machine    │ ◄────tick──────────►│ physics.<module>     │
│ ISA-88 states + cmds    │                     │ thermal/batch/flow   │
│ MachSpeed setpoint      │ ──reads state──────►│ produces tag values  │
└────────────┬────────────┘                     └──────────┬───────────┘
             │ status                                       │ read()
             ▼                              every PUBLISH_INTERVAL=1.0s
┌────────────────────────────────────────────────────────────────────┐
│ mqtt.publisher → monstermq:1883                                     │
│   {site}/{line}/{area}/{equipment}/Status/{tag}     (e.g. zone-3/temperature) │
│   {site}/{line}/{area}/{equipment}/Status/StateCurrentStr            │
│   subscribed to: .../Command/#                                      │
└────────────────────────────────────────────────────────────────────┘
```

One container = one piece of equipment. Behaviour driven by `UNIT_CONFIG`
env var (path to YAML mounted at `/scenarios/<scenario>/<unit>.yaml`).

## Folder layout

```
packml-sim/
├── Dockerfile
├── requirements.txt
├── server.py                  # entrypoint — loads YAML + runs loop
├── packml/
│   ├── state_machine.py       # ISA-88 PackML SM (libremfg-compatible state codes)
│   └── faults.py              # f1/f2/f8/f12/f13 fault registry
├── mqtt/
│   └── publisher.py           # paho-mqtt + UNS topic builder
├── physics/                   # 13 physics modules
│   ├── base.py                # PhysicsBase + registry decorator
│   ├── batch_mixer.py         (bakery)
│   ├── bulk_fermenter.py      (bakery — batch, 45-90 min)
│   ├── former.py              (bakery — continuous scaler)
│   ├── proofer.py             (bakery — Solve-C dwell-overshoot)
│   ├── tunnel_oven.py         (bakery — Solve-A drift canvas)
│   ├── spiral_cooler.py       (bakery)
│   ├── packaging_line.py      (bakery)
│   ├── cip_station.py         (bakery — Solve-B allergen guardrail)
│   ├── storage_tank.py        (dairy + bakery silos)
│   ├── separator.py           (dairy)
│   ├── pasteurizer.py         (dairy — HTST + divert safety)
│   ├── homogenizer.py         (dairy)
│   └── bottler.py             (dairy)
└── scenarios/
    ├── bakery/                # 20 unit configs: line-a (7) + line-b (8) + silos (5)
    │   ├── mixer-line-{a,b}.yaml
    │   ├── bulk-ferm-line-{a,b}.yaml
    │   ├── former-line-{a,b}.yaml
    │   ├── proofer-line-{a,b}.yaml
    │   ├── oven-line-{a,b}.yaml
    │   ├── cooler-line-{a,b}.yaml
    │   ├── packaging-line-{a,b}.yaml
    │   ├── cip-line-b.yaml
    │   └── silo-{flour-wheat,flour-glutenfree,sugar,salt,fat}.yaml
    └── dairy/                 # 5 unit configs
        ├── receiving-tank-01.yaml
        ├── separator-01.yaml
        ├── pasteurizer-01.yaml
        ├── homogenizer-01.yaml
        └── bottler-01.yaml
```

## Run

Scenarios live in their own folders and use the shared `packml-sim/` image.

```bash
cd c:/tools/techflow-os/sub-os/idp-os

# DairyPlant — 5 sims, alongside legacy dairy-sim/ (OPC-UA, port 4841)
docker compose -f docker-compose.yml \
               -f scenarios/dairy-plant/docker-compose.dairy.yml \
               up -d --build

# BakeryWorks Utrecht — Mini (3 sims: mixer + oven + packaging line-a)
docker compose -f docker-compose.yml \
               -f scenarios/bakery-works-utrecht/docker-compose.bakery.yml \
               up -d --build \
               bakery-mixer-line-a bakery-oven-line-a bakery-packaging-line-a

# BakeryWorks Utrecht — Full (20 sims: line-a + line-b × 7 stations + 5 silos + CIP)
docker compose -f docker-compose.yml \
               -f scenarios/bakery-works-utrecht/docker-compose.bakery.yml \
               up -d --build

# Everything (dairy + bakery Full, ~25 sims) — needs ~2 GB RAM
docker compose -f docker-compose.yml \
               -f scenarios/dairy-plant/docker-compose.dairy.yml \
               -f scenarios/bakery-works-utrecht/docker-compose.bakery.yml \
               up -d --build
```

## Bakery Full topology

| Tier | Containers | Stations |
|------|------------|----------|
| **Line A — witbrood (60%)** | 7 | mixer · bulk-ferm · former · proofer · oven *(Solve-A drift)* · cooler · packaging |
| **Line B — volkoren + specialty (40%)** | 8 | mixer · bulk-ferm · former · proofer · oven · cooler · packaging · CIP *(Solve-B guardrail)* |
| **Shared silos** | 5 | flour-wheat · flour-glutenfree · sugar · salt · fat |
| **One-shot importers** | 2 | grafana-import · n8n-import |
| **Total** | **22 services** | — |

Each sim is ~96 MB RAM-capped, so Full footprint = ~2 GB. On the €8/mo VPS
(2 GB RAM) this is tight — recommend running Mini in production and Full
only for cohort/workshop demos on a beefier VPS.

### Solve event triggers

| Solve | Source unit | Trigger |
|-------|-------------|---------|
| **A** Oven zone-3 drift | `bakery-oven-line-a` | scheduled `drift_event` fires 10 min after container start (injects fault f12 magnitude 0.4 on zone-3) |
| **B** Allergen-CIP guardrail | `bakery-cip-line-b` | `cycle-stale-min` accumulates while idle; N8N workflow checks before allowing glutenfree recipe-switch |
| **C** Proofing overshoot | `bakery-proofer-line-a` / `line-b` | belt slow-down (fault f13) → `dwell-overshoot-min` climbs → planner workflow |

## Grafana dashboards

The PackML topics flow:
```
packml-sim → monstermq:1883 (MQTT) → MonsterMQ archive → MongoDB → FastAPI → Grafana
```

FastAPI has new endpoints (added in v1.1.0) that query the MonsterMQ
archive collections directly. **Rebuild FastAPI** before the dashboards
will show data:

```bash
docker compose up -d --build fastapi
```

Endpoint quick-ref:
- `GET /archive/dairy/topics` — list all DairyPlant topics seen
- `GET /archive/dairy/latest?topic=<topic>` — latest value
- `GET /archive/dairy/history?topic=<topic>&minutes=15` — time-series
- `GET /archive/dairy/snapshot?prefix=DairyPlant/` — latest value per topic
- (replace `dairy` with `bakery` or `plc` for the other scopes)

Provisioned dashboards live in `grafana/dashboards/` and auto-load:
- [`dairy-packml-overview.json`](../grafana/dashboards/dairy-packml-overview.json)
  — 5-unit overview, HTST temperature with 71.5°C threshold line,
    divert-valve state, separator RPM + fat%, homogenizer pressure,
    tank level/temp, bottler throughput + rejects + fill mL.
- [`bakery-packml-overview.json`](../grafana/dashboards/bakery-packml-overview.json)
  — Solve-A/B/C canvases up top, 4-zone oven temperatures with
    zone-3 highlighted (Solve-A drift), CIP cycle-stale-min with 60
    min threshold (Solve-B), proofer dwell-overshoot per line
    (Solve-C), line-A vs line-B throughput comparison, 5 silo gauges,
    ferm/proof climate.

Open at: `grafana.techflow24.com` (prod) or `http://localhost:3000` (dev),
folder "Industrial Data Platform" → "DairyPlant — PackML Overview".

> **Known config quirk:** datasource [`grafana/provisioning/datasources/mongodb.yml`](../grafana/provisioning/datasources/mongodb.yml)
> points at `http://fastapi-idp:8000` but `docker-compose.yml` uses
> container name `fastapi`. If panels show "Bad Gateway", update the
> datasource URL to `http://fastapi:8000` (matches the actual hostname).

## Verify

```bash
# Subscribe to all topics for one scenario
docker compose exec monstermq sh -c "apk add mosquitto-clients 2>/dev/null; \
  mosquitto_sub -h localhost -t 'bakery-works-utrecht/#' -v"

# Or via MonsterMQ web UI
open http://localhost:4000

# In MongoDB — bakery_data archive group writes here
docker compose exec mongo mongosh \
  --quiet --username admin --password changeme --authenticationDatabase admin \
  idp --eval 'db.bakery_data.find().limit(5).toArray()'
```

## Commands

Send standard PackML commands to any unit by publishing to its Command
topic. Payload `"1"` is conventional but any non-empty value works.

```bash
# Force-stop the oven
mosquitto_pub -h localhost -t \
  bakery-works-utrecht/line-a/baking/tunnel-oven-01/Command/Stop -m 1

# Restart it
mosquitto_pub -h localhost -t \
  bakery-works-utrecht/line-a/baking/tunnel-oven-01/Command/Reset -m 1
mosquitto_pub -h localhost -t \
  bakery-works-utrecht/line-a/baking/tunnel-oven-01/Command/Start -m 1

# Change belt-speed setpoint (0-120, percent of design speed)
mosquitto_pub -h localhost -t \
  bakery-works-utrecht/line-a/baking/tunnel-oven-01/Command/MachSpeed -m 75

# Trigger the Solve-A drift event manually (zone-3 heater loss)
mosquitto_pub -h localhost -t \
  bakery-works-utrecht/line-a/baking/tunnel-oven-01/Command/Fault/Inject \
  -m '{"fault":"f12","magnitude":0.4}'

# Clear all faults
mosquitto_pub -h localhost -t \
  bakery-works-utrecht/line-a/baking/tunnel-oven-01/Command/Fault/Clear -m 1

# DairyPlant HTST — trigger sensor bias making temp read high
mosquitto_pub -h localhost -t \
  DairyPlant/Process/Pasteurizer/HTST-01/Command/Fault/Inject \
  -m '{"fault":"f12","magnitude":0.6}'
# → HTST temp droops below 71.5 → divert valve trips → SM auto-Aborts
```

## Fault codes

Standard across all physics modules (subset relevant to each):

| Code | Name              | Effect                                          |
|------|-------------------|-------------------------------------------------|
| f1   | sensor bias       | reading offset on PV                            |
| f2   | sensor drift      | slow drift over time                            |
| f8   | actuator clogged  | flow / output rate -50…70%                      |
| f12  | heater / cooling loss | setpoint not reached (heater for oven/HTST, cooler for tank) |
| f13  | motor slip        | speed below setpoint, reject rate up            |

## Adding a new physics module

1. Create `physics/<your_module>.py` subclassing `PhysicsBase`
2. Decorate with `@PhysicsRegistry.register("kebab-name")`
3. Import in `physics/__init__.py`
4. Write a unit YAML under `scenarios/<scenario>/`
5. Add a service block to the relevant `docker-compose.<scenario>.yml`

The PackML state machine, MQTT publisher, command-routing, and fault
infrastructure are all reused — physics module just implements
`step(dt)` and `read() -> dict`.

## Why not vanilla PackML+Sim3Tanks?

Sim3Tanks' 3-tank physics (h1/h2/h3 levels, Qp1 pump) is well-suited
for water-process plants. The bakery is thermal+batch+conveyor, the
dairy is thermal+rotational+pressure. Keeping the PackML state machine
(universal) but writing domain-specific physics gives ~5x more
demo-credible tag values for ~2x the work versus shoe-horning every
work-center into a tank model.

The PackML state numeric codes match libremfg's spec, so downstream
consumers (Grafana panels, training material) using standard PackML
state tables work unchanged.
