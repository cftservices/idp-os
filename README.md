# Industrial Data Platform (IDP-OS)

> **The missing AI data layer — reference implementation.**
>
> *"You're missing one layer — and AI can't fix what it can't understand."*

Open-source reference implementation of the missing AI data layer for industrial factories. Connects PLC, SCADA, MES, and ERP into one semantic layer (ISA-95 ontology + event-driven architecture) so raw machine data becomes AI-ready information.

Live demo: **[techflow24.com](https://techflow24.com)** — the dashboard you see there is this stack, running on an €8/month VPS.

---

## Why this exists

Factories run dozens of disconnected systems — PLC, SCADA, MES, ERP, historian — that don't talk to each other. That's why every AI initiative stalls. What's missing is one semantic data layer that connects them and gives raw machine data context.

This repo is the **technical proof** behind the **Build Your AI Data Layer** program. Every claim made publicly about the architecture (LinkedIn posts, YouTube videos, the curriculum) ships from this codebase. If it isn't here, it isn't true yet.

**Role in TechFlow-OS:** IDP-OS is one of 13 sub-OS units in [`cftservices/techflow-os`](https://github.com/cftservices/techflow-os). It owns the *implementation* — strategy, curriculum, and waitlist live elsewhere (see [Boundaries](#boundaries)).

---

## The 7-step build framework

The layer is built in seven stages — no more, no less. Each stage maps to a module in the program, and every component in this repo belongs to one of them.

| # | Stage | What it does | Components |
|---|-------|--------------|------------|
| 1 | **Connect** | Pull raw values from OPC-UA / MQTT / REST historians | `opcua-sim`, `dairy-sim`, `ip21-stub`, `iot-publisher`, NiFi `GetOPCData` |
| 2 | **Condition** | Cleansing, throttling, deadbands, tag-alias normalisation | NiFi processors + tag-alias table |
| 3 | **Model** | ISA-95 hierarchy / semantic topics / UNS — *the heart of the layer* | RabbitMQ topic structure, Neo4j knowledge graph, MongoDB `warehouse.*` |
| 4 | **Store** | Time-series + structured archive (staging → warehouse → marts) | MongoDB collections, Neo4j relationships |
| 5 | **Orchestrate** | Event-driven workflows — work orders, alarms, shift reports | N8N workflows `wf-001..006` over RabbitMQ AMQP |
| 6 | **Visualize** | Dashboards + ad-hoc queries | Grafana (time-series), Next.js webapp (live tags) |
| 7 | **Distribute** | REST / GraphQL APIs for downstream AI, BI, apps | FastAPI (REST + ISA-95 endpoints), MonsterMQ GraphQL |

### Solve is the test, not an 8th step

The seven stages tell you **how** to build the layer. Whether it's worth building is decided by an outcome test we call **Solve**: does raw data, all the way through the pipeline, lead to a real decision or action? You can ship all seven stages and still fail at Solve — that's when the layer ends up "trapped in the middle" and every downstream AI initiative stalls.

> Don't rename to "8 steps". Solve is the outcome-toets, not a build stage.

### DataOps for OT — horizontal layer

Five disciplines run *parallel* to the seven stages — version control, isolated DEV/CI/PROD environments, automated data quality, refresh schedule, tag/topic change review. None of them is a step in the build; together they're what separates a hobby-grade stack from a production-grade data layer. The whole repo is infra-as-code-first: `docker-compose.v3.yml` + `.env` + git is the foundation, not an afterthought.

---

## Stack — v3 (current)

The active stack file is [`docker-compose.v3.yml`](./docker-compose.v3.yml). Older files (`docker-compose.yml`, `docker-compose.idp.yml`) are kept for the v2 baseline and dev environments — refer to v3 for anything you publish.

### Core data plane

| Service | Image | Role | Step(s) |
|---------|-------|------|---------|
| **RabbitMQ** | `rabbitmq:3.13-management` | Message broker (AMQP + MQTT). Exchanges: `raw-data`, `events`, `commands` | 1, 3, 5 |
| **Apache NiFi 2.0** | `apache/nifi:2.0.0` | Dataflow + ISA-95 rule engine — added May 2026 | 1, 2, 3 |
| **MonsterMQ** | `rocworks/monstermq:latest` | All-in-one alternative: MQTT (port 1884) + OPC-UA client + GraphQL + flow engine | 1, 7 |
| **MongoDB** | `mongo:7` | Document + time-series store. Collections: `staging.*` → `warehouse.*` → `marts.*` | 4 |
| **Neo4j** | `neo4j:5-community` | Knowledge graph — ISA-95 equipment hierarchy, P&ID topology, root-cause paths | 3, 4 |

> **Both brokers run side-by-side on purpose.** RabbitMQ + NiFi is the MDH-style microservices path; MonsterMQ is the all-in-one path. Same data, two architectures — the program teaches both so engineers can pick what fits their plant.

### Workflows + APIs

| Service | Image | Role | Step(s) |
|---------|-------|------|---------|
| **N8N** | `n8nio/n8n:latest` | ISA-95 Definition→Demand→Result workflows (work orders, fault detection, shift reports) | 5 |
| **FastAPI** | custom (`./fastapi`) | REST API on top of MongoDB + Neo4j; Grafana JSON datasource | 7 |

### Visualisation

| Service | Image | Role | Step(s) |
|---------|-------|------|---------|
| **Grafana** | `grafana/grafana:latest` | Time-series dashboards (JSON datasource on FastAPI) | 6 |
| **Webapp** | Next.js (custom) | Live PLC dashboard — queries MongoDB every 5s | 6 |

### Simulators (so the demo runs without a real plant)

| Service | What it simulates |
|---------|-------------------|
| `opcua-sim` | 3 generic PLCs over OPC-UA (port 4840) |
| `dairy-sim` | DairyPlant ISA-95 namespace (Receiving / Process / Packaging, port 4841) |
| `ip21-stub` | Aspen IP.21 historian REST endpoint |
| `iot-publisher` | Direct MQTT publisher — proves you don't always need OPC-UA |
| `packml-*` (4 lines) | PackML ISA-88 state machine + Sim3Tanks 3-tank physics (Assembly L1/L2, Packaging, CNC) |

### Infrastructure

| Service | Image | Role |
|---------|-------|------|
| **Traefik** | `traefik:v3` | Reverse proxy + automatic Let's Encrypt SSL |
| **Portainer** | `portainer/portainer-ce:latest` | Container management UI |

> **Stale references to watch for.** Older docs may mention **Mosquitto** or **SQL Server** — those are not part of v3. Mosquitto was replaced by MonsterMQ (and then by RabbitMQ alongside it); the time-series + structured store is MongoDB + Neo4j, not SQL Server. Always verify against `docker-compose.v3.yml` before publishing facts.

---

## The €8/month proof point

The full stack above runs on an **€8/month VPS** (Hostinger KVM2 / Hetzner CX22 class — 2 vCPU, 8 GB RAM). NiFi is the heaviest component (set `NIFI_JVM_HEAP_MAX=512m` for that class; 2 GB on bigger boxes).

> **The price tag is proof, not the pitch.** €8/month doesn't make this work — DataOps discipline does. AVEVA Connect at €40K isn't the villain either; it's a symptom of vendor lock-in around a problem (the missing layer) that no amount of money can buy your way out of. *"€8/mo with the discipline of a €40K stack"* — that's the actual claim.

---

## Run it locally

### 1. Prerequisites

- Docker + Docker Compose v2 (`docker compose`, not `docker-compose`)
- ~4 GB free RAM for the full v3 stack (NiFi alone wants 512 MB – 2 GB)
- Optional: a domain pointed at the host for Traefik SSL

### 2. Clone + configure

```bash
git clone https://github.com/cftservices/idp-os.git
cd idp-os
cp .env.example .env
# fill in DOMAIN, RABBITMQ_USER/PASS, MONGO_INITDB_ROOT_*, NIFI_PASSWORD,
# GRAFANA_ADMIN_*, NEO4J_AUTH, API_SECRET_KEY
chmod 600 traefik/acme.json
```

### 3. Bring it up

```bash
docker compose -f docker-compose.v3.yml up -d
```

First boot takes 2-3 minutes — NiFi and RabbitMQ have generous start-periods on their healthchecks.

### 4. Verify

```bash
docker compose -f docker-compose.v3.yml ps
docker compose -f docker-compose.v3.yml logs -f nifi rabbitmq monstermq
```

### 5. Access

| UI | URL (with DOMAIN configured) | Local fallback |
|----|------------------------------|-----------------|
| Webapp (live dashboard) | `https://${DOMAIN}` | — |
| Grafana | `https://grafana.${DOMAIN}` | — |
| FastAPI docs | `https://api.${DOMAIN}/docs` | — |
| NiFi | `https://nifi.${DOMAIN}` | `https://localhost:8443/nifi` |
| RabbitMQ management | `https://mqtt.${DOMAIN}` | `http://localhost:15672` |
| MonsterMQ | (Traefik route) | port 4000 |
| Portainer | `https://portainer.${DOMAIN}` | — |
| Neo4j browser | — | `http://localhost:7474` |
| N8N | — | `http://localhost:5678` |

---

## Skills (TechFlow-OS hub commands)

When this repo is checked out as part of the parent TechFlow-OS workspace, two Claude Code skills become available from the hub:

- **`/idp-status`** — HTTP health-check against the live endpoints on `techflow24.com`. No SSH or Docker required.
- **`/idp-update`** — closes the community feedback loop. Takes a LinkedIn post that generated engagement on the IDP stack, distils the technical signal, proposes concrete improvements to this repo, drafts the follow-up post in the IDP series, and stages a git commit.

---

## Boundaries

IDP-OS owns the **implementation** of the missing data layer. It does **not** own:

| Lives in | Concern |
|----------|---------|
| [`sub-os/strategy-os`](https://github.com/cftservices/strategy-os) | Curriculum, offer architecture, positioning canon, content planning |
| [`sub-os/education-os`](https://github.com/cftservices/education-os) | Build Your AI Data Layer waitlist + waitlist site |
| [`sub-os/website-os`](https://github.com/cftservices/website-os) | techflow24.com main marketing site |
| [`sub-os/workshop-os`](https://github.com/cftservices/techflow-workshop) | June 2026 launch workshop + cohort delivery |
| [`sub-os/linkedin-os`](https://github.com/cftservices/linkedin-os) / [`youtube-os`](https://github.com/cftservices/youtube-os) / [`facebook-os`](https://github.com/cftservices/facebook-os) | Channel content, outreach, engagement |

If you're looking for "how do I learn this?" → that's the program. This repo is the engine room.

---

## Tooling rules

- **Never** Node-RED. MonsterMQ has its own flow engine; NiFi does the heavier dataflow.
- N8N is for **workflows** (production orders, alarms, shift reports) — **not** for data routing. Data routing is RabbitMQ + NiFi (or MonsterMQ, depending on path).
- Open source first: RabbitMQ, NiFi, MonsterMQ, MongoDB, Neo4j, Grafana, FastAPI, Next.js, Traefik. No cloud historian.

---

## Repo layout

```
.
├── docker-compose.v3.yml      # current stack (use this)
├── docker-compose.yml         # v2 baseline
├── docker-compose.idp.yml     # subset for IDP-only services
├── docker-compose.dev.yml     # dev overrides
├── traefik/                   # reverse proxy + ACME config
├── config/rabbitmq/           # RabbitMQ config + enabled plugins
├── monstermq/                 # MonsterMQ config + OPC-UA init script
├── opcua-simulator/           # 3 generic PLCs (asyncua)
├── dairy-sim/                 # DairyPlant ISA-95 OPC-UA server
├── ip21-stub/                 # Aspen IP.21 REST stub
├── iot-publisher/             # Direct MQTT publisher
├── packml-sim/                # PackML + Sim3Tanks process simulator
├── n8n-workflows/             # wf-001..006 ISA-95 workflows
├── fastapi/                   # REST API source
├── grafana/                   # provisioning + dashboards
├── webapp/                    # Next.js live dashboard
├── factory-model/             # ISA-95 model definitions
├── scenarios/                 # Workshop scenarios (bakery, dairy, …)
├── research/                  # Architecture research notes
└── lead-magnet/               # Lead-magnet build (Build Your AI Data Layer)
```

---

## License

Open source. No vendor lock-in. The whole point is that you can rebuild this on your own plant without asking anyone for permission.

## Parent project

[`cftservices/techflow-os`](https://github.com/cftservices/techflow-os) — the master control room for TechFlow's industrial education business. IDP-OS is one of 13 sub-OS units, each with a single responsibility.
