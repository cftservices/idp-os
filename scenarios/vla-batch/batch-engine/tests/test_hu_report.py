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
