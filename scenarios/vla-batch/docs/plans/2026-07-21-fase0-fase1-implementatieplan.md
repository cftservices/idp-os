# Vla Batch Demo — Fase 0 (drift-fix) + Fase 1 (orders & scan-flow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the existing vla-batch build (`sub-os/idp-os/scenarios/vla-batch/`) exactly in line with the approved v0.4 specs (datalayer repo: PRD PR-01..35, FDS, 05-Backend, 06-Model), then add the first missing MES layer: production orders + the scan-driven shop-floor flow.

**Architecture:** The batch-engine (FastAPI, `batch-engine/vla/`) is the MES layer on top of the factory-as-OPC-UA-server + MonsterMQ UNS. Fase 0 renames Mongo collections to `dw_*`, aligns API contracts with 05-Backend, and adds the 06-Model master-data fields. Fase 1 adds `vla/orders.py` (order lifecycle) and `vla/scan.py` (scan-flow) plus dashboard screens. Everything stays offline-first (in-memory DB + no-op bus when Mongo/MQTT absent).

**Tech Stack:** Python 3.11+, FastAPI, pydantic, paho-mqtt (optional), pymongo (optional), reportlab, pytest (new, dev-only), nginx static SPA (vanilla JS), MonsterMQ (GraphQL init), Docker Compose.

**Design doc:** `c:\tools\techflow-os\sub-os\project-os\projects\datalayer\09-Build\2026-07-21-bouwdesign-fase0-fase1.md`

## Global Constraints

- **Working dir for all engine commands:** `c:\tools\techflow-os\sub-os\idp-os\scenarios\vla-batch\batch-engine` (PowerShell: `cd` there first).
- **Offline-first is non-negotiable:** every feature must work with `db.backend == "memory"` and `bus=None`/disconnected. `python selftest.py` must exit 0 without Mongo/MQTT/factory.
- **Collections rename to `dw_*` — but container/image/service names keep the `vla-` prefix** (only MongoDB collection names + the archive group change).
- **UNS canon:** topics are `DairyWorks/Vla/{Area}/{Equipment}/Status/{tag}`; line-level batch = `DairyWorks/Vla/Batch/Status/{tag}`; new order topics = `DairyWorks/Vla/Orders/{order_id}/Status/{tag}`.
- **Tolerances are percent values** (e.g. `2.0` = 2%), per material, fields `tolerance_pos_pct`/`tolerance_neg_pct` (06-Model B.1). The old fractional `tol_pct=0.02` disappears.
- **Sample types (exactly these 4):** `dose_check | cook_temp | hold | viscosity` (06-Model B.1).
- **Order lifecycle:** `OPEN | RUNNING | DONE`; no bookings on a COMPLETE batch or DONE order (FDS mapping table).
- **Verdict rule unchanged** (`vla/batches.py::_verdict` stays as-is).
- **Code + comments in English** (repo convention). No employer/vendor names from the confidential source docs anywhere (anonymization rule).
- **NEVER Node-RED.** No new brokers/services; reportlab stays the BIRT stand-in.
- **Repo:** commits go to the idp-os repo (`c:\tools\techflow-os\sub-os\idp-os`, branch main). Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- **Tests:** new unit tests in `batch-engine/tests/` (pytest); `python selftest.py` remains the integration gate and must pass at the END of every task.

---

### Task 1: Test scaffolding + `dw_*` collection rename

**Files:**
- Create: `batch-engine/requirements-dev.txt`
- Create: `batch-engine/tests/__init__.py` (empty)
- Create: `batch-engine/tests/test_db.py`
- Modify: `batch-engine/vla/db.py` (COLLECTIONS list, docstring, seed)
- Modify: `batch-engine/vla/batches.py` (all `self.db.vla_*` refs)
- Modify: `batch-engine/selftest.py` (no direct `vla_*` refs exist, but run it)

**Interfaces:**
- Produces: `Database` attribute access `db.dw_batches`, `db.dw_recipes`, `db.dw_materials`, `db.dw_doses`, `db.dw_production`, `db.dw_samples`, `db.dw_batch_events`, `db.dw_alarms`, `db.dw_orders`, `db.dw_equipment_state`. Later tasks use exactly these names.
- Note: `vla_events` → `dw_batch_events` (05-Backend name), `vla_production` → `dw_production`. `dw_orders` + `dw_equipment_state` are new (empty until Tasks 4/7).

- [ ] **Step 1: Create dev requirements + test package**

`batch-engine/requirements-dev.txt`:
```
pytest>=8.0
```
Create empty `batch-engine/tests/__init__.py` and `batch-engine/tests/conftest.py`:
```python
import os

# Keep app startup fast + fully offline in tests: no MQTT wait, no Mongo.
os.environ.setdefault("MQTT_WAIT_S", "0")
os.environ.pop("MONGO_URL", None)
```
Install: `pip install -r requirements-dev.txt`

- [ ] **Step 2: Write the failing test**

`batch-engine/tests/test_db.py`:
```python
"""dw_* collection contract (05-Backend §3)."""
from vla.db import get_db, seed_recipes, COLLECTIONS

EXPECTED = [
    "dw_batches", "dw_recipes", "dw_materials", "dw_doses", "dw_production",
    "dw_samples", "dw_batch_events", "dw_alarms", "dw_orders", "dw_equipment_state",
]


def test_collections_are_dw_prefixed():
    assert COLLECTIONS == EXPECTED


def test_attribute_access_and_seed():
    db = get_db(mongo_url=None)  # force in-memory
    seed_recipes(db)
    assert db.backend == "memory"
    assert db.dw_recipes.count_documents({}) >= 1
    assert db.dw_orders.count_documents({}) == 0
    assert db.dw_equipment_state.count_documents({}) == 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py -q`
Expected: FAIL (`COLLECTIONS` still `vla_*`, `dw_orders` AttributeError)

- [ ] **Step 4: Rename collections in db.py**

In `vla/db.py` replace the COLLECTIONS list:
```python
COLLECTIONS = [
    "dw_batches",
    "dw_recipes",
    "dw_materials",
    "dw_doses",
    "dw_production",
    "dw_samples",
    "dw_batch_events",
    "dw_alarms",
    "dw_orders",
    "dw_equipment_state",
]
```
Update the module docstring collection list the same way. In `seed_recipes()` replace `db.vla_recipes` → `db.dw_recipes` (2×) and `db.vla_materials` → `db.dw_materials` (2×).

- [ ] **Step 5: Rename all references in batches.py**

Mechanical replace in `vla/batches.py` (only these five names occur):
`self.db.vla_batches` → `self.db.dw_batches` · `self.db.vla_doses` → `self.db.dw_doses` · `self.db.vla_samples` → `self.db.dw_samples` · `self.db.vla_alarms` → `self.db.dw_alarms` · `self.db.vla_events` → `self.db.dw_batch_events`.
Also update the docstring bullet mentioning collection names.

- [ ] **Step 6: Run tests + selftest**

Run: `python -m pytest tests -q` → PASS. Run: `python selftest.py` → `RESULT: ALL PASS`.
Also: `Select-String -Path vla\*.py -Pattern 'db\.vla_'` → no matches.

- [ ] **Step 7: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "refactor(vla): rename Mongo collections to dw_* per 05-Backend §3 (fase 0.1/0.2)"
```

---

### Task 2: Material-master + per-material tolerances + Recipe release-gate

**Files:**
- Modify: `batch-engine/vla/model.py` (Material, Dose, Recipe dataclasses + seeds)
- Modify: `batch-engine/vla/db.py` (seed_recipes: extra fields)
- Modify: `batch-engine/vla/batches.py` (create_batch: release-gate + dose rows; `_book_doses` tolerance fields)
- Test: `batch-engine/tests/test_master_data.py`

**Interfaces:**
- Consumes: `db.dw_recipes`, `db.dw_materials` (Task 1).
- Produces: `Material(material_id, name, uom, category, tolerance_pos_pct, tolerance_neg_pct, density_kg_L, whole_bag, bag_size_kg, shelf_life_days, stock_qty, reorder_level)`; `Dose(material_id, qty_target, qty_actual, tol_pos_pct, tol_neg_pct, uom, source_equipment, lot_no, operator_id)` with properties `tol_min`/`tol_max`; `Recipe.status` (str, default `"released"`); constant `M.SAMPLE_TYPES`; `M.FINISHED_GOOD_ID = "vla-1L"`. `create_batch` raises `ValueError("recipe <id> is not released (status=<s>)")` when status != released. Dose rows in `dw_doses` now carry `tol_pos_pct`, `tol_neg_pct`, `source_equipment: None`, `lot_no: None`, `operator_id: None`, `staged: []`, `qty_prepared: 0.0`.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_master_data.py`:
```python
import random
import pytest

from vla import model as M
from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner


def make_runner():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    return db, BatchRunner(db, bus=None, rng=random.Random(1))


def test_material_master_fields():
    milk = M.MATERIALS["milk"]
    assert milk.category == "LiquidBase" and milk.density_kg_L == 1.03
    starch = M.MATERIALS["starch"]
    assert starch.whole_bag is True and starch.bag_size_kg == 25.0
    assert M.MATERIALS["cocoa"].tolerance_pos_pct == 1.0
    assert M.FINISHED_GOOD_ID in M.MATERIALS  # finished good in the master (PR-27)


def test_dose_tolerance_comes_from_master():
    r = M.get_recipe("chocolate-vla-1L")
    doses = {d.material_id: d for d in r.scaled_doses(5000)}
    # milk: tol 1.0% -> 5000 +/- 50
    assert doses["milk"].tol_min == 4950.0 and doses["milk"].tol_max == 5050.0
    # cocoa: tol 1.0%/1.0% -> 100 +/- 1
    assert doses["cocoa"].tol_min == 99.0 and doses["cocoa"].tol_max == 101.0


def test_release_gate_blocks_unreleased_recipe():
    db, runner = make_runner()
    db.dw_recipes.update_one({"recipe_id": "chocolate-vla-1L"},
                             {"$set": {"status": "draft"}})
    with pytest.raises(ValueError, match="not released"):
        runner.create_batch("chocolate-vla-1L", planned_L=5000)


def test_released_recipe_still_creates_batch():
    db, runner = make_runner()
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    assert b["state"] == "IDLE"
    row = db.dw_doses.find_one({"batch_id": b["batch_id"], "material_id": "milk"})
    assert row["tol_pos_pct"] == 1.0 and row["staged"] == [] and row["qty_prepared"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_master_data.py -q` → FAIL (`category` attribute missing).

- [ ] **Step 3: Extend model.py dataclasses + seeds**

Replace the `Material` and `Dose` dataclasses in `vla/model.py`:
```python
@dataclass
class Material:
    """Material-master row (06-Model B.1, MES practice pattern 1)."""
    material_id: str
    name: str
    uom: str = "kg"
    category: str = "DryPowder"          # LiquidBase | DryPowder | FinishedGood
    tolerance_pos_pct: float = 2.0       # percent, per material
    tolerance_neg_pct: float = 2.0
    density_kg_L: Optional[float] = None # liquids only (kg<->L)
    whole_bag: bool = False              # bagged goods: book n x bag_size_kg
    bag_size_kg: Optional[float] = None
    shelf_life_days: Optional[int] = None
    stock_qty: float = 0.0               # PR-27 inventory
    reorder_level: float = 0.0


@dataclass
class Dose:
    """One recipe dose (target) and its booked actual, tolerances from the master."""
    material_id: str
    qty_target: float
    qty_actual: Optional[float] = None
    tol_pos_pct: float = 2.0
    tol_neg_pct: float = 2.0
    uom: str = "kg"
    source_equipment: Optional[str] = None
    lot_no: Optional[str] = None
    operator_id: Optional[str] = None

    @property
    def tol_min(self) -> float:
        return round(self.qty_target * (1.0 - self.tol_neg_pct / 100.0), 4)

    @property
    def tol_max(self) -> float:
        return round(self.qty_target * (1.0 + self.tol_pos_pct / 100.0), 4)

    def in_tolerance(self) -> bool:
        if self.qty_actual is None:
            return True
        return self.tol_min <= self.qty_actual <= self.tol_max
```
Add to `Recipe`: field `status: str = "released"  # draft|approved|released (pattern 2)`.
In `Recipe.scaled_doses` copy tolerances from the master:
```python
    def scaled_doses(self, planned_L: float) -> list[Dose]:
        scale = float(planned_L) / float(self.basis_L) if self.basis_L else 1.0
        out: list[Dose] = []
        for d in self.doses:
            mat = MATERIALS.get(d.material_id)
            out.append(Dose(
                material_id=d.material_id,
                qty_target=round(d.qty_target * scale, 4),
                tol_pos_pct=mat.tolerance_pos_pct if mat else 2.0,
                tol_neg_pct=mat.tolerance_neg_pct if mat else 2.0,
                uom=d.uom,
            ))
        return out
```
Replace the `MATERIALS` seed:
```python
FINISHED_GOOD_ID = "vla-1L"

MATERIALS = {
    "milk":   Material("milk", "Milk", "kg", category="LiquidBase",
                       tolerance_pos_pct=1.0, tolerance_neg_pct=1.0,
                       density_kg_L=1.03, stock_qty=20000.0, reorder_level=6000.0),
    "sugar":  Material("sugar", "Sugar", "kg", category="DryPowder",
                       tolerance_pos_pct=0.5, tolerance_neg_pct=0.5,
                       shelf_life_days=365, stock_qty=2000.0, reorder_level=600.0),
    "starch": Material("starch", "Modified Starch", "kg", category="DryPowder",
                       tolerance_pos_pct=0.5, tolerance_neg_pct=0.5,
                       whole_bag=True, bag_size_kg=25.0,
                       shelf_life_days=365, stock_qty=1000.0, reorder_level=300.0),
    "cocoa":  Material("cocoa", "Cocoa", "kg", category="DryPowder",
                       tolerance_pos_pct=1.0, tolerance_neg_pct=1.0,
                       shelf_life_days=540, stock_qty=400.0, reorder_level=120.0),
    FINISHED_GOOD_ID: Material(FINISHED_GOOD_ID, "Chocolate Vla 1L", "pack",
                               category="FinishedGood", shelf_life_days=21,
                               stock_qty=0.0, reorder_level=0.0),
}

SAMPLE_TYPES = ["dose_check", "cook_temp", "hold", "viscosity"]  # 06-Model B.1
```

- [ ] **Step 4: Seed the new fields in db.py**

In `seed_recipes()` add `"status": r.status,` to the recipe doc and extend the material doc:
```python
            db.dw_materials.insert_one({
                "material_id": m.material_id, "name": m.name, "uom": m.uom,
                "category": m.category,
                "tolerance_pos_pct": m.tolerance_pos_pct,
                "tolerance_neg_pct": m.tolerance_neg_pct,
                "density_kg_L": m.density_kg_L,
                "whole_bag": m.whole_bag, "bag_size_kg": m.bag_size_kg,
                "shelf_life_days": m.shelf_life_days,
                "stock_qty": m.stock_qty, "reorder_level": m.reorder_level,
            })
```

- [ ] **Step 5: Release-gate + dose-row fields in batches.py**

In `create_batch`, directly after the `recipe is None` check:
```python
        rec_doc = self.db.dw_recipes.find_one({"recipe_id": recipe_id})
        status = (rec_doc or {}).get("status", recipe.status)
        if status != "released":
            raise ValueError(f"recipe {recipe_id!r} is not released (status={status})")
```
Replace the dose-row insert:
```python
        for d in scaled:
            self.db.dw_doses.insert_one({
                "batch_id": batch_id,
                "material_id": d.material_id,
                "qty_target": d.qty_target,
                "qty_actual": None,
                "tol_min": d.tol_min,
                "tol_max": d.tol_max,
                "tol_pos_pct": d.tol_pos_pct,
                "tol_neg_pct": d.tol_neg_pct,
                "uom": d.uom,
                "in_tolerance": None,
                "source_equipment": None,
                "lot_no": None,
                "operator_id": None,
                "staged": [],
                "qty_prepared": 0.0,
            })
```
In `_book_doses`, when booking an actual, also default the source:
```python
            self.db.dw_doses.update_one(
                {"batch_id": batch_id, "material_id": line["material_id"]},
                {"$set": {"qty_actual": actual, "in_tolerance": in_tol,
                          "source_equipment": line.get("source_equipment") or "dosing-unit",
                          "ts": _iso()}},
            )
```
and skip already-booked lines (scan-flow commits set qty_actual earlier): at the top of the loop add
```python
            if line.get("qty_actual") is not None:
                continue
```
In `get_batch`'s dose projection add the new fields: `"lot_no": d.get("lot_no"), "source_equipment": d.get("source_equipment"), "operator_id": d.get("operator_id"),`.

- [ ] **Step 6: Run tests + selftest**

`python -m pytest tests -q` → PASS. `python selftest.py` → ALL PASS (selftest uses in-tolerance telemetry; milk 5000.0 within ±1%).

- [ ] **Step 7: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): material-master tolerances + recipe release-gate + dose provenance fields (fase 0.5-0.7)"
```

---

### Task 3: Four sample types + label_printed + reworked sample plan

**Files:**
- Modify: `batch-engine/vla/batches.py` (`_SAMPLE_PLAN`, `_take_sample`, `_book_doses`, `_finalize`, `take_sample`)
- Modify: `batch-engine/vla/model.py` (nothing new — `SAMPLE_TYPES` exists from Task 2)
- Modify: `batch-engine/app.py` (`TakeSample` model validation)
- Modify: `batch-engine/selftest.py` (sample expectations)
- Test: `batch-engine/tests/test_samples.py`

**Interfaces:**
- Consumes: `M.SAMPLE_TYPES` (Task 2).
- Produces: sample rows carry `label_printed: bool` (default True — the demo prints on creation) and `operator_id`. `_take_sample(batch_id, sample_type, phase, value=None, unit=None, spec_min=None, spec_max=None, ok=None, operator_id=None)` — `ok` forces status approved/failed when no value/spec pair; one-sided specs allowed (spec_max may be None). A full run books exactly 4 samples: `dose_check` (DOSING), `cook_temp` + `hold` (COOKING), `viscosity` (COOLING). `POST /samples` rejects unknown types with 400. Events: `sample_taken` (existing) + `sample_label_printed`.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_samples.py`:
```python
import random

from vla import model as M
from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner

TELEM_OK = {
    "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
    "packs_total": 4980, "reject_count": 20,
    "dose_actuals": {"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "cocoa": 100.0},
}


def run_ok_batch():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(3))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    return db, runner, runner.start_batch(b["batch_id"], telemetry=TELEM_OK)


def test_full_run_books_the_four_spec_sample_types():
    _, _, res = run_ok_batch()
    types = sorted(s["sample_type"] for s in res["samples"])
    assert types == sorted(M.SAMPLE_TYPES)
    assert all(s["label_printed"] is True for s in res["samples"])
    assert res["verdict"] == "APPROVED"


def test_cook_temp_sample_fails_on_undertemp():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(4))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    res = runner.start_batch(b["batch_id"], telemetry={
        "fault": "cook_undertemp", "magnitude": 0.6,
        "hold_elapsed_sec": 300.0, "packs_total": 4900, "reject_count": 100})
    cook = [s for s in res["samples"] if s["sample_type"] == "cook_temp"][0]
    assert cook["status"] == "failed"
    assert res["verdict"] in ("REJECTED", "HOLD")
```

- [ ] **Step 2: Run test to verify it fails**

`python -m pytest tests/test_samples.py -q` → FAIL (types are `in-process-viscosity`/`finished-product`; no `label_printed`).

- [ ] **Step 3: Rework the sample plan in batches.py**

Replace `_SAMPLE_PLAN` (module level) with a comment only — the plan is now inline:
```python
# QA sample plan (06-Model: dose_check | cook_temp | hold | viscosity) is booked
# inline: dose_check at end of DOSING, cook_temp + hold at cook capture,
# viscosity during COOLING (the Solve input).
```
Replace `_take_sample` with:
```python
    def _take_sample(self, batch_id: str, sample_type: str, phase: str,
                     value: Optional[float] = None, unit: Optional[str] = None,
                     spec_min: Optional[float] = None,
                     spec_max: Optional[float] = None,
                     ok: Optional[bool] = None,
                     operator_id: Optional[str] = None) -> dict:
        sample_id = f"S-{batch_id}-{uuid.uuid4().hex[:5].upper()}"
        status, result = "completed", "pass"
        if ok is not None:
            status, result = ("approved", "pass") if ok else ("failed", "fail")
        elif value is not None and (spec_min is not None or spec_max is not None):
            in_spec = ((spec_min is None or value >= spec_min)
                       and (spec_max is None or value <= spec_max))
            status, result = ("approved", "pass") if in_spec else ("failed", "fail")
        row = {
            "sample_id": sample_id,
            "batch_id": batch_id,
            "sample_type": sample_type,
            "phase": phase,
            "status": status,
            "result": result,
            "value": value,
            "unit": unit,
            "label_printed": True,
            "operator_id": operator_id,
            "ts": _iso(),
        }
        self.db.dw_samples.insert_one(row)
        self._event(batch_id, "sample_taken",
                    {"sample_type": sample_type, "status": status, "value": value})
        self._event(batch_id, "sample_label_printed",
                    {"sample_id": sample_id, "sample_type": sample_type})
        if self.control is not None:
            self.control.take_sample(sample_type)
        if self.bus is not None:
            self.bus.take_sample(sample_type)
        return row
```
At the end of `_book_doses` (after the loop) add the dose_check sample:
```python
        rows = self.db.dw_doses.find({"batch_id": batch_id})
        all_in_tol = all(r.get("in_tolerance") is not False for r in rows)
        self._take_sample(batch_id, "dose_check", M.DOSING, ok=all_in_tol)
```
In `_finalize`, after the `cook_captured` event (and its fault alarm block), add:
```python
        self._take_sample(batch_id, "cook_temp", M.COOKING,
                          value=peak_temp, unit="C",
                          spec_min=recipe.cook_setpoint_C - 5.0,
                          spec_max=recipe.cook_setpoint_C + 5.0)
        self._take_sample(batch_id, "hold", M.COOKING,
                          value=hold_elapsed, unit="s",
                          spec_min=recipe.hold_sec * 0.95)
```
Change the COOLING viscosity sample call from `"in-process-viscosity"` to `"viscosity"`. **Delete** the `finished-product` sample call in the FILLING block.
In `take_sample` (ad-hoc API path) validate the type:
```python
    def take_sample(self, batch_id: str, sample_type: str,
                    operator_id: Optional[str] = None) -> dict:
        if sample_type not in M.SAMPLE_TYPES:
            raise ValueError(f"unknown sample_type {sample_type!r} "
                             f"(allowed: {', '.join(M.SAMPLE_TYPES)})")
        batch = self._raw_batch(batch_id)
        phase = batch["state"] if batch else M.IDLE
        return self._take_sample(batch_id, sample_type, phase, operator_id=operator_id)
```

- [ ] **Step 4: API validation in app.py**

Change `TakeSample` model and endpoint:
```python
class TakeSample(BaseModel):
    batch_id: str
    sample_type: str = "viscosity"
    operator_id: str | None = None
```
```python
@app.post(f"{API}/samples")
def take_sample(body: TakeSample):
    runner = _runner()
    if runner.get_batch(body.batch_id) is None:
        raise HTTPException(404, f"batch {body.batch_id} not found")
    try:
        return runner.take_sample(body.batch_id, body.sample_type, body.operator_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
```

- [ ] **Step 5: Update selftest expectations**

In `selftest.py` check 3: replace `n_samples >= 2` context — set `n_samples = len(res["samples"])` unchanged but assert `n_samples == 4`. In check 8 replace `ctl.take_sample("in-process-viscosity")` with `ctl.take_sample("viscosity")`.

- [ ] **Step 6: Run tests + selftest**

`python -m pytest tests -q` → PASS. `python selftest.py` → ALL PASS.

- [ ] **Step 7: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): four spec sample types + label_printed + phase-anchored sample plan (fase 0.8)"
```

---

### Task 4: dw_production bookings + equipment-state feed

**Files:**
- Modify: `batch-engine/vla/batches.py` (`_finalize` production booking; `_update` equipment states; new `_book_production`)
- Test: `batch-engine/tests/test_production_state.py`

**Interfaces:**
- Consumes: `db.dw_production`, `db.dw_equipment_state` (Task 1).
- Produces: `BatchRunner._book_production(batch_id, packs, rejects, source, operator_id=None) -> dict` inserting `{batch_id, packs, reject_count, pack_size_L: 1, source, operator_id, ts}` into `dw_production` and incrementing `batch.packs_total`; `production_booked` event. Auto path (`_finalize`) books with `source="filler_counter"`. `dw_equipment_state` gets a history row per equipment on every batch-state change: `{equipment_id, area, state: "Running"|"Idle", ts}`. Helper `EQUIPMENT_IDS` list in batches.py. Later tasks call `_book_production(..., source="operator_booking", operator_id=...)`.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_production_state.py`:
```python
import random

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def test_auto_production_booked_with_source_filler_counter():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(5))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    rows = db.dw_production.find({"batch_id": b["batch_id"]})
    assert len(rows) == 1
    assert rows[0]["source"] == "filler_counter" and rows[0]["packs"] == 4980


def test_equipment_state_history_written():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(6))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    states = db.dw_equipment_state.find({"equipment_id": "cook-unit-01"})
    assert any(s["state"] == "Running" for s in states)
    assert states[-1]["state"] == "Idle"  # back to Idle on COMPLETE
    assert states[0]["area"] == "Cook"
```

- [ ] **Step 2: Run test to verify it fails**

`python -m pytest tests/test_production_state.py -q` → FAIL (dw_production empty).

- [ ] **Step 3: Implement production booking + state feed**

In `vla/batches.py` add at module level (below `_DOSE_TARGET`):
```python
# Equipment covered by the state feed (06-Model EquipmentState).
EQUIPMENT_IDS = ["receiving-tank-01", "process-tank-01", "cook-unit-01",
                 "cooler-01", "filler-01"]

# Which equipment is Running in which batch state.
_RUNNING_IN_STATE = {
    M.DOSING: {"receiving-tank-01", "process-tank-01"},
    M.COOKING: {"cook-unit-01"},
    M.COOLING: {"cooler-01"},
    M.FILLING: {"filler-01"},
}
```
Add methods to `BatchRunner`:
```python
    def _book_production(self, batch_id: str, packs: int, rejects: int,
                         source: str, operator_id: Optional[str] = None) -> dict:
        """Book produced finished goods (06-Model Production; source =
        filler_counter | operator_booking)."""
        row = {
            "batch_id": batch_id,
            "packs": int(packs),
            "reject_count": int(rejects),
            "pack_size_L": 1,
            "source": source,
            "operator_id": operator_id,
            "ts": _iso(),
        }
        self.db.dw_production.insert_one(row)
        total = sum(p["packs"] for p in
                    self.db.dw_production.find({"batch_id": batch_id}))
        self._update(batch_id, {"packs_total": total})
        self._event(batch_id, "production_booked",
                    {"packs": int(packs), "source": source,
                     "packs_total": total})
        return row

    def _feed_equipment_state(self, batch_state: str) -> None:
        running = _RUNNING_IN_STATE.get(batch_state, set())
        for eq in EQUIPMENT_IDS:
            self.db.dw_equipment_state.insert_one({
                "equipment_id": eq,
                "area": M.area_of(eq),
                "state": "Running" if eq in running else "Idle",
                "ts": _iso(),
            })
```
In `_update`, after the existing bus mirror, add:
```python
        if "state" in fields:
            self._feed_equipment_state(fields["state"])
```
In `_finalize`, replace the FILLING packs block:
```python
        self._update(batch_id, {"state": M.FILLING})
        self._update(batch_id, {"reject_count": rejects})
        self._book_production(batch_id, packs, rejects, source="filler_counter")
        self._event(batch_id, "filling_done",
                    {"packs_total": packs, "reject_count": rejects})
```
(The `packs_total` field is now maintained by `_book_production`.)

- [ ] **Step 4: Run tests + selftest**

`python -m pytest tests -q` → PASS. `python selftest.py` → ALL PASS (check 3 asserts `packs_total == 4980`, still true).

- [ ] **Step 5: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): dw_production bookings with source + equipment-state history feed (fase 0.2/0.7, PR-17-light)"
```

---

### Task 5: 05-Backend API contracts (`/admin/command`, batch responses) + dashboard calls

**Files:**
- Modify: `batch-engine/app.py` (AdminCommand model + endpoint, create_batch response, get_batch response)
- Modify: `dashboard/index.html` (JS: `lineCmd`, `setSetpoint`, `injectFault`, `clearFault` → new contract)
- Test: `batch-engine/tests/test_api_contract.py`

**Interfaces:**
- Consumes: `BatchRunner` (unchanged), `OpcuaControl`, `VlaBus`.
- Produces: `POST /api/v1/admin/command` accepts `{"equipment_id": str, "cmd": str, "params": {…}|null}` with cmds `start|stop|sample|fault|clear|setpoint`; params: `start:{recipe_id?}`, `sample:{sample_type}`, `fault:{fault_id, magnitude?}`, `setpoint:{target, value}`. `POST /api/v1/batches` returns `{batch_id, state, dose_setpoints:{milk,sugar,starch,cocoa}}`. `GET /api/v1/batches/{id}` includes `telemetry_summary` dict. Task 13 (UI) relies on these shapes.

- [ ] **Step 1: Write the failing test** (FastAPI TestClient; add `httpx` to requirements-dev.txt)

Append to `batch-engine/requirements-dev.txt`: `httpx>=0.27`. Then `pip install -r requirements-dev.txt`.

`batch-engine/tests/test_api_contract.py`:
```python
from fastapi.testclient import TestClient

import app as appmod


def client():
    return TestClient(appmod.app)


def test_create_batch_returns_dose_setpoints_and_telemetry_summary():
    with client() as c:
        r = c.post("/api/v1/batches",
                   json={"recipe_id": "chocolate-vla-1L", "planned_L": 5000})
        assert r.status_code == 200
        body = r.json()
        assert body["dose_setpoints"] == {
            "milk": 5000.0, "sugar": 500.0, "starch": 250.0, "cocoa": 100.0}
        r2 = c.get(f"/api/v1/batches/{body['batch_id']}")
        ts = r2.json()["telemetry_summary"]
        assert set(ts) == {"peak_cook_temp_C", "hold_elapsed_sec",
                           "end_viscosity_cP", "packs_total", "reject_count"}


def test_admin_command_new_contract():
    with client() as c:
        r = c.post("/api/v1/admin/command", json={
            "equipment_id": "cook-unit-01", "cmd": "setpoint",
            "params": {"target": "cook.setpoint_C", "value": 88.0}})
        assert r.status_code == 200 and r.json()["accepted"] is True
        r2 = c.post("/api/v1/admin/command", json={
            "equipment_id": "Batch", "cmd": "fault",
            "params": {"fault_id": "cook_undertemp", "magnitude": 0.6}})
        assert r2.status_code == 200
        r3 = c.post("/api/v1/admin/command", json={
            "equipment_id": "Batch", "cmd": "unknown-cmd"})
        assert r3.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

`python -m pytest tests/test_api_contract.py -q` → FAIL (422 on new body shape; no dose_setpoints).

- [ ] **Step 3: Implement the new contracts in app.py**

Replace `AdminCommand` and the endpoint:
```python
class AdminCommand(BaseModel):
    """05-Backend §4.3 contract: {equipment_id, cmd, params}."""
    equipment_id: str                    # "Batch" | equipment_id
    cmd: str                             # start|stop|sample|fault|clear|setpoint
    params: dict | None = None
```
```python
@app.post(f"{API}/admin/command")
def admin_command(body: AdminCommand):
    """Route a control action to the factory (PRIMARY = direct OPC-UA method;
    MQTT Command publish secondary). Contract 05-Backend §4.3."""
    control = STATE.get("control")
    bus = STATE.get("bus")
    if control is None:
        raise HTTPException(503, "engine not initialized")
    p = body.params or {}
    cmd = body.cmd.lower()

    if cmd == "start":
        recipe_id = str(p.get("recipe_id") or M.RECIPE_CHOCOLATE_VLA_1L.recipe_id)
        result = control.start_batch(recipe_id)
        if bus is not None:
            bus.start_batch(recipe_id)
    elif cmd == "stop":
        runner = _runner()
        active = next((b for b in runner.list_batches()
                       if b["state"] in ("DOSING", "COOKING", "COOLING", "FILLING")),
                      None)
        if active is not None:
            booked = runner.db.dw_production.count_documents(
                {"batch_id": active["batch_id"]})
            if booked == 0:
                raise HTTPException(
                    409, "stop refused: no production booked for active batch "
                         f"{active['batch_id']} (PR-34 stop rule)")
        result = control.stop()
        if bus is not None:
            bus.stop_batch()
    elif cmd == "sample":
        stype = str(p.get("sample_type") or "viscosity")
        if stype not in M.SAMPLE_TYPES:
            raise HTTPException(400, f"unknown sample_type {stype!r}")
        result = control.take_sample(stype)
        if bus is not None:
            bus.take_sample(stype)
    elif cmd == "fault":
        fid = str(p.get("fault_id") or "cook_undertemp")
        mag = float(p.get("magnitude", 0.5) or 0.5)
        result = control.inject_fault(fid, mag)
        if bus is not None:
            bus.inject_fault(fid, mag)
    elif cmd == "clear":
        result = control.clear_fault()
        if bus is not None:
            bus.clear_fault()
    elif cmd == "setpoint":
        target = p.get("target")
        from vla.opcua_control import SETPOINT_TARGETS
        if target not in SETPOINT_TARGETS:
            raise HTTPException(400, f"unknown setpoint target {target!r}")
        try:
            value = float(p.get("value"))
        except (TypeError, ValueError):
            raise HTTPException(400, "setpoint needs a numeric params.value")
        result = control.set_setpoint(target, value)
        if bus is not None:
            bus.set_setpoint(target, value)
    else:
        raise HTTPException(400, f"unknown cmd {body.cmd!r} "
                                 "(allowed: start|stop|sample|fault|clear|setpoint)")

    return {"accepted": True, "path": "opcua", "equipment_id": body.equipment_id,
            "cmd": cmd, "opcua": result}
```
Delete `_COMMAND_ALIASES` (unused now). In `create_batch` return dose setpoints:
```python
    return {"batch_id": batch["batch_id"], "state": batch["state"],
            "dose_setpoints": {d["material_id"]: d["qty_target"]
                               for d in batch["doses"]}}
```
In `get_batch` build the summary:
```python
@app.get(f"{API}/batches/{{batch_id}}")
def get_batch(batch_id: str):
    batch = _runner().get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    batch["telemetry_summary"] = {
        "peak_cook_temp_C": batch.get("peak_cook_temp_C"),
        "hold_elapsed_sec": batch.get("hold_elapsed_sec"),
        "end_viscosity_cP": batch.get("end_viscosity_cP"),
        "packs_total": batch.get("packs_total", 0),
        "reject_count": batch.get("reject_count", 0),
    }
    return batch
```

- [ ] **Step 4: Update dashboard JS to the new contract**

In `dashboard/index.html` replace the admin helper functions (they currently post `{target, command, value}`):
```javascript
async function lineCmd(cmd, sampleType){
  const params = cmd === 'sample' ? {sample_type: sampleType || 'viscosity'} : {};
  try { await post('/admin/command', {equipment_id:'Batch', cmd, params});
        msg('command sent: '+cmd); } catch(e){ msg('command failed: '+e.message); }
}
async function setSetpoint(){
  try { await post('/admin/command', {equipment_id:'Batch', cmd:'setpoint',
        params:{target: $('#spTarget').value, value: parseFloat($('#spValue').value)}});
        msg('setpoint sent'); } catch(e){ msg('setpoint failed: '+e.message); }
}
async function injectFault(){
  try { await post('/admin/command', {equipment_id:'Batch', cmd:'fault',
        params:{fault_id: $('#fault').value, magnitude: parseFloat($('#mag').value)}});
        msg('fault injected'); } catch(e){ msg('fault failed: '+e.message); }
}
async function clearFault(){
  try { await post('/admin/command', {equipment_id:'Batch', cmd:'clear', params:{}});
        msg('faults cleared'); } catch(e){ msg('clear failed: '+e.message); }
}
```
(Keep the existing `msg()` helper if present; otherwise `function msg(t){ $('#adminMsg').textContent=t; }`. Check the existing file — a `msg`-like line exists near the current implementations; match its name.) Update the two `lineCmd('TakeSample','viscosity')` button handlers in the HTML to `lineCmd('sample','viscosity')`, `lineCmd('StartBatch')` → `lineCmd('start')`, `lineCmd('Stop')` → `lineCmd('stop')`.

- [ ] **Step 5: Run tests + selftest**

`python -m pytest tests -q` → PASS. `python selftest.py` → ALL PASS.

- [ ] **Step 6: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine scenarios/vla-batch/dashboard
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): 05-Backend API contracts — admin/command {equipment_id,cmd,params}, dose_setpoints, telemetry_summary + stop rule (fase 0.3/0.4)"
```

---

### Task 6: Archive group rename + config/docs sync (closes Fase 0)

**Files:**
- Modify: `c:\tools\techflow-os\sub-os\idp-os\monstermq\config.yaml` (archive group `vla_data` → `dw_uns_archive`)
- Modify: `scenarios/vla-batch/docker-compose.vla.yml` (comment line 10)
- Modify: `scenarios/vla-batch/.env.example` (collections comment)
- Modify: `scenarios/vla-batch/README.md` (archive references + collection list)

**Interfaces:**
- Produces: MonsterMQ archives `DairyWorks/Vla/#` into Mongo collection `dw_uns_archive` (05-Backend §3). ⚠ On the VPS this is a clean-start change: the old `vla_data` collection stays behind as dead data until dropped at deploy (per design: old demo data may go).

- [ ] **Step 1: Patch the archive group**

In `sub-os/idp-os/monstermq/config.yaml` find the group (line ~47):
```yaml
    # archived to Mongo collection vla_data and bridged to TDengine.
    - Name: vla_data
```
Replace with:
```yaml
    # archived to Mongo collection dw_uns_archive and bridged to TDengine.
    - Name: dw_uns_archive
```
(Only the group name + comment; topic filter `DairyWorks/Vla/#` and retention stay.)

- [ ] **Step 2: Sync comments/docs**

- `docker-compose.vla.yml` line 10: `--archive group vla_data--> mongo (idp.vla_data)` → `--archive group dw_uns_archive--> mongo (idp.dw_uns_archive)`.
- `.env.example` line ~29: replace the collection enumeration with `#  dw_batches, dw_recipes, dw_materials, dw_doses, dw_production, dw_samples, dw_batch_events, dw_alarms, dw_orders, dw_equipment_state) + archive collection dw_uns_archive.`
- `README.md`: update the two `vla_data` architecture-diagram mentions and the config table row (line ~168) to `dw_uns_archive`; update any `vla_*` collection lists to the new names.

- [ ] **Step 3: Verify no stragglers**

Run from `scenarios/vla-batch`: `Get-ChildItem -Recurse -Include *.py,*.yml,*.yaml,*.sh,*.md,*.html | Select-String -Pattern 'vla_data|vla_batches|vla_doses|vla_samples|vla_events|vla_alarms|vla_production|vla_recipes|vla_materials'` → only hits allowed: none. Then `python -m pytest tests -q` + `python selftest.py` → PASS.

- [ ] **Step 4: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add monstermq/config.yaml scenarios/vla-batch
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "refactor(vla): archive group vla_data -> dw_uns_archive + docs sync (fase 0.1 afgerond)"
```

---

### Task 7: ProductionOrder model + OrderManager + endpoints (PR-24, PR-23-context)

**Files:**
- Create: `batch-engine/vla/orders.py`
- Modify: `batch-engine/vla/model.py` (order states + operations map)
- Modify: `batch-engine/vla/batches.py` (create_batch gains `order_id`/`operator_id`; implicit order; order RUNNING on start; operations context on UNS)
- Modify: `batch-engine/vla/bus.py` (add `publish_json`)
- Modify: `batch-engine/app.py` (order endpoints + wire OrderManager)
- Test: `batch-engine/tests/test_orders.py`

**Interfaces:**
- Consumes: `db.dw_orders`, `db.dw_batches`, `db.dw_production` (Tasks 1/4).
- Produces:
  - `model.py`: `ORDER_OPEN, ORDER_RUNNING, ORDER_DONE = "OPEN", "RUNNING", "DONE"`; `OPERATION_OF_STATE = {DOSING: "Preparation", COOKING: "Processing", COOLING: "Processing", FILLING: "Packaging"}` (PR-23).
  - `orders.py`: `class OrderManager(db, bus=None)` with `create_order(recipe_id, target_qty_L, due_date=None) -> dict` (id `PO-YYYYMMDD-XXXX`, status OPEN, event `order_created` batch_id=None), `get_order(order_id)`, `list_orders()` (each with `progress`), `order_progress(order_id) -> {"batched_L","produced_packs","batch_ids"}`, `mark_running(order_id)`, `close_order(order_id)` (raises `ValueError` if no production booked on any of its batches — PR-34 stop rule at order level; sets DONE + event `order_closed`), `publish_status(order)` → UNS `Orders/{order_id}/Status/{status|progress}`.
  - `bus.py`: `publish_json(topic_rel: str, payload: dict) -> bool` publishing to `{uns_root}/{topic_rel}` (no-op offline, returns False).
  - `batches.py`: `create_batch(recipe_id, planned_L=None, auto_start=False, order_id=None, operator_id=None)`; without `order_id` it creates an implicit order via the runner's `orders` manager (constructor gains `orders: Optional[OrderManager]`); raises `ValueError` on unknown/DONE order. `start_batch` marks the order RUNNING and publishes the PR-23 operation context to `Batch/Status/operation` on every phase change.
  - `app.py`: `POST /api/v1/orders {recipe_id, target_qty_L, due_date?}`, `GET /api/v1/orders`, `GET /api/v1/orders/{id}`, `POST /api/v1/orders/{id}/batches {planned_L?, operator_id?}`, `POST /api/v1/orders/{id}/close`. Batch docs now carry `order_id`.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_orders.py`:
```python
import random
import pytest

from vla import model as M
from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.orders import OrderManager

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def setup():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    orders = OrderManager(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(9), orders=orders)
    return db, orders, runner


def test_order_lifecycle_open_running_done():
    db, orders, runner = setup()
    o = orders.create_order("chocolate-vla-1L", target_qty_L=10000)
    assert o["status"] == M.ORDER_OPEN and o["order_id"].startswith("PO-")
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000,
                            order_id=o["order_id"])
    assert b["order_id"] == o["order_id"]
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    assert orders.get_order(o["order_id"])["status"] == M.ORDER_RUNNING
    closed = orders.close_order(o["order_id"])
    assert closed["status"] == M.ORDER_DONE
    prog = orders.order_progress(o["order_id"])
    assert prog["batched_L"] == 5000 and prog["produced_packs"] == 4980


def test_close_refused_without_production():
    db, orders, runner = setup()
    o = orders.create_order("chocolate-vla-1L", target_qty_L=5000)
    runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o["order_id"])
    with pytest.raises(ValueError, match="no production"):
        orders.close_order(o["order_id"])


def test_batch_on_done_order_refused_and_implicit_order():
    db, orders, runner = setup()
    o = orders.create_order("chocolate-vla-1L", target_qty_L=5000)
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o["order_id"])
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    orders.close_order(o["order_id"])
    with pytest.raises(ValueError, match="DONE"):
        runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o["order_id"])
    # no order_id -> implicit order is created (PR-24 demo continuity)
    b2 = runner.create_batch("chocolate-vla-1L", planned_L=2500)
    assert b2["order_id"].startswith("PO-")
```

- [ ] **Step 2: Run test to verify it fails**

`python -m pytest tests/test_orders.py -q` → FAIL (`vla.orders` missing).

- [ ] **Step 3: Add order constants to model.py**

```python
# Order lifecycle (PR-24)
ORDER_OPEN, ORDER_RUNNING, ORDER_DONE = "OPEN", "RUNNING", "DONE"

# ISA-88 operations context layer (PR-23) — derived from the batch FSM.
OPERATION_OF_STATE = {
    DOSING: "Preparation",
    COOKING: "Processing",
    COOLING: "Processing",
    FILLING: "Packaging",
}
```

- [ ] **Step 4: Implement `vla/orders.py`**

```python
"""OrderManager — production orders for the Vla demo (PR-24).

Order lifecycle OPEN -> RUNNING -> DONE maps onto the batch FSM (FDS mapping
table). Multiple batches per order; progress = batched_L vs target_qty_L and
produced packs. Status + progress are mirrored to the UNS under
DairyWorks/Vla/Orders/{order_id}/Status/*.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import model as M

log = logging.getLogger("vla.orders")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderManager:
    def __init__(self, db, bus=None):
        self.db = db
        self.bus = bus

    def create_order(self, recipe_id: str, target_qty_L: float,
                     due_date: Optional[str] = None) -> dict:
        if M.get_recipe(recipe_id) is None:
            raise ValueError(f"unknown recipe_id {recipe_id!r}")
        if float(target_qty_L) <= 0:
            raise ValueError("target_qty_L must be > 0")
        order_id = f"PO-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        doc = {
            "order_id": order_id,
            "recipe_id": recipe_id,
            "target_qty_L": float(target_qty_L),
            "due_date": due_date,
            "status": M.ORDER_OPEN,
            "created_at": _iso(),
        }
        self.db.dw_orders.insert_one(doc)
        self._event(order_id, "order_created", {"recipe_id": recipe_id,
                                                "target_qty_L": float(target_qty_L)})
        self.publish_status(doc)
        return dict(doc)

    def get_order(self, order_id: str) -> Optional[dict]:
        return self.db.dw_orders.find_one({"order_id": order_id})

    def order_progress(self, order_id: str) -> dict:
        batches = self.db.dw_batches.find({"order_id": order_id})
        batch_ids = [b["batch_id"] for b in batches]
        produced = sum(p["packs"] for p in self.db.dw_production.find({})
                       if p["batch_id"] in batch_ids)
        return {
            "batched_L": sum(float(b.get("planned_L") or 0) for b in batches),
            "produced_packs": produced,
            "batch_ids": batch_ids,
        }

    def list_orders(self) -> list[dict]:
        return [{**o, "progress": self.order_progress(o["order_id"])}
                for o in self.db.dw_orders.find({})]

    def mark_running(self, order_id: str) -> None:
        order = self.get_order(order_id)
        if order and order["status"] == M.ORDER_OPEN:
            self.db.dw_orders.update_one({"order_id": order_id},
                                         {"$set": {"status": M.ORDER_RUNNING}})
            self._event(order_id, "order_running", {})
            self.publish_status({**order, "status": M.ORDER_RUNNING})

    def close_order(self, order_id: str) -> dict:
        order = self.get_order(order_id)
        if order is None:
            raise ValueError(f"unknown order {order_id!r}")
        if order["status"] == M.ORDER_DONE:
            return order
        prog = self.order_progress(order_id)
        if prog["produced_packs"] == 0:
            raise ValueError(f"order {order_id} has no production booked "
                             "— close refused (PR-34 stop rule)")
        self.db.dw_orders.update_one({"order_id": order_id},
                                     {"$set": {"status": M.ORDER_DONE,
                                               "completed_at": _iso()}})
        self._event(order_id, "order_closed", {"produced_packs": prog["produced_packs"]})
        out = self.get_order(order_id)
        self.publish_status(out)
        return out

    def publish_status(self, order: dict) -> None:
        if self.bus is None:
            return
        oid = order["order_id"]
        self.bus.publish_json(f"Orders/{oid}/Status/status",
                              {"value": order["status"], "ts": _iso()})
        prog = self.order_progress(oid)
        self.bus.publish_json(f"Orders/{oid}/Status/progress", {
            "target_qty_L": order.get("target_qty_L"),
            "batched_L": prog["batched_L"],
            "produced_packs": prog["produced_packs"],
            "ts": _iso(),
        })

    def _event(self, order_id: str, event_type: str, payload: dict) -> None:
        self.db.dw_batch_events.insert_one({
            "batch_id": None, "order_id": order_id,
            "event_type": event_type, "payload": payload, "ts": _iso(),
        })
```

- [ ] **Step 5: `publish_json` in bus.py**

Add to `VlaBus` (next to the existing publish helpers; follow their style — look at `command()` for the connected/no-op guard):
```python
    def publish_json(self, topic_rel: str, payload: dict) -> bool:
        """Publish a JSON payload on {uns_root}/{topic_rel} (no-op offline)."""
        if self.client is None or not self.connected:
            return False
        topic = f"{self.uns_root}/{topic_rel}"
        try:
            self.client.publish(topic, json.dumps(payload), qos=0, retain=True)
            return True
        except Exception as e:  # pragma: no cover
            log.warning("publish_json %s failed: %s", topic, e)
            return False
```

- [ ] **Step 6: Wire orders into BatchRunner**

`BatchRunner.__init__` gains `orders=None` and stores `self.orders = orders`. In `create_batch` signature: `def create_batch(self, recipe_id, planned_L=None, auto_start=False, order_id=None, operator_id=None)`. After the release-gate block:
```python
        if order_id is not None:
            if self.orders is None:
                raise ValueError("order support not wired")
            order = self.orders.get_order(order_id)
            if order is None:
                raise ValueError(f"unknown order {order_id!r}")
            if order["status"] == M.ORDER_DONE:
                raise ValueError(f"order {order_id} is DONE — no new batches")
        elif self.orders is not None:
            order = self.orders.create_order(recipe_id,
                                             float(planned_L or recipe.basis_L))
            order_id = order["order_id"]
```
Add `"order_id": order_id, "created_by": operator_id,` to `batch_doc`. In `start_batch`, right after the `batch_started` event:
```python
        if self.orders is not None and batch.get("order_id"):
            self.orders.mark_running(batch["order_id"])
```
In `_update`, extend the state mirror with the PR-23 operation context:
```python
        if self.bus is not None and "state" in fields:
            self.bus.command("Batch", "state", value=fields["state"])
            op = M.OPERATION_OF_STATE.get(fields["state"])
            if op:
                self.bus.publish_json("Batch/Status/operation",
                                      {"value": op, "ts": _iso()})
```

- [ ] **Step 7: Endpoints in app.py**

In `_startup` create the manager and pass it in:
```python
    orders = OrderManager(db, bus)
    STATE.update({"db": db, "bus": bus, "control": control, "orders": orders,
                  "runner": BatchRunner(db, bus, control=control, orders=orders)})
```
(import: `from vla.orders import OrderManager`). Add models + endpoints:
```python
class CreateOrder(BaseModel):
    recipe_id: str
    target_qty_L: float
    due_date: str | None = None


class CreateOrderBatch(BaseModel):
    planned_L: float | None = None
    operator_id: str | None = None


def _orders() -> "OrderManager":
    om = STATE.get("orders")
    if om is None:
        raise HTTPException(503, "engine not initialized")
    return om


@app.post(f"{API}/orders")
def create_order(body: CreateOrder):
    try:
        return _orders().create_order(body.recipe_id, body.target_qty_L, body.due_date)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get(f"{API}/orders")
def list_orders():
    return _orders().list_orders()


@app.get(f"{API}/orders/{{order_id}}")
def get_order(order_id: str):
    order = _orders().get_order(order_id)
    if order is None:
        raise HTTPException(404, f"order {order_id} not found")
    return {**order, "progress": _orders().order_progress(order_id)}


@app.post(f"{API}/orders/{{order_id}}/batches")
def create_order_batch(order_id: str, body: CreateOrderBatch):
    runner = _runner()
    order = _orders().get_order(order_id)
    if order is None:
        raise HTTPException(404, f"order {order_id} not found")
    auto = os.environ.get("AUTO_START", "1") not in ("0", "false", "False")
    try:
        batch = runner.create_batch(order["recipe_id"], body.planned_L,
                                    auto_start=auto, order_id=order_id,
                                    operator_id=body.operator_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"batch_id": batch["batch_id"], "state": batch["state"],
            "order_id": order_id,
            "dose_setpoints": {d["material_id"]: d["qty_target"]
                               for d in batch["doses"]}}


@app.post(f"{API}/orders/{{order_id}}/close")
def close_order(order_id: str):
    try:
        return _orders().close_order(order_id)
    except ValueError as e:
        code = 404 if "unknown order" in str(e) else 409
        raise HTTPException(code, str(e))
```

- [ ] **Step 8: Run tests + selftest**

`python -m pytest tests -q` → PASS (older tests construct `BatchRunner(db, bus=None, rng=…)` without orders — implicit-order branch is skipped because `self.orders is None`; batch then has `order_id: None`, allowed). `python selftest.py` → ALL PASS.

- [ ] **Step 9: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): production orders (PR-24) + operations context on UNS (PR-23) + order close rule"
```

---

### Task 8: No-bookings-on-COMPLETE guard (order-lifecycle mapping)

**Files:**
- Modify: `batch-engine/vla/batches.py` (`_guard_bookable` + use in `take_sample`, `_book_production` manual path)
- Test: `batch-engine/tests/test_lifecycle_guard.py`

**Interfaces:**
- Produces: `BatchRunner._guard_bookable(batch_id) -> dict` returning the batch or raising `ValueError("batch <id> is COMPLETE — no bookings allowed")` / unknown-batch ValueError. Used by every manual booking path (scan-flow in Tasks 10/11 must call it too).

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_lifecycle_guard.py`:
```python
import random
import pytest

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def test_no_sample_booking_on_complete_batch():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(2))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    with pytest.raises(ValueError, match="COMPLETE"):
        runner.take_sample(b["batch_id"], "viscosity")
```

- [ ] **Step 2: Run test to verify it fails**

`python -m pytest tests/test_lifecycle_guard.py -q` → FAIL (sample is booked).

- [ ] **Step 3: Implement the guard**

In `BatchRunner` add:
```python
    def _guard_bookable(self, batch_id: str) -> dict:
        """FDS order-lifecycle rule: on a COMPLETE batch nothing may be booked."""
        batch = self._raw_batch(batch_id)
        if batch is None:
            raise ValueError(f"unknown batch {batch_id!r}")
        if batch["state"] == M.COMPLETE:
            raise ValueError(f"batch {batch_id} is COMPLETE — no bookings allowed")
        return batch
```
In `take_sample` replace the `_raw_batch` lookup with `batch = self._guard_bookable(batch_id)`. (Internal `_take_sample` calls from `_finalize` are unaffected — they run before COMPLETE.) In `app.py` the `POST /samples` endpoint already converts `ValueError` → 400.

- [ ] **Step 4: Run tests + selftest** → all PASS.

- [ ] **Step 5: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): no-bookings-on-COMPLETE guard (FDS order-lifecycle mapping)"
```

---

### Task 9: Inventory (PR-27) — stock, mutations, threshold event, GET /materials

**Files:**
- Create: `batch-engine/vla/inventory.py`
- Modify: `batch-engine/vla/batches.py` (consumption/production hooks)
- Modify: `batch-engine/app.py` (`GET /api/v1/materials`)
- Test: `batch-engine/tests/test_inventory.py`

**Interfaces:**
- Consumes: `db.dw_materials` (seeded with `stock_qty`/`reorder_level`, Task 2), `M.FINISHED_GOOD_ID`.
- Produces: `inventory.consume(db, events, material_id, qty, batch_id)` and `inventory.produce(db, events, material_id, qty, batch_id)` where `events` is a callable `(batch_id, event_type, payload)` (pass `runner._event`). Mutations update `dw_materials.stock_qty`; below `reorder_level` fires event `stock_below_threshold` (once per crossing: only when previous stock was >= level). `BatchRunner._book_doses` consumes per booked dose; `_book_production` produces `FINISHED_GOOD_ID`. `GET /api/v1/materials` returns the master incl. stock.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_inventory.py`:
```python
import random

from vla import model as M
from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner

TELEM_OK = {
    "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
    "packs_total": 4980, "reject_count": 20,
    "dose_actuals": {"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "cocoa": 100.0},
}


def test_stock_mutates_on_consumption_and_production():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(8))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    milk = db.dw_materials.find_one({"material_id": "milk"})
    assert milk["stock_qty"] == 20000.0 - 5000.0
    fg = db.dw_materials.find_one({"material_id": M.FINISHED_GOOD_ID})
    assert fg["stock_qty"] == 4980


def test_below_threshold_event_fires_once_per_crossing():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(8))
    db.dw_materials.update_one({"material_id": "cocoa"},
                               {"$set": {"stock_qty": 130.0}})  # level 120
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)  # cocoa -100 -> 30
    evs = [e for e in db.dw_batch_events.find({"event_type": "stock_below_threshold"})
           if e["payload"]["material_id"] == "cocoa"]
    assert len(evs) == 1 and evs[0]["payload"]["stock_qty"] == 30.0
```

- [ ] **Step 2: Run test to verify it fails**

`python -m pytest tests/test_inventory.py -q` → FAIL (stock unchanged).

- [ ] **Step 3: Implement `vla/inventory.py`**

```python
"""Inventory mutations (PR-27): stock on the material master, moved by
consumptions (-) and productions (+). Below-reorder-level fires a
stock_below_threshold event once per crossing."""

from __future__ import annotations

from typing import Callable, Optional

Events = Callable[[Optional[str], str, dict], None]


def _mutate(db, events: Events, material_id: str, delta: float,
            batch_id: Optional[str], kind: str) -> Optional[float]:
    mat = db.dw_materials.find_one({"material_id": material_id})
    if mat is None:
        return None
    before = float(mat.get("stock_qty", 0.0))
    after = round(before + delta, 4)
    db.dw_materials.update_one({"material_id": material_id},
                               {"$set": {"stock_qty": after}})
    events(batch_id, "stock_mutation",
           {"material_id": material_id, "delta": delta, "stock_qty": after,
            "kind": kind})
    level = float(mat.get("reorder_level", 0.0))
    if level > 0 and after < level <= before:
        events(batch_id, "stock_below_threshold",
               {"material_id": material_id, "stock_qty": after,
                "reorder_level": level})
    return after


def consume(db, events: Events, material_id: str, qty: float,
            batch_id: Optional[str]) -> Optional[float]:
    return _mutate(db, events, material_id, -abs(float(qty)), batch_id, "consumption")


def produce(db, events: Events, material_id: str, qty: float,
            batch_id: Optional[str]) -> Optional[float]:
    return _mutate(db, events, material_id, abs(float(qty)), batch_id, "production")
```

- [ ] **Step 4: Hook into batches.py**

Import: `from . import inventory`. In `_book_doses`, after each successful dose update (inside the loop, after the `dose_booked` event):
```python
            inventory.consume(self.db, self._event,
                              line["material_id"], actual, batch_id)
```
In `_book_production`, before the return:
```python
        inventory.produce(self.db, self._event, M.FINISHED_GOOD_ID,
                          int(packs), batch_id)
```
Note: `_event(batch_id, …)` already accepts the batch id first — signature matches `Events`.

- [ ] **Step 5: `GET /materials` in app.py**

```python
@app.get(f"{API}/materials")
def list_materials():
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    return db.dw_materials.find({})
```

- [ ] **Step 6: Run tests + selftest** → all PASS.

- [ ] **Step 7: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): inventory stock mutations + reorder-threshold event + GET /materials (PR-27)"
```

---

### Task 10: Scan-flow part 1 — order-scan gate, label-scan, weigh guidance + staging (PR-34 steps 0–3)

**Files:**
- Create: `batch-engine/vla/scan.py`
- Modify: `batch-engine/app.py` (scan endpoints)
- Test: `batch-engine/tests/test_scan_flow.py`

**Interfaces:**
- Consumes: `runner._guard_bookable` (Task 8), `OrderManager` (Task 7), dose rows with `staged`/`qty_prepared` (Task 2).
- Produces: `class ScanFlow(db, runner, orders)` with:
  - `scan_order(code, operator_id) -> dict` — gate for every booking function. `code` is an `order_id` or `batch_id`. OK → `{"ok": True, "order": {...}|None, "batch": {...}|None}` + event `order_scanned`. Rejections raise `ScanRejected(reason)` (custom exception carrying `reason`), logging event `scan_rejected` with `{"code", "reason", "operator_id"}`. Reasons: `unknown` (no order/batch match), `not_active` (order DONE or batch COMPLETE), `line_busy` (start attempted while another batch active — checked by callers that pass `for_start=True`).
  - `scan_label(batch_id, material_id, lot_no, operator_id) -> dict` — validates material against the recipe doses of the batch; unknown material → ScanRejected `wrong_material` + MEDIUM alarm; OK → weigh guidance `{"material_id","lot_no","qty_target","tol_min","tol_max","booked","remaining","whole_bag","bag_size_kg"}` + event `label_scanned`.
  - `weigh(batch_id, material_id, qty_kg=None, lot_no=None, source_equipment="scale-01", operator_id=None, total=False) -> dict` — stages a booking `{qty_kg, lot_no, source_equipment, operator_id, ts}` into the dose row's `staged[]`, updates `qty_prepared`; `total=True` books the remaining `qty_target − qty_prepared` in one action; over-staging (prepared > target) allowed with event `overconsumption_booked`; whole_bag materials require `qty_kg` = n × `bag_size_kg` (else ScanRejected `not_whole_bags`). Returns updated guidance dict.
  - `ScanRejected` exception class (subclass of `ValueError`) with `.reason`.
- `app.py` endpoints: `POST /api/v1/scan/order {code, operator_id}` (ScanRejected → 409 with `{"detail", "reason"}`; unknown → 404), `POST /api/v1/scan/label {batch_id, material_id, lot_no, operator_id}`, `POST /api/v1/scan/weigh {batch_id, material_id, qty_kg?, lot_no?, source_equipment?, operator_id, total?}`.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_scan_flow.py`:
```python
import random
import pytest

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.orders import OrderManager
from vla.scan import ScanFlow, ScanRejected


def setup():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    orders = OrderManager(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(10), orders=orders)
    flow = ScanFlow(db, runner, orders)
    o = orders.create_order("chocolate-vla-1L", target_qty_L=5000)
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o["order_id"])
    return db, runner, flow, o, b


def test_order_scan_gate_accepts_and_rejects():
    db, runner, flow, o, b = setup()
    ok = flow.scan_order(o["order_id"], operator_id="OP-7")
    assert ok["ok"] is True and ok["order"]["order_id"] == o["order_id"]
    with pytest.raises(ScanRejected) as ei:
        flow.scan_order("PO-DOES-NOT-EXIST", operator_id="OP-7")
    assert ei.value.reason == "unknown"
    evs = db.dw_batch_events.find({"event_type": "scan_rejected"})
    assert len(evs) == 1


def test_label_scan_validates_against_recipe():
    db, runner, flow, o, b = setup()
    g = flow.scan_label(b["batch_id"], "cocoa", lot_no="L-2331", operator_id="OP-7")
    assert g["qty_target"] == 100.0 and g["remaining"] == 100.0
    with pytest.raises(ScanRejected) as ei:
        flow.scan_label(b["batch_id"], "vanilla", lot_no="L-1", operator_id="OP-7")
    assert ei.value.reason == "wrong_material"


def test_weigh_staging_total_and_overconsumption():
    db, runner, flow, o, b = setup()
    flow.scan_label(b["batch_id"], "cocoa", lot_no="L-2331", operator_id="OP-7")
    g1 = flow.weigh(b["batch_id"], "cocoa", qty_kg=60.0, lot_no="L-2331",
                    operator_id="OP-7")
    assert g1["booked"] == 60.0 and g1["remaining"] == 40.0
    g2 = flow.weigh(b["batch_id"], "cocoa", total=True, lot_no="L-2331",
                    operator_id="OP-7")
    assert g2["booked"] == 100.0 and g2["remaining"] == 0.0
    g3 = flow.weigh(b["batch_id"], "cocoa", qty_kg=5.0, lot_no="L-2331",
                    operator_id="OP-7")
    assert g3["booked"] == 105.0
    evs = db.dw_batch_events.find({"event_type": "overconsumption_booked"})
    assert len(evs) == 1


def test_whole_bag_material_requires_bag_multiples():
    db, runner, flow, o, b = setup()
    with pytest.raises(ScanRejected) as ei:
        flow.weigh(b["batch_id"], "starch", qty_kg=30.0, lot_no="L-9",
                   operator_id="OP-7")  # bag_size 25
    assert ei.value.reason == "not_whole_bags"
    g = flow.weigh(b["batch_id"], "starch", qty_kg=250.0, lot_no="L-9",
                   operator_id="OP-7")  # 10 bags
    assert g["booked"] == 250.0
```

- [ ] **Step 2: Run test to verify it fails**

`python -m pytest tests/test_scan_flow.py -q` → FAIL (`vla.scan` missing).

- [ ] **Step 3: Implement `vla/scan.py`**

```python
"""Scan-driven shop-floor flow (PR-34, FDS §B steps 0-6).

Demo translation: no physical scanner — the operator UI posts label/order
payloads. Every rejection is logged as a scan_rejected BatchEvent so the
UNS shows the full story.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import model as M

log = logging.getLogger("vla.scan")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanRejected(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class ScanFlow:
    def __init__(self, db, runner, orders):
        self.db = db
        self.runner = runner
        self.orders = orders

    # ------------------------------------------------------------ step 0: gate

    def scan_order(self, code: str, operator_id: Optional[str] = None,
                   for_start: bool = False) -> dict:
        order = self.orders.get_order(code) if self.orders else None
        batch = None
        if order is None:
            batch = self.db.dw_batches.find_one({"batch_id": code})
            if batch is None:
                self._reject(None, code, "unknown", operator_id)
            if batch.get("order_id"):
                order = self.orders.get_order(batch["order_id"])
        if order is not None and order["status"] == M.ORDER_DONE:
            self._reject(batch and batch.get("batch_id"), code, "not_active",
                         operator_id)
        if batch is not None and batch["state"] == M.COMPLETE:
            self._reject(batch["batch_id"], code, "not_active", operator_id)
        if for_start:
            active = next((b for b in self.db.dw_batches.find({})
                           if b["state"] in (M.DOSING, M.COOKING, M.COOLING,
                                             M.FILLING)), None)
            if active is not None:
                self._reject(active["batch_id"], code, "line_busy", operator_id)
        self._event(batch and batch.get("batch_id"), "order_scanned",
                    {"code": code, "operator_id": operator_id,
                     "order_id": order and order.get("order_id")})
        return {"ok": True, "order": order, "batch": batch}

    # ------------------------------------------------------ step 2: label scan

    def scan_label(self, batch_id: str, material_id: str, lot_no: str,
                   operator_id: Optional[str] = None) -> dict:
        self.runner._guard_bookable(batch_id)
        dose = self.db.dw_doses.find_one({"batch_id": batch_id,
                                          "material_id": material_id})
        if dose is None:
            self.runner._alarm(batch_id, "process-tank-01",
                               "wrong_material_scanned", M.MEDIUM,
                               f"{material_id} is not in the recipe for {batch_id}",
                               impact=False, resolved=False)
            self._reject(batch_id, material_id, "wrong_material", operator_id)
        self._event(batch_id, "label_scanned",
                    {"material_id": material_id, "lot_no": lot_no,
                     "operator_id": operator_id})
        return self._guidance(batch_id, material_id, lot_no)

    # ------------------------------------------- step 3: weigh guidance + stage

    def weigh(self, batch_id: str, material_id: str,
              qty_kg: Optional[float] = None, lot_no: Optional[str] = None,
              source_equipment: str = "scale-01",
              operator_id: Optional[str] = None, total: bool = False) -> dict:
        self.runner._guard_bookable(batch_id)
        dose = self.db.dw_doses.find_one({"batch_id": batch_id,
                                          "material_id": material_id})
        if dose is None:
            self._reject(batch_id, material_id, "wrong_material", operator_id)
        mat = self.db.dw_materials.find_one({"material_id": material_id}) or {}
        prepared = float(dose.get("qty_prepared") or 0.0)
        target = float(dose["qty_target"])

        if total:
            qty = round(max(0.0, target - prepared), 4)
            if qty == 0.0:
                self._reject(batch_id, material_id, "nothing_remaining", operator_id)
        else:
            if qty_kg is None or float(qty_kg) <= 0:
                self._reject(batch_id, material_id, "invalid_qty", operator_id)
            qty = round(float(qty_kg), 4)

        if mat.get("whole_bag") and mat.get("bag_size_kg"):
            bags = qty / float(mat["bag_size_kg"])
            if abs(bags - round(bags)) > 1e-6:
                self._reject(batch_id, material_id, "not_whole_bags", operator_id)

        staged = list(dose.get("staged") or [])
        staged.append({"qty_kg": qty, "lot_no": lot_no,
                       "source_equipment": source_equipment,
                       "operator_id": operator_id, "ts": _iso(),
                       "total_action": bool(total)})
        new_prepared = round(prepared + qty, 4)
        self.db.dw_doses.update_one(
            {"batch_id": batch_id, "material_id": material_id},
            {"$set": {"staged": staged, "qty_prepared": new_prepared,
                      "lot_no": lot_no or dose.get("lot_no"),
                      "operator_id": operator_id or dose.get("operator_id")}})
        self._event(batch_id, "dose_staged",
                    {"material_id": material_id, "qty_kg": qty,
                     "qty_prepared": new_prepared, "total_action": bool(total)})
        if new_prepared > target and prepared <= target:
            self._event(batch_id, "overconsumption_booked",
                        {"material_id": material_id, "qty_prepared": new_prepared,
                         "qty_target": target, "operator_id": operator_id})
        return self._guidance(batch_id, material_id, lot_no)

    # ---------------------------------------------------------------- helpers

    def _guidance(self, batch_id: str, material_id: str,
                  lot_no: Optional[str]) -> dict:
        dose = self.db.dw_doses.find_one({"batch_id": batch_id,
                                          "material_id": material_id})
        mat = self.db.dw_materials.find_one({"material_id": material_id}) or {}
        prepared = float(dose.get("qty_prepared") or 0.0)
        target = float(dose["qty_target"])
        return {
            "material_id": material_id,
            "lot_no": lot_no or dose.get("lot_no"),
            "qty_target": target,
            "tol_min": dose.get("tol_min"),
            "tol_max": dose.get("tol_max"),
            "booked": prepared,
            "remaining": round(max(0.0, target - prepared), 4),
            "whole_bag": bool(mat.get("whole_bag")),
            "bag_size_kg": mat.get("bag_size_kg"),
        }

    def _event(self, batch_id: Optional[str], event_type: str, payload: dict) -> None:
        self.db.dw_batch_events.insert_one({
            "batch_id": batch_id, "event_type": event_type,
            "payload": payload, "ts": _iso()})

    def _reject(self, batch_id: Optional[str], code: str, reason: str,
                operator_id: Optional[str]) -> None:
        self._event(batch_id, "scan_rejected",
                    {"code": code, "reason": reason, "operator_id": operator_id})
        raise ScanRejected(reason, f"scan rejected ({reason}): {code}")
```

- [ ] **Step 4: Endpoints in app.py**

Wire the flow in `_startup`: `from vla.scan import ScanFlow, ScanRejected` and after the runner is built: `STATE["scan"] = ScanFlow(db, STATE["runner"], orders)`.
```python
class ScanOrder(BaseModel):
    code: str
    operator_id: str | None = None


class ScanLabel(BaseModel):
    batch_id: str
    material_id: str
    lot_no: str
    operator_id: str | None = None


class ScanWeigh(BaseModel):
    batch_id: str
    material_id: str
    qty_kg: float | None = None
    lot_no: str | None = None
    source_equipment: str = "scale-01"
    operator_id: str | None = None
    total: bool = False


def _scan() -> "ScanFlow":
    s = STATE.get("scan")
    if s is None:
        raise HTTPException(503, "engine not initialized")
    return s


def _scan_call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ScanRejected as e:
        code = 404 if e.reason == "unknown" else 409
        raise HTTPException(code, {"detail": str(e), "reason": e.reason})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post(f"{API}/scan/order")
def scan_order(body: ScanOrder):
    return _scan_call(_scan().scan_order, body.code, body.operator_id)


@app.post(f"{API}/scan/label")
def scan_label(body: ScanLabel):
    return _scan_call(_scan().scan_label, body.batch_id, body.material_id,
                      body.lot_no, body.operator_id)


@app.post(f"{API}/scan/weigh")
def scan_weigh(body: ScanWeigh):
    return _scan_call(_scan().weigh, body.batch_id, body.material_id,
                      qty_kg=body.qty_kg, lot_no=body.lot_no,
                      source_equipment=body.source_equipment,
                      operator_id=body.operator_id, total=body.total)
```

- [ ] **Step 5: Run tests + selftest** → all PASS.

- [ ] **Step 6: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): scan-flow gate + label scan + weigh staging with Totaal/overconsumption/whole-bag rules (PR-34 steps 0-3)"
```

---

### Task 11: Scan-flow part 2 — report-scan commit, manual production, samples via scan (PR-34 steps 4–6)

**Files:**
- Modify: `batch-engine/vla/scan.py` (`scan_report`, `book_production`)
- Modify: `batch-engine/vla/batches.py` (none — reuse `_book_production`, `take_sample`)
- Modify: `batch-engine/app.py` (`POST /scan/report`, `POST /production`, `POST /samples/{id}/reprint-label`)
- Test: `batch-engine/tests/test_scan_commit.py`

**Interfaces:**
- Consumes: staged dose rows (Task 10), `runner._book_production` (Task 4), `inventory.consume` (Task 9 — via the dose commit path below).
- Produces:
  - `ScanFlow.scan_report(batch_id, operator_id) -> dict` — the §B step-4 consumption confirmation: for every dose row with `qty_prepared > 0` and `qty_actual is None`, set `qty_actual = qty_prepared`, `in_tolerance` from `tol_min/tol_max`, `source_equipment` from the last staged entry, keep `lot_no`/`operator_id`; fire `dose_booked` + `report_scanned` events; MEDIUM alarm when out of tolerance (same message pattern as `_book_doses`); consume stock per booked qty. Raises ScanRejected `nothing_staged` when no staged doses.
  - `ScanFlow.book_production(batch_id, packs, operator_id) -> dict` — manual yield booking via `runner._book_production(..., source="operator_booking", operator_id=...)` after `_guard_bookable`.
  - `POST /api/v1/scan/report {batch_id, operator_id}`; `POST /api/v1/production {batch_id, packs, operator_id}`; `POST /api/v1/samples/{sample_id}/reprint-label` → sets `label_printed: True` (idempotent) + `sample_label_printed` event, 404 on unknown sample.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_scan_commit.py`:
```python
import random
import pytest

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.orders import OrderManager
from vla.scan import ScanFlow, ScanRejected


def setup():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    orders = OrderManager(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(12), orders=orders)
    flow = ScanFlow(db, runner, orders)
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    return db, runner, flow, b


def test_report_scan_commits_staged_doses_and_stock():
    db, runner, flow, b = setup()
    flow.weigh(b["batch_id"], "cocoa", qty_kg=100.0, lot_no="L-2331",
               operator_id="OP-7")
    before = db.dw_materials.find_one({"material_id": "cocoa"})["stock_qty"]
    res = flow.scan_report(b["batch_id"], operator_id="OP-7")
    assert res["booked_materials"] == ["cocoa"]
    dose = db.dw_doses.find_one({"batch_id": b["batch_id"], "material_id": "cocoa"})
    assert dose["qty_actual"] == 100.0 and dose["in_tolerance"] is True
    assert dose["lot_no"] == "L-2331" and dose["operator_id"] == "OP-7"
    after = db.dw_materials.find_one({"material_id": "cocoa"})["stock_qty"]
    assert after == before - 100.0
    with pytest.raises(ScanRejected):
        flow.scan_report(b["batch_id"], operator_id="OP-7")  # nothing staged left


def test_manual_production_booking():
    db, runner, flow, b = setup()
    row = flow.book_production(b["batch_id"], packs=120, operator_id="OP-7")
    assert row["source"] == "operator_booking" and row["operator_id"] == "OP-7"
    batch = runner.get_batch(b["batch_id"])
    assert batch["packs_total"] == 120
```

- [ ] **Step 2: Run test to verify it fails**

`python -m pytest tests/test_scan_commit.py -q` → FAIL (`scan_report` missing).

- [ ] **Step 3: Implement commit + manual production in scan.py**

Add imports at the top: `from . import inventory`. Add methods to `ScanFlow`:
```python
    # ------------------------------------------- step 4: report scan = commit

    def scan_report(self, batch_id: str,
                    operator_id: Optional[str] = None) -> dict:
        self.runner._guard_bookable(batch_id)
        rows = self.db.dw_doses.find({"batch_id": batch_id})
        staged_rows = [r for r in rows
                       if float(r.get("qty_prepared") or 0.0) > 0
                       and r.get("qty_actual") is None]
        if not staged_rows:
            self._reject(batch_id, batch_id, "nothing_staged", operator_id)
        booked = []
        for r in staged_rows:
            actual = round(float(r["qty_prepared"]), 4)
            in_tol = float(r["tol_min"]) <= actual <= float(r["tol_max"])
            last = (r.get("staged") or [{}])[-1]
            self.db.dw_doses.update_one(
                {"batch_id": batch_id, "material_id": r["material_id"]},
                {"$set": {"qty_actual": actual, "in_tolerance": in_tol,
                          "source_equipment": last.get("source_equipment", "scale-01"),
                          "operator_id": operator_id or r.get("operator_id"),
                          "ts": _iso()}})
            self._event(batch_id, "dose_booked",
                        {"material_id": r["material_id"], "qty_actual": actual,
                         "in_tolerance": in_tol, "lot_no": r.get("lot_no"),
                         "operator_id": operator_id})
            if not in_tol:
                self.runner._alarm(batch_id, "process-tank-01",
                                   "dose_out_of_tolerance", M.MEDIUM,
                                   f"{r['material_id']} dose {actual} kg out of tol "
                                   f"({r['tol_min']}-{r['tol_max']})",
                                   impact=True, resolved=False)
            inventory.consume(self.db, self._event, r["material_id"],
                              actual, batch_id)
            booked.append(r["material_id"])
        self._event(batch_id, "report_scanned",
                    {"operator_id": operator_id, "booked_materials": booked})
        return {"ok": True, "booked_materials": booked}

    # --------------------------------------- step 5: manual production booking

    def book_production(self, batch_id: str, packs: int,
                        operator_id: Optional[str] = None) -> dict:
        self.runner._guard_bookable(batch_id)
        if int(packs) <= 0:
            self._reject(batch_id, str(packs), "invalid_qty", operator_id)
        return self.runner._book_production(batch_id, int(packs), 0,
                                            source="operator_booking",
                                            operator_id=operator_id)
```

- [ ] **Step 4: Endpoints in app.py**

```python
class ScanReport(BaseModel):
    batch_id: str
    operator_id: str | None = None


class BookProduction(BaseModel):
    batch_id: str
    packs: int
    operator_id: str | None = None


@app.post(f"{API}/scan/report")
def scan_report(body: ScanReport):
    return _scan_call(_scan().scan_report, body.batch_id, body.operator_id)


@app.post(f"{API}/production")
def book_production(body: BookProduction):
    return _scan_call(_scan().book_production, body.batch_id, body.packs,
                      body.operator_id)


@app.post(f"{API}/samples/{{sample_id}}/reprint-label")
def reprint_sample_label(sample_id: str):
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    row = db.dw_samples.find_one({"sample_id": sample_id})
    if row is None:
        raise HTTPException(404, f"sample {sample_id} not found")
    db.dw_samples.update_one({"sample_id": sample_id},
                             {"$set": {"label_printed": True}})
    db.dw_batch_events.insert_one({
        "batch_id": row["batch_id"], "event_type": "sample_label_printed",
        "payload": {"sample_id": sample_id, "reprint": True},
        "ts": row["ts"]})
    return {"ok": True, "sample_id": sample_id}
```

- [ ] **Step 5: Run tests + selftest** → all PASS. (Note: `_book_doses` skips scan-committed doses because `qty_actual` is set — verified by test 3 in `test_master_data.py` staying green.)

- [ ] **Step 6: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): report-scan dose commit + manual production + sample label reprint (PR-34 steps 4-6)"
```

---

### Task 12: Dashboard — Operator (scan) tab + Orders tab + kleurentaal

**Files:**
- Modify: `dashboard/index.html` (nav, 2 new sections, JS)

**Interfaces:**
- Consumes: all Task 5/7/10/11 endpoints. Existing helpers `j(path, opts)`, `post(path, body)`, `$` selector, tab-switching via `nav button[data-tab]` (verify the existing tab-switch handler toggles `.hidden` on `#tab-{name}` — follow it exactly).
- Produces: nav buttons `Orders` and `Operator`; sections `#tab-orders`, `#tab-operator`. Completed states render **dark green** (`#0b4f2a`) per the 04-Frontend kleurentaal (color only where attention is needed; completed ≠ running).

- [ ] **Step 1: Add nav buttons + sections**

In the `<nav>` add (before the admin button):
```html
    <button data-tab="orders">Orders</button>
    <button data-tab="operator">Operator</button>
```
Add sections before the admin section:
```html
  <!-- ORDERS (PR-24) -->
  <section id="tab-orders" class="hidden">
    <h2>New production order</h2>
    <div class="row">
      <label>Recipe<select id="ordRecipe"><option value="chocolate-vla-1L">chocolate-vla-1L</option></select></label>
      <label>Target (L)<input id="ordQty" type="number" value="5000" min="500" step="500" style="width:120px"></label>
      <label>Due date<input id="ordDue" type="date"></label>
      <button class="act" onclick="createOrder()">Create order</button>
    </div>
    <h2>Orders</h2>
    <table><thead><tr><th>Order</th><th>Recipe</th><th>Target (L)</th><th>Batched (L)</th><th>Produced (packs)</th><th>Status</th><th></th></tr></thead>
      <tbody id="ordersRows"></tbody></table>
    <p id="ordersMsg" class="sub"></p>
  </section>

  <!-- OPERATOR — SCAN & WEIGH (PR-34) -->
  <section id="tab-operator" class="hidden">
    <div class="row">
      <label>Operator ID<input id="opId" value="OP-7" style="width:90px"></label>
      <label>Scan order / batch<input id="scanCode" placeholder="PO-… of B-…" style="width:220px"></label>
      <button class="act" onclick="scanOrder()">Scan</button>
      <span id="scanGate" class="pill bad">no order scanned</span>
    </div>
    <div id="opPanel" class="hidden">
      <h2>Weigh &amp; stage (per material)</h2>
      <div class="grid" id="weighCards" style="grid-template-columns:repeat(auto-fill,minmax(280px,1fr))"></div>
      <div class="row" style="margin-top:10px">
        <button class="act" onclick="scanReport()">📄 Scan weigh report → book consumption</button>
      </div>
      <h2>Yield booking (manual)</h2>
      <div class="row">
        <label>Packs<input id="prodPacks" type="number" value="100" min="1" style="width:100px"></label>
        <button class="gh" onclick="bookProduction()">Book production</button>
      </div>
      <h2>Sample</h2>
      <div class="row">
        <label>Type<select id="sampleType">
          <option value="dose_check">dose_check</option>
          <option value="cook_temp">cook_temp</option>
          <option value="hold">hold</option>
          <option value="viscosity">viscosity</option>
        </select></label>
        <button class="gh" onclick="takeScanSample()">Take sample + print label</button>
      </div>
      <p id="opMsg" class="sub"></p>
    </div>
  </section>
```

- [ ] **Step 2: Add the JS**

Append inside the existing `<script>` (reusing `j`/`post`/`$`):
```javascript
// ---- Orders tab (PR-24) ----
async function createOrder(){
  try{
    await post('/orders', {recipe_id: $('#ordRecipe').value,
      target_qty_L: parseFloat($('#ordQty').value),
      due_date: $('#ordDue').value || null});
    await refreshOrders();
    $('#ordersMsg').textContent = 'order created';
  }catch(e){ $('#ordersMsg').textContent = 'create failed: '+e.message; }
}
async function refreshOrders(){
  try{
    const rows = await j('/orders');
    $('#ordersRows').innerHTML = rows.map(o => `<tr>
      <td>${o.order_id}</td><td>${o.recipe_id}</td>
      <td>${o.target_qty_L}</td><td>${o.progress.batched_L}</td>
      <td>${o.progress.produced_packs}</td>
      <td><span class="badge" style="background:${
        o.status==='DONE' ? '#0b4f2a' : o.status==='RUNNING' ? '#1a7f37' : '#57606a'
      };color:#fff">${o.status}</span></td>
      <td>${o.status!=='DONE'
        ? `<button class="gh" onclick="orderBatch('${o.order_id}')">+ batch</button>
           <button class="gh" onclick="closeOrder('${o.order_id}')">close</button>` : ''}</td>
    </tr>`).join('');
  }catch(e){ /* engine offline */ }
}
async function orderBatch(oid){
  try{ await post(`/orders/${oid}/batches`, {operator_id: $('#opId').value});
       await refreshOrders(); $('#ordersMsg').textContent='batch created on '+oid; }
  catch(e){ $('#ordersMsg').textContent='batch failed: '+e.message; }
}
async function closeOrder(oid){
  try{ await post(`/orders/${oid}/close`, {}); await refreshOrders(); }
  catch(e){ $('#ordersMsg').textContent='close refused: '+e.message; }
}

// ---- Operator scan tab (PR-34) ----
let opBatch = null;
async function scanOrder(){
  try{
    const r = await post('/scan/order', {code: $('#scanCode').value.trim(),
                                         operator_id: $('#opId').value});
    opBatch = r.batch ? r.batch.batch_id
            : (r.order ? (await j('/orders/'+r.order.order_id)).progress.batch_ids.at(-1) : null);
    $('#scanGate').textContent = 'gate OK — batch '+(opBatch||'—');
    $('#scanGate').className = 'pill ok';
    $('#opPanel').classList.remove('hidden');
    await refreshWeigh();
  }catch(e){
    $('#scanGate').textContent = 'REJECTED: '+e.message;
    $('#scanGate').className = 'pill bad';
    $('#opPanel').classList.add('hidden');
  }
}
async function refreshWeigh(){
  if(!opBatch) return;
  const b = await j('/batches/'+opBatch);
  $('#weighCards').innerHTML = b.doses.map(d => {
    const booked = d.qty_actual ?? 0;
    const done = d.qty_actual != null;
    return `<div class="card" style="${done?'border-color:#0b4f2a':''}">
      <h3>${d.material_id} ${done?'✔':''}</h3>
      <div class="sub">target ${d.qty_target} kg · band ${d.tol_min}–${d.tol_max}</div>
      <div class="sub">booked <b>${booked}</b> · rest <b>${done?0:(d.qty_target-booked).toFixed(1)}</b></div>
      ${done?'' : `<div class="row">
        <input id="lot-${d.material_id}" placeholder="lot" style="width:80px">
        <input id="qty-${d.material_id}" type="number" placeholder="kg" style="width:80px">
        <button class="gh" onclick="weigh('${d.material_id}',false)">Boek</button>
        <button class="gh" onclick="weigh('${d.material_id}',true)">Totaal</button>
      </div>`}
    </div>`; }).join('');
}
async function weigh(mid, total){
  try{
    const lot = $('#lot-'+mid).value || 'L-0000';
    await post('/scan/label', {batch_id: opBatch, material_id: mid,
                               lot_no: lot, operator_id: $('#opId').value});
    await post('/scan/weigh', {batch_id: opBatch, material_id: mid,
      qty_kg: total ? null : parseFloat($('#qty-'+mid).value),
      lot_no: lot, operator_id: $('#opId').value, total});
    $('#opMsg').textContent = 'staged '+mid;
  }catch(e){ $('#opMsg').textContent = 'weigh rejected: '+e.message; }
}
async function scanReport(){
  try{ const r = await post('/scan/report', {batch_id: opBatch,
                                             operator_id: $('#opId').value});
       $('#opMsg').textContent = 'consumption booked: '+r.booked_materials.join(', ');
       await refreshWeigh(); }
  catch(e){ $('#opMsg').textContent = 'report scan rejected: '+e.message; }
}
async function bookProduction(){
  try{ await post('/production', {batch_id: opBatch,
        packs: parseInt($('#prodPacks').value), operator_id: $('#opId').value});
       $('#opMsg').textContent = 'production booked'; }
  catch(e){ $('#opMsg').textContent = 'production rejected: '+e.message; }
}
async function takeScanSample(){
  try{ const s = await post('/samples', {batch_id: opBatch,
        sample_type: $('#sampleType').value, operator_id: $('#opId').value});
       $('#opMsg').textContent = 'sample '+s.sample_id+' — label printed'; }
  catch(e){ $('#opMsg').textContent = 'sample rejected: '+e.message; }
}
```
Hook `refreshOrders()` into the existing polling loop (find the existing `setInterval`/refresh function and add a call when the orders tab is visible, or unconditionally — it is cheap). Verify the existing tab-switch handler picks up the new `data-tab` buttons automatically (it iterates `nav button`); if it uses a hardcoded list, extend it.

- [ ] **Step 3: Manual smoke test (offline)**

From `batch-engine`: `python -m uvicorn app:app --port 8000` and from `dashboard`: `python -m http.server 8080`. Open `http://localhost:8080` with `window.VLA_API` override: quickest is `http://localhost:8080/?` + temporarily `const API = 'http://localhost:8000/api/v1'` — or run only the API smoke via curl:
```
curl -s -X POST localhost:8000/api/v1/orders -H "Content-Type: application/json" -d '{"recipe_id":"chocolate-vla-1L","target_qty_L":5000}'
curl -s localhost:8000/api/v1/orders
```
Expected: order JSON with `PO-…` + list with progress. Check the Operator tab flow in the browser: scan order code → weigh cocoa 100 → report-scan → cards turn green-bordered with ✔.

- [ ] **Step 4: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/dashboard
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): dashboard Orders + Operator scan/weigh tabs with kleurentaal (PR-24/PR-34 UI)"
```

---

### Task 13: Integration gate + docs + spec-status + push

**Files:**
- Modify: `batch-engine/selftest.py` (2 new checks)
- Modify: `scenarios/vla-batch/README.md` (endpoints + fase 1 features)
- Modify (datalayer repo): `01-PRD/PRD-VlaBatchDemo.md` (build-status note), `09-Build/2026-07-21-bouwdesign-fase0-fase1.md` (status)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Extend selftest with fase-1 checks**

Append before the `# --- report ---` block in `selftest.py`:
```python
# --- 9. orders + scan-flow end-to-end (fase 1) ---
try:
    from vla.orders import OrderManager
    from vla.scan import ScanFlow, ScanRejected

    db9 = get_db()
    seed_recipes(db9)
    orders9 = OrderManager(db9, bus=None)
    runner9 = BatchRunner(db9, bus=None, rng=random.Random(21), orders=orders9)
    flow9 = ScanFlow(db9, runner9, orders9)

    o9 = orders9.create_order("chocolate-vla-1L", target_qty_L=5000)
    b9 = runner9.create_batch("chocolate-vla-1L", planned_L=5000,
                              order_id=o9["order_id"])
    gate = flow9.scan_order(o9["order_id"], operator_id="OP-7")
    flow9.scan_label(b9["batch_id"], "cocoa", lot_no="L-1", operator_id="OP-7")
    flow9.weigh(b9["batch_id"], "cocoa", total=True, lot_no="L-1",
                operator_id="OP-7")
    flow9.scan_report(b9["batch_id"], operator_id="OP-7")
    runner9.start_batch(b9["batch_id"], telemetry={
        "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
        "packs_total": 4980, "reject_count": 20,
        "dose_actuals": {"milk": 5000.0, "sugar": 500.0, "starch": 250.0}})
    closed = orders9.close_order(o9["order_id"])
    dose9 = db9.dw_doses.find_one({"batch_id": b9["batch_id"],
                                   "material_id": "cocoa"})
    check("9. orders + scan-flow e2e (gate/label/weigh/report/close)",
          gate["ok"] and closed["status"] == "DONE"
          and dose9["qty_actual"] == 100.0 and dose9["operator_id"] == "OP-7",
          f"order={closed['status']} cocoa_actual={dose9['qty_actual']}")
except Exception as e:
    import traceback
    check("9. orders + scan-flow e2e", False,
          f"exception: {e}\n{traceback.format_exc()}")


# --- 10. stop rule + scan rejections ---
try:
    db10 = get_db()
    seed_recipes(db10)
    orders10 = OrderManager(db10, bus=None)
    runner10 = BatchRunner(db10, bus=None, rng=random.Random(22), orders=orders10)
    flow10 = ScanFlow(db10, runner10, orders10)
    o10 = orders10.create_order("chocolate-vla-1L", target_qty_L=5000)
    try:
        orders10.close_order(o10["order_id"])
        stop_rule_ok = False
    except ValueError:
        stop_rule_ok = True
    try:
        flow10.scan_order("PO-NOPE", operator_id="OP-7")
        reject_ok = False
    except ScanRejected as ex:
        reject_ok = ex.reason == "unknown"
    check("10. stop rule (close w/o production) + scan rejection",
          stop_rule_ok and reject_ok,
          f"stop_rule={stop_rule_ok} reject={reject_ok}")
except Exception as e:
    import traceback
    check("10. stop rule + rejections", False,
          f"exception: {e}\n{traceback.format_exc()}")
```
Run: `python selftest.py` → `RESULT: ALL PASS` (10 checks). Run `python -m pytest tests -q` → all green.

- [ ] **Step 2: README update**

In `scenarios/vla-batch/README.md`: add the new endpoints to the API table/section (`/orders`, `/orders/{id}/batches`, `/orders/{id}/close`, `/scan/order`, `/scan/label`, `/scan/weigh`, `/scan/report`, `/production`, `/materials`, `/samples/{id}/reprint-label`) and a short "Fase 1 (v0.4): orders + scan-driven shop-floor flow" paragraph naming PR-23/24/25/26/27/34. Keep it factual and short.

- [ ] **Step 3: Spec status update (datalayer repo)**

In `c:\tools\techflow-os\sub-os\project-os\projects\datalayer\`:
- `01-PRD/PRD-VlaBatchDemo.md`: in the status line at the top append: `Bouwstatus: fase 0+1 gebouwd (PR-01..16 conform, PR-23..27 + PR-34 geïmplementeerd in de demo-stack, <datum>).`
- `09-Build/2026-07-21-bouwdesign-fase0-fase1.md`: change the Status line to `uitgevoerd (fase 0 + fase 1) — <datum>`.
Regenerate the two docx via the existing scratchpad converter `md_to_docx_any.py` if available; otherwise note it for the next docs session. Run the anonymization grep over both changed files (pattern from the repo convention) → 0 hits.

- [ ] **Step 4: Full offline compose check (optional but recommended)**

If Docker Desktop is available: from `c:\tools\techflow-os\sub-os\idp-os`:
```
docker compose -f docker-compose.slim.yml -f scenarios/vla-batch/docker-compose.vla.yml up -d --build vla-factory vla-batch-engine vla-dashboard
```
Then `curl -s localhost:<engine-port>/api/v1/health` → `{"status":"ok"}` and one order+batch round-trip. Tear down with `docker compose … down`. If Docker is not available locally, skip — the VPS deploy verifies this (with OBS running: this is devlog E2/E3 material).

- [ ] **Step 5: Commit + push both repos**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add -A
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): fase 1 integration gate — selftest 9+10, README, docs"
git -C c:\tools\techflow-os\sub-os\idp-os push origin main
git -C c:\tools\techflow-os\sub-os\project-os\projects\datalayer add -A
git -C c:\tools\techflow-os\sub-os\project-os\projects\datalayer commit -m "docs(build): bouwstatus fase 0+1 — PR-23..27 + PR-34 geimplementeerd"
git -C c:\tools\techflow-os\sub-os\project-os\projects\datalayer push origin main
```
(For the idp-os push: `git fetch` + rebase first if the remote moved.)

---

## Verification summary (end state)

1. `python -m pytest tests -q` → all tests pass (7 test files).
2. `python selftest.py` → 10 checks, `RESULT: ALL PASS`, fully offline.
3. `Select-String` sweep: no `vla_*` collection names anywhere in the scenario.
4. Dashboard: Orders tab creates/closes orders; Operator tab runs gate → label → weigh (Totaal) → report-scan → production → sample with label.
5. Spec docs updated + both repos pushed.
6. NOT in scope here (fase 2/3): PR-35 HU/verpakking, PR-17/18/21/22/29/30/32/33.
