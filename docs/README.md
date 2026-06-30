# IDP-OS Documentation

> Reference documentation for the **missing AI data layer** — the open-source
> stack that runs the live demo on [techflow24.com](https://techflow24.com) and
> backs the **Build Your AI Data Layer** program.

The repo [`README.md`](../README.md) is the entry point (what this is, why it
exists, how to run it). These docs go one level deeper — the architecture, the
build framework, the data model, and how to operate it.

## Map

| Doc | Read it for |
|-----|-------------|
| [architecture.md](architecture.md) | The v2 → v3 evolution, the dual-broker design, the field → edge → core → consumer layering |
| [seven-step-build.md](seven-step-build.md) | The Connect → Distribute framework — the curriculum anchor, with Solve and DataOps as the cross-cutting tests |
| [data-modeling.md](data-modeling.md) | ISA-95 / UNS semantic modeling + the 3-layer staging → warehouse → marts discipline + tag aliasing |
| [store-layer.md](store-layer.md) | The Store step (4): MongoDB today, TDengine as a time-series option, and why the OT connectors are the real vendor paywall |
| [dataops-for-ot.md](dataops-for-ot.md) | The five DataOps disciplines that separate a hobby stack from a production data layer |
| [operations.md](operations.md) | Deploy, environment variables, VPS sizing, first-boot checks, troubleshooting |

## Decision records

Architecture Decision Records live in [`adr/`](adr/). They capture *why* a
choice was made, not just *what* the choice is.

| ADR | Decision |
|-----|----------|
| [0001-v3-mdh-architecture.md](adr/0001-v3-mdh-architecture.md) | Split the all-in-one MonsterMQ into a Rhize-style Manufacturing Data Hub (RabbitMQ + NiFi), keeping MonsterMQ as the parallel all-in-one path |

> Deeper research notes (Rhize MDH, ISA-95) live in [`../research/`](../research/)
> and are indexed in the TechFlow-OS research dashboard.

## Conventions

- **Verify against the stack file.** When in doubt, [`docker-compose.v3.yml`](../docker-compose.v3.yml)
  is the source of truth. Older docs may mention Mosquitto or SQL Server — those
  are **not** part of v3.
- **Canon lives upstream.** Positioning, pitch, and curriculum are owned by
  [`strategy-os`](https://github.com/cftservices/strategy-os), not here. These
  docs describe the *implementation* and link to canon rather than restating it.
- **Open source first.** Every component is replaceable on your own plant
  without asking a vendor for permission. That is the whole point.
