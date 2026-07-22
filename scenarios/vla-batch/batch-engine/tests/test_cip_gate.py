import random
import pytest

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.equipment import EquipmentMonitor, DIRTY_AFTER_BATCHES

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def test_dirty_gate_blocks_and_cip_unblocks():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(42), equipment=mon)
    for _ in range(DIRTY_AFTER_BATCHES):
        b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    assert mon.is_dirty("cook-unit-01") is True
    with pytest.raises(ValueError, match="Dirty"):
        runner.create_batch("chocolate-vla-1L", planned_L=5000)
    meta = mon.perform_cip("cook-unit-01", operator_id="OP-7")
    assert meta["batches_since_cip"] == 0 and meta["dirty"] is False
    assert mon.open_alerts("cook-unit-01") == []
    b5 = runner.create_batch("chocolate-vla-1L", planned_L=5000)  # unblocked
    assert b5["state"] == "IDLE"
    evs = [e["event_type"] for e in db.dw_batch_events.find({})]
    assert "cip_performed" in evs and "equipment_dirty" in evs


def test_allocated_state_written_on_create():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(43), equipment=mon)
    runner.create_batch("chocolate-vla-1L", planned_L=5000)
    states = [r["state"] for r in db.dw_equipment_state.find(
        {"equipment_id": "cook-unit-01"})]
    assert "Allocated" in states
