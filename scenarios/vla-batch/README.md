# Vla Batch v2 demo — the missing AI data layer, end-to-end

> Generic anonymized batch-dairy plant **"Vla"** with **two lines and five end products**
> (1L packs). A running simulated factory drives one secure demo URL: live batches through
> **Preparation → Receiving → Mixing → Cook → Cooling → Filling**, with an EBR-style batch
> report as the proof artifact. This is the **v2 architecture**: the factory is an OPC-UA
> server ("black box with SCADA knobs"), MonsterMQ is the data layer / UNS bus, and
> everything downstream (historian, MES, Grafana, dashboard) consumes the UNS.
>
> **⚠ Anonymization is hard.** DairyWorks + generic names only. No real client/vendor
> names, IPs or schemas anywhere.

### Lines and products

| Line | Hall | Products (recipe id) | Factory / UNS |
|------|------|----------------------|---------------|
| **L1** | Hal 1 | Chocolate Vla 1L (`chocolate-vla-1L`) | `vla-factory`, `DairyWorks/Vla/#` |
| **L2** | Hal 2 | Vanilla, Banana, Caramel, Strawberry Vla 1L (`vanilla-`, `banana-`, `caramel-`, `strawberry-vla-1L`) | `vla-factory-l2` (own OPC-UA server, registered by `vla-l2-opcua-init`), `DairyWorks/Vla-L2/#` |

- **Preparation room** (`vla/preparation.py`, `dw_prep_orders`): one room serves both lines. Each line
  has its own weigh station and premix tank, so a fault on one line never holds up the other. Sugar,
  starch and the flavouring are weighed and premixed per batch; milk comes straight from the silo.
  API: `GET/POST /api/v1/prep-orders`; UI page `/voorbereiding`.
- **Changeovers:** a CIP is only required when the next product is lighter than the previous one
  (colour residue would show), not between every batch.
- **Line-aware API and UI:** equipment, OEE, health, batches and alarms accept `?line=L1|L2`; the
  autopilot runs per line (`/autopilot` → `lines.{L1,L2}`). Equipment ids follow ISA-95 numbering
  (`-01` on L1, `-02` on L2).

---

## Topology

```
                          scenarios/vla-batch/factory-model/isa95-vla.json
                          (ISA-95/88 single source of truth — tags, recipe, methods)
                                          │ (read-only mount into factory + batch-engine + init)
                                          ▼
  ┌───────────────┐   opc.tcp://vla-factory:4840/DairyWorks (ns=2)   ┌────────────────────┐
  │  vla-factory  │  ──── OPC-UA subscribe (Status nodes) ─────────▶  │   monstermq        │
  │ asyncua OPC-UA│                                                   │  NATIVE OPC-UA     │
  │ server + batch│      ▲  registered by one-shot vla-opcua-init     │  client            │
  │ physics       │      │  (GraphQL addOpcUaDevice "vla")            │  (ingest)          │
  │ (no public    │      │                                           └─────────┬──────────┘
  │  port)        │      └── vla-opcua-init (curl → :4000/graphql)              │ publishes
  └───────┬───────┘                                                            ▼
          │ OPC-UA methods (StartBatch/Stop/SetSetpoint/…)   ┌──────────────────────────────────────┐
          │  ◀── control-pad ───────────────────────────────│ monstermq:1883  (DATA LAYER / UNS bus)│
          └─────────────────────────────────────────────────│ archive group dw_uns_archive (DairyWorks/Vla/#)
                                                    ┌────────└───┬───────────────────┬───────────────┘
                       archive → Mongo              │            │                   │
                       (idp.dw_uns_archive)         ▼            ▼                   ▼
                                    ┌──────────────┐   ┌──────────────────┐   ┌───────────────────┐
                                    │    mongo     │   │ vla-tdengine-    │   │  vla-batch-engine │
                                    │ (store)      │   │ bridge (reuse    │   │  FastAPI MES-laag │
                                    │              │   │ tdengine-poc)    │   │  REST /api/v1 +   │
                                    │ idp.dw_*     │   │  ILP write       │   │  batch report     │
                                    │ domein-      │   │      │           │   │  (Mongo domein-   │
                                    │ collections  │◀──┼──────┼───────────┼──▶│   collections)    │
                                    └──────────────┘   │      ▼           │   └─────────┬─────────┘
                                                       │ ┌──────────────┐ │             │ REST
                                                       │ │ vla-tdengine │ │             ▼
                                                       │ │  historian   │ │   ┌───────────────────┐
                                                       │ │  (TSDB-OSS)  │ │   │   vla-dashboard   │
                                                       │ └──────┬───────┘ │   │  nginx SPA        │
                                                       └────────┼─────────┘   │  proxy /api → engine
                                                                │             └───────────────────┘
                                                                ▼                       │
                                                         ┌──────────────┐               │  web-facing (Traefik)
                                                         │   grafana    │               ▼
                                                         │ reads TDengine│      https://milkdemo.${DOMAIN}
                                                         └──────┬───────┘             (basic-auth)
                                                                │ web-facing (Traefik)
                                                                ▼
                                                       https://grafana.${DOMAIN}

   FALLBACK (--profile fallback):  vla-connector  replaces MonsterMQ's native
   OPC-UA client (asyncua client + paho-mqtt) if the native ingest hits quirks.
```

**Only `vla-dashboard` and `grafana` are web-facing** (via Traefik). The bus (MonsterMQ),
the OPC-UA endpoint (vla-factory), and the databases (mongo, vla-tdengine) stay internal.

---

## Ingest + control paths

**Ingest (Status → UNS) — NATIVE.** MonsterMQ has its own OPC-UA client. The one-shot
**`vla-opcua-init`** container registers the factory as an OPC-UA device via GraphQL
(`addOpcUaDevice`, name `vla`, endpoint `opc.tcp://vla-factory:4840/DairyWorks`), and
MonsterMQ then subscribes to every Status node and republishes it onto the UNS under
`DairyWorks/Vla/{Area}/{Equipment}/Status/{tag}`. The device config persists in Mongo, so
the init runs once and exits. **This is the primary ingest path** — no separate bridge
process is needed to get the factory onto the data layer.

**Control (commands → factory) — DIRECT OPC-UA.** The `vla-batch-engine` commands the
factory **directly via OPC-UA methods** (`StartBatch`, `Stop`, `SetSetpoint`, `TakeSample`,
`InjectFault`, `ClearFault` on the line-level `Batch` object) — it does not route control
through separate MQTT Command topics. (MonsterMQ's `writeConfig` on the device also exposes
manual node-writes via `DairyWorks/Vla/write/{nodeId}` for ad-hoc setpoint pokes, but the
MES control path is method calls.)

**Fallback — `vla-connector` (`--profile fallback`).** If MonsterMQ's native OPC-UA client
hits config quirks (most likely an **ns-index mismatch**: asyncua's `urn:dairyworks` is
assumed to land on `ns=2`, verify in the MonsterMQ logs — `docker logs monstermq | grep -i
opcua`), start the standalone connector instead. It does the same OPC-UA ↔ MQTT job in
~150 lines of asyncua + paho-mqtt. Do **not** run it alongside an enabled `vla` device
(double-publish) — disable the device first (`toggleOpcUaDevice(name:"vla",enabled:false)`).

See the full v2 architecture docs in the CFT Services **Datalayer** doc set
(`05-Backend` — OPC-UA/MQTT/REST contracts + deployment + Docker diagram, and
`assets/diagrams/architecture.png`).

---

## Run

Always run the **slim-base + this overlay together** (from the `idp-os` root):

```bash
# 1. Provide a .env in the idp-os root (see scenarios/vla-batch/.env.example)
cp scenarios/vla-batch/.env.example .env   # then edit DOMAIN + DASHBOARD_AUTH + secrets

# 2. Bring the full stack up
docker compose -f docker-compose.slim.yml \
               -f scenarios/vla-batch/docker-compose.vla.yml up -d --build

# 3. Tail logs
docker compose -f docker-compose.slim.yml \
               -f scenarios/vla-batch/docker-compose.vla.yml logs -f vla-connector vla-batch-engine

# Stop (keep data)
docker compose -f docker-compose.slim.yml \
               -f scenarios/vla-batch/docker-compose.vla.yml down

# Stop + wipe volumes (Mongo + TDengine data)
docker compose -f docker-compose.slim.yml \
               -f scenarios/vla-batch/docker-compose.vla.yml down -v
```

**Fallback ingest** — only if MonsterMQ's native OPC-UA client fails (see *Ingest + control
paths*). First disable the native device (GraphQL `toggleOpcUaDevice(name:"vla",enabled:false)`),
then start the standalone connector:

```bash
docker compose -f docker-compose.slim.yml \
               -f scenarios/vla-batch/docker-compose.vla.yml --profile fallback up -d vla-connector
```

### Kick off a batch

```bash
# Via the MES REST layer (creates the batch, derives dose setpoints, auto-starts):
curl -s -X POST http://vla-batch-engine:8000/api/v1/batches \
     -H 'content-type: application/json' \
     -d '{"recipe_id":"chocolate-vla-1L"}'

# Watch the UNS live (from inside the network):
#   subscribe DairyWorks/Vla/# on monstermq:1883
```

---

## API (`vla-batch-engine`, `/api/v1`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | liveness |
| GET | `/tags` | latest UNS snapshot (dashboard) |
| GET | `/materials` | master-data materials |
| GET | `/batches` | list batches |
| POST | `/batches` | create + (default) auto-start a batch |
| GET | `/batches/{batch_id}` | batch detail + telemetry summary |
| POST | `/batches/{batch_id}/start` | start an existing batch |
| POST | `/orders` | create a production order |
| GET | `/orders` | list orders (+ progress) |
| GET | `/orders/{order_id}` | order detail + progress |
| POST | `/orders/{order_id}/batches` | create a batch against an order |
| POST | `/orders/{order_id}/close` | close an order (stop-rule guarded, PR-34) |
| POST | `/scan/order` | shop-floor gate scan (order/batch code) |
| POST | `/scan/label` | scan a material label against the recipe |
| POST | `/scan/weigh` | stage a weighed/dosed quantity |
| POST | `/scan/report` | commit staged doses (report scan) |
| POST | `/production` | manual finished-goods booking |
| GET | `/samples` | list samples (optionally by batch) |
| POST | `/samples` | take a sample |
| POST | `/samples/{sample_id}/reprint-label` | reprint a sample label |
| GET | `/report/{batch_id}` | batch report (`?format=json\|pdf`) |
| POST | `/admin/command` | route a control action to the factory (OPC-UA primary) |
| POST | `/hu` | wrap packs into a handling unit (APPROVED-gated) |
| POST | `/hu/{hu_id}/putaway` | book HU into cold-store |
| POST | `/hu/{hu_id}/ship` | ship HU to expedition |
| GET | `/hu` | list handling units (optionally by batch) |
| GET | `/equipment` | equipment snapshot (state, running hours, dirty flag) |
| GET | `/equipment/health` | snapshot extended with heat-up trend + open CBM alerts |
| POST | `/equipment/{equipment_id}/cip` | perform CIP cleaning — resets fouling counter, clears Dirty |
| GET | `/oee` | per-equipment OEE-light (availability × performance × quality) |
| POST | `/alarms/{alarm_id}/ack` | acknowledge an alarm |
| POST | `/batches/{batch_id}/ack-verdict` | acknowledge a batch verdict (idempotent) |
| GET | `/report/period` | plant-wide management report over the last `days` days (`?format=json\|pdf`) |
| GET | `/report/equipment/{equipment_id}` | per-equipment maintenance report over the last `days` days (`?format=json\|pdf`) |

### Fase 1 (v0.4): orders + scan-driven shop-floor flow

Adds production orders (`OrderManager`, `dw_orders`) and a scan-driven shop-floor
flow (`ScanFlow`, `dw_batch_events` scan_rejected trail) on top of the fase-0
batch engine: gate scan → label scan → weigh → report scan (commit) → production
booking, plus the order stop-rule (`close_order` refuses without booked
production). Implements PR-23 (orders model), PR-24 (order lifecycle + batch
linkage), PR-25 (scan gate + label/weigh guidance), PR-26 (report-scan commit +
inventory consumption), PR-27 (manual production booking), and PR-34 (stop-rule
+ scan-rejection audit trail). Covered end-to-end by `selftest.py` checks 9-10
and `tests/test_orders.py`, `tests/test_scan_flow.py`, `tests/test_scan_commit.py`.

### Fase 2 (PR-35): handling-unit flow — pallet → cold-store → ship

Adds a light warehouse layer (`HandlingUnitManager`, `dw_handling_units`) on top
of the fase-0/1 batch + order flow: filled packs get **wrapped** into a
handling unit (HU) carrying an 18-digit SSCC-**placeholder** label (`80` +
date + serial + check digit — never a real GS1 company prefix), booked into
the **koelmagazijn** (cold-store putaway), then **shipped** to expedition.
The **APPROVED-gate** is the Solve story extended into logistics: `create_hu`
refuses to wrap packs from any batch whose verdict isn't `APPROVED` (raises
`ScanRejected(reason="not_approved")`) — a REJECTED or HOLD batch physically
cannot enter the warehouse. Handling units are folded into the batch report
(`handling_units[]`) for full delivery → HU → batch traceability. Deliberately
**out of scope**: palletizer simulation, a real WMS, and real GS1 SSCC
registration. Covered end-to-end by `selftest.py` check 11 and
`tests/test_handling.py`.

### Fase 3: condition-based maintenance, OEE and the Electronic Batch Record

Adds a maintenance/KPI cluster (`EquipmentMonitor`, `dw_equipment_meta` /
`dw_equipment_state` / `dw_cbm_alerts`) on top of the fase-0/1/2 batch, order
and handling-unit flow. The **CBM fouling model lives in the MES layer as a
documented substitution**: the factory sim itself is untouched — cook-unit
heat-up time per batch is trended (`heatup_history`, last 20 batches) and, once
it crosses `BASE_HEATUP_SEC × 1.35`, raises a fouling alert ("plan CIP
cleaning") *before* the equipment is forced Dirty. Only after
`DIRTY_AFTER_BATCHES` (4) batches without a CIP does the unit flip to `Dirty`
and `create_batch` refuses new work on it — **alert-before-Dirty** is the
predictive half of the Solve story, complementing the reactive
under-cook/viscosity Solve from fase 0. A `POST /equipment/{id}/cip` clears the
counter, the Dirty flag and any open alerts, and the line is available again.

**The refusal is self-explaining.** `POST /batches` and `POST /orders/{id}/batches`
answer `400` with a structured `detail` (`CipRequired`, same shape as the scan
rejections) instead of a bare string, so an operator never sees just a status code:

```json
{"detail": {
  "message": "cook-unit-01 is Dirty: 4 of max 4 batches since the last CIP. Clean cook-unit-01 (CIP) before starting a new batch.",
  "reason": "cip_required", "equipment_id": "cook-unit-01",
  "batches_since_cip": 4, "limit": 4, "last_cip_at": null,
  "action": {"label": "Run CIP on cook-unit-01", "method": "POST",
             "path": "/api/v1/equipment/cook-unit-01/cip"}}}
```

The dashboard renders this as a red alert under the order list (message + last
CIP) with a **Run CIP on cook-unit-01** button; pressing it performs the CIP and
retries the batch. Clients that only read `detail.message` keep working.

**OEE-light** (`GET /oee`) is the classic one-liner — `OEE = Availability ×
Performance × Quality` — computed per equipment from the state-history
durations (availability), the cook-unit heat-up trend vs its clean baseline
(performance, 1.0 elsewhere), and the plant-wide approved-vs-total pack ratio
(quality, same for every row).

The batch report is now explicitly an **Electronic Batch Record (EBR)**:
order/lot genealogy, doses, process events, alarms and the **verdict
acknowledgement** (`POST /batches/{id}/ack-verdict`, operator sign-off,
idempotent) are all part of the one report artifact
(`report_type: "Electronic Batch Record (BIRT-style)"`). Two new report
endpoints sit alongside it for the maintenance/management side: `GET
/report/period` (plant-wide KPI roll-up over N days) and `GET
/report/equipment/{id}` (per-equipment maintenance report — CIP history,
alerts, OEE trend), both `?format=json|pdf`. Covered end-to-end by
`selftest.py` checks 12-13 and `tests/test_equipment_monitor.py`,
`tests/test_cbm.py`, `tests/test_cip_gate.py`, `tests/test_oee_health.py`,
`tests/test_ack.py`, `tests/test_ebr.py`, `tests/test_period_reports.py`.

---

## Component map

| Service | Owner submap | Build context | Web-facing | Role |
|---------|--------------|---------------|:----------:|------|
| `vla-factory` | `factory/` | `./scenarios/vla-batch/factory` | — (internal `4840`) | asyncua OPC-UA server + ISA-88 batch FSM + viscosity physics |
| `vla-opcua-init` | `monstermq-init/` | `curlimages/curl` | — (one-shot) | **primary ingest** — registers the `vla` OPC-UA device in MonsterMQ (GraphQL `addOpcUaDevice`), then exits |
| `vla-connector` | `connector/` | `./scenarios/vla-batch/connector` | — (**profile `fallback`**) | optional fallback: standalone OPC-UA client ↔ MQTT if native ingest quirks |
| `vla-batch-engine` | `batch-engine/` | `./scenarios/vla-batch/batch-engine` | — (internal `8000`) | FastAPI MES-laag: `/api/v1` batches/samples/report; commands factory **direct via OPC-UA methods**; Mongo domein-collections |
| `vla-tdengine` | (image) | `tdengine/tdengine:latest` | — | historian (TSDB-OSS), db `idp` |
| `vla-tdengine-bridge` | (reuse) | `./tdengine-poc` | — | MonsterMQ → TDengine bridge, `MQTT_TOPICS=DairyWorks/Vla/#` (unmodified `bridge.py`) |
| `vla-dashboard` | `dashboard/` | `nginx:alpine` | ✅ `milkdemo.${DOMAIN}` | sales + admin SPA; nginx proxies `/api` → batch-engine |
| `grafana` | `grafana/` | slim-base image | ✅ `grafana.${DOMAIN}` | reads `vla-tdengine`; provisioning mounted from `scenarios/vla-batch/grafana/` |
| `monstermq` / `mongo` / `traefik` | (slim-base) | slim-base | traefik only | bus (+ native OPC-UA client) + store + TLS/routing |

### The integration layer (this agent's deliverables)

| File | Purpose |
|------|---------|
| `factory-model/isa95-vla.json` | ISA-95/88 single source of truth — areas, equipment, exact tags, recipe `chocolate-vla-1L`, sample types, methods, UNS convention |
| `monstermq-init/init-vla-opcua.sh` | one-shot GraphQL `addOpcUaDevice` — registers the factory as MonsterMQ's native OPC-UA ingest (24 Status tags), enables `writeConfig` |
| `docker-compose.vla.yml` | scenario overlay on `idp-network`; wires the vla services + `vla-opcua-init` + fallback `vla-connector` + grafana override |
| `../../monstermq/config.yaml` (patch) | added archive group `dw_uns_archive` → `DairyWorks/Vla/#`, 30-day retention |
| `.env.example` | DOMAIN, DASHBOARD_AUTH, MONGO_*, TD_* with inline docs |
| `README.md` | this file |

### 3D site-twin (`/site`)

A game-style 3D twin of the whole site (react-three-fiber), live at
`https://milkdemo.${DOMAIN}/site`. Everything it shows comes from the batch engine; the scene never
invents state.

- **Line:** receiving tank, mixer, cooker, cooler and filler, each with its live process value and
  status (running, idle, attention, alarm, stale, no data). Hall 1 holds line L1, Hall 2 line L2.
- **Preparation room:** a separate room west of both halls (where the silos used to stand), with
  weigh stations and premix tanks for both lines; a premix header feeds L1 and L2 by pipe. Active
  stations show their set (state, flavour, line) on a shared board.
- **Raw materials:** four silos west of the preparation room (milk, sugar, starch, cocoa) filled to `stock / (4 × reorder level)`.
  Each material has its own supplier vehicle: a stainless tanker for milk, bulk tankers for sugar
  and starch, a pallet truck for cocoa. A silo below 50% places an order; while unloading the
  vehicle books real goods receipts in chunks (`inventory.receive`), capped at silo capacity.
  Each vehicle has its own unloading bay (`INBOUND_BAYS` in `ui/lib/twin/flow.ts`).
- **Warehouse:** each line has its own outfeed conveyor and palletiser robot. A cold store with
  192 rack slots in 8 rows, split into one zone per line (L1 slots 0–95, L2 96–191, `ZONE_SLOTS`
  in `vla/warehouse.py`), with a door per line. Four forklifts with battery and trips: HT-01 and
  HT-03 do the putaway for L1 and L2, HT-02 and HT-04 load trucks, and they share one charger.
  Six docks. Docked trailers are see-through and show the loaded pallets. Cartons and pallets
  (also on the forklift forks) carry the colour of their flavour. A truck loads at the dock, leaves
  when full, and its chip shows which orders it carries; the dock chips form one board.
- **Order traceability:** `Toon orderpad` follows an order from preparation set through batch,
  pallet and dock to the departing truck.
- **WareTrack overlay:** glass panels with tabs per group, search (`/`), floating labels with
  priority (selected, then alarm, within a label budget that skips a label landing on another one
  on screen), a detail card with sparklines, and a batch timeline.

Driven by the autopilot (`DEMO_AUTOPILOT`, `DEMO_AUTOPILOT_LOGISTICS`): batches, warehouse moves and
the logistics simulation (`vla/logistics_sim.py`, exposed at `GET /api/v1/logistics`). The pure rules
are tested without a browser in `ui/e2e/twin-flow.spec.ts` and `twin-derive.spec.ts`, the engine side
in `batch-engine/tests/test_logistics_sim.py`. Deployment notes: `DEPLOY.md` §5b.

### 3D site-twin: borrowed models (Open-Industry-Project, MIT)

The twin under `ui/components/twin/` borrows a handful of MIT-licensed models from
[Open-Industry-Project](https://github.com/Open-Industry-Project/Open-Industry-Project). It uses only
the assets, not the Godot engine or the PLC simulation. The models are optimised GLB files in
`ui/public/models/oip/`, and their source commit, original names and optimisation are listed in `CREDITS.md`
next to the licence (`LICENSE`).

| Model | Used in |
|-------|---------|
| `robot.glb` (six-axis arm) | `objects/Robot.tsx`, palletiser |
| `dockdeur.glb`, `leveller.glb` | `DockBay` in `objects/Logistics.tsx` |
| `trailer.glb` | `TruckModel` (container + doors that open when docked) |
| `tex/pallet-*.webp` | wood texture on `PalletModel` |

Each model is loaded inside `<Suspense>`. For the dock and the truck, the old primitive geometry is the fallback. Colours
come exclusively from palette tokens (`lint:tokens`).

---

## UNS topic tree (locked)

```
DairyWorks/Vla/{Area}/{Equipment}/Status/{tag}          # e.g. .../Cook/cook-unit-01/Status/viscosity_cP
DairyWorks/Vla/{Area}/{Equipment}/Command/{cmd}
DairyWorks/Vla/Batch/Status/{state|batch_id|active_recipe}
DairyWorks/Vla/Batch/Command/{StartBatch|Stop|InjectFault|ClearFault|TakeSample}
```

Payload JSON: `{"value": <scalar>, "unit": "<u>", "ts": "<iso8601>", "quality": "GOOD"}`.

## The Solve (why this demo matters)

Under-cooking (low peak-temp × hold) → starch gelatinisation `g` low → `end_viscosity_cP < 150`
→ out of the 150–300 cP spec → batch verdict **HOLD/REJECTED** + critical alarm. Raw data
(peak-temp × hold) drives a **real decision**: hold/re-cook instead of blind-filling. Inject
it with the `cook_undertemp` fault (`InjectFault` on the Batch object) to demo it live.
