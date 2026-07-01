# batch-engine — Vla Batch v2 MES-laag

FastAPI MES-laag voor de Vla-demo. Reframe van de v1 `mes-engine` (order → batch,
EBR → BIRT-stijl batch-rapport). Consumeert de UNS (MonsterMQ) als data-layer en
schrijft domein-collections naar MongoDB. **Offline-first**: draait volledig
zonder Mongo én zonder MQTT (in-memory store + no-op bus + gefabriceerde
telemetrie).

## Rol in de v2-architectuur

```
vla-factory (OPC-UA) → opcua-uns-connector → MonsterMQ (UNS) ─┬─► batch-engine (dit component)
                                                              └─► Grafana / dashboard
```

De batch-engine:
1. maakt een batch uit een recept (`chocolate-vla-1L`), schaalt de doses naar `planned_L`;
2. pusht dose- + cook/cool-setpoints als `SetSetpoint` Commands en start via de
   line-level `StartBatch(recipeId)` (MQTT `DairyWorks/Vla/Batch/Command/StartBatch`);
3. volgt UNS-telemetrie (`GET /tags` snapshot-cache) voor state / peak-cook-temp /
   hold / viscositeit / packs, boekt dose-actuals + samples;
4. bepaalt het verdict (§verdict-regel) en genereert een BIRT-stijl batch-rapport
   (PDF via reportlab + JSON).

## REST (base `/api/v1`)

| Method | Path | Beschrijving |
|--------|------|--------------|
| GET  | `/health` | `{status:"ok"}` |
| GET  | `/tags` | snapshot laatste UNS-waarden (dict topic→value) |
| GET  | `/batches` | lijst (batch_id, recipe_id, product_name, state, started_at, verdict, packs_total) |
| POST | `/batches` | body `{recipe_id, planned_L?}` → maakt (+auto-start) → `{batch_id, state}` |
| GET  | `/batches/{id}` | volledige batch + doses + samples + peak_cook_temp/hold/viscosity + packs + verdict |
| POST | `/batches/{id}/start` | `StartBatch` → drijft de batch naar COMPLETE |
| GET  | `/samples?batch_id=` | samples |
| POST | `/samples` | body `{batch_id, sample_type}` → ad-hoc sample |
| GET  | `/report/{id}?format=pdf\|json` | BIRT-stijl rapport |
| POST | `/admin/command` | body `{target:"batch"\|equipment, command, value?}` → MQTT Command |

Domein-collections (Mongo `idp`): `vla_batches, vla_recipes, vla_materials,
vla_doses, vla_production, vla_samples, vla_events, vla_alarms`.

## Verdict-regel (§contract)

`end_viscosity_cP < spec_min` (150 cP) **of** unresolved CRITICAL → REJECTED ·
afwijking (out-of-tol dose / warning / failed sample) → HOLD · pending sample →
PENDING · alles OK → APPROVED.

De **Solve**: `cook_undertemp` capt de piek-kooktemp → gelatinisatie laag →
`end_viscosity_cP < 150` → out-of-spec → CRITICAL alarm → verdict REJECTED
(operator moet vasthouden/herkoken).

## Draaien

```bash
# lokaal (offline; in-memory + no-op bus)
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
# → http://localhost:8000/api/v1/health

# via docker-compose overlay (samen met slim-base)
docker compose -f docker-compose.slim.yml -f scenarios/vla-batch/docker-compose.vla.yml up -d --build batch-engine
```

Env: `MONGO_URL` (bv. `mongodb://root:example@mongo:27017`), `MONGO_DB` (default
`idp`), `MQTT_HOST` (default `monstermq`), `MQTT_PORT` (1883), `MQTT_WAIT_S` (3.0),
`AUTO_START` (1 → `POST /batches` start meteen).

## Testen

```bash
python selftest.py    # offline PASS/FAIL — geen Mongo/MQTT nodig
```

Dekt: recept-seed + dose-scaling · viscositeit-physics · normale run → APPROVED ·
cook_undertemp → lage viscositeit → HOLD/REJECTED (Solve) · JSON + PDF rapport ·
`import app` + verdict-regel-assertie.
