# cornflour-sim — Corn-Flour Mill Process Simulator (SPEC)

> Demo data source for the AGS-007 two-host YouTube workshop. A software simulation
> of a 4-machine corn-flour mill that publishes asset telemetry to MQTT, so the
> full 7-step data layer can be built live without any physical PLC.
>
> Clean-room rebuild of a generic corn-flour process assignment. No third-party
> branding, screenshots, or verbatim numbers. Canon stack only: Python -> MQTT
> (MonsterMQ) -> MongoDB -> Grafana, all Docker. NO Node-RED.

## Why this exists

The workshop teaches the 7-step "Build Your AI Data Layer" framework. Every step
needs real data flowing. A physical mill is not available to viewers, so we
simulate one in Python. The sim is deliberately *honest*: a grinder whose blades
wear down over time, so the predictive-maintenance Solve test is real signal, not
a scripted fake.

Mirrors the conventions already in [`packml-sim`](../../packml-sim/README.md) and
[`dairy-sim`](../../dairy-sim/server.py): paho-mqtt publisher, ISA-95-shaped topics,
one process loop, env-driven config.

## The process

```
 Raw corn (100 kg batch)
      │
      ▼
 ┌─────────┐   ┌────────┐   ┌─────────┐   ┌────────────┐
 │ Washer  │──►│ Dryer  │──►│ Grinder │──►│ BagFiller  │──► 10 kg bags
 │ 60 kg   │   │ 30 kg  │   │ 30 kg   │   │ buffer 120 │
 └─────────┘   └────────┘   └─────────┘   └────────────┘
   removes        dries       grinds to     fills + seals
   debris/water   moisture    flour         10 kg bags
                              (~10% grounds loss)
```

- A **batch** is 100 kg of raw corn. Material flows machine to machine; each machine
  has a finite capacity, so the factory runs as a staged pipeline, not all-at-once.
- The Grinder loses ~10% as "grounds" (bran/waste), so 100 kg raw yields ~90 kg flour
  ~= 9 full 10 kg bags per batch.
- The whole factory has one master Start/Stop; each machine also has its own.

## The 4 assets (classes)

Each machine is one class with these common attributes, plus machine-specific ones.
This class list IS the asset model the workshop turns into ISA-95 in Step 3 (Model).

| Attribute | Type | All machines | Notes |
|-----------|------|:---:|-------|
| `capacity_kg` | float | yes | max material the machine holds |
| `cmd_start_stop` | bool (cmd in) | yes | subscribed command topic |
| `status` | int | yes | 0 = Stopped, 1 = Running, 2 = Error |
| `running_hours` | float | yes | cumulative, persisted across batches |
| `level_kg` | float | yes | current material in the machine |

Machine-specific attributes (straight from the process):

| Machine | Extra attributes | Behaviour |
|---------|------------------|-----------|
| **Washer** | (level only) | intake 60 kg, removes debris, passes wet corn to Dryer |
| **Dryer** | `temperature_c` | heats to ~80 C to drive off moisture; temperature ramps on start |
| **Grinder** | `speed_rpm`, `blade_wear_pct`, `throughput_kgph`, `performance_pct` | grinds to flour; **throughput decays as blade_wear rises** (the Solve story) |
| **BagFiller** | `bags_filled` (per batch + lifetime) | fills + seals 10 kg bags from the flour buffer |

### Grinder wear model (the heart of the demo)

```
blade_wear_pct grows ~linearly with grinder running_hours (configurable rate).
performance_pct   = 100 - k * blade_wear_pct           (k tuned so it visibly droops)
throughput_kgph   = nominal_throughput * performance_pct / 100
```

- New blades: `performance_pct` ~= 100, throughput nominal.
- As `blade_wear_pct` climbs, `performance_pct` and `throughput_kgph` fall. Batches
  take longer; if performance drops below a threshold (e.g. 80%) the flour starts
  going out of spec (too coarse). That threshold crossing is the Solve trigger.
- `WEAR_RATE` env var lets us **fast-forward** wear during a recording so the alarm
  fires on camera in minutes, not weeks. (Document this clearly so it is not read as
  faking the signal: the mechanism is real, only the clock is sped up.)

## Batch / factory state machine (Step 5 — Orchestrate)

A simple per-batch lifecycle, emitted as an `ad-hoc` event (Walker Reynolds namespace):

```
IDLE -> RUNNING (washer pulls 100 kg raw) -> ... staged flow ...
     -> COMPLETE (BagFiller seals last bag) -> writes batch record -> IDLE
```

Each batch produces one record: `batch_id`, `start_time`, `end_time`, `raw_kg_in`,
`flour_kg_out`, `bags_out`, `avg_grinder_performance_pct`.

## MQTT topic tree (Step 1 Connect + Step 3 Model)

Site/line/area/equipment shape, matching the existing sims so cohort members reuse
the same Grafana/Mongo patterns. Site is a fictional TechFlow demo plant.

```
TechFlow/Mill1/Milling/Washer/Status/status
TechFlow/Mill1/Milling/Washer/Status/level_kg
TechFlow/Mill1/Milling/Washer/Status/running_hours
TechFlow/Mill1/Milling/Dryer/Status/temperature_c
TechFlow/Mill1/Milling/Grinder/Status/speed_rpm
TechFlow/Mill1/Milling/Grinder/Status/blade_wear_pct
TechFlow/Mill1/Milling/Grinder/Status/throughput_kgph
TechFlow/Mill1/Milling/Grinder/Status/performance_pct
TechFlow/Mill1/Packaging/BagFiller/Status/bags_filled
TechFlow/Mill1/Plant/Factory/Status/state           # IDLE|RUNNING|COMPLETE
TechFlow/Mill1/Plant/Factory/Event/batch_complete    # ad-hoc: full batch record (JSON)

# command topics (subscribed)
TechFlow/Mill1/Milling/Washer/Command/start_stop
TechFlow/Mill1/Plant/Factory/Command/start_stop
```

Raw values publish ~1 Hz (`PUBLISH_INTERVAL=1.0`); the batch-complete event fires
once per batch.

## Storage (Step 4 — Store) — MongoDB collections

Follows the 3-layer pattern from `step-00-offer.md` Step 3:

| Collection | Layer | Content |
|------------|-------|---------|
| `staging.cornflour_raw` | Staging | 1:1 raw MQTT payloads, never mutated |
| `warehouse.signals` | Canonical | ISA-95 telemetry (enriched: unit, normal range, asset path) |
| `warehouse.assets` | Canonical | the 4 machines as asset docs (definitional metadata) |
| `marts.batches` | Mart | one doc per batch (BatchID/start/end/amount/avg performance) |
| `marts.grinder_health` | Mart | grinder performance trend + predicted blade-change date |

## Visualize (Step 6) — Grafana panels

1. **Factory overview** — current batch state, raw-material storage, bags produced (matches deck slide 9/10 intent).
2. **Equipment status** — per-machine status (Running/Stopped/Error) + running hours.
3. **Grinder performance trend** — `performance_pct` over time, with the 80% spec line. THE panel.
4. **Batch table** — `marts.batches`: BatchID / start / end / flour out / avg performance (deck slide 10 intent).

## Solve test (the payoff)

Event frame: when `performance_pct` crosses below the spec threshold (or the
projected crossing date is < N days out), raise a **maintenance heads-up**:
"Grinder blades approaching end of life, schedule a change before output drops
out of spec." Concrete Solve question (canon concreteness rule):

> "Can maintenance schedule a blade change before the grinder's output quietly
> drops below spec, instead of finding out from a bad batch?"

Solve is the *test* that the layer delivers a decision, not an 8th build step.

## Files to build (implementation, follow-up pass)

```
cornflour-sim/
├── SPEC.md            # this file
├── Dockerfile         # mirror dairy-sim/Dockerfile
├── requirements.txt   # paho-mqtt
├── server.py          # loop: tick machines, run batch SM, publish ~1Hz
├── machines.py        # Washer/Dryer/Grinder/BagFiller classes + wear model
└── README.md          # run instructions + WEAR_RATE fast-forward note
```

Env vars: `MQTT_HOST` (default monstermq), `MQTT_PORT` (1883), `PUBLISH_INTERVAL`
(1.0), `SIM_STEP` (0.2), `WEAR_RATE` (demo fast-forward multiplier), `SITE`/`LINE`
prefix overrides.

## Verification (matches plan)

```bash
# 1. stack up
cd sub-os/idp-os && docker compose -f docker-compose.v3.yml up -d monstermq mongo grafana
docker compose -f docker-compose.v3.yml up -d cornflour-sim   # after compose entry added

# 2. telemetry flowing
mosquitto_sub -h localhost -p 1883 -t 'TechFlow/Mill1/#' -v

# 3. batches landing in Mongo
#    mongosh -> use idp -> db['marts.batches'].find().sort({end_time:-1}).limit(3)

# 4. Grafana renders grinder performance trend; with high WEAR_RATE the
#    performance line droops below 80% on camera and the Solve alarm fires.
```
