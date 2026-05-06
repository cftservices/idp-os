# Architecture Decision Record — IDP Stack v3

**Date:** 2025-04-29  
**Status:** Proposed  
**Author:** Johannes Sanderse

---

## Context

Stack v2 uses MonsterMQ as a combined MQTT broker + OPC-UA client + flow engine + REST API.
This creates tight coupling: one component failure brings down data collection, routing, and storage.

The [Rhize Manufacturing Data Hub](https://docs.rhize.com/get-started/manufacturing-data-hub/)
architecture shows the correct separation: dedicated message broker → dedicated rule engine →
workflow engine → graph database.

---

## Decision

Replace MonsterMQ with two dedicated components:

| Component     | v2                  | v3                      | Rationale                                              |
|---------------|---------------------|-------------------------|--------------------------------------------------------|
| Message broker | MonsterMQ (MQTT)   | **RabbitMQ**            | Battle-tested, AMQP + MQTT plugin, N8N native support  |
| OPC-UA client  | MonsterMQ built-in | **Apache NiFi**         | Visual flow builder, OPC-UA processor, threshold rules |
| Rule engine    | MonsterMQ flows     | **Apache NiFi**         | Condition routing, ISA-95 field mapping, filtering     |
| BPMN/workflow  | N8N                 | N8N (unchanged)         | ISA-95 Definition→Demand→Result patterns               |
| Graph DB       | Neo4j               | Neo4j (unchanged)       | Equipment hierarchy, P&ID topology                     |
| Time-series    | MongoDB             | MongoDB (unchanged)     | plc_data collection, event archive                     |
| API            | FastAPI             | FastAPI (unchanged)     | REST + future GraphQL                                  |
| Analytics      | Grafana             | Grafana (unchanged)     | Time-series dashboards                                 |
| Dashboard      | Next.js             | Next.js (unchanged)     | Live OPC-UA data viewer                                |

---

## Architecture Diagram (text)

```
┌─────────────────────────────────────────────────────────────────┐
│                     FIELD LAYER                                  │
│  OPC-UA Sim (PLC_01, PLC_02, PLC_03)    External MQTT devices   │
└─────────────┬───────────────────────────────────┬───────────────┘
              │ OPC-UA (port 4840)                 │ MQTT (port 1883)
              ▼                                    ▼
┌─────────────────────────┐         ┌──────────────────────────────┐
│      APACHE NIFI        │         │         RABBITMQ             │
│  ┌─────────────────┐    │         │  ┌────────────────────────┐  │
│  │ GetOPCData      │───►│─publish►│  │ Exchange: raw-data      │  │
│  │ (poll 1s)       │    │         │  │ Exchange: events        │  │
│  └─────────────────┘    │         │  │ Exchange: commands      │  │
│  ┌─────────────────┐    │◄─sub────│  └────────────────────────┘  │
│  │ Rule Engine     │    │         └──────────────┬───────────────┘
│  │ - Threshold     │    │                        │ AMQP
│  │ - ISA-95 map    │    │                        ▼
│  │ - Quality gate  │    │         ┌──────────────────────────────┐
│  └─────────────────┘    │         │            N8N               │
└─────────────────────────┘         │  wf-001 start-order (Demand) │
              │                     │  wf-002 receive-order        │
              │ direct publish      │  wf-003 scan-material        │
              ▼                     │  wf-004 scan-equipment       │
┌─────────────────────────────────────── wf-005 shift-report ──────┤
│  MONGODB (time-series + events)   │  wf-006 fault-detection      │
│  plc_data / events / wf_history   └──────────────────────────────┘
└─────────────┬───────────────────────        │
              │                               │ Cypher
              ▼                               ▼
┌─────────────────────────┐   ┌──────────────────────────────────┐
│        FASTAPI           │   │              NEO4J               │
│  /equipment  /events     │   │  ISA-95 equipment hierarchy      │
│  /plc-data   /isa95      │   │  Asset relationships, P&ID topo  │
└─────────────┬────────────┘   └──────────────────────────────────┘
              │ REST
    ┌─────────┴──────────┐
    ▼                    ▼
GRAFANA              NEXT.JS WEBAPP
(time-series)        (live PLC dashboard)
```

---

## ISA-95 Data Flow

Following the **Definition → Demand → Result** pattern from Rhize:

| ISA-95 Concept    | Object               | Flow                                        |
|-------------------|----------------------|---------------------------------------------|
| **Definition**    | Work Order Template  | Neo4j node (created once by engineer)       |
| **Demand**        | Work Order Request   | N8N wf-001-start-order → RabbitMQ commands  |
| **Result**        | Work Response        | N8N wf-003/004/005 → MongoDB events         |
| **Execution**     | Real-time alarm      | NiFi fault detection → RabbitMQ events      |

---

## RabbitMQ Exchange Design

| Exchange      | Type   | Routing key examples              | Consumers               |
|---------------|--------|-----------------------------------|-------------------------|
| `raw-data`    | topic  | `plc.PLC_01.Temperature`          | N8N (monitor), FastAPI  |
| `events`      | direct | `alarm.high-temp`, `fault.e-stop` | N8N wf-006, Teams hook  |
| `commands`    | direct | `order.start`, `order.complete`   | N8N wf-001, wf-002      |

MQTT devices publish to RabbitMQ via topic routing:  
`MQTT topic: plc/PLC_01/Temperature` → `AMQP routing key: plc.PLC_01.Temperature`

---

## Apache NiFi Flow Design

### Flow 1: OPC-UA → RabbitMQ (raw-data)
```
GetOPCData (poll 1s)
  → EvaluateJsonPath (extract tagName, value, timestamp)
  → UpdateAttribute (add ISA-95 fields: site, area, unit, process-cell)
  → PublishAMQP (exchange=raw-data, routing-key=plc.${tagName})
```

### Flow 2: Rule Engine (threshold → events)
```
ConsumeAMQP (exchange=raw-data)
  → RouteOnAttribute (temperature > 85 || pressure > 12)
  → UpdateAttribute (event-type=alarm, severity=high)
  → PublishAMQP (exchange=events, routing-key=alarm.${event-type})
```

### Flow 3: Archive (raw-data → MongoDB)
```
ConsumeAMQP (exchange=raw-data)
  → PutMongo (collection=plc_data, document=payload)
```

---

## Resource Requirements

| Component  | Min RAM | Recommended RAM | Notes                              |
|------------|---------|-----------------|-------------------------------------|
| RabbitMQ   | 256MB   | 512MB           | With MQTT plugin                    |
| Apache NiFi | 1GB    | 2GB             | JVM-based; reduce with -Xmx512m    |
| MongoDB    | 512MB   | 1GB             | With WiredTiger cache               |
| Neo4j      | 512MB   | 1GB             |                                     |
| N8N        | 256MB   | 512MB           |                                     |
| Grafana    | 128MB   | 256MB           |                                     |
| FastAPI    | 64MB    | 128MB           |                                     |
| **Total**  | **~3GB**| **~5.5GB**      |                                     |

### VPS Recommendation

| VPS         | Price/mnd | RAM  | Verdict                                         |
|-------------|-----------|------|-------------------------------------------------|
| Hetzner CX11 | €3.29    | 2GB  | ❌ Too small for NiFi                           |
| Hetzner CX22 | €5.77    | 4GB  | ⚠️  NiFi with -Xmx512m, limited flows          |
| Hetzner CX32 | €12.09   | 8GB  | ✅ Recommended for full stack + NiFi 1GB heap  |
| Hetzner CX42 | €24.19   | 16GB | ✅ Production-grade, course demo + students     |

---

## Trade-offs

### RabbitMQ vs Mosquitto (MQTT-only)
- **Chosen: RabbitMQ** — AMQP-native means N8N and NiFi get reliable message delivery with
  acknowledgements, dead-letter queues, and exchange routing. MQTT plugin adds external device support.
- **Alternative: Mosquitto** — lightweight, but MQTT-only. N8N needs a separate AMQP broker anyway.

### Apache NiFi vs Node-RED (rule engine)
- **Chosen: Apache NiFi** — enterprise-grade, OPC-UA processor, visual dataflow, scales to production.
  ISA-95 field mapping is cleaner as NiFi attributes. Better story for "replace SCADA historian".
- **Alternative: Node-RED** — lighter (128MB), but JavaScript-based rules, no native OPC-UA.

### NiFi OPC-UA Processor
The `nifi-opcua-bundle` is a community processor. Options:
1. **Community bundle** — download JAR from GitHub, mount into `nifi-conf/lib/`
2. **Groovy script** — use ExecuteGroovyScript + Eclipse Milo (Java OPC-UA library)
3. **Sidecar Python** — lightweight OPC-UA poller (opcua-asyncio) → publishes to RabbitMQ,
   NiFi consumes from RabbitMQ only

**Current recommendation:** Option 3 (sidecar Python OPC-UA poller) for v3 MVP.
This avoids NiFi community bundle complexity and keeps NiFi focused on rule engine/routing.

---

## Migration from v2

1. Keep `docker-compose.yml` (v2) running for reference
2. Deploy `docker-compose.v3.yml` on different ports (or test VPS)
3. Re-import N8N workflows (JSON format unchanged)
4. Reconfigure Grafana datasources (FastAPI URL unchanged)
5. Load Neo4j ISA-95 graph (same Cypher scripts)
6. Remove MonsterMQ init script, replace with RabbitMQ exchange init

---

## References

- [Rhize Manufacturing Data Hub](https://docs.rhize.com/get-started/manufacturing-data-hub/)
- [Rhize ISA-95 Guide](https://docs.rhize.com/isa-95/how-to-speak-isa-95/)
- [RabbitMQ MQTT Plugin](https://www.rabbitmq.com/docs/mqtt)
- [Apache NiFi Documentation](https://nifi.apache.org/docs.html)
- [research/rhize-isa95-research.md](./rhize-isa95-research.md)
