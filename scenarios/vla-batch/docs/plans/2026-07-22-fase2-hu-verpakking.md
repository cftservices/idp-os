# Vla Batch Demo — Fase 2 (PR-35 verpakking & expeditie) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement PR-35 — light packaging & expedition: filled packs → tray/pallet → wrap → HU label + scan → cold-store putaway → shipping scan, with the APPROVED-gate and full traceability (delivery → HU → batch → report).

**Architecture:** New `vla/handling.py` (HandlingUnitManager) on the existing batch-engine, a new `dw_handling_units` collection, three scan-style endpoints reusing the `_scan_call` error mapping, an HU section in the batch report, and a Packaging block in the dashboard Operator tab. Events ride the existing BatchEvent stream (`hu_scanned`, `putaway_booked`, `hu_shipped`). No palletizer simulation, no WMS, no real GS1 registration (spec: PR-35 is deliberately light).

**Tech Stack:** unchanged — Python/FastAPI (batch-engine), vanilla-JS SPA (dashboard), pytest + selftest offline gate.

**Spec anchors:** PRD PR-35 + UC15 · FDS §B "Verpakking & expeditie" (4 steps + business rules) · 06-Model B.1 HandlingUnit + relatie `Batch 1–N HandlingUnit`.

## Global Constraints

- **Working dir for all engine commands:** `c:\tools\techflow-os\sub-os\idp-os\scenarios\vla-batch\batch-engine` (PowerShell).
- **Offline-first:** `python selftest.py` must end `RESULT: ALL PASS` without Mongo/MQTT; all new logic testable with in-memory DB.
- **HandlingUnit entity (06-Model, exact):** `{hu_id, batch_id, packs_count, location[koelmagazijn|expeditie], status[wrapped|stored|awaiting_shipment|shipped], ts}`. Flow uses `wrapped → awaiting_shipment (putaway, location koelmagazijn) → shipped (location expeditie)`; enum value `stored` stays defined for spec-compat but is not produced by the v1 flow (documented).
- **SSCC placeholder:** `hu_id` = 18 digits starting with prefix `80`, last digit = GS1 mod-10 check digit. NEVER a real GS1 company prefix.
- **APPROVED-gate (FDS):** an HU can only be created for a batch with `verdict == "APPROVED"`; HOLD/REJECTED/PENDING → rejection. HU creation happens on COMPLETE batches — do **NOT** use `_guard_bookable` here (that guard forbids COMPLETE; HU is a post-batch flow by design).
- **Σ packs_count rule (FDS/06-Model):** sum of `packs_count` over a batch's HUs may not exceed `batch.packs_total` → creation that would exceed is rejected; on `ship`, when the batch's HU-total ≠ `packs_total`, fire a difference event once per ship action.
- **Rejections:** reuse `ScanRejected(reason, message)` from `vla/scan.py` + the existing `_scan_call` mapping (unknown → 404, others → 409). New reasons: `not_approved`, `exceeds_production`, `wrong_status`.
- **English** code/comments/UI copy. No confidential names. NEVER Node-RED.
- **Repo:** commits to `c:\tools\techflow-os\sub-os\idp-os` main, trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Tests: new pytest files in `batch-engine/tests/`; selftest stays the integration gate at the END of every task.

---

### Task 1: dw_handling_units collection + SSCC helper

**Files:**
- Modify: `batch-engine/vla/db.py` (COLLECTIONS + docstring)
- Modify: `batch-engine/vla/model.py` (HU constants + sscc helpers)
- Test: `batch-engine/tests/test_hu_model.py`

**Interfaces:**
- Produces: `db.dw_handling_units` collection; `model.py`: `HU_WRAPPED, HU_STORED, HU_AWAITING, HU_SHIPPED = "wrapped", "stored", "awaiting_shipment", "shipped"`; `HU_STATUSES = [HU_WRAPPED, HU_STORED, HU_AWAITING, HU_SHIPPED]`; `LOC_COLDSTORE, LOC_EXPEDITION = "koelmagazijn", "expeditie"`; `sscc_check_digit(d17: str) -> int` (GS1 mod-10); `new_hu_id() -> str` (18 digits, prefix "80", valid check digit).

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_hu_model.py`:
```python
from vla import model as M
from vla.db import get_db, COLLECTIONS


def test_handling_units_collection_exists():
    assert "dw_handling_units" in COLLECTIONS
    db = get_db(mongo_url=None)
    assert db.dw_handling_units.count_documents({}) == 0


def test_sscc_check_digit_known_value():
    # GS1 reference: 17-digit base 00000000000000001 -> check digit 7
    assert M.sscc_check_digit("0" * 16 + "1") == 7


def test_new_hu_id_shape_and_checksum():
    hid = M.new_hu_id()
    assert len(hid) == 18 and hid.startswith("80") and hid.isdigit()
    assert int(hid[-1]) == M.sscc_check_digit(hid[:17])
    assert M.new_hu_id() != hid  # unique enough for a demo


def test_hu_constants():
    assert M.HU_STATUSES == ["wrapped", "stored", "awaiting_shipment", "shipped"]
    assert M.LOC_COLDSTORE == "koelmagazijn" and M.LOC_EXPEDITION == "expeditie"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hu_model.py -q` → FAIL (no `dw_handling_units`, no helpers).

- [ ] **Step 3: Implement**

`vla/db.py`: append `"dw_handling_units",` to `COLLECTIONS` (and mention it in the module docstring list).

`vla/model.py` (below the order constants):
```python
# HandlingUnit (PR-35, light packaging & expedition)
HU_WRAPPED, HU_STORED, HU_AWAITING, HU_SHIPPED = (
    "wrapped", "stored", "awaiting_shipment", "shipped",
)
HU_STATUSES = [HU_WRAPPED, HU_STORED, HU_AWAITING, HU_SHIPPED]
LOC_COLDSTORE, LOC_EXPEDITION = "koelmagazijn", "expeditie"

# SSCC placeholder prefix — NEVER a real GS1 company prefix (anonymization rule).
SSCC_PREFIX = "80"


def sscc_check_digit(d17: str) -> int:
    """GS1 mod-10 check digit over a 17-digit base (rightmost digit weight 3)."""
    total = sum(int(c) * (3 if i % 2 == 0 else 1)
                for i, c in enumerate(reversed(d17)))
    return (10 - total % 10) % 10


def new_hu_id() -> str:
    """18-digit SSCC-placeholder: '80' + date + random serial + check digit."""
    import random as _random
    from datetime import datetime as _dt
    base = f"{SSCC_PREFIX}{_dt.now().strftime('%y%m%d')}{_random.randint(0, 10**9 - 1):09d}"
    return base + str(sscc_check_digit(base))
```

- [ ] **Step 4: Run tests + selftest**

`python -m pytest tests/test_hu_model.py tests/test_db.py -q` → note: `tests/test_db.py::test_collections_are_dw_prefixed` asserts the exact EXPECTED list — extend that list with `"dw_handling_units"` in the same edit. Then full `python -m pytest tests -q` + `python selftest.py` → all green.

- [ ] **Step 5: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): dw_handling_units collection + SSCC-placeholder helpers (PR-35 basis)"
```

---

### Task 2: HandlingUnitManager — create (APPROVED-gate) / putaway / ship

**Files:**
- Create: `batch-engine/vla/handling.py`
- Test: `batch-engine/tests/test_handling.py`

**Interfaces:**
- Consumes: `ScanRejected` from `vla/scan.py`; `M.APPROVED`, HU constants (Task 1); `db.dw_handling_units`, `db.dw_batches`, `db.dw_batch_events`.
- Produces: `class HandlingUnitManager(db)` with:
  - `create_hu(batch_id, packs_count, operator_id=None) -> dict` — batch must exist (`unknown`), verdict APPROVED (`not_approved`), `packs_count > 0` (`invalid_qty`), Σ existing + new ≤ `batch.packs_total` (`exceeds_production`). Inserts `{hu_id: new_hu_id(), batch_id, packs_count, location: None, status: "wrapped", operator_id, ts}` + event `hu_scanned` `{hu_id, batch_id, packs_count, operator_id}`.
  - `putaway(hu_id, operator_id=None) -> dict` — HU must exist (`unknown`), status `wrapped` (`wrong_status`). Sets `location: "koelmagazijn"`, `status: "awaiting_shipment"` + event `putaway_booked`.
  - `ship(hu_id, operator_id=None) -> dict` — HU must exist (`unknown`), status `awaiting_shipment` (`wrong_status`). Sets `location: "expeditie"`, `status: "shipped"` + event `hu_shipped`; afterwards compares the batch's Σ packs_count vs `packs_total` and fires `hu_packs_difference` `{batch_id, packs_total, hu_total, difference}` when ≠ (every ship action re-checks; equal → no event).
  - `list_hus(batch_id=None) -> list[dict]`.
  - All rejections via internal `_reject` that logs a `scan_rejected` BatchEvent (same payload shape as scan.py: `{code, reason, operator_id}`) then raises `ScanRejected`.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_handling.py`:
```python
import random
import pytest

from vla import model as M
from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.handling import HandlingUnitManager
from vla.scan import ScanRejected

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20,
            "dose_actuals": {"milk": 5000.0, "sugar": 500.0,
                             "starch": 250.0, "cocoa": 100.0}}


def approved_batch():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(30))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    res = runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    assert res["verdict"] == "APPROVED"
    return db, res["batch_id"]


def test_full_hu_flow_wrapped_putaway_shipped():
    db, bid = approved_batch()
    hum = HandlingUnitManager(db)
    hu = hum.create_hu(bid, packs_count=2400, operator_id="OP-7")
    assert hu["status"] == "wrapped" and hu["hu_id"].startswith("80")
    assert len(hu["hu_id"]) == 18
    hu = hum.putaway(hu["hu_id"], operator_id="OP-7")
    assert hu["status"] == "awaiting_shipment" and hu["location"] == "koelmagazijn"
    hu = hum.ship(hu["hu_id"], operator_id="OP-7")
    assert hu["status"] == "shipped" and hu["location"] == "expeditie"
    diff = [e for e in db.dw_batch_events.find({"event_type": "hu_packs_difference"})]
    assert len(diff) == 1 and diff[0]["payload"]["difference"] == 4980 - 2400


def test_approved_gate_blocks_hold_rejected():
    db, bid = approved_batch()
    db.dw_batches.update_one({"batch_id": bid}, {"$set": {"verdict": "REJECTED"}})
    hum = HandlingUnitManager(db)
    with pytest.raises(ScanRejected) as ei:
        hum.create_hu(bid, packs_count=100, operator_id="OP-7")
    assert ei.value.reason == "not_approved"


def test_sum_packs_may_not_exceed_production():
    db, bid = approved_batch()
    hum = HandlingUnitManager(db)
    hum.create_hu(bid, packs_count=3000)
    with pytest.raises(ScanRejected) as ei:
        hum.create_hu(bid, packs_count=2000)  # 5000 > 4980
    assert ei.value.reason == "exceeds_production"


def test_lifecycle_order_enforced():
    db, bid = approved_batch()
    hum = HandlingUnitManager(db)
    hu = hum.create_hu(bid, packs_count=100)
    with pytest.raises(ScanRejected) as ei:
        hum.ship(hu["hu_id"])  # wrapped -> ship skips putaway
    assert ei.value.reason == "wrong_status"
```

- [ ] **Step 2: Run test to verify it fails** → `python -m pytest tests/test_handling.py -q` FAIL (`vla.handling` missing).

- [ ] **Step 3: Implement `vla/handling.py`**

```python
"""HandlingUnit flow (PR-35, light): filled packs -> pallet -> wrap -> HU label
-> cold-store putaway -> shipping. APPROVED-gate: only an APPROVED batch may
enter the warehouse (the Solve story extended to logistics). Deliberately NOT
covered (spec): palletizer simulation, WMS, real GS1 registration.

Runs POST-batch: HUs are created for COMPLETE batches, so this module does not
use BatchRunner._guard_bookable (that guard is for in-process bookings)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import model as M
from .scan import ScanRejected

log = logging.getLogger("vla.handling")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HandlingUnitManager:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ create

    def create_hu(self, batch_id: str, packs_count: int,
                  operator_id: Optional[str] = None) -> dict:
        batch = self.db.dw_batches.find_one({"batch_id": batch_id})
        if batch is None:
            self._reject(None, batch_id, "unknown", operator_id)
        if batch.get("verdict") != M.APPROVED:
            self._reject(batch_id, batch_id, "not_approved", operator_id)
        if int(packs_count) <= 0:
            self._reject(batch_id, str(packs_count), "invalid_qty", operator_id)
        already = sum(int(h["packs_count"]) for h in
                      self.db.dw_handling_units.find({"batch_id": batch_id}))
        if already + int(packs_count) > int(batch.get("packs_total", 0)):
            self._reject(batch_id, str(packs_count), "exceeds_production",
                         operator_id)
        row = {
            "hu_id": M.new_hu_id(),
            "batch_id": batch_id,
            "packs_count": int(packs_count),
            "location": None,
            "status": M.HU_WRAPPED,
            "operator_id": operator_id,
            "ts": _iso(),
        }
        self.db.dw_handling_units.insert_one(row)
        self._event(batch_id, "hu_scanned",
                    {"hu_id": row["hu_id"], "batch_id": batch_id,
                     "packs_count": int(packs_count), "operator_id": operator_id})
        return {k: v for k, v in row.items()}

    # ----------------------------------------------------------------- putaway

    def putaway(self, hu_id: str, operator_id: Optional[str] = None) -> dict:
        hu = self._hu_or_reject(hu_id, operator_id)
        if hu["status"] != M.HU_WRAPPED:
            self._reject(hu["batch_id"], hu_id, "wrong_status", operator_id)
        self.db.dw_handling_units.update_one(
            {"hu_id": hu_id},
            {"$set": {"location": M.LOC_COLDSTORE, "status": M.HU_AWAITING,
                      "ts": _iso()}})
        self._event(hu["batch_id"], "putaway_booked",
                    {"hu_id": hu_id, "location": M.LOC_COLDSTORE,
                     "operator_id": operator_id})
        return self.db.dw_handling_units.find_one({"hu_id": hu_id})

    # -------------------------------------------------------------------- ship

    def ship(self, hu_id: str, operator_id: Optional[str] = None) -> dict:
        hu = self._hu_or_reject(hu_id, operator_id)
        if hu["status"] != M.HU_AWAITING:
            self._reject(hu["batch_id"], hu_id, "wrong_status", operator_id)
        self.db.dw_handling_units.update_one(
            {"hu_id": hu_id},
            {"$set": {"location": M.LOC_EXPEDITION, "status": M.HU_SHIPPED,
                      "ts": _iso()}})
        self._event(hu["batch_id"], "hu_shipped",
                    {"hu_id": hu_id, "operator_id": operator_id})
        batch = self.db.dw_batches.find_one({"batch_id": hu["batch_id"]}) or {}
        hu_total = sum(int(h["packs_count"]) for h in
                       self.db.dw_handling_units.find({"batch_id": hu["batch_id"]}))
        packs_total = int(batch.get("packs_total", 0))
        if hu_total != packs_total:
            self._event(hu["batch_id"], "hu_packs_difference",
                        {"batch_id": hu["batch_id"], "packs_total": packs_total,
                         "hu_total": hu_total,
                         "difference": packs_total - hu_total})
        return self.db.dw_handling_units.find_one({"hu_id": hu_id})

    # ------------------------------------------------------------------- query

    def list_hus(self, batch_id: Optional[str] = None) -> list[dict]:
        query = {"batch_id": batch_id} if batch_id else {}
        return self.db.dw_handling_units.find(query)

    # ----------------------------------------------------------------- helpers

    def _hu_or_reject(self, hu_id: str, operator_id: Optional[str]) -> dict:
        hu = self.db.dw_handling_units.find_one({"hu_id": hu_id})
        if hu is None:
            self._reject(None, hu_id, "unknown", operator_id)
        return hu

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

- [ ] **Step 4: Run tests + selftest** → `python -m pytest tests/test_handling.py -q` PASS, then full `python -m pytest tests -q` + `python selftest.py` → all green.

- [ ] **Step 5: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): HandlingUnitManager — APPROVED-gate, putaway, ship, packs-sum rule (PR-35)"
```

---

### Task 3: HU endpoints

**Files:**
- Modify: `batch-engine/app.py`
- Test: `batch-engine/tests/test_hu_api.py`

**Interfaces:**
- Consumes: `HandlingUnitManager` (Task 2), `_scan_call` (existing).
- Produces: `POST /api/v1/hu {batch_id, packs_count, operator_id?}`, `POST /api/v1/hu/{hu_id}/putaway {operator_id?}`, `POST /api/v1/hu/{hu_id}/ship {operator_id?}`, `GET /api/v1/hu?batch_id=` — all through `_scan_call` (unknown → 404, other ScanRejected → 409, body `{"detail": {"message", "reason"}}`). `STATE["handling"]` wired in `_startup`.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_hu_api.py`:
```python
from fastapi.testclient import TestClient

import app as appmod


def test_hu_endpoints_roundtrip_and_gate():
    with TestClient(appmod.app) as c:
        r = c.post("/api/v1/batches",
                   json={"recipe_id": "chocolate-vla-1L", "planned_L": 5000})
        bid = r.json()["batch_id"]
        # AUTO_START default runs the batch to COMPLETE synchronously offline;
        # verdict is APPROVED with fabricated healthy telemetry
        b = c.get(f"/api/v1/batches/{bid}").json()
        assert b["verdict"] == "APPROVED"
        r2 = c.post("/api/v1/hu", json={"batch_id": bid, "packs_count": 1200,
                                        "operator_id": "OP-7"})
        assert r2.status_code == 200
        hu_id = r2.json()["hu_id"]
        assert len(hu_id) == 18 and hu_id.startswith("80")
        r3 = c.post(f"/api/v1/hu/{hu_id}/putaway", json={"operator_id": "OP-7"})
        assert r3.json()["status"] == "awaiting_shipment"
        r4 = c.post(f"/api/v1/hu/{hu_id}/ship", json={"operator_id": "OP-7"})
        assert r4.json()["status"] == "shipped"
        r5 = c.get(f"/api/v1/hu?batch_id={bid}")
        assert len(r5.json()) == 1
        # gate: unknown HU -> 404 with reason
        r6 = c.post("/api/v1/hu/800000000000000000/putaway", json={})
        assert r6.status_code == 404
        assert r6.json()["detail"]["reason"] == "unknown"
```

- [ ] **Step 2: Run test to verify it fails** → `python -m pytest tests/test_hu_api.py -q` FAIL (404 route).

- [ ] **Step 3: Implement in app.py**

Import `from vla.handling import HandlingUnitManager`. In `_startup`, after the scan wiring: `STATE["handling"] = HandlingUnitManager(db)`. Models + endpoints:
```python
class CreateHu(BaseModel):
    batch_id: str
    packs_count: int
    operator_id: str | None = None


class HuAction(BaseModel):
    operator_id: str | None = None


def _handling() -> "HandlingUnitManager":
    h = STATE.get("handling")
    if h is None:
        raise HTTPException(503, "engine not initialized")
    return h


@app.post(f"{API}/hu")
def create_hu(body: CreateHu):
    return _scan_call(_handling().create_hu, body.batch_id, body.packs_count,
                      body.operator_id)


@app.post(f"{API}/hu/{{hu_id}}/putaway")
def putaway_hu(hu_id: str, body: HuAction):
    return _scan_call(_handling().putaway, hu_id, body.operator_id)


@app.post(f"{API}/hu/{{hu_id}}/ship")
def ship_hu(hu_id: str, body: HuAction):
    return _scan_call(_handling().ship, hu_id, body.operator_id)


@app.get(f"{API}/hu")
def list_hus(batch_id: str | None = Query(default=None)):
    return _handling().list_hus(batch_id)
```

- [ ] **Step 4: Run tests + selftest** → targeted test PASS, full suite + selftest green.

- [ ] **Step 5: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): HU endpoints create/putaway/ship/list via scan error-contract (PR-35)"
```

---

### Task 4: Traceability — HU section in the batch report

**Files:**
- Modify: `batch-engine/vla/report.py` (`assemble_report` + PDF section)
- Modify: `batch-engine/vla/batches.py` (`get_batch` bundles `handling_units`)
- Test: `batch-engine/tests/test_hu_report.py`

**Interfaces:**
- Consumes: `dw_handling_units` rows.
- Produces: `BatchRunner.get_batch` bundle gains `"handling_units": [...]` (same projection as stored rows); `assemble_report` gains top-level `"handling_units"` key (list of `{hu_id, packs_count, location, status, ts}`); the PDF renders an "Handling units (PR-35)" table when non-empty (follow the existing reportlab table style in `render_pdf` — read it first, mirror the doses-table pattern). JSON report unchanged otherwise.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_hu_report.py`:
```python
import random

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.handling import HandlingUnitManager
from vla.report import render_json, render_pdf

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def test_report_contains_handling_units():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(31))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    hum = HandlingUnitManager(db)
    hu = hum.create_hu(b["batch_id"], packs_count=2400, operator_id="OP-7")
    hum.putaway(hu["hu_id"])
    rep = render_json(runner.get_batch(b["batch_id"]))
    assert len(rep["handling_units"]) == 1
    assert rep["handling_units"][0]["hu_id"] == hu["hu_id"]
    assert rep["handling_units"][0]["status"] == "awaiting_shipment"
    pdf = render_pdf(runner.get_batch(b["batch_id"]))
    assert pdf[:4] == b"%PDF"
```

- [ ] **Step 2: Run to verify it fails** → KeyError `handling_units`.

- [ ] **Step 3: Implement**

`batches.py` `get_batch`: add to the returned bundle:
```python
            "handling_units": self.db.dw_handling_units.find({"batch_id": batch_id}),
```
`report.py` `assemble_report`: add
```python
        "handling_units": [
            {"hu_id": h.get("hu_id"), "packs_count": h.get("packs_count"),
             "location": h.get("location"), "status": h.get("status"),
             "ts": h.get("ts")}
            for h in batch.get("handling_units", [])
        ],
```
`render_pdf`: read the existing function first; after the samples/alarms table add an equivalent table section titled `Handling units (PR-35)` with columns `HU (SSCC-placeholder) | Packs | Location | Status`, rendered only when the list is non-empty — reuse the same table-style helper the other sections use.

- [ ] **Step 4: Run tests + selftest** → green (existing report tests untouched: `handling_units` is additive).

- [ ] **Step 5: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/batch-engine
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): HU traceability in batch report json+pdf (PR-35, levering->HU->batch->rapport)"
```

---

### Task 5: Dashboard — Packaging & shipping block in the Operator tab

**Files:**
- Modify: `dashboard/index.html`

**Interfaces:**
- Consumes: Task 3 endpoints; existing helpers `j/post/$`, error shape `{"detail":{"message","reason"}}` already unwrapped by `j()`.
- Produces: a "Packaging & shipping (PR-35)" section inside `#tab-operator` below the sample block: HU create form (packs count + button), HU list for the scanned batch (hu_id, packs, status badge, location; action button per status: wrapped→"Putaway", awaiting_shipment→"Ship"; shipped rows show dark-green badge `#0b4f2a`, no button). The block also works for a COMPLETE batch: the operator gate currently rejects COMPLETE batches (`not_active`) — so this section gets its own small lookup: a batch-id input prefilled from the last scanned/created batch + "Load HUs" button calling `GET /hu?batch_id=` and `GET /batches/{id}` (for verdict/packs_total display: "APPROVED · 4980 packs · in HUs: n"). English copy throughout.

- [ ] **Step 1: Add the HTML** (inside `#tab-operator`, after the Sample `<div class="row">…</div>` block):
```html
      <h2>Packaging &amp; shipping (PR-35)</h2>
      <div class="row">
        <label>Batch<input id="huBatch" placeholder="B-…" style="width:220px"></label>
        <button class="gh" onclick="loadHus()">Load HUs</button>
        <span id="huInfo" class="sub"></span>
      </div>
      <div class="row">
        <label>Packs on pallet<input id="huPacks" type="number" value="1200" min="1" style="width:110px"></label>
        <button class="act" onclick="createHu()">Wrap pallet + print HU label</button>
      </div>
      <table><thead><tr><th>HU (SSCC-placeholder)</th><th>Packs</th><th>Status</th><th>Location</th><th></th></tr></thead>
        <tbody id="huRows"></tbody></table>
```
Note: this section lives inside `#opPanel`? NO — place it OUTSIDE `#opPanel` (directly at the end of `section#tab-operator`), because HU work targets COMPLETE batches for which the scan-gate panel stays closed. Verify placement in the actual file.

- [ ] **Step 2: Add the JS** (append in the script, English copy):
```javascript
// ---- Packaging & shipping (PR-35) ----
async function loadHus(){
  const bid = $('#huBatch').value.trim() || opBatch;
  if(!bid){ $('#huInfo').textContent = 'enter a batch id'; return; }
  $('#huBatch').value = bid;
  try{
    const [b, hus] = [await j('/batches/'+bid), await j('/hu?batch_id='+bid)];
    const inHu = hus.reduce((s,h)=>s+h.packs_count,0);
    $('#huInfo').textContent = `${b.verdict||'—'} · ${b.packs_total} packs · in HUs: ${inHu}`;
    $('#huRows').innerHTML = hus.map(h => `<tr>
      <td>${h.hu_id}</td><td>${h.packs_count}</td>
      <td><span class="badge" style="background:${
        h.status==='shipped' ? '#0b4f2a' : h.status==='awaiting_shipment' ? '#1a7f37' : '#57606a'
      };color:#fff">${h.status}</span></td>
      <td>${h.location||'—'}</td>
      <td>${h.status==='wrapped' ? `<button class="gh" onclick="huAction('${h.hu_id}','putaway')">Putaway</button>`
          : h.status==='awaiting_shipment' ? `<button class="gh" onclick="huAction('${h.hu_id}','ship')">Ship</button>` : ''}</td>
    </tr>`).join('');
  }catch(e){ $('#huInfo').textContent = 'load failed: '+e.message; }
}
async function createHu(){
  const bid = $('#huBatch').value.trim() || opBatch;
  if(!bid){ $('#huInfo').textContent = 'enter a batch id'; return; }
  try{ await post('/hu', {batch_id: bid, packs_count: parseInt($('#huPacks').value),
                          operator_id: $('#opId').value});
       await loadHus(); }
  catch(e){ $('#huInfo').textContent = 'HU rejected: '+e.message; }
}
async function huAction(huId, action){
  try{ await post(`/hu/${huId}/${action}`, {operator_id: $('#opId').value});
       await loadHus(); }
  catch(e){ $('#huInfo').textContent = action+' rejected: '+e.message; }
}
```

- [ ] **Step 3: Static verification** — HTML parses (html.parser), all new onclick handlers defined, no duplicate ids, section outside `#opPanel` so it is usable without a scan-gate. Then curl round-trip against the offline engine (create batch → COMPLETE → POST /hu → putaway → ship → GET /hu shows shipped).

- [ ] **Step 4: Commit**

```bash
git -C c:\tools\techflow-os\sub-os\idp-os add scenarios/vla-batch/dashboard
git -C c:\tools\techflow-os\sub-os\idp-os commit -m "feat(vla): dashboard Packaging & shipping block — HU wrap/putaway/ship (PR-35 UI)"
```

---

### Task 6: Integration gate + docs + push (closes fase 2)

**Files:**
- Modify: `batch-engine/selftest.py` (check 11)
- Modify: `scenarios/vla-batch/README.md` (HU endpoints + fase 2 paragraph)
- Modify (datalayer repo): `01-PRD/PRD-VlaBatchDemo.md` (bouwstatus PR-35 → gebouwd), `09-Build/2026-07-21-bouwdesign-fase0-fase1.md` (roadmap-rij fase 2 → uitgevoerd + datum)

**Steps:**

- [ ] **Step 1: selftest check 11** (append before the report block):
```python
# --- 11. HU flow e2e (PR-35): APPROVED-gate + wrap/putaway/ship + traceability ---
try:
    from vla.handling import HandlingUnitManager

    db11 = get_db()
    seed_recipes(db11)
    runner11 = BatchRunner(db11, bus=None, rng=random.Random(33))
    b11 = runner11.create_batch("chocolate-vla-1L", planned_L=5000)
    r11 = runner11.start_batch(b11["batch_id"], telemetry={
        "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
        "packs_total": 4980, "reject_count": 20,
        "dose_actuals": {"milk": 5000.0, "sugar": 500.0,
                         "starch": 250.0, "cocoa": 100.0}})
    hum11 = HandlingUnitManager(db11)
    hu11 = hum11.create_hu(b11["batch_id"], 2400, operator_id="OP-7")
    hum11.putaway(hu11["hu_id"]); hum11.ship(hu11["hu_id"])
    shipped = db11.dw_handling_units.find_one({"hu_id": hu11["hu_id"]})
    # gate: a REJECTED batch may not enter the warehouse
    db11.dw_batches.update_one({"batch_id": b11["batch_id"]},
                               {"$set": {"verdict": "REJECTED"}})
    try:
        hum11.create_hu(b11["batch_id"], 100)
        gate_ok = False
    except ScanRejected as ex:
        gate_ok = ex.reason == "not_approved"
    rep11 = render_json(runner11.get_batch(b11["batch_id"]))
    check("11. HU flow e2e (wrap/putaway/ship + APPROVED-gate + report)",
          r11["verdict"] == "APPROVED" and shipped["status"] == "shipped"
          and gate_ok and len(rep11["handling_units"]) == 1,
          f"hu={hu11['hu_id']} shipped={shipped['status']} gate_ok={gate_ok}")
except Exception as e:
    import traceback
    check("11. HU flow e2e", False, f"exception: {e}\n{traceback.format_exc()}")
```
(`ScanRejected` import: reuse the one from check 10 — it is already imported there; if scoping is unclear, import inside the try.) Run `python selftest.py` → 11 checks ALL PASS + full `python -m pytest tests -q`.

- [ ] **Step 2: README** — add the 4 HU endpoints to the API list + short "Fase 2 (PR-35)" paragraph (wrap → HU label SSCC-placeholder 80 → putaway koelmagazijn → ship, APPROVED-gate, geen WMS/palletizer/GS1).

- [ ] **Step 3: Datalayer docs** — PRD status line: append `PR-35 gebouwd (fase 2, <datum>)`; bouwdesign §4 roadmap-rij Fase 2 → `uitgevoerd <datum>`. Docx regen via scratchpad `md_to_docx_any.py` (PYTHONIOENCODING=utf-8). Anonymization grep over both changed .md files (same pattern as fase 1 Task 13) → 0 hits, paste in report.

- [ ] **Step 4: Commit + push both repos** (idp-os: `feat(vla): fase 2 integration gate — selftest 11, README, docs (PR-35 af)`; datalayer: `docs(build): bouwstatus fase 2 — PR-35 gebouwd`). Fetch/rebase first if remotes moved.

---

## Verification summary (end state)

1. Full pytest suite green (27 + ~4 new HU tests); `python selftest.py` → 11 checks ALL PASS offline.
2. HU flow end-to-end via API én dashboard-blok; APPROVED-gate aantoonbaar (REJECTED batch → 409 not_approved).
3. Batch report (json + pdf) toont handling_units — traceability levering → HU → batch → rapport.
4. Beide repo's gepusht; PRD-status PR-35 = gebouwd.
5. Blijft open (fase 3): PR-17/18/21/22/29/30/32/33; VPS runtime-verify (E4).
