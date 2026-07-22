# Vla Batch v2 demo — the missing AI data layer, end-to-end

> Generic anonymized batch-dairy line **"Vla"** (chocolate vla, 1L packs). A running
> simulated factory drives one secure demo URL: a live batch through
> **Receiving → Mixing → Cook → Cooling → Filling**, with an EBR-style batch report
> as the proof artifact. This is the **v2 architecture**: the factory is an OPC-UA
> server ("black box with SCADA knobs"), MonsterMQ is the data layer / UNS bus, and
> everything downstream (historian, MES, Grafana, dashboard) consumes the UNS.
>
> **⚠ Anonymization is hard.** DairyWorks + generic names only. No real client/vendor
> names, IPs or schemas anywhere.

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
