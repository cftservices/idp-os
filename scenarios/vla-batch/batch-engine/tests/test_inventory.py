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
