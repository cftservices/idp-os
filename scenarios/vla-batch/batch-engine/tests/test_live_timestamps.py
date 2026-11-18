"""started_at / completed_at of a live batch come from the factory, not from the bookkeeping.

On 2026-11-18 the VPS lost 50-70 % of its CPU to steal. The factory still ran a batch in
~120 s, but the engine took up to 18 s to book doses, samples and production, and stamped
completed_at only after that. The OEE then counted the bookkeeping as line run time. A real
MES takes the time from the equipment event; these tests hold the engine to that, with every
database write made deliberately slow.
"""
import random
from datetime import datetime, timedelta, timezone

import pytest

from vla import batches as B
from vla.db import get_db, seed_recipes

BASE = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
FACTORY_RUN_S = 120.0            # DOSING, COOKING, COOLING, FILLING, 30 s each
SLOW_WRITE_S = 1.5               # a starved host: every write costs this much wall time


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return BASE + timedelta(seconds=self.t)


class FactoryBus:
    """The factory over the UNS: IDLE until StartBatch, then four 30 s phases, then COMPLETE."""

    connected = True

    def __init__(self, clock, stop_early_at=None):
        self.clock, self.started, self.stop_early_at = clock, None, stop_early_at

    def start_batch(self, recipe_id):
        self.started = self.clock.t

    def set_setpoint(self, target, value):
        pass

    def command(self, *a, **k):
        pass

    def publish_json(self, *a, **k):
        pass

    def take_sample(self, *a, **k):
        pass

    def state(self):
        if self.started is None:
            return "IDLE"
        dt = self.clock.t - self.started
        if self.stop_early_at is not None and dt >= self.stop_early_at:
            return "IDLE"
        if dt >= FACTORY_RUN_S:
            return "COMPLETE"
        return ("DOSING", "COOKING", "COOLING", "FILLING")[int(dt // 30)]

    def latest_value(self, equipment, tag, max_age_s=None):
        if (equipment, tag) == ("Batch", "state"):
            return self.state()
        return {"temp_C": 88.0, "hold_elapsed_sec": 300.0, "viscosity_cP": 258.0,
                "packs_total": 4980, "reject_count": 20}.get(tag)


class SyncThread:
    def __init__(self, target, args=(), **_kw):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def starved(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(B, "_now", clock.now)
    monkeypatch.setattr(B.time, "monotonic", lambda: clock.t)
    monkeypatch.setattr(B.time, "sleep", lambda d: setattr(clock, "t", clock.t + d))
    monkeypatch.setattr(B.threading, "Thread", SyncThread)
    return clock


def _runner(clock, bus):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = B.BatchRunner(db, bus=bus, rng=random.Random(3))
    for name in ("_update", "_event"):
        real = getattr(runner, name)

        def slow(*a, _real=real, **k):
            out = _real(*a, **k)
            clock.t += SLOW_WRITE_S
            return out
        setattr(runner, name, slow)
    return db, runner


def _seconds(batch, field):
    return (datetime.fromisoformat(batch[field]) - BASE).total_seconds()


def test_live_batch_duration_is_the_factory_run_not_the_bookkeeping(starved):
    bus = FactoryBus(starved)
    db, runner = _runner(starved, bus)
    batch_id = runner.create_batch("chocolate-vla-1L")["batch_id"]
    runner.start_batch(batch_id)
    b = db.dw_batches.find_one({"batch_id": batch_id})
    assert b["state"] == "COMPLETE"
    cycle = _seconds(b, "completed_at") - _seconds(b, "started_at")
    # observed within one poll on each side of the factory run
    assert FACTORY_RUN_S - B.BatchRunner.POLL_S <= cycle <= FACTORY_RUN_S + B.BatchRunner.POLL_S
    # the bookkeeping did take long, and is still visible as such
    assert starved.t - bus.started > FACTORY_RUN_S + 10 * SLOW_WRITE_S
    assert b["commanded_at"] <= b["started_at"]


def test_completed_at_is_when_the_factory_stopped_if_it_stopped_early(starved):
    bus = FactoryBus(starved, stop_early_at=50.0)
    db, runner = _runner(starved, bus)
    batch_id = runner.create_batch("chocolate-vla-1L")["batch_id"]
    runner.start_batch(batch_id)
    b = db.dw_batches.find_one({"batch_id": batch_id})
    cycle = _seconds(b, "completed_at") - _seconds(b, "started_at")
    assert 50.0 - B.BatchRunner.POLL_S <= cycle <= 50.0 + B.BatchRunner.POLL_S


def test_instant_mode_keeps_its_own_clock():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = B.BatchRunner(db, bus=None, rng=random.Random(3))
    batch_id = runner.create_batch("chocolate-vla-1L")["batch_id"]
    runner.start_batch(batch_id)
    b = db.dw_batches.find_one({"batch_id": batch_id})
    assert b["state"] == "COMPLETE" and b["started_at"] <= b["completed_at"]
