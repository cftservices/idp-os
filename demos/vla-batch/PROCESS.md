# Vla batch-proces — demo-fabriek (generiek batch-food)

> De batch-fabriek die we simuleren voor de workshop + demo. Klein, uitlegbaar, batch
> (geen continu proces). Raw materials → één produced material. Dit is de input die
> Eugene vroeg: "schrijf het proces uit — wat is de business, raw materials, produced material?"
>
> Gekozen product: **chocoladevla** (herkenbaar NL zuivelproduct, simpel batch-recept).

## Business in één zin
We maken **1-liter pakken chocoladevla** in batches: per batch gaat een tank raw materials
erin, en er komen gevulde pakken vla uit. Klein en herkenbaar genoeg om in een workshop uit te leggen.

## Raw materials → produced material
| Raw materials (in) | Produced (uit) |
|--------------------|----------------|
| Gestandaardiseerde melk (vetgehalte ingesteld) | Chocoladevla, gevuld in 1L pakken |
| Suiker | (bijproduct: spoel-/CIP-verlies) |
| Gemodificeerd zetmeel (bindmiddel/verdikker) | |
| Cacaopoeder | |

## Het batch-proces (5 stappen)

```
 Melk + suiker + zetmeel + cacao
        │
        ▼
 (1) Ontvangst / standaardisatie  ── melktank, vet instellen
        │
        ▼
 (2) Doseren + mengen  ── procestank: ingrediënten doseren, roerwerk mengt tot slurry
        │
        ▼
 (3) KOKEN / verstijfselen + pasteuriseren  ── ~85-90 °C, hold   ◀── de kwaliteitsstap
        │
        ▼
 (4) Koelen  ── terug naar ~20-25 °C vultemperatuur
        │
        ▼
 (5) Vullen  ── 1L pakken, tellen
```

1. **Ontvangst / standaardisatie** — melk in een ontvangsttank, vetgehalte ingesteld.
2. **Doseren + mengen** — in de procestank worden melk, suiker, zetmeel en cacao gedoseerd; het
   roerwerk mengt tot een homogene slurry.
3. **Koken / verstijfselen + pasteuriseren** — de mix wordt verhit naar **~85-90 °C** en vastgehouden.
   Hier gebeurt het echte werk: het **zetmeel verstijfselt** (de vla wordt dik) én de mix wordt
   gepasteuriseerd. Dit is **de** kwaliteitsstap.
4. **Koelen** — terug naar vultemperatuur (~20-25 °C).
5. **Vullen** — in 1L pakken, met telling.

Eén **batch** = één tankvulling (bv. 5000 L). Batch-record: BatchID, start/eind, doseringen per
ingrediënt, piek-kooktemperatuur, hold-tijd, eind-viscositeit, aantal pakken.

## SCADA-knoppen (wat je in de demo kan draaien)
> Eugene's wens: "een fabriek aan de onderkant waar we knoppen kunnen draaien."

- Dosering-setpoints per ingrediënt (melk / suiker / zetmeel / cacao)
- Roerwerk-snelheid (rpm)
- **Kook-temperatuur setpoint + hold-tijd**  ← stuurt de kwaliteit
- Koel-target
- Sample nemen → naar de data layer
- Start / stop batch

## ISA-95 / OPC-UA tags (de "fabriek als OPC-UA")
> UNS-topics zoals de gebouwde stack ze publiceert (site `DairyWorks`, line `Vla`).
> OPC-UA node-ids: `ns=2;s=DairyWorks.Vla.{Area}.{Equipment}.{tag}` → MonsterMQ's native
> OPC-UA-client publiceert ze op `DairyWorks/Vla/{Area}/{Equipment}/Status/{tag}`.
```
DairyWorks/Vla/Mixing/process-tank-01/Status/level_L
DairyWorks/Vla/Mixing/process-tank-01/Status/temp_C
DairyWorks/Vla/Mixing/process-tank-01/Status/agitator_rpm
DairyWorks/Vla/Cook/cook-unit-01/Status/temp_C
DairyWorks/Vla/Cook/cook-unit-01/Status/setpoint_C
DairyWorks/Vla/Cook/cook-unit-01/Status/hold_sec
DairyWorks/Vla/Cook/cook-unit-01/Status/viscosity_cP    # afgeleide kwaliteit
DairyWorks/Vla/Cooling/cooler-01/Status/temp_C
DairyWorks/Vla/Filling/filler-01/Status/packs_total
DairyWorks/Vla/Batch/Status/state          # IDLE | DOSING | COOKING | COOLING | FILLING | COMPLETE
DairyWorks/Vla/Batch/Status/batch_id       # actieve batch-id
```

## De Solve-test (het bewijs dat de data layer waarde levert)
De zetmeel-verstijfseling hangt aan het **halen van de kooktemperatuur**. Drijft de kooktemp weg
(verkeerd setpoint, of een vervuilende warmtewisselaar), dan verstijfselt het zetmeel onvoldoende
en wordt de **vla te dun** (viscositeit onder spec).

> **Solve-vraag:** *"Kan de operator een batch vasthouden of herkoken vóórdat dunne vla de vuller
> bereikt, in plaats van het te horen via een klant-klacht?"*

Dat is precies waarom je de data layer wil: de afwijking (kooktemp → viscositeit) zie je real-time
en per batch, niet pas achteraf.

## Hoe dit in de demo-architectuur past
De Vla-fabriek is de **bron**: hij wordt als **OPC-UA server** ontsloten (zwarte doos met knoppen).
**MonsterMQ** ingest hem via zijn **native OPC-UA-client** naar de **data layer / UNS-bus** (+ archive
naar MongoDB voor context); een **bridge (MQTT → line-protocol)** schrijft de time-series naar
**TDengine** (historian), en **Grafana** (trends/productie) + **BIRT** (batch/shift-rapporten) + AI
consumeren vanaf de UNS. Zie [`architecture.html`](../../../strategy-os/user-workspace/Meetings/2026-06-30-eugene/architecture.html).

> **Gebouwd** in [`scenarios/vla-batch/`](../../scenarios/vla-batch/) (2026-07-01): `factory/` (asyncua
> OPC-UA server + 5-staps batch-physics, lifecycle IDLE→DOSING→COOKING→COOLING→FILLING→COMPLETE),
> `batch-engine/` (FastAPI MES + BIRT-stijl PDF), `monstermq-init/` (native OPC-UA ingest),
> `connector/` (optionele fallback), `dashboard/` + `grafana/`. Deploy: `scenarios/vla-batch/DEPLOY.md`.
