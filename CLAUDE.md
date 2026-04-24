# Industrial Data Platform — v2

> Open source stack die AVEVA Connect (€40K/jaar) vervangt voor system integrators. Draait op een VPS van €8/maand.

**Live op:** techflow24.com  
**Stack:** Traefik + MonsterMQ + MongoDB + Next.js webapp + OPC-UA Simulator + Portainer

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

**Wat vervalt t.o.v. v1:** Mosquitto, N8N, Grafana, RabbitMQ, FastAPI  
**Wat blijft:** Traefik, Portainer  
**Wat nieuw is:** MonsterMQ, OPC-UA simulator, Next.js webapp

---

## Services

| Service | URL | Functie |
|---------|-----|---------|
| Traefik | techflow24.com | Reverse proxy + SSL (Let's Encrypt) |
| Next.js webapp | techflow24.com | Live PLC data dashboard |
| MonsterMQ | mqtt.techflow24.com | MQTT broker + OPC-UA client + web UI |
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

## Cursus integratie

Stack is de technische basis voor de Industrial Data Platform cursus:
- Module 1: OPC-UA connectiviteit (asyncua simulator → MonsterMQ)
- Module 2: MQTT architectuur (topics, retained, QoS)
- Module 3: MongoDB data opslag (archive, aggregation queries)
- Module 4: Next.js dashboard bouwen (MongoDB driver, polling)
- Module 5: Edge computing (MonsterMQ op Raspberry Pi, bridge naar VPS)

**Demo data:** Automatisch via OPC-UA simulator container

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
- **Vervangen:** Grafana → Next.js custom webapp
- **Vervangen:** FastAPI → MonsterMQ REST API / GraphQL
- **Vervangen:** RabbitMQ → niet meer nodig
- **Nieuw:** OPC-UA Simulator (Python asyncua, 3 PLCs)
- **Nieuw:** MonsterMQ (rocworks/monstermq:latest)
- **Nieuw:** Next.js webapp (live MongoDB dashboard)
- **Reden:** LinkedIn community engagement → MonsterMQ creator (Andreas Vogler/ETM/Siemens) bevestigde stack richting

### v1 (initieel)
- Mosquitto MQTT + N8N + Grafana + FastAPI + MongoDB + RabbitMQ + Traefik
