# The 7-step build

> The layer is built in seven stages — no more, no less. Each stage maps to a
> module in the **Build Your AI Data Layer** program and to components in this
> repo. **Solve** is the outcome test, and **DataOps for OT** is a horizontal
> operating layer — neither is an eighth step.

## The seven stages

| # | Stage | What it does | Components in this repo |
|---|-------|--------------|--------------------------|
| 0 | **Foundation** *(pre-flight)* | `docker-compose` + git + provisioned VPS. Infra-as-code first. | Whole repo + Traefik + Portainer |
| 1 | **Connect** | Pull raw values from OPC-UA / MQTT / REST historians | `opcua-sim`, `dairy-sim`, `ip21-stub`, `iot-publisher`, NiFi `GetOPCData` |
| 2 | **Condition** | Cleansing, throttling, deadbands, tag-alias normalisation | NiFi processors + [tag-alias table](data-modeling.md#tag-aliasing) |
| 3 | **Model** | ISA-95 hierarchy / semantic topics / UNS — **the heart of the layer** | RabbitMQ topic structure, Neo4j graph, MongoDB `warehouse.*` |
| 4 | **Store** | Time-series + structured archive (staging → warehouse → marts) | MongoDB; TDengine optional — see [store-layer.md](store-layer.md) |
| 5 | **Orchestrate** | Event-driven workflows — work orders, alarms, shift reports | N8N `wf-001..006` over RabbitMQ AMQP |
| 6 | **Visualize** | Dashboards + ad-hoc queries | Grafana, Next.js webapp |
| 7 | **Distribute** | REST / GraphQL APIs for downstream AI, BI, apps | FastAPI (REST + ISA-95), MonsterMQ GraphQL |

> **Foundation is not an 8th step.** It's the pre-flight discipline (infra-as-code)
> that makes the other seven reproducible. The canonical count stays **7**.

## Solve — the test, not a step

The seven stages tell you **how** to build the layer. Whether it's worth
building is decided by one outcome test we call **Solve**: does raw data, all the
way through the pipeline, lead to a real decision or action?

You can ship all seven stages and still fail at Solve — that's when the layer
ends up "trapped in the middle" and every downstream AI initiative stalls. Solve
is the *why*; the seven stages are the *how*.

> Use it as honest tension: *"you can build all seven stages and still fail — if
> you don't push through to Solve."* Don't rename to "8 steps."

A worked Solve example using the optional TDengine store —
raw temperature → anomaly detection → maintenance action — is in
[`../tdengine-poc/tdgpt-example.md`](../tdengine-poc/tdgpt-example.md).

## DataOps for OT — the horizontal layer

Five disciplines run **parallel** to the seven stages. None is a build step;
together they separate a hobby-grade stack from a production-grade data layer.

```
                    DataOps for OT  ← horizontal operating layer
       ┌─────────────────────────────────────────────────┐
       ▼      ▼       ▼       ▼          ▼         ▼      ▼
Connect → Condition → Model → Store → Orchestrate → Visualize → Distribute
                                                                    ↓
                                                                  Solve  ← outcome test
```

The five disciplines — version control, isolated environments, automated data
quality, refresh schedule, tag/topic change review — are detailed in
[dataops-for-ot.md](dataops-for-ot.md).

## Why "no more, no less"

Each stage is a distinct transformation with a distinct failure mode. Skip
**Condition** and bad-quality data poisons everything downstream. Skip **Model**
and you have a data swamp, not a layer. The seven-step framing is what makes the
build teachable *and* auditable — every component has exactly one home, and every
gap is visible.

> Canon for the framework (positioning, the "two gaps one bridge" framing) lives
> in [`strategy-os`](https://github.com/cftservices/strategy-os). This doc
> describes the implementation, not the pitch.
