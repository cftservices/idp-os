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
