import random

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.equipment import EquipmentMonitor, BASE_HEATUP_SEC

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def run_batches(n):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(41), equipment=mon)
    for _ in range(n):
        b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    return db, mon, runner


def test_heatup_rises_with_batches_since_cip():
    db, mon, runner = run_batches(2)
    batches = db.dw_batches.find({})
    heatups = sorted(b["cook_heatup_sec"] for b in batches)
    assert heatups[0] == BASE_HEATUP_SEC * 1.15   # batch 1 -> N=1
    assert heatups[1] == BASE_HEATUP_SEC * 1.30   # batch 2 -> N=2


def test_cbm_alert_fires_on_third_batch_only_once():
    db, mon, runner = run_batches(3)
    alerts = mon.open_alerts("cook-unit-01")
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "fouling_heatup"
    assert alerts[0]["batches_since_cip"] == 3
    # a 4th batch must NOT create a second unresolved alert
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    assert len(mon.open_alerts("cook-unit-01")) == 1
