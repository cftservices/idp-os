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
