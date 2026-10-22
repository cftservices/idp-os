import random
from datetime import datetime, timedelta, timezone

from vla import model as M
from vla import warehouse as W
from vla.autopilot import OPERATOR
from vla.batches import BatchRunner
from vla.db import get_db, seed_recipes
from vla.handling import HandlingUnitManager
from vla.warehouse import WarehouseSim

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def _setup(n_batches=1):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(3))
    ids = []
    for i in range(n_batches):
        b = runner.create_batch("chocolate-vla-1L", auto_start=True,
                                operator_id=OPERATOR)
        db.dw_batches.update_one(
            {"batch_id": b["batch_id"]},
            {"$set": {"created_by": OPERATOR, "verdict": M.APPROVED,
                      "state": M.COMPLETE, "packs_total": 1000,
                      "completed_at": (T0 + timedelta(seconds=i)).isoformat()}})
        ids.append(b["batch_id"])
    sim = WarehouseSim(db, HandlingUnitManager(db), OPERATOR, rng=random.Random(1))
    return db, sim, ids


def _run(sim, db, seconds, start=T0, tick=3, monkey=None):
    """Tick the simulation; HU timestamps follow the simulated clock."""
    t = start
    end = start + timedelta(seconds=seconds)
    while t <= end:
        monkey.setattr("vla.handling._iso", lambda t=t: t.isoformat())
        sim.step(now=t)
        t += timedelta(seconds=tick)
    return t


def _hus(db, status=None):
    return [h for h in db.dw_handling_units.find({})
            if status is None or h["status"] == status]


def test_batch_is_palletised_into_full_pallets_and_a_rest(monkeypatch):
    db, sim, (bid,) = _setup()
    _run(sim, db, 60, monkey=monkeypatch)
    hus = _hus(db)
    assert sorted(h["packs_count"] for h in hus) == [40, 480, 480]
    assert all(h["batch_id"] == bid for h in hus)


def test_pallets_are_put_away_into_distinct_slots(monkeypatch):
    db, sim, _ = _setup(n_batches=2)
    _run(sim, db, 90, monkey=monkeypatch)
    stored = _hus(db, M.HU_AWAITING)
    assert len(stored) == 6
    slots = [h["slot"] for h in stored]
    assert len(set(slots)) == 6 and all(0 <= s < W.RACK_SLOTS for s in slots)


def test_ships_per_batch_after_dwell_and_drops_shipped_later(monkeypatch):
    db, sim, (a, b) = _setup(n_batches=2)
    t = _run(sim, db, 90, monkey=monkeypatch)
    assert not _hus(db, M.HU_SHIPPED)  # still within the dwell time
    t = _run(sim, db, W.STORE_DWELL_S, start=t, monkey=monkeypatch)
    shipped = _hus(db, M.HU_SHIPPED)
    assert {h["batch_id"] for h in shipped} == {a, b}
    assert not _hus(db, M.HU_AWAITING)
    _run(sim, db, W.KEEP_SHIPPED_S + 60, start=t, monkey=monkeypatch)
    assert _hus(db) == []
    # dropped HUs must not make the batch look unpalletised again
    assert _hus(db, M.HU_WRAPPED) == []


def test_one_truck_at_a_time_oldest_batch_first(monkeypatch):
    db, sim, (a, b) = _setup(n_batches=2)
    _run(sim, db, 90 + W.STORE_DWELL_S + 120, monkey=monkeypatch)
    order = [e["payload"]["hu_id"] for e in db.dw_batch_events.find({})
             if e["event_type"] == "hu_shipped"]
    batch_of = {h["hu_id"]: h["batch_id"] for h in _hus(db)}
    assert [batch_of[h] for h in order] == [a, a, a, b, b, b]


def test_old_or_unapproved_batches_are_left_alone(monkeypatch):
    db, sim, (old, held) = _setup(n_batches=2)
    db.dw_batches.update_one({"batch_id": old}, {"$set": {
        "completed_at": (T0 - timedelta(hours=2)).isoformat()}})
    db.dw_batches.update_one({"batch_id": held}, {"$set": {"verdict": M.HOLD}})
    _run(sim, db, 60, monkey=monkeypatch)
    assert _hus(db) == []


def test_full_rack_holds_pallets_at_palletiser(monkeypatch):
    db, sim, _ = _setup()
    monkeypatch.setattr(W, "RACK_SLOTS", 1)
    _run(sim, db, 60, monkey=monkeypatch)
    assert len(_hus(db, M.HU_AWAITING)) <= 1
    assert len(_hus(db, M.HU_WRAPPED)) >= 1


def test_each_line_palletises_into_its_own_rack_zone(monkeypatch):
    """Twee palletisers: een L2-batch krijgt HU's met line L2, in de noordelijke helft van de stelling."""
    db, sim, (a, b) = _setup(n_batches=2)
    db.dw_batches.update_one({"batch_id": b}, {"$set": {"line": "L2"}})
    _run(sim, db, 90, monkey=monkeypatch)
    stored = _hus(db, M.HU_AWAITING)
    assert {h["line"] for h in stored if h["batch_id"] == a} == {"L1"}
    assert {h["line"] for h in stored if h["batch_id"] == b} == {"L2"}
    assert all(h["slot"] < W.ZONE_SLOTS for h in stored if h["line"] == "L1")
    assert all(h["slot"] >= W.ZONE_SLOTS for h in stored if h["line"] == "L2")
