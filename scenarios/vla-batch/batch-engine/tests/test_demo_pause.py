"""Demo pause: a presenter gets one line; autopilot, factory self-chaining and random
breakdowns leave it alone until resume or the time is up."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from vla import demo_pause as DP
from vla.autopilot import AUTOSTART_REASSERT_S, OPERATOR, Autopilot
from vla.batches import BatchRunner
from vla.cmms import CmmsConnector
from vla.db import get_db, seed_recipes
from vla.equipment import EquipmentMonitor
from vla.orders import OrderManager

T0 = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)


class FakeControl:
    """Records set_autostart; every other control call (setpoints, StartBatch) is a no-op."""

    def __getattr__(self, name):
        return lambda *a, **k: {"connected": True, "rc": 0}

    def __init__(self, connected=True):
        self.calls = []
        self.connected = connected

    def set_autostart(self, enabled):
        self.calls.append(enabled)
        if not self.connected:
            return {"connected": False, "rc": None, "error": "unreachable"}
        return {"connected": True, "rc": 0}


def _db():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    return db


def _pilot(control=None):
    db = _db()
    eq = EquipmentMonitor(db, bus=None)
    orders = OrderManager(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(7), orders=orders, equipment=eq,
                         control=control)
    return db, Autopilot(db, runner, equipment=eq, orders=orders, gap_s=10, prune_every_s=1e9)


# ------------------------------------------------------------------ the store

def test_pause_resume_and_expiry():
    db = _db()
    st = DP.pause(db, "L1", 30, "op-01", now=T0)
    assert st["paused"] and st["by"] == "op-01" and st["minutes_left"] == 30.0
    assert DP.is_paused(db, "L1", T0 + timedelta(minutes=29))
    assert not DP.is_paused(db, "L1", T0 + timedelta(minutes=31))   # a pause always ends
    assert not DP.is_paused(db, "L2", T0)
    DP.resume(db, "L1", "op-01")
    assert not DP.is_paused(db, "L1", T0)


@pytest.mark.parametrize("line,minutes", [("L9", 30), ("L1", 0), ("L1", 181)])
def test_pause_refuses_unknown_line_and_out_of_range_minutes(line, minutes):
    with pytest.raises(ValueError):
        DP.pause(_db(), line, minutes, "op-01", now=T0)


# --------------------------------------------------------------- the autopilot

def test_paused_line_starts_nothing_and_holds_factory_autostart_off():
    control = FakeControl()
    db, pilot = _pilot(control)
    assert pilot.step(now=0) == "wait_gap"            # first step: autostart told "on"
    assert control.calls == [True]
    DP.pause(db, "L1", 30, "op-01")
    for t in (11, 20, 40):
        assert pilot.step(now=t) == "paused"
    assert db.dw_batches.find({"created_by": OPERATOR}) == []
    assert control.calls == [True, False]             # off once, not on every step
    pilot.step(now=11 + AUTOSTART_REASSERT_S + 1)     # re-sent: a factory restart turns it on
    assert control.calls == [True, False, False]
    assert pilot.status["paused_until"] is not None

    DP.resume(db, "L1", "op-01")
    assert pilot.step(now=200) == "wait_gap"
    assert control.calls[-1] is True                  # self-chaining back on
    assert pilot.step(now=211).startswith("started ")
    assert pilot.status["paused_until"] is None


def test_unreachable_factory_is_retried_once_per_interval_not_every_step():
    control = FakeControl(connected=False)
    db, pilot = _pilot(control)
    DP.pause(db, "L1", 30, "op-01")
    for t in range(0, 30, 3):
        assert pilot.step(now=t) == "paused"
    assert control.calls == [False]                   # each attempt can block ~10 s
    assert "SetAutostart" in (pilot.status["last_error"] or "")
    pilot.step(now=AUTOSTART_REASSERT_S + 1)
    assert control.calls == [False, False]
    control.connected = True                          # the factory is back
    pilot.step(now=2 * AUTOSTART_REASSERT_S + 2)
    assert control.calls == [False, False, False]
    assert pilot.status["last_error"] is None         # no stale error once it worked


def test_other_line_keeps_running_while_l1_is_paused():
    db, pilot = _pilot()
    DP.pause(db, "L2", 30, "op-01")
    assert pilot.step(now=0) == "wait_gap"
    assert pilot.step(now=11).startswith("started ")


# ------------------------------------------------------------------- the CMMS

def test_no_random_breakdown_on_a_paused_line():
    db = _db()
    mon = EquipmentMonitor(db, bus=None)
    cmms = CmmsConnector(db, mon, rng=random.Random(3), auto_breakdowns=True,
                         mean_breakdown_interval_s=1e-6)   # a breakdown on every tick
    DP.pause(db, "L1", 30, "op-01")
    cmms.tick()
    cmms._last_tick -= 10                                 # 10 s later
    out = cmms.tick()
    reported = [a for a in out if a.startswith("breakdown")]
    assert reported, "the unpaused line still gets breakdowns"
    l1 = set(__import__("vla.model", fromlist=["LINES"]).LINES["L1"].equipment_ids)
    assert not any(a.split()[1] in l1 for a in reported)
