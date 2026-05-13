# BakeryWorks Utrecht — second canonical demo plant

> **Doel:** fictieve industriële bakkerij als demo-canvas voor Johannes's eerste webinar / workshop (juni 2026 launch — TP10 → TP11 cohort). Companion van de beverage/sauce-plant die op techflow24.com draait.
>
> **Waarom een tweede scenario:** bewijst dat de 7-stappen build (Connect → Distribute) over branches herbruikbaar is. Voor het publiek (Marco/Marcus persona's) maakt het de cursus geloofwaardiger — niet "TechFlow-specific" maar generic.
>
> **Live deployment:** kan naast de hoofd-IDP draaien (`docker compose up` met `-f docker-compose.bakery.yml` als overlay).

## Twee versies — kies de juiste voor de juiste sessie

| Versie | Bestand | Stations | Solve-events | Wanneer gebruiken |
|--------|---------|----------|--------------|-------------------|
| **Mini** | [`factory-model/isa95-bakery-mini.json`](factory-model/isa95-bakery-mini.json) | 3 (mixer → oven → packaging) | 1 (oven drift) | Pilot-webinar 28 mei, publieke webinar 11 juni, alle korte/cold-traffic content |
| **Full** | [`factory-model/isa95-bakery.json`](factory-model/isa95-bakery.json) | 9 (2 lijnen, allergeen-CIP, planner-decisions) | 3 archetypen | Foundation cohort week 4-7, workshop dag 2, alles ná de pilot |

**Waarom twee versies:** Walker's Steelco-demo was technisch indrukwekkend maar voor een engelse PLC-engineer zonder staal-ervaring vol jargon (heat, ladle, tundish, billet). Eenzelfde valkuil voor onze full bakkerij. Mini is in 3 min uit te leggen aan iemand die NIETS van bakkerijen weet — de volledige 7-stappen build laten zien zonder de luisteraar te verdrinken.

**Migration-pad voor cohort-deelnemers**: Mini in week 1-3 (één Solve-event end-to-end), Full in week 4-7 (multi-line, allergeen-protocol, planner-decisions).

## Profiel (Full versie)

- **Sector:** industriële bakkerij (B2B retail, NL)
- **Locatie:** Utrecht
- **Capaciteit:** 5.000 broden/dag, 2 productielijnen
  - Line A: 60% witbrood
  - Line B: 40% volkoren + specialty (incl. glutenvrije batches)
- **Operatie:** 24/6 (zaterdag = CIP-dag)

## Procesketen — gemapt op 7-stappen build

| # | Procesarea | Process type | PLC | Smart things |
|---|------------|--------------|-----|--------------|
| 1 | Grondstof-silos | Continuous | PLC-SILO | Niveau + temp per silo, fysieke scheiding glutenvrij |
| 2 | Mixen | Batch (200kg) | PLC-MIX-{A,B} | Recipe, batch-ID, energie, deeg-temp |
| 3 | Bulk-fermentatie | Batch (45-90 min) | PLC-FERM-{A,B} | Temp 28°C, vocht 75% |
| 4 | Vormen | Continuous | PLC-FORM-{A,B} | Scaler-gewicht per stuk |
| 5 | Eindrijs (proofer) | Continuous (60 min tunnel) | PLC-PROOF-{A,B} | Temp 35°C, vocht 85%, dwell-time |
| 6 | Bakken (tunnel-oven) | Continuous (25 min, 4 zones) | DCS-OVEN-{A,B} | Zone-temperaturen 240/220/200/180°C, bandsnelheid |
| 7 | Koelen | Continuous (spiraal, 45 min) | PLC-COOL-{A,B} | Bandsnelheid, ambient temp |
| 8 | Snijden + verpakken | Continuous | PLC-PACK-{A,B} | Output rate, recipe-ID, sticker-printer |
| 9 | Warehouse | Batch/scheduled | PLC-WHS | Pallet-count, order-status |

Plus enterprise-laag: SAP B1 ERP (recepten + orders), AVEVA MES, en CIP-station op line-B.

## Drie Solve-events (drie archetypen — past op SOLVE-DOCTRINE)

| ID | Naam | Archetype | Workflow |
|----|------|-----------|----------|
| SOLVE-A | Oven-zone-3 temperatuur drift | operator-action | [`n8n-workflows/wf-bakery-001-oven-drift.json`](n8n-workflows/wf-bakery-001-oven-drift.json) |
| SOLVE-B | Allergeen-switch CIP-verificatie (guardrail) | automated-action | [`n8n-workflows/wf-bakery-002-allergen-cip.json`](n8n-workflows/wf-bakery-002-allergen-cip.json) |
| SOLVE-C | Proofing-overschrijdingen → planner herplanning | planning-decision | [`n8n-workflows/wf-bakery-003-proofing-replan.json`](n8n-workflows/wf-bakery-003-proofing-replan.json) |

Volledige spec in [`factory-model/isa95-bakery.json`](factory-model/isa95-bakery.json) onder `solve_events`.

## Topic-conventie (UNS / Walker Reynolds-stijl)

```
bakery-works-utrecht/
├── line-a/                          # witbrood
│   ├── mixing/mixer-01/{recipe-id,batch-id,load,power,dough-temp}
│   ├── bulk-ferm/chamber-01/{temperature,humidity}
│   ├── forming/scaler-01/...
│   ├── proofing/proofer-01/{temperature,humidity,belt-speed,dwell-time-actual}
│   ├── baking/tunnel-oven-01/{belt-speed,power}
│   │   ├── zone-1/{temperature,product-present}
│   │   ├── zone-2/...
│   │   ├── zone-3/...               # critical for Solve-A
│   │   └── zone-4/...
│   ├── cooling/spiral-cooler-01/...
│   └── packaging/{slicer-01,wrapper-01}/{output-rate,recipe-id}
├── line-b/                          # volkoren/specialty (identieke structuur + CIP)
│   ├── cip/station-01/{status,last-completed,allergen-mode}  # critical for Solve-B
│   └── ... (rest identiek aan line-a)
├── shared/
│   ├── silos/{flour-wheat,flour-glutenfree,sugar,salt,fat}/{level,temperature}
│   ├── water-supply/...
│   └── yeast-cold-storage/...
└── enterprise/
    ├── erp/{open-orders/count, orders-due-24h/count}
    ├── mes-recipes/...
    └── warehouse/...
```

## Files in deze scenario

| File | Wat |
|------|-----|
| [`factory-model/isa95-bakery.json`](factory-model/isa95-bakery.json) | Volledige ISA-95 model met alle work-centers, tags, en Solve-event specs |
| [`n8n-workflows/wf-bakery-001-oven-drift.json`](n8n-workflows/wf-bakery-001-oven-drift.json) | Solve-A — MQTT trigger → Teams push naar shift-leider met approve/escalate/reject actions |
| [`n8n-workflows/wf-bakery-002-allergen-cip.json`](n8n-workflows/wf-bakery-002-allergen-cip.json) | Solve-B — Pre-start guardrail; blocks allergen-switch batches if CIP cycle stale (>60 min) |
| [`n8n-workflows/wf-bakery-003-proofing-replan.json`](n8n-workflows/wf-bakery-003-proofing-replan.json) | Solve-C — End-of-shift cron → aggregate proofing overshoots → planner aanbeveling |
| [`grafana/bakery-overview-dashboard.json`](grafana/bakery-overview-dashboard.json) | Main dashboard: 4-zone oven temps + proofer dwell + mixer + Solve-events feed |
| [`docker-compose.bakery.yml`](docker-compose.bakery.yml) | Overlay docker-compose voor scenario-specifieke services |
| [`README.md`](README.md) | Dit bestand |

## Deployment

### Optie 1 — Bakery alongside main beverage plant (recommended for demo)

```bash
cd c:/tools/techflow-os/sub-os/idp-os
docker compose -f docker-compose.yml -f scenarios/bakery-works-utrecht/docker-compose.bakery.yml up -d
```

Beide scenarios draaien parallel. Bakery topics zitten onder `bakery-works-utrecht/...`, beverage topics blijven onder `idp/plc0X/...`.

### Optie 2 — Standalone bakery (voor lokaal testen)

```bash
# Bouw de bakery OPC-UA simulator (eerst zorgen dat opcua-simulator/Dockerfile bestaat — zie note hieronder)
cd c:/tools/techflow-os/sub-os/idp-os/scenarios/bakery-works-utrecht
docker compose -f docker-compose.bakery.yml up -d
```

### Verificatie

- MonsterMQ web UI: http://localhost:8080 → check topics onder `bakery-works-utrecht/`
- Grafana: http://localhost:3000 → "BakeryWorks Utrecht — Line A Overview" dashboard
- N8N: http://localhost:5678 → check 3 imported workflows (WF-BAKERY-001/002/003)

## Wat ontbreekt nog (TODO)

> Deze scenario is in deze sessie aangemaakt als demo-blueprint. Voor productie-grade deploy zijn deze nog te doen:

1. **OPC-UA simulator code** — `opcua-simulator/server.py` moet de tags uit `isa95-bakery.json` simuleren met realistische ranges en occasional drift-events (voor Solve-A trigger). Hergebruik de Python asyncua-template uit `../opcua-simulator/`.
2. **MonsterMQ init script** — `monstermq-init.sh` moet de bakery OPC-UA endpoints registreren bij MonsterMQ.
3. **Line B work_centers** — `isa95-bakery.json` heeft line-A volledig uitgewerkt en line-B alleen het CIP-station. De rest van line-B moet nog (mirror van line-A met allergeen-protocol).
4. **Tweede dashboard** — `bakery-line-b-dashboard.json` voor de glutenvrije lijn.
5. **Executive dashboard** — overzicht voor plant-manager (yields, energy, Solve-events trend).

## Hergebruik in webinar / cursus

Deze scenario wordt het canvas voor:
- **Pilot-webinar** (eind mei 2026, 10 LinkedIn-1e-graads)
- **Publieke webinar** (2e week juni 2026, TP10 launch)
- **Workshop dag 1** (theorie, dezelfde demo)
- **Workshop dag 2** (deelnemers bouwen dit zelf op hun eigen VPS)
- **Foundation cohort modules** (week 1-7, één procesarea per week)

Strategy-anchor: zie [`../../sub-os/strategy-os/user-workspace/webinars/studied/2026-05-12-walker-reynolds-plant-floor/strategy-diff.md`](../../../../strategy-os/user-workspace/webinars/studied/2026-05-12-walker-reynolds-plant-floor/strategy-diff.md) §3.0.

## Anchors

- 7-stappen build canon → root [`CLAUDE.md`](../../../../CLAUDE.md) Positionering — The Missing Data Layer
- Solve-doctrine → [`../../../strategy-os/user-workspace/webinars/playbook/SOLVE-DOCTRINE.md`](../../../strategy-os/user-workspace/webinars/playbook/SOLVE-DOCTRINE.md)
- Beverage/sauce-plant (companion scenario) → [`../../factory-model/isa95-model.json`](../../factory-model/isa95-model.json)
