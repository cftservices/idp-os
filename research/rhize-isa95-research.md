# Rhize Manufacturing Data Hub — Research Notes
_Gedaan: 2026-05-01 | Bronnen: docs.rhize.com, rhize.com/blog_

---

## DEEL 1: Ontologie & Semantische Laag

### Wat is een Ontologie?

> "A formal specification of the conceptual knowledge in a domain."

Een ontologie definieert:
- **Entiteiten** — welke concepten bestaan in het domein? (Equipment, Material, Personnel, Order)
- **Relaties** — hoe hangen die concepten samen? (assembled from, defined by, references)
- **Eigenschappen** — welke attributen heeft elke entiteit? (temperature range, alarm limits)
- **Regels** — wat mag je afleiden uit de data?

**Zonder ontologie**: data governance faalt altijd. Je hebt DBA's en programmeurs nodig die data-kwaliteitsregels coderen.
**Met ontologie (ISA-95)**: één schema, één "version of the truth".

### ISA-95 IS de Manufacturing Ontologie

ISA-95 is niet alleen een hiërarchie — het is een **complete formele ontologie** voor manufacturing:
- Definieert alle entiteiten + relaties in moderne productie
- Dekt: productie, maintenance, kwaliteit, voorraad, scheduling
- Integreert Level 0 (sensors) t/m Level 4 (ERP)
- Complementair met ISA-88 (batch control), OPC-UA, ISA-99 (security)

**De semantische laag** = ISA-95. Het geeft ruwe sensordata betekenis door context toe te voegen.

```
Zonder semantische laag:  TIC-001 = 67.3
Met semantische laag:     Site1/Packaging/Line1/Reactor/Temperature = 67.3°C
                          [Normal: 55-75°C | Alarm Hi: 80°C | Asset: WC-PAST-01]
```

### Data Silos — Het Kernprobleem

Typische manufacturing data silos:
- ERP (orders, planning)
- Time Series (sensor data)
- MRP (materials)
- Warehousing
- Quality
- Downtime Tracking
- Recipe Management

Gevolg: kosten van data governance omhoog, risico op slechte beslissingen omhoog.
Oplossing: één ontologie = één schema = alle silos spreken dezelfde taal.

### Semantische Laag vs Knowledge Graph

**Semantische laag**: het conceptuele model dat data betekenis geeft (ISA-95 is de spec)
**Knowledge Graph**: de technische implementatie van die semantische laag als nodes + edges

Rhize implementeert ISA-95 als knowledge graph op Dgraph (GraphQL-native):
- Nodes = entiteiten (Equipment, Order, Material Lot, Person)
- Edges = relaties (defined by, assembled from, references)
- Queries over relaties zijn native en snel

### Waarom Graph > Relationeel voor ISA-95

**Object-relational impedance mismatch**: ISA-95 is van nature een graph (netwerk van relaties).
In een relationele database (SQL) is dit moeilijk te modelleren → leidt tot frustratie → mensen zeggen "ISA-95 is verouderd".

Maar ISA-95 is NIET verouderd — de architectuur was verkeerd:

| Architectuur | ISA-95 implementeerbaar? |
|---|---|
| Relationele DB (SQL) | Moeilijk — impedance mismatch |
| Document DB (MongoDB) | Gedeeltelijk — flexibel maar geen native graph queries |
| Graph DB (Dgraph, Neo4j) | Ja — native nodes + edges = perfect fit |

**Onze MongoDB aanpak**: niet native graph, maar voldoende voor system integrators:
- ISA-95 structuur zit in de MQTT topic hiërarchie (niet in DB relaties)
- JSON asset model beschrijft context per entiteit
- Trade-off: minder querying over relaties, maar eenvoudiger te deployen

### "Events, niet Data"

> "The data is mostly irrelevant on its own. The events produced by the data are everything. Therefore, the architecture of an MDH must be event-driven, not data-driven."

Dit is een kerninsight voor LinkedIn content:
- Niet: "ik sla sensor data op"
- Wel: "ik reageer op events (temperatuur overschrijdt alarm, batch klaar, order gestart)"
- Onze N8N workflows zijn event-driven (MQTT trigger → actie)

---

## DEEL 2: Rhize Architecture & Stack

### Wat is Rhize?

Enterprise Manufacturing Data Hub (MDH) — open source, ISA-95 native, Kubernetes.
Stack: Dgraph (graph DB) + Kafka + BPMN engine + GraphQL API + Restate

### Mapping Rhize → Onze Open Source Stack

| Rhize Component | Onze Stack | IDP Stap |
|---|---|---|
| Rhize Agent (MQTT + OPC-UA collector) | MonsterMQ | Stap 1: Connect |
| BPMN conditioning rules | N8N validation | Stap 2: Condition |
| ISA-95 graph database (Dgraph) | MongoDB + ISA-95 topic structuur | Stap 3: Model |
| Time-series component | MongoDB time-series collections | Stap 4: Store |
| BPMN workflow engine | N8N workflows | Stap 5: Orchestrate |
| Grafana via GraphQL | Grafana via FastAPI | Stap 6: Visualize |
| GraphQL API (single endpoint) | FastAPI REST API | Stap 7: Distribute |

## ISA-95 Equipment Hierarchy (Rhize definitie)

```
Enterprise
  └── Site
        └── Area
              └── Work Center
                    └── Work Unit
                          └── Equipment (instance)
```

MQTT topic structuur volgt hierarchy:
`site1/areaA/line1/plc01/temperature`

Rhize equipment relationships:
- **Equipment Class** → groep van gelijksoortige machines
- **Equipment Instance** → specifieke machine (versioned)
- **Equipment Actual** → welke machine deed welk werk
- **Equipment Properties** → attributen zoals rotation_speed, alarm_hi

## ISA-95 Resource Types

1. **Equipment** — machines met rol in productie
2. **Material** — grondstof t/m eindproduct (Classes → Definitions → Lots → Actuals)
3. **Personnel** — mensen die werk uitvoeren (Classes → Persons → Actuals)
4. **Physical Assets** — uitwisselbare onderdelen voor maintenance tracking

## Definition → Demand → Result (ISA-95 core flow)

Elk productieproces volgt dit patroon:
1. **Definition** — hoe wordt een product gemaakt? (recipe, bill of materials, process segments)
2. **Demand** — productie order/schedule (vraag vanuit ERP/business)
3. **Result** — actual — wat is echt geproduceerd?

**Mapping naar onze N8N workflows:**
- wf-001-start-order → Production Dispatching (Demand)
- wf-003-material-scan / wf-004-product-scan → Production Data Collection (Result)
- wf-005-shift-report → Production Performance Analysis (Result)
- wf-006-fault-detection → Production Execution Management

## ISA-95 MOM Activiteiten (Level 3)

De 8 activiteiten van een Manufacturing Operations Management systeem:
1. Product Definition (wat gaat erin, welke resources?)
2. Resource Management (wat is beschikbaar?)
3. Detailed Production Scheduling (wanneer/wat produceren?)
4. **Production Dispatching** (resources toewijzen aan orders)
5. **Production Execution Management** (hoe voer je de order uit?)
6. **Production Data Collection** (data opslaan tijdens uitvoering)
7. **Production Tracking** (genealogy, EBR, track & trace)
8. **Production Performance Analysis** (OEE, deviatie, golden batch)

## Pub/Sub Architectuur Rationale (uit Rhize docs)

Point-to-point → schaalt slecht (O(n²) verbindingen)
Hub-and-spoke polling → teveel onnodige traffic
**Pub/sub MQTT** → decoupled, event-driven, schaalt lineair

Rhize gebruikt Kafka boven MQTT voor interne service communicatie (hogere throughput, replay).
Onze stack gebruikt MQTT topics voor alles (vereenvoudigd, voldoende voor system integrators).

## MDH Technical Requirements (relevant voor positionering)

- Zero Downtime Architecture → onze VPS heeft single point of failure (bewust trade-off)
- ACID-compliant → MongoDB heeft transacties maar geen native ACID graph
- Type-safe schema → onze MongoDB is schema-less (flexibel maar geen enforcement)
- Headless operation → onze FastAPI REST doet dit
- ISA-95 extensible → onze JSON model is extensible

## Positionering

> "Rhize doet dit voor enterprises op Kubernetes (enterprise pricing).
> Wij bouwen hetzelfde voor system integrators op een €8/maand VPS.
> Zelfde ISA-95 model. Geen Kubernetes. Geen enterprise sales cycle."

## LinkedIn Post Potentieel

- "Rhize vs open source stack — zelfde ISA-95 model, andere schaal"
- "Definition → Demand → Result: hoe elk fabriek werkt (ISA-95)"
- "Waarom pub/sub MQTT het point-to-point probleem oplost"
- "De 8 activiteiten van een MES — en welke je kunt bouwen met N8N"
