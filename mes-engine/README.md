# mes-engine — DairyWorks order-centric MES layer

Order-centric **MES** service for the DairyWorks batch-dairy demo. It sits on top
of the existing **packml-sim** (libremfg PackML-MQTT simulator) and turns the
ISA-95 factory model into running work orders: recipe-explode → consume → process
→ produce → HU/SSCC → sample → OEE → batch-verdict → EBR.

Anonymized / generic (DairyWorks). No real client, vendor, IP or schema names.

## What it does

Given a recipe + planned quantity, the **OrderRunner** drives a work order through
its `recipe.process_path`:

```
NEW → RELEASED → STARTED → COMPLETED → CLOSED
```

For each order it:
- explodes the recipe BOM into a `dw_job_bom` scaled to `planned_qty`
- books **Consumption** rows (actual within tolerance, small variance, occasional out-of-tolerance)
- commands the relevant sim units over MQTT (`Start`) — no-op if no broker
- books **Production** + one **HandlingUnit** per pallet, each with a valid 18-digit **SSCC** (GS1 mod-10 check digit)
- schedules **Samples** per phase from the model's `sample_types`
- records **BatchEvents**, computes **OEE** (A×P×Q), raises **BatchAlarms**
- applies the **batch-verdict** rule:
  - unresolved **Critical** → `REJECTED`
  - Warning (High/Medium, or out-of-tolerance, or failed sample) → `HOLD`
  - pending sample → `PENDING`
  - all OK → `APPROVED`
- **Solve (SOLVE-HTST):** if the pasteurizer HTST temp drops below **71.5 °C**, it raises a
  CRITICAL alarm, triggers auto-divert + audit-trail, and the batch is REJECTED.

Everything works **fully offline** (pure-simulation mode): with no broker and no
Mongo it fabricates the process values it would otherwise read from the sim and
persists to an in-memory store. That is what the demo and tests use.

## Conventions (from the factory model)

- UNS status: `DairyWorks/Plant/{Area}/{equipment}/Status/{tag}`
- UNS command: `DairyWorks/Plant/{Area}/{equipment}/Command/{cmd}` (payload string)
- Areas: `Storage · Preparation · Processing · Packaging`
- Sim commands used: `Start`, `Fault/Inject` (`{"fault":..,"magnitude":..}`)

Single source of truth: `../scenarios/dairyworks/factory-model/isa95-dairyworks.json`.

## Run

### Local (in-memory, no broker)

```bash
pip install -r requirements.txt
uvicorn app:app --reload
# http://localhost:8000/health  ·  http://localhost:8000/docs
```

### Docker

```bash
docker build -t mes-engine .
docker run -p 8000:8000 -e MQTT_HOST=monstermq mes-engine
```

### Env

| Var | Default | Meaning |
|-----|---------|---------|
| `FACTORY_MODEL` | scenarios path | override model JSON location |
| `MONGO_URL` | *(unset)* | if set + reachable → Mongo backend, else in-memory |
| `MQTT_HOST` | `monstermq` | broker host (offline-safe if absent) |
| `MQTT_PORT` | `1883` | broker port |
| `MQTT_WAIT_S` | `3.0` | seconds to wait for broker before degrading |

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/health` | status, db backend, mqtt connected, recipes, units |
| GET  | `/orders` | list work orders |
| GET  | `/orders/{id}` | full order bundle (bom, consumptions, HUs, samples, alarms, oee) |
| POST | `/orders` | `{recipe_id, planned_qty}` → create **and auto-run** |
| POST | `/orders/{id}/start` | (re)run an order |
| GET  | `/oee` | all OEE rows |
| GET  | `/samples?order_id=` | samples (optionally filtered) |
| GET  | `/ebr/{id}?fmt=html\|json` | Electronic Batch Record |
| POST | `/admin/command` | `{equipment, command, payload}` → publish MQTT command |
| POST | `/admin/fault` | `{equipment, fault, magnitude}` → inject sim fault |

CORS is enabled (`*`).

## Collections

`dw_work_orders · dw_job_bom · dw_item_cons · dw_item_prod · dw_handling_units ·
dw_sscc · dw_samples · dw_oee · dw_batch_events · dw_batch_alarms`

## Self-test (offline)

```bash
python selftest.py
```

Verifies: SSCC build+validate, model load + recipe-explode scaling, OrderRunner
end-to-end (incl. forced HTST-Solve → REJECTED), EBR HTML render, and `import app`
without a broker. Requires no running Mongo/MQTT.

## Package layout

```
app.py                 FastAPI app + startup wiring + background order-runner
mes/model.py           load factory-model.json; recipes/units/materials/area_of
mes/sscc.py            build_sscc / validate_sscc (GS1 mod-10 check digit)
mes/db.py              Mongo (pymongo) or in-memory store, same API
mes/orders.py          OrderRunner — order FSM + recipe-explode + simulation
mes/oee.py             oee(availability, performance, quality)
mes/ebr.py             assemble EBR + render HTML (PDF via weasyprint if present)
mes/bus.py             MQTT client (paho) — tag cache + commands + engine events
```
