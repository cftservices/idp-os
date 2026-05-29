# DairyPlant — workshop demo plant (MonsterMQ-primary)

> **Doel:** fictieve industriële melkfabriek als demo-canvas voor workshop 1 CONNECT (vrijdag 5 juni 2026, 16:00 CET). Vervangt bakery-works-utrecht als primaire workshop-demo. Bakery blijft als cohort week 4-7 multi-line / allergeen-CIP scenario.
>
> **Waarom dairy:** HTST-pasteurisatie geeft één iconische, **regulatorisch verplichte** Solve-event (T < 71.5°C → divert) die in 30 seconden uit te leggen is aan iedere PLC-engineer — geen branche-specifiek jargon zoals oven-zone-temperaturen of allergeen-protocollen. Procesketen is lineair (tank → separator → pasteurizer → homogenizer → bottler), geen multi-line complexiteit.
>
> **Waarom MonsterMQ-direct als primair:** workshop 1 leert "hoe kom ik aan de data?" — de 5 packml-sim containers publiceren rechtstreeks via MQTT naar MonsterMQ op poort 1883. Geen tussenliggende OPC-UA sim, geen Python-script per tag. Voor real-PLC scenario's (Siemens S7, Beckhoff, KEPServerEX) blijft MonsterMQ-native OPC-UA als alternatief in beeld, maar de workshop-demo zelf draait op MQTT-direct.
>
> **Live deployment:** draait als overlay naast main IDP. Bakery + dairy kunnen gelijktijdig actief zijn (verschillende topic-roots).

## Twee versies — kies de juiste voor de juiste sessie

| Versie | Bestand | Units | Solve-events | Wanneer gebruiken |
|--------|---------|-------|--------------|-------------------|
| **Mini** | [`factory-model/isa95-dairy-mini.json`](factory-model/isa95-dairy-mini.json) | 3 (tank → separator → pasteurizer) | 1 (HTST divert) | **Pilot-webinar 28 mei (internal dry-run), publieke workshop 1 op vrijdag 5 juni 2026 16:00 CET**, daarna biweekly public workshops + alle korte/cold-traffic content |
| **Full** | [`factory-model/isa95-dairy.json`](factory-model/isa95-dairy.json) | 5 (+ homogenizer + bottler, CIP-cyclus, planner-decisions) | 3 archetypen | Foundation cohort week 2-3, workshop dag 2, alles ná de pilot |

**Waarom twee versies:** workshop 1 heeft 60 minuten. Mini-versie (3 units, één Solve-event) past in een 8-min demo zonder dat de luisteraar afhaakt. Full-versie heeft het volledige zuivel-proces inclusief homogenisatie, vulinstallatie, CIP-cyclus en planner-recommendation — die dichtheid hoort in cohort-modules, niet in een gratis instap-workshop.

**Migration-pad voor cohort-deelnemers:** Mini in week 1 (één Solve-event end-to-end), Full vanaf week 2 (multi-stage, CIP-protocol, planner-decisions).

## Profiel (Full versie)

- **Sector:** industriële zuivelverwerking (B2B retail + private label, NL)
- **Locatie:** Noord-Nederland (fictional)
- **Capaciteit:** 150.000 L rauwe melk/dag → consumentenmelk + slagroom
- **Operatie:** 24/7, dagelijks CIP-window 02:00–04:00
- **Regulatie:** EU Reg. 853/2004 (hygiëne dierlijke producten) — HTST-pasteurisatie verplicht ≥71.7°C × 15s; onderschrijding = batch divert + audit-trail

## Procesketen — gemapt op 7-stappen build

| # | Procesarea | Process type | Controller | Smart things |
|---|------------|--------------|------------|--------------|
| 1 | Receiving tank | Continuous (storage 4°C) | PLC-RCV | Niveau, temperatuur, batch-ID |
| 2 | Separator (centrifuge) | Continuous | PLC-SEP | RPM, vetgehalte uit, doorstroom |
| 3 | HTST Pasteurizer | Continuous | PLC-PAST | Temp 72°C × 15s, divert-positie, regen-eff |
| 4 | Homogenizer | Continuous | PLC-HOMO | Druk (bar), doorstroom, eindgrootte vetbol |
| 5 | Bottler | Continuous | PLC-BOT | Vulnauwkeurigheid, output-rate, cap-fail rate |

Plus enterprise-laag: ERP (batch-tracking + traceability), MES (CIP-scheduler), audit-trail database (HTST-records per batch, 5 jaar retentie verplicht).

## Drie Solve-events (drie archetypen — past op SOLVE-DOCTRINE)

| ID | Naam | Archetype | Workflow |
|----|------|-----------|----------|
| SOLVE-A | HTST onder-pasteurisatie (T < 71.5°C → divert) | automated-action + audit-trail | [`n8n-workflows/wf-dairy-001-htst-divert.json`](n8n-workflows/wf-dairy-001-htst-divert.json) |
| SOLVE-B | CIP-window gemist → batch-blokkade | automated-action (guardrail) | [`n8n-workflows/wf-dairy-002-cip-overdue.json`](n8n-workflows/wf-dairy-002-cip-overdue.json) |
| SOLVE-C | Bottler cap-fail rate stijgt → planner stop-en-onderhoud | planning-decision | [`n8n-workflows/wf-dairy-003-cap-fail-trend.json`](n8n-workflows/wf-dairy-003-cap-fail-trend.json) |

**Workshop 1 demo focust uitsluitend op SOLVE-A.** De HTST-divert is regulatorisch verplicht (EU 853/2004), wat hem voor elke industriële engineer herkenbaar maakt zonder dat je zuivel hoeft te kennen. SOLVE-B en C horen in cohort-modules.

Volledige spec in [`factory-model/isa95-dairy.json`](factory-model/isa95-dairy.json) onder `solve_events`.

## Topic-conventie (UNS / Walker Reynolds-stijl)

MQTT-direct publish via packml-sim → MonsterMQ op poort 1883.

```
DairyPlant/
├── Receiving/
│   └── Tank/Tank-01/Status/
│       ├── level_L
│       ├── temperature_C
│       └── batch_id
├── Process/
│   ├── Separator/Sep-01/Status/{rpm,fat_out_pct,flow_lph}
│   ├── Pasteurizer/HTST-01/Status/      # critical for Solve-A
│   │   ├── temperature_C                # < 71.5 → divert
│   │   ├── divert_position              # 0 = product, 1 = divert
│   │   ├── hold_time_s
│   │   └── regen_efficiency_pct
│   ├── Homogenizer/Homo-01/Status/{pressure_bar,flow_lph}
│   └── ...
├── Packaging/
│   └── Bottler/Bot-01/Status/{output_units_h,fill_accuracy_pct,cap_fail_rate}
└── Enterprise/
    ├── CIP-Scheduler/{last_completed,next_due,overdue_flag}
    ├── MES/{active_batch,recipe_id}
    └── HTST-Audit/{record_count,last_divert_at}
```

**PackML state machine** (alle units): `STOPPED → STARTING → EXECUTE → COMPLETE → IDLE`. Status-topic publiceert PackML state mode parallel aan procestags — bron voor OEE-berekening in Visualize.

## Files in deze scenario

| File | Wat | Status |
|------|-----|--------|
| [`docker-compose.dairy.yml`](docker-compose.dairy.yml) | Overlay docker-compose — 5 packml-sim containers, MQTT-direct naar monstermq:1883 | ✅ bestaat |
| [`docker-compose.workshop-mini.yml`](docker-compose.workshop-mini.yml) | Mini-overlay — alleen tank/separator/pasteurizer voor workshop 1 demo | 🔨 te bouwen |
| [`factory-model/isa95-dairy.json`](factory-model/isa95-dairy.json) | Volledige ISA-95 model met alle units, tags, Solve-event specs | 🔨 te bouwen |
| [`factory-model/isa95-dairy-mini.json`](factory-model/isa95-dairy-mini.json) | Mini ISA-95 model — 3 units, alleen SOLVE-A | 🔨 te bouwen |
| [`n8n-workflows/wf-dairy-001-htst-divert.json`](n8n-workflows/wf-dairy-001-htst-divert.json) | SOLVE-A — MQTT trigger op HTST < 71.5°C → divert-signal + audit-trail write + Teams push | 🔨 te bouwen |
| [`n8n-workflows/wf-dairy-002-cip-overdue.json`](n8n-workflows/wf-dairy-002-cip-overdue.json) | SOLVE-B — Pre-start guardrail; blocks line-restart als CIP > 22u oud | 🔨 te bouwen |
| [`n8n-workflows/wf-dairy-003-cap-fail-trend.json`](n8n-workflows/wf-dairy-003-cap-fail-trend.json) | SOLVE-C — Cron-aggregaat over bottler cap-fail rate → planner aanbeveling | 🔨 te bouwen |
| [`grafana/dairy-overview-dashboard.json`](grafana/dairy-overview-dashboard.json) | Main dashboard: HTST temp + divert-events + tank level + bottler output | 🔨 te bouwen |
| [`README.md`](README.md) | Dit bestand | ✅ |

**Bestaand achter de schermen** (al gebouwd in vorige sessies, wordt door deze scenario gebruikt):
- [`../../packml-sim/scenarios/dairy/`](../../packml-sim/scenarios/dairy/) — 5 unit-configs (tank-01, separator-01, pasteurizer-01, homogenizer-01, bottler-01) ✅
- [`../../packml-sim/`](../../packml-sim/) — generieke PackML-sim image met physics-modellen ✅

## Deployment

### Workshop-mini (workshop 1 demo)

```bash
cd c:/tools/techflow-os/sub-os/idp-os
docker compose -f docker-compose.v3.yml -f scenarios/dairy-plant/docker-compose.workshop-mini.yml up -d
```

Alleen tank + separator + pasteurizer draaien. Topics live onder `DairyPlant/Receiving/...` en `DairyPlant/Process/Separator/...` + `DairyPlant/Process/Pasteurizer/HTST-01/...`.

### Full dairy (cohort + post-workshop)

```bash
cd c:/tools/techflow-os/sub-os/idp-os
docker compose -f docker-compose.v3.yml -f scenarios/dairy-plant/docker-compose.dairy.yml up -d
```

Alle 5 units draaien. Bakery + dairy kunnen gelijktijdig actief zijn — verschillende topic-roots (`bakery-works-utrecht/` vs `DairyPlant/`), geen overlap.

### Verificatie

```bash
# Live MQTT-stream van alle DairyPlant topics
mosquitto_sub -h techflow24.com -p 1884 -t 'DairyPlant/#' -v

# Specifiek de HTST critical topic (workshop demo)
mosquitto_sub -h techflow24.com -p 1884 -t 'DairyPlant/Process/Pasteurizer/HTST-01/Status/temperature_C' -v
```

- MonsterMQ web UI: https://mqtt.techflow24.com → topic-browser open op `DairyPlant/`
- Grafana: https://grafana.techflow24.com → "DairyPlant Overview" dashboard
- N8N: https://n8n.techflow24.com → check 3 imported workflows (WF-DAIRY-001/002/003)

## OPC-UA als "real PLC" alternatief (NIET workshop-primary)

De legacy [`../../dairy-sim/`](../../dairy-sim/) container draait een asyncua OPC-UA server op poort 4841. Dezelfde DairyPlant-namespace, maar de bron is een OPC-UA endpoint i.p.v. directe MQTT-publish. Use case: laten zien dat MonsterMQ ook OPC-UA endpoints inleest (voor engineers met Siemens S7-1500 of Beckhoff TwinCAT in hun fabriek). **Niet** de workshop 1 primary demo — die loopt op packml-sim MQTT-direct.

## Hergebruik in webinar / cursus

Deze scenario wordt het canvas voor:
- **Pilot-webinar** (28 mei 2026, internal dry-run)
- **Publieke workshop 1** (vrijdag 5 juni 2026, 16:00 CET — POP launch-event)
- **Workshops 2-7** (biweekly tot eind nov 2026) — alle 7 build-stappen met dezelfde dairy-canvas
- **Foundation cohort module 1-2** (Connect + Condition) — Mini scenario
- **Foundation cohort module 3-7** — Full scenario (HTST audit, CIP-protocol, planner-decisions)

## Anchors

- 7-stappen build canon → root [`CLAUDE.md`](../../../../CLAUDE.md) Positionering — The Missing Data Layer
- Solve-doctrine → [`../../../strategy-os/user-workspace/webinars/playbook/SOLVE-DOCTRINE.md`](../../../strategy-os/user-workspace/webinars/playbook/SOLVE-DOCTRINE.md)
- Bakery scenario (cohort week 4-7 multi-line) → [`../bakery-works-utrecht/README.md`](../bakery-works-utrecht/README.md)
- PackML-sim source → [`../../packml-sim/README.md`](../../packml-sim/README.md)
