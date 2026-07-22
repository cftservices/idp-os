# Vla Batch Demo — Fase 3 (CBM / OEE / management-rapporten / EBR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the maintenance/KPI cluster: PR-17 (running_hours + status on UNS), PR-18 (CBM cook-unit fouling — the second Solve story: predictive alert before batches fail), PR-29 (Dirty/Allocated + CIP-gating), PR-19-part (acknowledge-flow, needed by PR-30), PR-21 (OEE view), PR-32 (equipment-health view), PR-30 (EBR), PR-22 (period management report), PR-33 (maintenance report per equipment).

**Architecture:** New `vla/equipment.py` (EquipmentMonitor: per-equipment meta in `dw_equipment_meta`, running-hours derived from the existing `dw_equipment_state` history, CBM fouling model + alerts in `dw_cbm_alerts`, CIP action). BatchRunner hooks: heat-up capture in `_finalize`, Dirty-gate in `create_batch`, Allocated on create. Report layer grows into an EBR + two new report types (period, equipment). Dashboard gets an Equipment tab. **Documented substitution:** fouling lives in the MES layer (measured/fabricated heat-up per batch), NOT in factory physics — the factory sim stays untouched this fase; factory-native degradation is a VPS-phase enhancement.

**Spec anchors:** PRD §Maintenance/events/KPI (PR-17/18/19/21/22/32/33) + PR-29/30 · FDS §G.1 (views) + §G.2 (reports) + FSM init-row ("equipment niet Dirty").

## Global Constraints

- **Working dir:** `c:\tools\techflow-os\sub-os\idp-os\scenarios\vla-batch\batch-engine` (PowerShell). Repo: `c:\tools\techflow-os\sub-os\idp-os`, trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- **Offline-first:** `python selftest.py` (11 checks, grows to 13) must stay ALL PASS without Mongo/MQTT; every feature testable with in-memory DB.
- **CBM model constants (single source in `vla/equipment.py`):** `BASE_HEATUP_SEC = 120.0` · `HEATUP_INCREASE_PER_BATCH = 0.15` (fabricated heat-up = base × (1 + 0.15 × batches_since_cip)) · `CBM_ALERT_FACTOR = 1.35` (alert when heat-up ≥ base × 1.35 → fires on the 3rd batch after CIP) · `DIRTY_AFTER_BATCHES = 4` (cook-unit → Dirty after the 4th batch; the 5th refuses to start). Predictive story: **alert (batch 3) comes BEFORE refusal (batch 5)**.
- **OEE formula (PR-21, explicit):** per equipment — `availability` = running_sec / (running_sec + down_sec + error_sec + dirty_sec) from the `dw_equipment_state` history (1.0 when no data); `performance` = cook-unit only: BASE_HEATUP_SEC / avg(cook_heatup_sec) capped at 1.0, others 1.0; `quality` = Σ packs of APPROVED batches / Σ packs of all COMPLETE batches (1.0 when no batches). `oee = availability × performance × quality`.
- **Status enum grows:** Running | Idle | Down | Error | **Dirty** | **Allocated** (06-Model + PR-29). Existing history feed keeps writing Running/Idle; Dirty/Allocated are written by the new hooks.
- **No internal PR-codes in customer-facing output** (PDF headings, dashboard h2's) — comments only (fase-2 review rule).
- **English** code/comments/UI copy. Reports stay reportlab (BIRT stand-in). Rejections reuse `ScanRejected` + `_scan_call` mapping where scan-style, plain 404/409 HTTPException where simpler.

---

### Task 1: EquipmentMonitor — meta, running_hours, UNS publish (PR-17)

**Files:**
- Create: `batch-engine/vla/equipment.py`
- Modify: `batch-engine/vla/db.py` (COLLECTIONS += `dw_equipment_meta`, `dw_cbm_alerts`)
- Modify: `batch-engine/tests/test_db.py` (EXPECTED list += both)
- Modify: `batch-engine/vla/batches.py` (`_feed_equipment_state` calls the monitor)
- Modify: `batch-engine/app.py` (wire `STATE["equipment"]`, `GET /equipment`)
- Test: `batch-engine/tests/test_equipment_monitor.py`

**Interfaces:**
- Produces: `class EquipmentMonitor(db, bus=None)` with:
  - `ensure_meta(equipment_id) -> dict` — upsert `{equipment_id, area, batches_since_cip: 0, dirty: False, last_cip_at: None, heatup_history: []}` in `dw_equipment_meta`.
  - `running_hours(equipment_id) -> float` — derived from `dw_equipment_state` history: sum the durations of intervals that START with a `Running` row and end at the next row for the same equipment (ISO ts parse); open Running interval counts to now; return hours rounded 4.
  - `on_state_change(equipment_id, state) -> None` — publishes `{Area}/{equipment_id}/Status/state` and `.../Status/running_hours` via `bus.publish_json` (no-op offline).
  - `snapshot() -> list[dict]` — per equipment: meta + latest state + running_hours.
- `BatchRunner._feed_equipment_state` additionally calls `self.equipment.on_state_change(eq, state)` when a monitor is attached (`BatchRunner.__init__` gains `equipment=None`).
- `app.py`: `_startup` creates `equipment = EquipmentMonitor(db, bus)` BEFORE the runner and passes it to `BatchRunner(..., equipment=equipment)` + `STATE["equipment"]`; `GET /api/v1/equipment` returns `snapshot()`.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_equipment_monitor.py`:
```python
import random

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.equipment import EquipmentMonitor

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def setup():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(40), equipment=mon)
    return db, mon, runner


def test_meta_upsert_and_snapshot():
    db, mon, runner = setup()
    meta = mon.ensure_meta("cook-unit-01")
    assert meta["batches_since_cip"] == 0 and meta["dirty"] is False
    snap = mon.snapshot()
    ids = {s["equipment_id"] for s in snap}
    assert "cook-unit-01" in ids and "filler-01" in ids


def test_running_hours_from_state_history():
    db, mon, runner = setup()
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    # instant mode writes the full Running->...->Idle history synchronously;
    # intervals are near-zero seconds but MUST be >= 0 and parseable
    rh = mon.running_hours("cook-unit-01")
    assert rh >= 0.0
    snap = {s["equipment_id"]: s for s in mon.snapshot()}
    assert snap["cook-unit-01"]["state"] == "Idle"
    assert "running_hours" in snap["cook-unit-01"]
```

- [ ] **Step 2: Run to verify it fails** → `python -m pytest tests/test_equipment_monitor.py -q` FAIL (module missing).

- [ ] **Step 3: Implement `vla/equipment.py`**

```python
"""EquipmentMonitor — per-equipment meta, derived running-hours, and the CBM
fouling model (PR-17/18/29). The fouling model lives in the MES layer as a
documented substitution: the factory sim stays untouched; heat-up per batch is
measured (live) or fabricated (instant) and trended here."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import model as M

log = logging.getLogger("vla.equipment")

# CBM fouling model (PR-18) — single source of truth for the constants.
BASE_HEATUP_SEC = 120.0
HEATUP_INCREASE_PER_BATCH = 0.15
CBM_ALERT_FACTOR = 1.35
DIRTY_AFTER_BATCHES = 4

EQUIPMENT_IDS = ["receiving-tank-01", "process-tank-01", "cook-unit-01",
                 "cooler-01", "filler-01"]


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class EquipmentMonitor:
    def __init__(self, db, bus=None):
        self.db = db
        self.bus = bus

    # ------------------------------------------------------------------- meta

    def ensure_meta(self, equipment_id: str) -> dict:
        meta = self.db.dw_equipment_meta.find_one({"equipment_id": equipment_id})
        if meta is None:
            meta = {
                "equipment_id": equipment_id,
                "area": M.area_of(equipment_id),
                "batches_since_cip": 0,
                "dirty": False,
                "last_cip_at": None,
                "heatup_history": [],
            }
            self.db.dw_equipment_meta.insert_one(meta)
            meta = self.db.dw_equipment_meta.find_one(
                {"equipment_id": equipment_id})
        return meta

    # ---------------------------------------------------------- running hours

    def running_hours(self, equipment_id: str) -> float:
        rows = [r for r in self.db.dw_equipment_state.find(
            {"equipment_id": equipment_id})]
        total = 0.0
        for i, row in enumerate(rows):
            if row.get("state") != "Running":
                continue
            start = _parse(row["ts"])
            end = _parse(rows[i + 1]["ts"]) if i + 1 < len(rows) \
                else datetime.now(timezone.utc)
            total += max(0.0, (end - start).total_seconds())
        return round(total / 3600.0, 4)

    # ------------------------------------------------------------ UNS publish

    def on_state_change(self, equipment_id: str, state: str) -> None:
        if self.bus is None:
            return
        area = M.area_of(equipment_id)
        self.bus.publish_json(f"{area}/{equipment_id}/Status/state",
                              {"value": state, "ts": _iso()})
        self.bus.publish_json(f"{area}/{equipment_id}/Status/running_hours",
                              {"value": self.running_hours(equipment_id),
                               "unit": "h", "ts": _iso()})

    # --------------------------------------------------------------- snapshot

    def snapshot(self) -> list[dict]:
        out = []
        for eq in EQUIPMENT_IDS:
            meta = self.ensure_meta(eq)
            hist = self.db.dw_equipment_state.find({"equipment_id": eq})
            latest = hist[-1]["state"] if hist else "Idle"
            if meta.get("dirty"):
                latest = "Dirty"
            out.append({
                "equipment_id": eq,
                "area": meta["area"],
                "state": latest,
                "running_hours": self.running_hours(eq),
                "batches_since_cip": meta.get("batches_since_cip", 0),
                "dirty": bool(meta.get("dirty")),
                "last_cip_at": meta.get("last_cip_at"),
            })
        return out
```

- [ ] **Step 4: Wire collections + runner + endpoint**

`db.py`: COLLECTIONS += `"dw_equipment_meta", "dw_cbm_alerts"` (+ docstring); `tests/test_db.py` EXPECTED += both. `batches.py`: `__init__` gains `equipment=None` → `self.equipment = equipment`; at the end of `_feed_equipment_state`'s loop body add:
```python
            if self.equipment is not None:
                self.equipment.on_state_change(eq, "Running" if eq in running else "Idle")
```
`app.py`: import `EquipmentMonitor`; in `_startup` create `equipment = EquipmentMonitor(db, bus)` before the runner, pass `equipment=equipment` into `BatchRunner`, add to STATE; endpoint:
```python
@app.get(f"{API}/equipment")
def equipment_snapshot():
    eq = STATE.get("equipment")
    if eq is None:
        raise HTTPException(503, "engine not initialized")
    return eq.snapshot()
```

- [ ] **Step 5: Full pytest + selftest** → green (existing tests construct BatchRunner without `equipment` → hook skipped).

- [ ] **Step 6: Commit** — `feat(vla): EquipmentMonitor — meta, derived running-hours, UNS state publish (PR-17)`

---

### Task 2: CBM fouling — heat-up trend + alert (PR-18)

**Files:**
- Modify: `batch-engine/vla/equipment.py` (`record_batch_completed`, `open_alerts`)
- Modify: `batch-engine/vla/batches.py` (`_finalize` captures heat-up + calls the monitor)
- Test: `batch-engine/tests/test_cbm.py`

**Interfaces:**
- Produces: `EquipmentMonitor.record_batch_completed(batch_id, cook_heatup_sec) -> Optional[dict]` — increments `batches_since_cip` for `cook-unit-01`, appends `{batch_id, heatup_sec, ts}` to `heatup_history` (keep last 20), and when `cook_heatup_sec >= BASE_HEATUP_SEC * CBM_ALERT_FACTOR` AND no unresolved fouling alert exists → inserts `{alert_id: "CBM-"+hex, equipment_id: "cook-unit-01", alert_type: "fouling_heatup", message: "Heat-up time <x>s exceeds <threshold>s — plan CIP cleaning", heatup_sec, batches_since_cip, resolved: False, ts}` into `dw_cbm_alerts` + `cbm_alert` BatchEvent; returns the alert or None. `open_alerts(equipment_id=None) -> list`.
- `BatchRunner._finalize`: before the verdict block, compute `cook_heatup_sec`: telemetry key `cook_heatup_sec` wins; else fabricate `BASE_HEATUP_SEC * (1 + HEATUP_INCREASE_PER_BATCH * batches_since_cip)` (read meta via the monitor; 0 when no monitor); store on the batch doc (`cook_heatup_sec`) + event `heatup_captured`; then `self.equipment.record_batch_completed(batch_id, heatup)` when a monitor is attached.
- Fabrication uses the PRE-increment counter (batch N after CIP has heat-up base×(1+0.15×N) with N counted BEFORE this batch completes — i.e. first batch after CIP: N=0 → 120s; third: N=2 → 156s; alert threshold 162s → fires on the 4th (N=3 → 174s)? **NO** — spec story says alert on batch 3. Use POST-increment: increment first, then fabricate with the NEW counter (batch 1 → N=1 → 138s, batch 3 → N=3 → 174s ≥ 162 → alert on batch 3, Dirty at N=4). `record_batch_completed` therefore increments FIRST and returns the used counter; `_finalize` fabricates from `meta.batches_since_cip + 1` to match. Implementer: keep this arithmetic exactly — the test pins it.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_cbm.py`:
```python
import random

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.equipment import EquipmentMonitor, BASE_HEATUP_SEC

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def run_batches(n):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(41), equipment=mon)
    for _ in range(n):
        b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    return db, mon, runner


def test_heatup_rises_with_batches_since_cip():
    db, mon, runner = run_batches(2)
    batches = db.dw_batches.find({})
    heatups = sorted(b["cook_heatup_sec"] for b in batches)
    assert heatups[0] == BASE_HEATUP_SEC * 1.15   # batch 1 -> N=1
    assert heatups[1] == BASE_HEATUP_SEC * 1.30   # batch 2 -> N=2


def test_cbm_alert_fires_on_third_batch_only_once():
    db, mon, runner = run_batches(3)
    alerts = mon.open_alerts("cook-unit-01")
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "fouling_heatup"
    assert alerts[0]["batches_since_cip"] == 3
    # a 4th batch must NOT create a second unresolved alert
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    assert len(mon.open_alerts("cook-unit-01")) == 1
```

- [ ] **Step 2: Run to verify it fails**, **Step 3: implement per the Interfaces block** (complete code analog to Task 1's style; alert message English), **Step 4: full pytest + selftest green**, **Step 5: Commit** — `feat(vla): CBM fouling model — rising heat-up trend + predictive alert (PR-18, tweede Solve)`

---

### Task 3: Dirty/Allocated + CIP-gating + CIP action (PR-29)

**Files:**
- Modify: `batch-engine/vla/equipment.py` (`maybe_mark_dirty`, `perform_cip`, `is_dirty`)
- Modify: `batch-engine/vla/batches.py` (Dirty-gate in `create_batch`; Allocated feed on create)
- Modify: `batch-engine/app.py` (`POST /equipment/{equipment_id}/cip`)
- Test: `batch-engine/tests/test_cip_gate.py`

**Interfaces:**
- Produces: in `record_batch_completed`, after the alert check: when the new counter ≥ `DIRTY_AFTER_BATCHES` → set `dirty: True` on the meta + `dw_equipment_state` history row `{equipment_id, area, state: "Dirty", ts}` + event `equipment_dirty`. `is_dirty(equipment_id) -> bool`. `perform_cip(equipment_id, operator_id=None) -> dict` — resets `batches_since_cip: 0`, `dirty: False`, `last_cip_at`, clears `heatup_history`, resolves all open fouling alerts (`resolved: True, resolved_at`), history row `state: "Idle"`, event `cip_performed` `{equipment_id, operator_id}`; returns fresh meta.
- `BatchRunner.create_batch`: after the release-gate, when a monitor is attached and `self.equipment.is_dirty("cook-unit-01")` → `raise ValueError("cook-unit-01 is Dirty — CIP cleaning required before a new batch (PR-29)")`. On successful create (before auto_start), write an `Allocated` history row for the five equipment ids via `_feed_equipment_state`-style insert — implement as `self.equipment and self._feed_allocated()` writing `{equipment_id, area, state: "Allocated", ts}` per equipment + monitor publish.
- `app.py`: `POST /api/v1/equipment/{equipment_id}/cip {operator_id?}` → 404 unknown equipment (not in `EQUIPMENT_IDS`), else `perform_cip` result.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_cip_gate.py`:
```python
import random
import pytest

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.equipment import EquipmentMonitor, DIRTY_AFTER_BATCHES

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def test_dirty_gate_blocks_and_cip_unblocks():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(42), equipment=mon)
    for _ in range(DIRTY_AFTER_BATCHES):
        b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    assert mon.is_dirty("cook-unit-01") is True
    with pytest.raises(ValueError, match="Dirty"):
        runner.create_batch("chocolate-vla-1L", planned_L=5000)
    meta = mon.perform_cip("cook-unit-01", operator_id="OP-7")
    assert meta["batches_since_cip"] == 0 and meta["dirty"] is False
    assert mon.open_alerts("cook-unit-01") == []
    b5 = runner.create_batch("chocolate-vla-1L", planned_L=5000)  # unblocked
    assert b5["state"] == "IDLE"
    evs = [e["event_type"] for e in db.dw_batch_events.find({})]
    assert "cip_performed" in evs and "equipment_dirty" in evs


def test_allocated_state_written_on_create():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(43), equipment=mon)
    runner.create_batch("chocolate-vla-1L", planned_L=5000)
    states = [r["state"] for r in db.dw_equipment_state.find(
        {"equipment_id": "cook-unit-01"})]
    assert "Allocated" in states
```

- [ ] **Step 2-4:** fail → implement → full pytest + selftest green. **Step 5: Commit** — `feat(vla): Dirty/Allocated states + CIP gate and CIP action (PR-29)`

---

### Task 4: Acknowledge-flows (PR-19-part + PR-30 basis)

**Files:**
- Modify: `batch-engine/vla/batches.py` (`_alarm` gains `alarm_id`; `ack_alarm`; `ack_verdict`)
- Modify: `batch-engine/app.py` (`POST /alarms/{alarm_id}/ack`, `POST /batches/{batch_id}/ack-verdict`)
- Test: `batch-engine/tests/test_ack.py`

**Interfaces:**
- Produces: alarm rows gain `alarm_id: f"A-{uuid.uuid4().hex[:8].upper()}"`. `BatchRunner.ack_alarm(alarm_id, operator_id) -> dict` — 404-style ValueError unknown; sets `acknowledged: True, ack_by, ack_at` + event `alarm_acknowledged`. `BatchRunner.ack_verdict(batch_id, operator_id) -> dict` — batch must exist and be COMPLETE with a verdict (else ValueError); sets `verdict_ack: {operator_id, ts}` on the batch + event `verdict_acknowledged`; idempotent (second call returns existing ack unchanged). Endpoints map ValueError → 404 when "unknown", else 409.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_ack.py`:
```python
import random
import pytest

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner

TELEM_BAD = {"fault": "cook_undertemp", "magnitude": 0.6,
             "hold_elapsed_sec": 300.0, "packs_total": 4900, "reject_count": 100}


def test_alarm_ack_flow():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(44))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_BAD)
    alarm = db.dw_alarms.find({"batch_id": b["batch_id"]})[0]
    assert alarm["alarm_id"].startswith("A-")
    acked = runner.ack_alarm(alarm["alarm_id"], operator_id="OP-7")
    assert acked["acknowledged"] is True and acked["ack_by"] == "OP-7"
    with pytest.raises(ValueError, match="unknown"):
        runner.ack_alarm("A-DOESNOTEXIST", operator_id="OP-7")


def test_verdict_ack_idempotent_and_gated():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(45))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    with pytest.raises(ValueError):
        runner.ack_verdict(b["batch_id"], operator_id="OP-7")  # not COMPLETE yet
    runner.start_batch(b["batch_id"], telemetry=TELEM_BAD)
    a1 = runner.ack_verdict(b["batch_id"], operator_id="OP-7")
    a2 = runner.ack_verdict(b["batch_id"], operator_id="OP-8")
    assert a1["verdict_ack"]["operator_id"] == "OP-7"
    assert a2["verdict_ack"]["operator_id"] == "OP-7"  # idempotent
```

- [ ] **Step 2-4:** fail → implement → green. **Step 5: Commit** — `feat(vla): alarm + verdict acknowledge flows (PR-19-part, EBR basis)`

---

### Task 5: OEE + equipment-health endpoints (PR-21, PR-32)

**Files:**
- Modify: `batch-engine/vla/equipment.py` (`oee`, `health`)
- Modify: `batch-engine/app.py` (`GET /oee`, `GET /equipment/health`)
- Test: `batch-engine/tests/test_oee_health.py`

**Interfaces:**
- Produces: `EquipmentMonitor.oee() -> list[dict]` — per equipment `{equipment_id, availability, performance, quality, oee}` per the Global-Constraints formula (all rounded 4; state-history durations computed like `running_hours` but per state class; Down/Error/Dirty count against availability; Idle/Allocated are neutral — excluded from the denominator). `health() -> list[dict]` — snapshot() extended per equipment with `heatup_trend` (the meta's `heatup_history` list) and `open_cbm_alerts` (unresolved alerts for that equipment).
- Endpoints: `GET /api/v1/oee`, `GET /api/v1/equipment/health` (503-guard like the rest).

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_oee_health.py`:
```python
import random

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.equipment import EquipmentMonitor, BASE_HEATUP_SEC

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def setup(n):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(46), equipment=mon)
    for _ in range(n):
        b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    return db, mon, runner


def test_oee_shape_and_cook_performance_drop():
    db, mon, runner = setup(2)
    rows = {r["equipment_id"]: r for r in mon.oee()}
    cook = rows["cook-unit-01"]
    assert set(cook) == {"equipment_id", "availability", "performance",
                         "quality", "oee"}
    # avg heat-up after 2 batches = base*(1.15+1.30)/2 -> performance < 1
    expected_perf = round(BASE_HEATUP_SEC / (BASE_HEATUP_SEC * (1.15 + 1.30) / 2), 4)
    assert cook["performance"] == expected_perf
    assert rows["filler-01"]["performance"] == 1.0
    assert 0.0 <= cook["oee"] <= 1.0


def test_health_includes_trend_and_alerts():
    db, mon, runner = setup(3)  # 3rd batch fires the fouling alert
    h = {r["equipment_id"]: r for r in mon.health()}
    cook = h["cook-unit-01"]
    assert len(cook["heatup_trend"]) == 3
    assert len(cook["open_cbm_alerts"]) == 1
    assert h["filler-01"]["open_cbm_alerts"] == []
```

- [ ] **Step 2-4:** fail → implement → green. Quality with all-APPROVED batches = 1.0; verify manually one HOLD run drops it (no extra test needed — formula is pinned). **Step 5: Commit** — `feat(vla): OEE-light per equipment + equipment-health endpoint (PR-21, PR-32)`

---

### Task 6: EBR — batch report grows to Electronic Batch Record (PR-30)

**Files:**
- Modify: `batch-engine/vla/batches.py` (`get_batch` bundle += `events`, `production_bookings`, `order`)
- Modify: `batch-engine/vla/report.py` (`assemble_report` += order/production/events/verdict_ack; PDF sections; title)
- Test: `batch-engine/tests/test_ebr.py`

**Interfaces:**
- Produces: `get_batch` bundle gains `"events": db.dw_batch_events.find({"batch_id": batch_id})`, `"production_bookings": db.dw_production.find({"batch_id": batch_id})`, `"order": db.dw_orders.find_one({"order_id": batch["order_id"]}) if batch.get("order_id") else None`. `assemble_report` gains `"report_type": "Electronic Batch Record (BIRT-style)"` (replaces the old string), `"order"` `{order_id, target_qty_L, due_date, status}` or None, `"production"` list `{packs, source, operator_id, ts}`, `"events"` list `{event_type, ts}` (payload omitted in the report — keep it lean), `"verdict_ack"` from the batch (or None). Existing keys unchanged (selftest check 5 asserts header/doses/cook/quality/packs/verdict — must keep passing). PDF: title line becomes "Electronic Batch Record"; new sections "Order context", "Production bookings", "Events (N)" (event_type + ts table, capped at the most recent 30), "Verdict acknowledgment" — all following the existing table/paragraph patterns, no internal PR-codes in headings.

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_ebr.py`:
```python
import random

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.orders import OrderManager
from vla.report import render_json, render_pdf

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20,
            "dose_actuals": {"milk": 5000.0, "sugar": 500.0,
                             "starch": 250.0, "cocoa": 100.0}}


def test_ebr_contains_order_production_events_and_ack():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    orders = OrderManager(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(47), orders=orders)
    o = orders.create_order("chocolate-vla-1L", target_qty_L=5000)
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000,
                            order_id=o["order_id"])
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    runner.ack_verdict(b["batch_id"], operator_id="OP-7")
    rep = render_json(runner.get_batch(b["batch_id"]))
    assert rep["report_type"].startswith("Electronic Batch Record")
    assert rep["order"]["order_id"] == o["order_id"]
    assert rep["production"][0]["source"] == "filler_counter"
    assert any(e["event_type"] == "batch_complete" for e in rep["events"])
    assert rep["verdict_ack"]["operator_id"] == "OP-7"
    assert rep["verdict"] == "APPROVED"          # existing keys intact
    assert len(rep["doses"]) == 4
    pdf = render_pdf(runner.get_batch(b["batch_id"]))
    assert pdf[:4] == b"%PDF"
```

- [ ] **Step 2-4:** fail → implement → full pytest + selftest green (check 5's assertions are all preserved — `report_type` is not asserted there; verify). **Step 5: Commit** — `feat(vla): batch report grows to Electronic Batch Record — order, production, events, ack (PR-30)`

---

### Task 7: Period management report + equipment maintenance report (PR-22, PR-33)

**Files:**
- Create: `batch-engine/vla/period_reports.py`
- Modify: `batch-engine/app.py` (`GET /report/period`, `GET /report/equipment/{equipment_id}`)
- Test: `batch-engine/tests/test_period_reports.py`

**Interfaces:**
- Produces: `assemble_period_report(db, days: int) -> dict` — over batches with `completed_at` within the last `days` days: `{"report_type": "Management Report", "window_days", "batches_total", "batches_by_verdict" {APPROVED, HOLD, REJECTED, PENDING}, "yield_pct" (Σ packs_total / Σ planned_L × 100, 0.0 when no batches), "hold_reject_ratio" ((HOLD+REJECTED)/total, 0.0 when none), "downtime_events" (count of dw_equipment_state rows with state Down|Error|Dirty in window), "cbm_alerts" (list from dw_cbm_alerts in window), "generated_at"}`. `assemble_equipment_report(db, equipment_id, days) -> dict` — `{"report_type": "Maintenance Report", "equipment_id", "window_days", "running_hours" (EquipmentMonitor calc), "state_history" (rows in window, cap 100), "cbm_alerts" (for equipment in window), "cip_events" (cip_performed events for equipment in window), "generated_at"}` — raises ValueError on unknown equipment. `render_period_pdf(rep) -> bytes` and `render_equipment_pdf(rep) -> bytes` via reportlab (same style patterns as report.py; import the style helpers or replicate minimally). Endpoints: `GET /api/v1/report/period?days=7&format=json|pdf`, `GET /api/v1/report/equipment/{equipment_id}?days=30&format=json|pdf` (404 unknown equipment).

- [ ] **Step 1: Write the failing test**

`batch-engine/tests/test_period_reports.py`:
```python
import random
import pytest

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.equipment import EquipmentMonitor
from vla.period_reports import (assemble_period_report,
                                assemble_equipment_report,
                                render_period_pdf, render_equipment_pdf)

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}
TELEM_BAD = {"fault": "cook_undertemp", "magnitude": 0.6,
             "hold_elapsed_sec": 300.0, "packs_total": 4900, "reject_count": 100}


def test_period_and_equipment_reports():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(48), equipment=mon)
    for telem in (TELEM_OK, TELEM_OK, TELEM_BAD):
        b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=telem)
    rep = assemble_period_report(db, days=7)
    assert rep["batches_total"] == 3
    assert rep["batches_by_verdict"]["REJECTED"] + \
           rep["batches_by_verdict"]["HOLD"] == 1
    assert rep["hold_reject_ratio"] == round(1 / 3, 4)
    assert rep["yield_pct"] > 0
    assert render_period_pdf(rep)[:4] == b"%PDF"

    er = assemble_equipment_report(db, "cook-unit-01", days=30)
    assert er["equipment_id"] == "cook-unit-01"
    assert er["running_hours"] >= 0.0
    assert render_equipment_pdf(er)[:4] == b"%PDF"
    with pytest.raises(ValueError, match="unknown"):
        assemble_equipment_report(db, "toaster-9000", days=30)
```

- [ ] **Step 2-4:** fail → implement → green. **Step 5: Commit** — `feat(vla): period management report + equipment maintenance report (PR-22, PR-33)`

---

### Task 8: Dashboard Equipment tab + selftest 12/13 + docs + push (closes fase 3)

**Files:**
- Modify: `dashboard/index.html` (Equipment tab)
- Modify: `batch-engine/selftest.py` (checks 12 + 13)
- Modify: `scenarios/vla-batch/README.md` (fase 3 endpoints + paragraph)
- Modify (datalayer repo): PRD status line + 09-Build roadmap-rij Fase 3

**Steps:**

- [ ] **Step 1: Equipment tab.** Nav button `Equipment` + `section#tab-equipment` (extend the hardcoded tab array in the switcher — it lists sales/batches/orders/operator/admin). Content: equipment-health table from `GET /equipment/health` (per equipment: state badge — Dirty `#bf3d00`, Running `#1a7f37`, Allocated `#8250df`, else `#57606a` —, running_hours, batches since CIP, heat-up trend as inline text `120 → 138 → 156 s`, open alert count + message, and a CIP button per equipment posting `/equipment/{id}/cip` with the operator id from `#opId`); an OEE table from `GET /oee` (availability/performance/quality/oee as percentages, OEE < 75% amber). Auto-refresh with the existing polling loop. Alarm-ack: in the batch-detail view, alarms table gains an "Ack" button when `!acknowledged` posting `/alarms/{alarm_id}/ack`. English copy; comments may reference PR-numbers, visible text NOT.

- [ ] **Step 2: selftest checks 12 + 13.**
```python
# --- 12. CBM + CIP-gate e2e (fase 3): alert op batch 3, Dirty op 4, CIP herstelt ---
try:
    from vla.equipment import EquipmentMonitor, DIRTY_AFTER_BATCHES

    db12 = get_db()
    seed_recipes(db12)
    mon12 = EquipmentMonitor(db12, bus=None)
    runner12 = BatchRunner(db12, bus=None, rng=random.Random(50), equipment=mon12)
    for _ in range(DIRTY_AFTER_BATCHES):
        b12 = runner12.create_batch("chocolate-vla-1L", planned_L=5000)
        runner12.start_batch(b12["batch_id"], telemetry={
            "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20})
    alert_ok = len(mon12.open_alerts("cook-unit-01")) == 1
    dirty_ok = mon12.is_dirty("cook-unit-01")
    try:
        runner12.create_batch("chocolate-vla-1L", planned_L=5000)
        gate_ok = False
    except ValueError:
        gate_ok = True
    mon12.perform_cip("cook-unit-01", operator_id="OP-7")
    after = runner12.create_batch("chocolate-vla-1L", planned_L=5000)
    check("12. CBM alert + Dirty gate + CIP recovery",
          alert_ok and dirty_ok and gate_ok and after["state"] == "IDLE",
          f"alert={alert_ok} dirty={dirty_ok} gate={gate_ok}")
except Exception as e:
    import traceback
    check("12. CBM + CIP gate", False, f"exception: {e}\n{traceback.format_exc()}")


# --- 13. OEE + EBR + periode-rapport (fase 3) ---
try:
    from vla.period_reports import assemble_period_report, render_period_pdf

    # start batch 5 FIRST: CIP cleared the heat-up history, so OEE performance
    # is only < 1.0 again once this batch's heat-up (base*1.15) is recorded
    runner12.start_batch(after["batch_id"], telemetry={
        "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
        "packs_total": 4980, "reject_count": 20})
    oee_rows = {r["equipment_id"]: r for r in mon12.oee()}
    cook_perf_ok = oee_rows["cook-unit-01"]["performance"] < 1.0
    runner12.ack_verdict(after["batch_id"], operator_id="OP-7")
    ebr = render_json(runner12.get_batch(after["batch_id"]))
    ebr_ok = (ebr["report_type"].startswith("Electronic Batch Record")
              and ebr["verdict_ack"]["operator_id"] == "OP-7")
    prep = assemble_period_report(db12, days=7)
    pdf_ok = render_period_pdf(prep)[:4] == b"%PDF"
    check("13. OEE performance-drop + EBR + periode-rapport",
          cook_perf_ok and ebr_ok and prep["batches_total"] == 5 and pdf_ok,
          f"perf={oee_rows['cook-unit-01']['performance']} ebr={ebr_ok} "
          f"batches={prep['batches_total']}")
except Exception as e:
    import traceback
    check("13. OEE + EBR + periode-rapport", False,
          f"exception: {e}\n{traceback.format_exc()}")
```
Run `python selftest.py` → 13 checks ALL PASS + full `python -m pytest tests -q`.

- [ ] **Step 3: README** — new endpoints (`/equipment`, `/equipment/health`, `/equipment/{id}/cip`, `/oee`, `/alarms/{id}/ack`, `/batches/{id}/ack-verdict`, `/report/period`, `/report/equipment/{id}`) + "Fase 3" paragraph (CBM fouling model as MES-side substitution, alert-before-Dirty story, OEE formula one-liner, EBR, periode/maintenance-rapporten).

- [ ] **Step 4: Datalayer docs** — PRD status line append `PR-17/18/19-deels/21/22/29/30/32/33 gebouwd (fase 3, <datum>)`; 09-Build §4 rij Fase 3 append `— uitgevoerd <datum>`. Docx regen (scratchpad `md_to_docx_any.py`, PYTHONIOENCODING=utf-8). Anonymization grep (same pattern as fase 2 Task 6) over both changed .md files → 0 hits, paste in report.

- [ ] **Step 5: Commit + push both repos** (idp-os `feat(vla): fase 3 integration — Equipment tab, selftest 12+13, docs (CBM/OEE/EBR af)`; datalayer `docs(build): bouwstatus fase 3 — maintenance/KPI-cluster gebouwd`). Fetch/rebase first.

---

## Verification summary (end state)

1. Full pytest green (37 + ~10 new); `python selftest.py` → 13 checks ALL PASS offline.
2. Demo-verhaal compleet: batch 3 → CBM-alert "plan CIP", batch 5 geweigerd op Dirty, CIP herstelt — predictief naast reactief (tweede Solve).
3. OEE toont de degradatie als performance-daling; equipment-health-view + Equipment-tab live.
4. Batch-rapport = EBR (order, lots, production, events, verdict-ack); periode- en maintenance-rapporten als json+pdf.
5. Beide repo's gepusht; PRD-status bijgewerkt. Rest: PR-31 blijft uit scope; VPS runtime-verify (E4) is het volgende echte gate.
