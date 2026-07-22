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


def test_book_doses_prefers_staged_weight_over_fabrication():
    db, runner, flow, b = setup()
    # Operator stages cocoa at 60 kg via the scan-flow (scale-01) but never
    # report-scans it, so qty_actual stays None and the staged trail is the
    # only record of what was actually weighed out.
    flow.weigh(b["batch_id"], "cocoa", qty_kg=60.0, lot_no="L-9001",
               operator_id="OP-7")
    dose_before = db.dw_doses.find_one({"batch_id": b["batch_id"],
                                        "material_id": "cocoa"})
    assert dose_before["qty_actual"] is None and dose_before["qty_prepared"] == 60.0

    # Run the batch to completion with telemetry that omits cocoa from
    # dose_actuals (only milk/sugar/starch are forced) so _book_doses has to
    # decide what to do with the still-unbooked, staged cocoa line.
    runner.start_batch(b["batch_id"], telemetry={
        "dose_actuals": {"milk": 5000.0, "sugar": 500.0, "starch": 250.0},
        "peak_cook_temp_C": 88, "hold_elapsed_sec": 300, "packs_total": 4980,
    })

    dose = db.dw_doses.find_one({"batch_id": b["batch_id"], "material_id": "cocoa"})
    # Must book the staged 60 kg (what was actually weighed), never a
    # fabricated value hovering near the 100 kg target.
    assert dose["qty_actual"] == 60.0
    # cocoa target 100 +/- 1% -> 60 kg is well outside tolerance.
    assert dose["in_tolerance"] is False
    assert dose["source_equipment"] == "scale-01"
