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


def test_scan_endpoint_rejection_shape():
    """Verify scan endpoint rejection produces single-nested {message, reason} shape."""
    from fastapi.testclient import TestClient
    import app as appmod
    from vla.bus import VlaBus
    from vla.opcua_control import OpcuaControl

    # Manually initialize STATE for this test
    db = get_db(mongo_url=None)
    seed_recipes(db)
    bus = VlaBus(host="monstermq", port=1883)
    # Don't actually connect, just create the object
    control = OpcuaControl()
    orders = OrderManager(db, bus=None)
    from vla.batches import BatchRunner
    runner = BatchRunner(db, bus=None, orders=orders)
    from vla.scan import ScanFlow
    scan_flow = ScanFlow(db, runner, orders)

    appmod.STATE.update({"db": db, "bus": bus, "control": control, "orders": orders,
                         "runner": runner, "scan": scan_flow})

    client = TestClient(appmod.app)
    r = client.post("/api/v1/scan/order", json={"code": "PO-DOES-NOT-EXIST",
                                                 "operator_id": "OP-7"})
    assert r.status_code == 404
    body = r.json()["detail"]
    assert body["reason"] == "unknown"
    assert "scan rejected" in body["message"]


# --- the shop-floor screen reads flat fields: batch_id, phase, materials ---

def _finish(runner, batch_id, peak=88.0):
    runner.start_batch(batch_id, telemetry={
        "peak_cook_temp_C": peak, "hold_elapsed_sec": 300.0,
        "packs_total": 4980, "reject_count": 20,
        "dose_actuals": {"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "cocoa": 100.0}})


def test_order_scan_returns_open_batch_with_materials():
    db, runner, flow, o, b = setup()
    flow.weigh(b["batch_id"], "cocoa", qty_kg=60.0, lot_no="L-1", operator_id="OP-7")
    r = flow.scan_order(o["order_id"], operator_id="OP-7")
    assert r["batch_id"] == b["batch_id"] and r["order_id"] == o["order_id"]
    assert r["phase"] == "dosing"
    cocoa = next(m for m in r["materials"] if m["material_id"] == "cocoa")
    assert cocoa["qty_target"] == 100.0 and cocoa["qty_staged"] == 60.0
    assert cocoa["remaining"] == 40.0


def test_order_without_batch_has_no_batch_id():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    orders = OrderManager(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(10), orders=orders)
    flow = ScanFlow(db, runner, orders)
    o = orders.create_order("chocolate-vla-1L", target_qty_L=5000)
    r = flow.scan_order(o["order_id"], operator_id="OP-7")
    assert r["ok"] is True and r["batch_id"] is None and r["phase"] is None
    assert r["materials"] == []


def test_complete_batch_opens_logistics_not_dosing():
    db, runner, flow, o, b = setup()
    _finish(runner, b["batch_id"])
    r = flow.scan_order(b["batch_id"], operator_id="OP-7")
    assert r["batch_id"] == b["batch_id"] and r["phase"] == "logistics"
    assert r["materials"] == []
    # scanning the order picks the same finished batch while the order is open
    assert flow.scan_order(o["order_id"], operator_id="OP-7")["phase"] == "logistics"


def test_closed_order_is_refused_but_its_batch_still_scans():
    db, runner, flow, o, b = setup()
    _finish(runner, b["batch_id"])
    flow.orders.close_order(o["order_id"])
    with pytest.raises(ScanRejected) as ei:
        flow.scan_order(o["order_id"], operator_id="OP-7")
    assert ei.value.reason == "not_active"
    assert flow.scan_order(b["batch_id"], operator_id="OP-7")["phase"] == "logistics"


# --- PR-34 (0): the gate refuses a batch that cannot run while the line is busy ---

def _second_batch(runner, orders):
    o2 = orders.create_order("chocolate-vla-1L", target_qty_L=5000)
    b2 = runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o2["order_id"])
    return o2, b2


def test_gate_refuses_a_waiting_batch_while_another_runs_on_its_line():
    db, runner, flow, o, b = setup()
    db.dw_batches.update_one({"batch_id": b["batch_id"]}, {"$set": {"state": "COOKING"}})
    o2, b2 = _second_batch(runner, flow.orders)
    with pytest.raises(ScanRejected) as ei:
        flow.scan_order(o2["order_id"], operator_id="OP-7")
    assert ei.value.reason == "line_busy"
    assert b["batch_id"] in str(ei.value)          # the operator sees which batch runs
    with pytest.raises(ScanRejected):
        flow.scan_order(b2["batch_id"], operator_id="OP-7")


def test_gate_opens_the_running_batch_itself():
    db, runner, flow, o, b = setup()
    db.dw_batches.update_one({"batch_id": b["batch_id"]}, {"$set": {"state": "DOSING"}})
    r = flow.scan_order(o["order_id"], operator_id="OP-7")
    assert r["batch_id"] == b["batch_id"] and r["phase"] == "dosing"


def test_a_running_batch_on_the_other_line_does_not_block():
    db, runner, flow, o, b = setup()
    db.dw_batches.insert_one({"batch_id": "B-L2-RUNNING", "line": "L2",
                              "recipe_id": "vanilla-vla-1L", "state": "COOKING",
                              "created_at": "2026-11-20T10:00:00+00:00"})
    assert flow.scan_order(o["order_id"], operator_id="OP-7")["phase"] == "dosing"


def test_a_finished_batch_still_opens_logistics_while_the_line_runs():
    db, runner, flow, o, b = setup()
    _finish(runner, b["batch_id"])
    o2, b2 = _second_batch(runner, flow.orders)
    db.dw_batches.update_one({"batch_id": b2["batch_id"]}, {"$set": {"state": "FILLING"}})
    assert flow.scan_order(b["batch_id"], operator_id="OP-7")["phase"] == "logistics"


# --- the commit refuses when stock cannot cover it, and then books nothing ---

def test_commit_refuses_a_stock_shortage_and_books_nothing():
    db, runner, flow, o, b = setup()
    db.dw_materials.update_one({"material_id": "cocoa"}, {"$set": {"stock_qty": 40.0}})
    flow.weigh(b["batch_id"], "sugar", qty_kg=500.0, lot_no="L-S", operator_id="OP-7")
    flow.weigh(b["batch_id"], "cocoa", qty_kg=100.0, lot_no="L-C", operator_id="OP-7")
    sugar_before = db.dw_materials.find_one({"material_id": "sugar"})["stock_qty"]
    with pytest.raises(ScanRejected) as ei:
        flow.scan_report(b["batch_id"], operator_id="OP-7")
    assert ei.value.reason == "insufficient_stock"
    assert "cocoa" in str(ei.value) and "40" in str(ei.value)
    # all or nothing: sugar had enough stock but is not booked either
    for m in ("sugar", "cocoa"):
        assert db.dw_doses.find_one({"batch_id": b["batch_id"], "material_id": m})["qty_actual"] is None
    assert db.dw_materials.find_one({"material_id": "sugar"})["stock_qty"] == sugar_before
    assert db.dw_materials.find_one({"material_id": "cocoa"})["stock_qty"] == 40.0


def test_commit_goes_through_once_stock_is_received():
    db, runner, flow, o, b = setup()
    db.dw_materials.update_one({"material_id": "cocoa"}, {"$set": {"stock_qty": 40.0}})
    flow.weigh(b["batch_id"], "cocoa", qty_kg=100.0, lot_no="L-C", operator_id="OP-7")
    with pytest.raises(ScanRejected):
        flow.scan_report(b["batch_id"], operator_id="OP-7")
    db.dw_materials.update_one({"material_id": "cocoa"}, {"$set": {"stock_qty": 400.0}})
    assert flow.scan_report(b["batch_id"], operator_id="OP-7")["booked_materials"] == ["cocoa"]
    assert db.dw_materials.find_one({"material_id": "cocoa"})["stock_qty"] == 300.0


# --- an aborted batch is finished: never dosing, never bookable (fout 3) ---

def _abort(db, batch_id):
    db.dw_batches.update_one({"batch_id": batch_id}, {"$set": {"state": "ABORTED"}})


def test_aborted_batch_scanned_by_its_code_is_refused():
    db, runner, flow, o, b = setup()
    _abort(db, b["batch_id"])
    with pytest.raises(ScanRejected) as ei:
        flow.scan_order(b["batch_id"], operator_id="OP-7")
    assert ei.value.reason == "aborted"


def test_order_scan_skips_an_aborted_batch():
    db, runner, flow, o, b = setup()
    _abort(db, b["batch_id"])
    r = flow.scan_order(o["order_id"], operator_id="OP-7")
    assert r["batch_id"] is None and r["phase"] is None      # order without a batch
    b2 = runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o["order_id"])
    assert flow.scan_order(o["order_id"], operator_id="OP-7")["batch_id"] == b2["batch_id"]


def test_nothing_can_be_booked_on_an_aborted_batch():
    db, runner, flow, o, b = setup()
    _abort(db, b["batch_id"])
    with pytest.raises(ValueError):
        flow.weigh(b["batch_id"], "cocoa", qty_kg=10.0, lot_no="L-1", operator_id="OP-7")
    with pytest.raises(ValueError):
        flow.book_production(b["batch_id"], 100, operator_id="OP-7")
