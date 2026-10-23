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
| **0. Foundation** *(pre-flight)* | `docker-compose.yml` + git + VPS-provisioning | Hele repo + Traefik + Portainer |
| 1. Connect | OPC-UA / MQTT bron aansluiten | OPC-UA simulator → MonsterMQ |
| 2. Condition | Cleansing, throttling, deadbands, **+ tag-alias normalisation** | MonsterMQ flow engine + (toe te voegen) `aliases.json` |
| 3. Model | ISA-95 ontologie / semantische topics — **het hart van de boodschap** | Hierarchical MQTT topics + UNS pattern, **3-layer collection-discipline** (zie hieronder) |
| 4. Store | Time-series + structured archive | MongoDB (collections `staging.*` → `warehouse.*` → `marts.*`) |
| 5. Orchestrate | Event-driven workflows (productie orders, alarmen, shift reports) | N8N (workflow only — niet voor data routing) |
| 6. Visualize | Dashboards, ad-hoc queries | Grafana + Next.js webapp |
| 7. Distribute | API's voor downstream AI / BI / apps | FastAPI REST + GraphQL |

**Stap 0 is geen 8e stap** — pre-flight discipline (infra-as-code). **Solve is geen 8e stap** — outcome-toets (zie root CLAUDE.md). **DataOps voor OT** loopt horizontaal onder alle 7 stappen (Version Control · DEV/CI/PROD · Automated DQ · Refresh-schedule · Tag/Topic change-review).

---

## 3-Layer Modeling Pattern (Stap 3 — productie-grade discipline)

> Bron: Kahan Data Solutions' Staging → Warehouse → Marts vertaald naar OT. Implementatie-status: **deels gerealiseerd in v2**, expliciete 3-layer collection-namen toe te voegen in v3.

| Sublaag | MongoDB collection-prefix | Inhoud | Mag muteren? |
|---------|---------------------------|--------|---------------|
| **1. Staging** | `staging.*` (1:1 per bron) | Raw payloads + metadata uit MonsterMQ — bv `staging.plc_siemens_line3` | ❌ Never touch raw |
| **2. Canonical Warehouse (UNS)** | `warehouse.*` | ISA-95 hiërarchie + canonical signal names — bv `warehouse.signals`, `warehouse.assets` | ❌ Append-only |
| **3. Use-case Marts** | `marts.*` | View-collections per business-outcome — bv `marts.oee_line3`, `marts.energy_per_batch` | ✅ Vrij |

**Tag-alias-tabel** (cross-cutting, ondersteunt Stap 2c naming-normalisation): `warehouse.tag_aliases` — `{legacy_tag, canonical_signal_uuid, vendor, retired_at}`. Iedere legacy-tag-rename in Stap 1 wordt hier vastgelegd zodat Stap 4-7 consumers nooit breken.

**Migratie-notitie (v2 → v3):** huidige `plc_data` collection wordt `staging.plc_data` (no-op rename); `warehouse.*` + `marts.*` zijn toe te voegen. Geen breaking change voor de Next.js dashboard.

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
| Grafana | milkdemo.techflow24.com/grafana | Time-series dashboards + ad-hoc queries (`grafana.techflow24.com` verwijst hierheen) |
| TDengine explorer | tdengine.techflow24.com | Ruwe historian-data; basic auth + TDengine-login (zie `scenarios/vla-batch/DEPLOY.md`) |
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

## Scenario vla-batch: 3D site-twin

`scenarios/vla-batch/` is de DairyWorks vla-demo, live op `milkdemo.techflow24.com` (basic-auth).
De route `/site` is een 3D site-twin: lijn, silo's met leveranciers per grondstof, magazijn met
palletiser-robot, heftrucks en docks, en een WareTrack-achtige glasoverlay. Details:
`scenarios/vla-batch/README.md` (sectie *3D site-twin*) en `DEPLOY.md` §5b.

- **De twin verzint niets.** Elke voorraad, rit of status komt uit de batch-engine. De logistiek-sim
  (`batch-engine/vla/logistics_sim.py`) boekt echte goederenontvangsten; de silo's lopen dus alleen
  vol als er een voertuig lost.
- **Constanten staan op twee plekken** en moeten gelijk blijven: stellingvakken
  (`warehouse.py` ↔ `ui/lib/twin/layout.ts`) en de rijtijden van de leveranciers
  (`logistics_sim.py` ↔ `tankerPose` in `ui/lib/twin/flow.ts`).
- **Kleuren alleen uit palette-tokens** (`npm run lint:tokens`), geen hex in TSX.
- **Uitrol:** gewijzigde bestanden als tar naar `/root/idp-os`, dan
  `docker compose -p idp-os -f docker-compose.slim.yml -f scenarios/vla-batch/docker-compose.vla.yml -f scenarios/vla-batch/docker-compose.vps-shared.yml up -d --no-deps --build vla-ui vla-batch-engine`.
  Altijd `-p idp-os` en `--no-deps`; een ongefilterde `down` legt de demo plat.
- **Beheer = één script:** `scenarios/vla-batch/deploy/stack.sh`
  (`status`, `offline`, `online [--build]`, `backup [--met-historian]`, `restore <map>`, `remove`).
  Eén compose-project `idp-os`, 13 diensten. Runbook en "offline en weer terug": `DEPLOY.md`,
  bovenste sectie. Er draait géén automatische back-up van deze stack.
- **Ingress:** `aios-caddy` (andere stack) bezit 80/443; ons blok komt uitsluitend uit
  `deploy/attach-ingress-caddy.sh`. `/grafana*` zit achter dezelfde basic auth plus de
  Grafana-login, met de `Authorization`-header gestript. Nooit de Caddyfile met de hand bewerken.
- **Lokale AI:** `vla-ollama` met `qwen2.5:1.5b` (`mem_limit 2g`, 2 CPU's ≈ 4 tok/s). De
  oorzaaksamenvatting draait daarom op de achtergrond (`rca.py`, cache per feitenset).
- **Tests:** `pytest tests/test_logistics_sim.py tests/test_warehouse.py` (de API-tests hangen zonder
  Mongo), `npx tsc --noEmit`, `npx playwright test e2e/twin-flow.spec.ts e2e/twin-derive.spec.ts`.

---

## Changelog

### 2026-10-23: grafana.* en tdengine.* werkend
- **Nieuw:** `grafana.techflow24.com` verwijst door naar `milkdemo…/grafana`; `tdengine.techflow24.com`
  toont taos-explorer achter basic auth plus een geheim gate-cookie, daarna de TDengine-login
  (`deploy/attach-ingress-caddy.sh`)
- **Les:** TDengine OSS 3.3.6 handhaaft `grant read`/`createdb 0` niet — het account `lezer` kan
  gewoon schrijven. Beveiliging zit in het slot ervoor, niet in TDengine-rechten.

### 2026-10-22: vla-batch beheer, Grafana, lokale AI
- **Nieuw:** `deploy/stack.sh` — offline, online, back-up en herstel van de hele stack in één commando
- **Nieuw:** Grafana op `milkdemo…/grafana` (dubbel slot), historian en lokale AI draaien op de VPS
- **Nieuw:** datalaag-lens met oorzaakanalyse; AI-samenvatting op de achtergrond
- **Nieuw:** zeven fabrieksdashboards in Grafana (`fabriek-*`), gegenereerd door
  `scenarios/vla-batch/grafana/build_dashboards.py` en paneel voor paneel gecontroleerd door
  `validate_dashboards.py`; de historian schrijft nu ook L2, voorbereiding en Vla-B weg. Valkuilen
  van de JSON- en TDengine-plugin staan in `scenarios/vla-batch/DEPLOY.md`.

### 2026-10-21: vla-batch site-twin
- **Nieuw:** 3D site-twin op `/site` met WareTrack-overlay, autopilot-magazijn en OIP-modellen (MIT)
- **Nieuw:** logistiek-sim met een leverancier per grondstof (tankwagen, bulkwagens, palletwagen) die
  echte goederenontvangsten boekt, en doorzichtige trailers die de geladen pallets tonen

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
