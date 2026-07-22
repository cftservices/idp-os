import random
from datetime import datetime, timedelta, timezone

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


def test_running_hours_tolerates_out_of_order_history():
    db, mon, runner = setup()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=60)
    # Insert OUT of order: Idle (t1) before Running (t0).
    db.dw_equipment_state.insert_one({
        "equipment_id": "cook-unit-01", "area": "Process",
        "state": "Idle", "ts": t1.isoformat(),
    })
    db.dw_equipment_state.insert_one({
        "equipment_id": "cook-unit-01", "area": "Process",
        "state": "Running", "ts": t0.isoformat(),
    })
    assert mon.running_hours("cook-unit-01") == round(60 / 3600, 4)
