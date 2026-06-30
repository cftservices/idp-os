# DataOps for OT

> Five disciplines that run **parallel** to the seven build steps. None is a
> step. Together they are what separates a hobby-grade stack from a
> production-grade data layer. €8/month works not because the stack is small, but
> because the discipline is small-and-tight.

```
                    DataOps for OT  ← horizontal operating layer
       ┌─────────────────────────────────────────────────┐
       ▼      ▼       ▼       ▼          ▼         ▼      ▼
Connect → Condition → Model → Store → Orchestrate → Visualize → Distribute
```

## The five disciplines

| # | Discipline | OT translation | Where it lives in this repo |
|---|-----------|----------------|------------------------------|
| 1 | **Version-controlled code** | Git for `docker-compose.v3.yml`, broker config, tag mappings, Grafana JSON | The whole repo is the unit of version control |
| 2 | **Isolated environments** | DEV (laptop) → CI (test VPS) → PROD (€8 VPS) with no reinstall | `docker-compose.dev.yml` overrides; same compose, different `.env` |
| 3 | **Automated data quality** | OPC bad-quality flags, per-signal range checks, tag-naming validators, EGU sanity | NiFi quality-gate processors (Condition step) |
| 4 | **Consistent refresh schedule** | MQTT real-time + nightly UNS rebuild + weekly mart refresh | Broker (real-time) + scheduled `marts.*` rebuilds |
| 5 | **Tag/topic change review** | A review step for every tag/topic change — one rename breaks downstream consumers | [Tag-alias table](data-modeling.md#tag-aliasing) + PR review |

All five are required for production-grade. Drop any one and the layer degrades
in a way that's invisible until it bites: skip quality (3) and dashboards show
confident nonsense; skip change review (5) and a single rename silently breaks a
consumer three steps downstream.

## Why this is the real differentiator

A €40K vendor stack and an €8/month open-source stack can hold the same data. The
difference that matters is **operating discipline**, not licence cost. The honest
sales claim is *"€8/month with the discipline of a €40K stack"* — and the part
that delivers it is this layer, not the price tag.

> Hobby-grade vs production-grade is not a stack-size question. €8/month works
> because the discipline is small and strict, not because the stack is minimal.

## Foundation step (pre-flight)

The Foundation stage — `docker-compose.v3.yml` + git + a provisioned VPS — is the
infra-as-code expression of disciplines 1 and 2. It is **not** an eighth build
step; it's the ground the build stands on. See
[seven-step-build.md](seven-step-build.md#dataops-for-ot--the-horizontal-layer).

## Source

The five-discipline checklist is adapted from the cloud-BI "DataOps workflow"
and translated into industrial context. It is a **horizontal operating model**,
deliberately not renamed into a step, so the canonical build stays at seven.
