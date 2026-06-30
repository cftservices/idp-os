# The Store layer (step 4)

> Step 4 (**Store**) keeps the time-series history and the structured archive.
> Today that's MongoDB. TDengine is a purpose-built time-series alternative —
> with one important catch about what its free edition does and doesn't include.

## What "Store" has to do

- Keep raw payloads (staging), the canonical model (warehouse), and use-case
  views (marts) — see [data-modeling.md](data-modeling.md).
- Serve time-windowed aggregations fast (averages, OEE, energy-per-batch).
- Survive on an €8/month VPS without a cloud historian.

The Store choice is **orthogonal to the model**: the 3-layer staging → warehouse
→ marts discipline maps onto either MongoDB collections or TDengine
super-tables/sub-tables.

## Option A — MongoDB (current default)

A document store. Flexible schema, one collection per layer, already wired into
the Next.js dashboard and FastAPI. Good enough until the Store layer becomes a
genuine bottleneck (high-frequency, high-cardinality tag data).

| Strength | Weakness |
|----------|----------|
| Flexible documents, trivial to ingest arbitrary payloads | No purpose-built time-series compression |
| Already integrated (webapp, FastAPI) | Time-window aggregation is more work than SQL `INTERVAL()` |

## Option B — TDengine (time-series database)

A purpose-built TSDB: SQL, native time-window functions, real compression, a
native Grafana datasource, and a built-in AI engine (TDgpt). It would replace
**MongoDB in the Store step** — *not* MonsterMQ, which stays the broker in front.

A runnable proof-of-concept lives in [`../tdengine-poc/`](../tdengine-poc/):
- [`bridge.py`](../tdengine-poc/bridge.py) — ~150 lines, subscribes to MonsterMQ
  and writes schemaless line protocol to TDengine (one sub-table per topic).
- [`docker-compose.tdengine-poc.yml`](../tdengine-poc/docker-compose.tdengine-poc.yml)
  — an overlay on the running stack; changes nothing in `docker-compose.v3.yml`.
- [`tdgpt-example.md`](../tdengine-poc/tdgpt-example.md) — anomaly detection +
  forecasting on `idp/plc01/temperature` (the Solve test).

## The catch: free database, paid OT connectors

TDengine's **database** is genuinely free and capable. The open-source edition
(AGPL-3.0) includes clustering, replication (RAFT), stream processing, SQL, the
schemaless line protocol, **and** the TDgpt AI features (forecasting, anomaly
detection, imputation, classification, bring-your-own-model).

What is **Enterprise-only** is the convenience and ops tooling around it:

| Enterprise-only | Free workaround in this PoC |
|-----------------|------------------------------|
| **taosX** — zero-code MQTT / OPC-UA / PI / AVEVA / Kafka ingestion | `bridge.py` (MonsterMQ → line protocol) |
| taos-explorer web GUI | `taos` CLI + Grafana |
| Tiered hot/cold storage, HA dual-replica, RBAC/audit/encryption-at-rest | Not needed for a single VPS |
| TDgpt model-evaluation tool + model-manager (Merlion/Kats) | Pick the model + tune via SQL by hand |

> **The teaching point.** TDengine paywalls exactly the OT-specific part — the
> connectors — while giving the database away. That's the same lock-in pattern as
> AVEVA Connect: vendors monetise the bridge into the plant, because that's where
> the difficulty (and the money) is. MonsterMQ keeps OPC-UA/MQTT free and open;
> `bridge.py` steps around taosX in ~150 lines. Lean on the Enterprise connectors
> and you've recreated the lock-in you set out to avoid.

## Licensing note

- **TDengine** — AGPL-3.0. Fine for engineers deploying on their own plant/VPS.
  An AGPL network-service obligation would apply only if TechFlow ever offered it
  as a hosted SaaS.
- **MonsterMQ** — GPL-3.0 (slightly milder for network use).

Full evaluation:
[`../research/`](../research/) → the Store-layer research brief lives in the
strategy-os research workspace (linked from the TechFlow-OS research dashboard).
