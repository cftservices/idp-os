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
