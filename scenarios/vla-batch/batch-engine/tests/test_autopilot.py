import random
from datetime import datetime, timedelta, timezone

from vla.autopilot import OPERATOR, Autopilot
from vla.batches import BatchRunner
from vla.db import get_db, seed_recipes
from vla.equipment import DIRTY_AFTER_BATCHES, EquipmentMonitor
from vla.orders import OrderManager


class FakeBus:
    def __init__(self, state="COMPLETE", connected=True):
        self.state = state
        self.connected = connected

    def latest_value(self, equipment, tag):
        return self.state if (equipment, tag) == ("Batch", "state") else None


def _setup(bus=None):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    eq = EquipmentMonitor(db, bus=None)
    orders = OrderManager(db, bus=None)
    # bus=None on the runner -> INSTANT mode: a started batch completes at once
    runner = BatchRunner(db, bus=None, rng=random.Random(7), orders=orders,
                         equipment=eq)
    pilot = Autopilot(db, runner, equipment=eq, orders=orders, bus=bus,
                      gap_s=10, prune_every_s=1e9)
    return db, eq, pilot


def test_starts_batch_after_gap_and_books_on_one_daily_order():
    db, _eq, pilot = _setup()
    assert pilot.step(now=0) == "wait_gap"
    assert pilot.step(now=11).startswith("started ")
    assert pilot.step(now=12) == "wait_gap"
    assert pilot.step(now=23).startswith("started ")
    batches = db.dw_batches.find({"created_by": OPERATOR})
    assert len(batches) == 2 and all(b["state"] == "COMPLETE" for b in batches)
    assert len({b["order_id"] for b in batches}) == 1
    assert pilot.status["batches_started"] == 2


def test_runs_cip_when_dirty_then_continues():
    db, eq, pilot = _setup()
    t = 0
    for _ in range(DIRTY_AFTER_BATCHES):
        pilot.step(now=t)
        t += 11
        assert pilot.step(now=t).startswith("started ")
    assert eq.is_dirty("cook-unit-01")
    pilot.step(now=t + 1)
    assert pilot.step(now=t + 12) == "cip"
    assert not eq.is_dirty("cook-unit-01")
    assert pilot.step(now=t + 13).startswith("started ")
    assert pilot.status["cip_runs"] == 1


def test_waits_for_factory_and_bus():
    bus = FakeBus(state="COOKING")
    _db, _eq, pilot = _setup(bus=bus)
    assert pilot.step(now=0) == "wait_factory"
    bus.connected = False
    assert pilot.step(now=20) == "wait_bus"
    bus.connected, bus.state = True, "COMPLETE"
    assert pilot.step(now=21) == "wait_gap"


def test_waits_while_an_mes_batch_runs():
    db, _eq, pilot = _setup()
    now = datetime.now(timezone.utc).isoformat()
    db.dw_batches.insert_one({"batch_id": "B-X", "recipe_id": "chocolate-vla-1L",
                              "state": "FILLING", "started_at": now})
    assert pilot.step(now=100) == "wait_batch"


def test_orphaned_running_batch_does_not_block_forever():
    db, _eq, pilot = _setup()
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    db.dw_batches.insert_one({"batch_id": "B-OLD", "recipe_id": "chocolate-vla-1L",
                              "state": "COOKING", "started_at": old})
    assert pilot.step(now=0) == "wait_gap"
    assert pilot.step(now=11).startswith("started ")


def test_prune_only_drops_old_autopilot_batches():
    db, _eq, pilot = _setup()
    pilot.step(now=0)
    pilot.step(now=11)
    manual = pilot.runner.create_batch("chocolate-vla-1L", operator_id="OP-1")
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    bid = pilot.status["last_batch_id"]
    db.dw_batches.update_one({"batch_id": bid}, {"$set": {"created_at": old}})
    db.dw_batches.update_one({"batch_id": manual["batch_id"]},
                             {"$set": {"created_at": old}})
    assert db.dw_doses.count_documents({"batch_id": bid}) > 0
    assert pilot.prune() == 1
    assert db.dw_batches.find_one({"batch_id": bid}) is None
    assert db.dw_doses.count_documents({"batch_id": bid}) == 0
    assert db.dw_batches.find_one({"batch_id": manual["batch_id"]}) is not None


def test_batch_orphaned_by_restart_does_not_block(monkeypatch):
    """Na een herstart midden in een batch leeft de follow-thread niet meer;
    de autopilot mag dan niet 16 minuten op de follow-timeout wachten."""
    import threading
    from vla import autopilot as AP
    db, _eq, pilot = _setup()
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)
    monkeypatch.setattr(AP, "PROCESS_STARTED", recent + timedelta(seconds=30))
    db.dw_batches.insert_one({"batch_id": "B-ORPH", "recipe_id": "chocolate-vla-1L",
                              "state": "COOKING", "started_at": recent.isoformat()})
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, name="vla-follow-B-ORPH", daemon=True)
    t.start()
    try:
        assert pilot.step(now=0) == "wait_batch"  # still followed: active
    finally:
        stop.set()
        t.join()
    assert pilot.step(now=1) == "wait_gap"  # nobody follows it: orphaned
