# Architecture

> How the stack is layered, why there are two brokers, and how v3 evolved from
> v2. For the per-service image/port table, see the repo
> [`README.md`](../README.md#stack--v3-current). For the full reasoning behind
> v3, see [ADR 0001](adr/0001-v3-mdh-architecture.md).

## Four layers, bottom to top

The layer isn't a single product — it's a pipeline from the plant floor to the
consumer. Every component in the repo sits in exactly one of these layers.

```
┌──────────────────────────────────────────────────────────────────┐
│ CONSUMER     Grafana · Next.js webapp · downstream AI / BI         │  Visualize · Distribute
├──────────────────────────────────────────────────────────────────┤
│ CORE         RabbitMQ · MongoDB · Neo4j · FastAPI · N8N            │  Model · Store · Orchestrate · Distribute
│              (parallel all-in-one path: MonsterMQ)                 │
├──────────────────────────────────────────────────────────────────┤
│ EDGE         Apache NiFi  (OPC-UA client · rule engine · routing)  │  Connect · Condition
├──────────────────────────────────────────────────────────────────┤
│ FIELD        OPC-UA sims · IP.21 stub · direct-MQTT IoT publisher  │  (sources)
└──────────────────────────────────────────────────────────────────┘
```

Map each layer to the [seven-step build](seven-step-build.md): FIELD+EDGE is
**Connect/Condition**, CORE is **Model/Store/Orchestrate**, CONSUMER is
**Visualize/Distribute**.

## Two brokers, on purpose

The stack runs **RabbitMQ + NiFi** *and* **MonsterMQ** side by side. This is a
teaching decision, not redundancy:

| Path | Components | Shape | When it fits |
|------|-----------|-------|--------------|
| **MDH (microservices)** | RabbitMQ broker → NiFi rule engine → MongoDB/Neo4j | Dedicated component per concern, loose coupling | Plants that want each piece swappable and independently scalable |
| **All-in-one** | MonsterMQ (MQTT + OPC-UA client + flow engine + GraphQL) | One process does Connect → Distribute | Small sites / edge boxes / a fast start on €8/month |

Same data, two architectures. The program teaches both so an engineer can pick
what fits their plant — and can argue *why*.

## v2 → v3 evolution

v2 used MonsterMQ as the single combined broker + OPC-UA client + flow engine +
API. That is the fastest way to a working layer, but it couples data collection,
routing, and storage into one failure domain. v3 adds the Rhize-style
Manufacturing Data Hub split (a dedicated broker and a dedicated rule engine)
*alongside* MonsterMQ, so both paths are demonstrable.

| Concern | v2 | v3 |
|---------|----|----|
| Message broker | MonsterMQ (MQTT) | **RabbitMQ** (AMQP + MQTT plugin) + MonsterMQ |
| OPC-UA client + rule engine | MonsterMQ flows | **Apache NiFi** + MonsterMQ |
| Workflow engine | N8N | N8N (unchanged) |
| Graph / hierarchy | Neo4j | Neo4j (unchanged) |
| Time-series + archive | MongoDB | MongoDB (unchanged — see [store-layer.md](store-layer.md)) |
| API | FastAPI | FastAPI + MonsterMQ GraphQL |
| Dashboards | Grafana + Next.js | Grafana + Next.js (unchanged) |

Full rationale, trade-offs, and the NiFi OPC-UA processor options:
[ADR 0001](adr/0001-v3-mdh-architecture.md).

## Data flow (v3, MDH path)

```
OPC-UA sim ──OPC-UA──► NiFi (GetOPCData, poll 1s)
                          │ extract → ISA-95 field map → quality gate
                          ▼
                       RabbitMQ  exchange: raw-data ──► FastAPI / monitors
                          │            exchange: events ──► N8N wf-006 (faults)
                          │            exchange: commands ──► N8N wf-001/002 (orders)
                          ▼
                       MongoDB (staging → warehouse → marts)  +  Neo4j (ISA-95 graph)
                          ▼
                       FastAPI (REST + ISA-95 endpoints) ──► Grafana + Next.js

direct path:  OPC-UA sim ──► MonsterMQ (native OPC-UA client) ──► MongoDB
```

## Where to go next

- Build framework and the Solve test → [seven-step-build.md](seven-step-build.md)
- The semantic model (ISA-95 / UNS / 3-layer) → [data-modeling.md](data-modeling.md)
- Running it → [operations.md](operations.md)
