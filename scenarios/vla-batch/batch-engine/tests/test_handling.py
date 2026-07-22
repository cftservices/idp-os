import random
import pytest

from vla import model as M
from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.handling import HandlingUnitManager
from vla.scan import ScanRejected

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20,
            "dose_actuals": {"milk": 5000.0, "sugar": 500.0,
                             "starch": 250.0, "cocoa": 100.0}}


def approved_batch():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(30))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    res = runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    assert res["verdict"] == "APPROVED"
    return db, res["batch_id"]


def test_full_hu_flow_wrapped_putaway_shipped():
    db, bid = approved_batch()
    hum = HandlingUnitManager(db)
    hu = hum.create_hu(bid, packs_count=2400, operator_id="OP-7")
    assert hu["status"] == "wrapped" and hu["hu_id"].startswith("80")
    assert len(hu["hu_id"]) == 18
    hu = hum.putaway(hu["hu_id"], operator_id="OP-7")
    assert hu["status"] == "awaiting_shipment" and hu["location"] == "koelmagazijn"
    hu = hum.ship(hu["hu_id"], operator_id="OP-7")
    assert hu["status"] == "shipped" and hu["location"] == "expeditie"
    diff = [e for e in db.dw_batch_events.find({"event_type": "hu_packs_difference"})]
    assert len(diff) == 1 and diff[0]["payload"]["difference"] == 4980 - 2400


def test_approved_gate_blocks_hold_rejected():
    db, bid = approved_batch()
    db.dw_batches.update_one({"batch_id": bid}, {"$set": {"verdict": "REJECTED"}})
    hum = HandlingUnitManager(db)
    with pytest.raises(ScanRejected) as ei:
        hum.create_hu(bid, packs_count=100, operator_id="OP-7")
    assert ei.value.reason == "not_approved"


def test_sum_packs_may_not_exceed_production():
    db, bid = approved_batch()
    hum = HandlingUnitManager(db)
    hum.create_hu(bid, packs_count=3000)
    with pytest.raises(ScanRejected) as ei:
        hum.create_hu(bid, packs_count=2000)  # 5000 > 4980
    assert ei.value.reason == "exceeds_production"


def test_lifecycle_order_enforced():
    db, bid = approved_batch()
    hum = HandlingUnitManager(db)
    hu = hum.create_hu(bid, packs_count=100)
    with pytest.raises(ScanRejected) as ei:
        hum.ship(hu["hu_id"])  # wrapped -> ship skips putaway
    assert ei.value.reason == "wrong_status"
