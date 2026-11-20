import random
import pytest

from vla import model as M
from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.orders import OrderManager

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def setup():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    orders = OrderManager(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(9), orders=orders)
    return db, orders, runner


def test_order_lifecycle_open_running_done():
    db, orders, runner = setup()
    o = orders.create_order("chocolate-vla-1L", target_qty_L=10000)
    assert o["status"] == M.ORDER_OPEN and o["order_id"].startswith("PO-")
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000,
                            order_id=o["order_id"])
    assert b["order_id"] == o["order_id"]
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    assert orders.get_order(o["order_id"])["status"] == M.ORDER_RUNNING
    closed = orders.close_order(o["order_id"])
    assert closed["status"] == M.ORDER_DONE
    prog = orders.order_progress(o["order_id"])
    assert prog["batched_L"] == 5000 and prog["produced_packs"] == 4980


def test_rejected_batch_does_not_count_towards_the_order():
    db, orders, runner = setup()
    o = orders.create_order("chocolate-vla-1L", target_qty_L=10000)
    good = runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o["order_id"])
    runner.start_batch(good["batch_id"], telemetry=TELEM_OK)
    bad = runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o["order_id"])
    res = runner.start_batch(bad["batch_id"], telemetry={**TELEM_OK, "peak_cook_temp_C": 72.0})
    assert res["verdict"] in (M.REJECTED, M.HOLD)
    prog = orders.order_progress(o["order_id"])
    assert prog["produced_packs"] == 4980          # only the approved batch
    assert prog["blocked_packs"] == 4980           # the rejected one, reported apart
    assert prog["remaining_L"] == 10000 - 4980     # so the order is not "in full" yet
    assert prog["batched_L"] == 10000 and prog["batches_count"] == 2


def test_close_refused_without_production():
    db, orders, runner = setup()
    o = orders.create_order("chocolate-vla-1L", target_qty_L=5000)
    runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o["order_id"])
    with pytest.raises(ValueError, match="no production"):
        orders.close_order(o["order_id"])


def test_batch_on_done_order_refused_and_implicit_order():
    db, orders, runner = setup()
    o = orders.create_order("chocolate-vla-1L", target_qty_L=5000)
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o["order_id"])
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    orders.close_order(o["order_id"])
    with pytest.raises(ValueError, match="DONE"):
        runner.create_batch("chocolate-vla-1L", planned_L=5000, order_id=o["order_id"])
    # no order_id -> implicit order is created (PR-24 demo continuity)
    b2 = runner.create_batch("chocolate-vla-1L", planned_L=2500)
    assert b2["order_id"].startswith("PO-")
