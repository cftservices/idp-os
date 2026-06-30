# ADR 0001 — v3 Manufacturing Data Hub split

- **Status:** Accepted (implemented in `docker-compose.v3.yml`)
- **Date:** 2026-04-29
- **Author:** Johannes Sanderse
- **Supersedes:** the v2 all-in-one MonsterMQ stack as the *primary* path

## Context

Stack v2 used MonsterMQ as one combined component: MQTT broker + OPC-UA client +
flow engine + REST API. That is the fastest route to a working layer, but it
couples data collection, routing, and storage into a single failure domain — one
component failure takes down all three.

The [Rhize Manufacturing Data Hub](https://docs.rhize.com/get-started/manufacturing-data-hub/)
architecture shows the production separation: dedicated message broker → dedicated
rule engine → workflow engine → graph database.

## Decision

Add the MDH split **alongside** MonsterMQ rather than replacing it:

| Concern | v2 | v3 |
|---------|----|----|
| Message broker | MonsterMQ (MQTT) | **RabbitMQ** (AMQP + MQTT plugin) |
| OPC-UA client + rule engine | MonsterMQ flows | **Apache NiFi** |
| Workflow engine | N8N | N8N (unchanged) |
| Graph / hierarchy | Neo4j | Neo4j (unchanged) |
| Time-series + archive | MongoDB | MongoDB (unchanged) |
| API | FastAPI | FastAPI + MonsterMQ GraphQL |

Both brokers run side by side on purpose — see
[architecture.md](../architecture.md#two-brokers-on-purpose). The program teaches
both the microservices (MDH) path and the all-in-one (MonsterMQ) path.

## Consequences

**Positive** — loose coupling and independent scaling on the MDH path; a stronger
"replace the SCADA historian" story; AMQP gives N8N/NiFi reliable delivery
(acks, dead-letter queues, exchange routing).

**Negative** — more moving parts and more RAM (NiFi is JVM-based, 512 MB – 2 GB);
the NiFi OPC-UA processor is a community bundle, so v3 MVP uses a sidecar Python
OPC-UA poller into RabbitMQ instead (keeps NiFi focused on rules/routing).

**Trade-offs in full** (RabbitMQ vs Mosquitto, NiFi vs Node-RED, the three
OPC-UA processor options, resource tables, and the v2→v3 migration steps) are
documented in the original research note:
[`../../research/architecture-v3.md`](../../research/architecture-v3.md).

## References

- [Rhize Manufacturing Data Hub](https://docs.rhize.com/get-started/manufacturing-data-hub/)
- [Rhize ISA-95 guide](https://docs.rhize.com/isa-95/how-to-speak-isa-95/)
- [`research/architecture-v3.md`](../../research/architecture-v3.md) — full ADR with diagrams + trade-offs
- [`research/rhize-isa95-research.md`](../../research/rhize-isa95-research.md)
