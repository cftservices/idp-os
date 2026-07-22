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
