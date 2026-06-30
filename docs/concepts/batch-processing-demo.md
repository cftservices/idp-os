# Batch-Processing Demo — concept & planning

> **Status:** concept (whiteboard-uitwerking, 2026-06-30) · intern
> **Type:** discrete **batch**-processing demo — los van de continue-proces IDP-stack.

Batch-processing = afzonderlijke processen die **niet direct** aan elkaar gelinkt
zijn (zeg maar een ketting van losse stappen). Dit in tegenstelling tot de
**continue processen** bij oil & gas, waar het 1 proces is met veel
afhankelijkheden. Deze demo modelleert het batch-geval; de bestaande
[`demos/`](../../demos/) (cornflour, dairy) en de v3-stack dekken het
continue/proces-geval.

---

## 1. Demo: Batch-Processing

### Processen (Operations)
`Preparation → Processing → Packaging` — drie afzonderlijke operations in de keten.

### Data / objecten
- Orders (progress)
- Consumptions per order
- Produced per order
- Samples — Quality
- Materials
- Inventory

### User-Interface
- Orders
- Recipe
- Materials consumed / produced
- Handling Units
- Equipment-status monitoren: **Running / Dirty / Allocated / OEE**

### Rapporten
- **BIRT** → Produced / Consumed per Order (**EBR** — Electronic Batch Record)

### Simulation engine
Simuleert de volledige batch-flow:
```
prep order → processing → packaging → events
```
Events o.a.: *prep started*, *HU scanned*, *productie boeken*, *equipment alarms (ebr)*.

- **OPC UA server** (Windows service)
- Hele fabriek simuleren, met een **admin dashboard** om een aantal zaken te
  configureren.

---

## 2. Business-markt

- **Bedrijven benaderen → marketing →** cacao-fabriek, voedingsmiddelen, mais
  productie, thee fabrieken, productie. Regio's: **Ghana, Kenia, Rwanda**.
  Zowel locals als internationals, **joint venture**.
- **Demo-systeem — Docker-Compose** (containers + netwerk, als 1 stack) → in te
  zetten door de sales-man.
- **Corn-flower (witte mais)** — speciaal voor **Oost-Afrika**.
- **Melk-proces** — voor **West-Afrika**.

---

## 3. Acties & Taakverdeling

| Wie | Actie |
|-----|-------|
| **Samen** | Melk-proces verder uitwerken / modelleren (conceptueel én functioneel): wat simuleren, wat visualiseren, dashboard, rapporten, etc. |
| **Johannes** | Docs van geanonimiseerde food/dairy-projecten |
| **Johannes** | Hoofdlijnen in PowerPoint |
| **Samen** | Data-architectuur technisch uitwerken: tool-keuzes, software stack |
| **Eugene** | Corn-flower (witte mais) speciaal voor Oost-Afrika |

---

## Open punten / aansluiting op IDP-OS

- Batch ≈ **ISA-88** (recipes, batch records) vs de huidige continue/proces-focus
  (ISA-95 hiërarchie). Te bepalen hoe de twee in één curriculum-verhaal passen.
- Software stack + tool-keuzes nog open (zie "Samen: data-architectuur") — koppelen
  aan de stack-canon van de IDP ([`../architecture.md`](../architecture.md),
  [`../store-layer.md`](../store-layer.md)).
- BIRT als rapportage-laag is nieuw t.o.v. de huidige Grafana/Next.js Visualize-stap.
