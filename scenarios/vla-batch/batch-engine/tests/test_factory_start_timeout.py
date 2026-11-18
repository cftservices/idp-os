"""A batch the factory never ran is aborted, not filled in with simulated values.

On 2026-11-18 an engine restart started B-20261118-7E4392 on L2 while the factory was still
busy. The factory never left IDLE for it; after START_TIMEOUT_S the engine fell back to the
instant simulation and booked 4980 packs, samples and an APPROVED verdict for a batch that was
never made. In a demo about real line data that is fabricated production. An ISA-88 batch the
equipment never takes is aborted: no doses, no production, no samples, no verdict.
"""
import random

from vla import batches as B
from vla import line as L
from vla import model as M
from vla.db import get_db, seed_recipes


class Clock:
    def __init__(self):
        self.t = 0.0


class DeafFactory:
    """Connected, but never takes the StartBatch: the state stays IDLE."""

    connected = True

    def start_batch(self, recipe_id):
        pass

    def set_setpoint(self, *a, **k):
        pass

    def command(self, *a, **k):
        pass

    def publish_json(self, *a, **k):
        pass

    def take_sample(self, *a, **k):
        pass

    def latest_value(self, equipment, tag, max_age_s=None):
        if (equipment, tag) == ("Batch", "state"):
            return "IDLE"
        # stale values of the previous batch are still in the tag cache
        return {"packs_total": 4980, "reject_count": 20, "viscosity_cP": 258.0}.get(tag)


class SyncThread:
    def __init__(self, target, args=(), **_kw):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def _abort_one(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(B.time, "monotonic", lambda: clock.t)
    monkeypatch.setattr(B.time, "sleep", lambda d: setattr(clock, "t", clock.t + d))
    monkeypatch.setattr(B.threading, "Thread", SyncThread)
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = B.BatchRunner(db, bus=DeafFactory(), rng=random.Random(3))
    batch_id = runner.create_batch("chocolate-vla-1L")["batch_id"]
    runner.start_batch(batch_id)
    return db, batch_id


def test_batch_the_factory_never_ran_is_aborted(monkeypatch):
    db, batch_id = _abort_one(monkeypatch)
    b = db.dw_batches.find_one({"batch_id": batch_id})
    assert b["state"] == M.ABORTED
    assert b.get("abort_reason") == "factory_start_timeout"
    assert b.get("aborted_at")
    events = [e["event_type"] for e in db.dw_batch_events.find({"batch_id": batch_id})]
    assert "factory_start_timeout" in events and "batch_aborted" in events


def test_aborted_batch_books_nothing(monkeypatch):
    db, batch_id = _abort_one(monkeypatch)
    b = db.dw_batches.find_one({"batch_id": batch_id})
    assert b.get("verdict") is None
    assert not b.get("packs_total")
    assert b.get("completed_at") is None
    assert b.get("end_viscosity_cP") is None
    assert list(db.dw_production.find({"batch_id": batch_id})) == []
    assert list(db.dw_samples.find({"batch_id": batch_id})) == []
    assert all(d.get("qty_actual") is None for d in db.dw_doses.find({"batch_id": batch_id}))


def test_aborted_batch_is_not_the_active_batch_of_the_line(monkeypatch):
    """line._active_batch prefers a running batch over the latest one. An aborted batch
    is finished, so a newer completed batch must win over it."""
    db, batch_id = _abort_one(monkeypatch)
    aborted = db.dw_batches.find_one({"batch_id": batch_id})
    db.dw_batches.insert_one({**{k: v for k, v in aborted.items() if k != "_id"},
                              "batch_id": "B-NEWER", "state": M.COMPLETE,
                              "started_at": "2999-01-01T00:00:00+00:00"})
    assert L._active_batch(db)["batch_id"] == "B-NEWER"
