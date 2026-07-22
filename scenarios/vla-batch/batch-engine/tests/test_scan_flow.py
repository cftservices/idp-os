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
