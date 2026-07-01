# DairyWorks — Milk Production Demo (scenario)

Generic, anonymized batch-dairy factory demo: **Preparation → Processing → Packaging**,
with orders, recipes, consumed/produced, handling units (+SSCC), samples, equipment
status/OEE and an **Electronic Batch Record (EBR)**. The whole factory is driven and
observed over **OPC-UA** (primary external surface); MQTT (MonsterMQ) is the internal bus.

> Conceptual/functional/PRD docs live in the CFT Services `Datalayer` doc-set. This folder
> is the technical implementation on top of the existing IDP stack.

## Topology
```
factory-model/isa95-dairyworks.json  (ISA-88/95 single source of truth)
        │
 10 packml-sim units (libremfg PackML, MQTT)  ──►  MonsterMQ (bus:1883)  ──►  Mongo archive (dairyworks_data)
   Storage:   raw-milk-tank-01, buffer-tank-01                                     │
   Processing: preheater-01, mixer-01, pasteurizer-01, fermenter-01, product-buffer-01
   Packaging: fill-line-01, fill-line-02, palletizer-01                            │
        ▲ Command (MQTT)                                                           ▼
 opcua-server  (★ primary facade: read Status→nodes, methods→MQTT cmd)      FastAPI /archive/dairyworks/*
        ▲                                                                          ▲
 mes-engine  (orders · recipe-explode · consume/produce · HU/SSCC · samples · OEE · EBR)
        │  REST /orders /oee /samples /ebr/{id} /admin/*
   Admin + Sales dashboard  ──►  Traefik + auth ──►  one secure URL
```

## UNS / OPC-UA conventions
- MQTT Status: `DairyWorks/Plant/{Area}/{equipment}/Status/{tag}`
- MQTT Command: `DairyWorks/Plant/{Area}/{equipment}/Command/{cmd}`  (Start, Stop, Reset, MachSpeed, Fault/Inject, Fault/Clear)
- OPC-UA nodes: `ns=2;s=DairyWorks.{Area}.{Equipment}.{tag}` @ `opc.tcp://<host>:4840/DairyWorks`
- OPC-UA methods per equipment: `Start/Stop/Reset/Hold/Unhold/SetMachSpeed/InjectFault/ClearFault`

## Run
From `sub-os/idp-os` (copy `.env.example` → `.env` first, incl. `DASHBOARD_AUTH`):
```bash
# RECOMMENDED — slim base (traefik+mongo+monstermq+fastapi+grafana) + DairyWorks overlay (~2 GB VPS)
docker compose -f docker-compose.slim.yml -f scenarios/dairyworks/docker-compose.dairyworks.yml up -d --build

# ALTERNATIVE — full v3 stack (adds Nifi/Neo4j/RabbitMQ/n8n; heavier)
# docker compose -f docker-compose.v3.yml  -f scenarios/dairyworks/docker-compose.dairyworks.yml up -d --build
```
The **packml-sim factory (10 units)** + mes-engine + opcua-server + dashboard come from the
overlay — they run in **both** the slim and full base. 18 containers total on slim.

## Verify
```bash
# 1. sim units publishing
docker compose exec monstermq sh -c "mosquitto_sub -t 'DairyWorks/#' -v" | head

# 2. archive filling
curl "http://localhost:8000/archive/dairyworks/topics" | jq   # FastAPI (idp)

# 3. OPC-UA facade (UaExpert / asyncua): browse ns=2;s=DairyWorks.Processing.pasteurizer-01.HTST_temp_C
#    call InjectFault("f12",0.4) -> divert_valve_status flips (Solve)

# 4. order end-to-end + EBR (mes-engine)
curl -X POST localhost:8010/orders -d '{"recipe_id":"R-YOG","planned_qty":1000}' -H "Content-Type: application/json"
curl "localhost:8010/ebr/<order_id>?fmt=html"
```

## Files
- `factory-model/isa95-dairyworks.json` — ISA-88/95 model (areas, units+tags, recipes, materials, samples, OEE, SSCC, solve).
- `docker-compose.dairyworks.yml` — 10 sim units (+ mes-engine + opcua-server + dashboard, see overlay).
- Unit configs: `../../packml-sim/scenarios/dairyworks/*.yaml`
- New physics: `../../packml-sim/physics/{fermenter,fill_line,palletizer,preheater}.py`
- MES layer: `../../mes-engine/`  ·  OPC-UA facade: `../../opcua-server/`
- Diagrams: `diagrams/*.excalidraw` (+ rendered PNGs)

## Deploy op Ubuntu VPS (alles als Docker containers)
Alles draait als Docker Compose — één overlay op de bestaande IDP-stack. Alle images zijn Linux-native
(python:3.12-slim voor mes-engine/opcua-server/units, nginx:alpine voor het dashboard, plus de bestaande
MonsterMQ/Mongo/FastAPI/Grafana/Traefik). De build gebeurt op de VPS, dus geen Windows-afhankelijkheid.

```bash
# 1. Docker + Compose plugin
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER   # opnieuw inloggen

# 2. repo + .env
#    DOMAIN=<jouw-domein>            (Traefik host-routing + TLS)
#    DASHBOARD_AUTH=<user>:<htpasswd-hash>   (basic-auth voor de demo-URL;
#                     genereer met:  htpasswd -nbB demo 'sterkwachtwoord'  — verdubbel $ naar $$ in .env)

# 3. bouw + start — SLIM base (past op ~2 GB VPS) + DairyWorks overlay
docker compose -f docker-compose.slim.yml -f scenarios/dairyworks/docker-compose.dairyworks.yml up -d --build
```

- **Demo-URL (beveiligd):** `https://milkdemo.<DOMAIN>` (Traefik + letsencrypt + basic-auth).
- **Lokaal testen zonder domein:** host-poorten zijn ook gemapt — dashboard `http://<vps-ip>:8090`,
  mes-engine API `:8010`, OPC-UA `opc.tcp://<vps-ip>:4840/DairyWorks`.
- **OPC-UA extern** (voor Eugene's client): open poort 4840 in de firewall, of tunnel/VPN.
- **Resource:** 10 unit-containers (~96 MB elk) + 3 kleine services ≈ 1,3 GB bovenop de base-stack.
  De volledige v3-stack (Nifi/Neo4j) is zwaar; voor een kale demo kun je een afgeslankte base draaien
  (monstermq + mongo + fastapi + traefik + grafana + de dairyworks-overlay). Vraag hierom als je wilt.

## Anonymization
Generic "DairyWorks"; no real client/vendor names, IPs, schemas. SSCC uses placeholder GS1 prefix `80` (format + Luhn are public standards).
