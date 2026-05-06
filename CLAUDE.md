# Industrial Data Platform — v2

> **The missing AI data layer — open source reference implementation.** Connects PLC, SCADA, MES, and ERP into one semantic layer (ISA-95 ontology + event-driven architecture) so raw machine data becomes AI-ready information. Runs on an €8/mo VPS as proof that the architecture matters more than the price tag — no vendor lock-in.

> **Hero:** *"You're missing one layer — and AI can't fix what it can't understand."*  Voor de volledige pitch (EN/NL, lange versie) en programma-naam **Build Your AI Data Layer**, zie root [`CLAUDE.md`](../../CLAUDE.md) → **Positionering — The Missing Data Layer**.

> **Rol binnen TechFlow-OS:** IDP-OS = de **referentie-implementatie** van de missing data layer. Wat hier draait is het bewijs achter alle posts, video's en het **Build Your AI Data Layer** programma. De 7-stappen build (Connect → Condition → Model → Store → Orchestrate → Visualize → Distribute) is op deze stack gebaseerd; elke stap heeft een module in het programma.

**Live op:** techflow24.com  
**Stack:** Traefik + MonsterMQ + MongoDB + Next.js webapp + OPC-UA Simulator + Portainer

---

## De 7-stappen build (curriculum-anker voor Build Your AI Data Layer)

| Stap | Wat | IDP-OS component |
|------|-----|-------------------|
| 1. Connect | OPC-UA / MQTT bron aansluiten | OPC-UA simulator → MonsterMQ |
| 2. Condition | Cleansing, throttling, deadbands | MonsterMQ flow engine |
| 3. Model | ISA-95 ontologie / semantische topics — **het hart van de boodschap** | Hierarchical MQTT topics + UNS pattern |
| 4. Store | Time-series + structured archive | MongoDB (collection `plc_data`) |
| 5. Orchestrate | Event-driven workflows (productie orders, alarmen, shift reports) | N8N (workflow only — niet voor data routing) |
| 6. Visualize | Dashboards, ad-hoc queries | Grafana + Next.js webapp |
| 7. Distribute | API's voor downstream AI / BI / apps | FastAPI REST + GraphQL |

---

## Architectuur v2

```
[OPC-UA Simulator]
  Python asyncua — simuleert 3 PLCs:
    PLC_01: temperatuur, druk, flow, alarm (1s)
    PLC_02: motortoerentallen x3, vermogen, foutbits (1s)
    PLC_03: batch teller, recept, fase, productiesnelheid (5s)
        ↓ opc.tcp://opcua-sim:4840
[MonsterMQ]
  MQTT broker + OPC-UA client + flow engine
  Schrijft live data naar MongoDB (Archive group: plc_data)
  Web dashboard + REST API + GraphQL op poort 4000
        ↓ mongodb://mongo:27017/idp
[MongoDB]
  Opslag van alle MQTT berichten (collection: plc_data)
        ↓
[Next.js webapp]
  Live PLC dashboard — query MongoDB elke 5s
  Draait op techflow24.com
```

**Wat vervalt t.o.v. v1:** Mosquitto, N8N, RabbitMQ  
**Wat blijft:** Traefik, Portainer, FastAPI, Grafana  
**Wat nieuw is:** MonsterMQ, OPC-UA simulator, Next.js webapp

---

## Services

| Service | URL | Functie |
|---------|-----|---------|
| Traefik | techflow24.com | Reverse proxy + SSL (Let's Encrypt) |
| Next.js webapp | techflow24.com | Live PLC data dashboard |
| MonsterMQ | mqtt.techflow24.com | MQTT broker + OPC-UA client + web UI |
| FastAPI | api.techflow24.com | REST API voor process data (Grafana datasource) |
| Grafana | grafana.techflow24.com | Time-series dashboards + ad-hoc queries |
| Portainer | portainer.techflow24.com | Container management UI |
| OPC-UA sim | intern (port 4840) | 3 gesimuleerde PLCs (asyncua) |
| MongoDB | intern | Process data store |

---

## Beheer

**Status checken:** `/idp-status` skill (HTTP health checks — geen docker nodig)

**SSH naar VPS:**
```bash
ssh user@techflow24.com
docker compose ps
docker compose logs [service] --tail=50
docker compose restart [service]
```

**OPC-UA devices opnieuw registreren:**
```bash
docker compose run --rm init-opcua
```

**Configuratie bestanden:**
- `docker-compose.yml` — volledige stack definitie
- `traefik/traefik.yml` — Traefik routing config
- `monstermq/config.yaml` — MonsterMQ config (TCP, MongoDB, Archive)
- `monstermq/init-opcua.sh` — GraphQL mutations voor OPC-UA device registratie
- `opcua-simulator/server.py` — Python asyncua PLC simulatie
- `webapp/` — Next.js dashboard broncode
- `.env` — secrets (niet in git)

**Belangrijk:** `monstermq/config.yaml` bevat hardcoded MongoDB credentials. Zorg dat deze overeenkomen met `.env` (MONGO_INITDB_ROOT_USERNAME/PASSWORD).

---

## OPC-UA topics in MonsterMQ

Eenmaal geregistreerd via `init-opcua` publiceert MonsterMQ:

| MQTT Topic | Waarde | Eenheid |
|------------|--------|---------|
| `idp/plc01/temperature` | 55–75 | °C |
| `idp/plc01/pressure` | 4.2–4.8 | bar |
| `idp/plc01/flow` | 230–270 | m³/h |
| `idp/plc01/alarm` | true/false | — |
| `idp/plc02/motor1_rpm` | ~1450 | RPM |
| `idp/plc02/motor2_rpm` | ~1450 | RPM |
| `idp/plc02/motor3_rpm` | ~960 / 0 | RPM |
| `idp/plc02/power_kw` | ~18.5 | kW |
| `idp/plc02/fault_bits` | 0–15 | int |
| `idp/plc03/batch_counter` | incrementeel | — |
| `idp/plc03/recipe_id` | 101/102/103/201 | — |
| `idp/plc03/phase` | 1–4 | — |
| `idp/plc03/production_rate` | ~120 | units/h |

---

## Programma integratie — Build Your AI Data Layer

Deze stack is de technische basis voor het online programma **Build Your AI Data Layer** (TP11 — eindproduct van de Priestley funnel). Curriculum volgt de 7-stappen build hierboven; modules per stap:

- **Modules 1–2 — Connect & Condition:** OPC-UA connectiviteit (asyncua simulator → MonsterMQ), MQTT topics/QoS, cleansing
- **Module 3 — Model (ISA-95 / UNS):** semantische topic-hiërarchie, ontologie, namespace ontwerp — het hart van de data layer
- **Module 4 — Store:** MongoDB archive, aggregation queries, retention strategy
- **Module 5 — Orchestrate:** event-driven workflows met N8N (niet voor data routing — dat doet MonsterMQ)
- **Module 6 — Visualize:** Grafana time-series + Next.js dashboard bouwen
- **Module 7 — Distribute:** FastAPI REST + GraphQL voor downstream AI / BI consumers
- **Capstone:** edge-deployment (MonsterMQ op Raspberry Pi, bridge naar VPS) + één AI use case die werkt omdat de layer in place is

**Demo data:** Automatisch via OPC-UA simulator container — engineers reproduceren elke module op hun eigen €8/mo VPS.

---

## Tool regels

- NOOIT Node-RED of Grafana — MonsterMQ heeft eigen dashboard + flow engine
- NOOIT cloud historian — MongoDB lokaal/VPS
- Open source first: MonsterMQ, MongoDB, asyncua, Next.js, Traefik

---

## Changelog

### v2 (2026-04-24)
- **Vervangen:** Mosquitto → MonsterMQ (native OPC-UA client + MongoDB storage)
- **Vervangen:** N8N (data routing) → MonsterMQ flow engine
- **Vervangen:** RabbitMQ → niet meer nodig
- **Behouden:** FastAPI (REST API + Grafana datasource)
- **Behouden:** Grafana (time-series dashboards)
- **Nieuw:** OPC-UA Simulator (Python asyncua, 3 PLCs)
- **Nieuw:** MonsterMQ (rocworks/monstermq:latest)
- **Nieuw:** Next.js webapp (live MongoDB dashboard)
- **Reden:** LinkedIn community engagement → MonsterMQ creator (Andreas Vogler/ETM/Siemens) bevestigde stack richting

### v1 (initieel)
- Mosquitto MQTT + N8N + Grafana + FastAPI + MongoDB + RabbitMQ + Traefik
