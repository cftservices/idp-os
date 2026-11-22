"""Configurable CIP trigger (22-11-2026): CIP_TRIGGER=rf|run_h plus CIP_RUN_H_INTERVAL.

rf (default) leaves today's behaviour alone: the measured fouling starts the CIP. run_h
cleans at a fixed number of pasteuriser run hours, so a proposed cleaning interval
(BVIP ADR-030) can be rolled out in the demo and measured. The trap is the factory: it
resets its run-hour counter only when its CIP has finished, so right after perform_cip
the bus still shows the old value, and counting it would start a CIP loop.
"""
import json
import logging

import pytest

from vla import equipment as E
from vla.db import get_db, seed_recipes
from vla.equipment import EquipmentMonitor
from vla.meters import topic

COOK = "cook-unit-01"
RUN_H_TOPIC = topic("L1", "Receiving", "pasteurizer", "run_h_since_cip")


class FakeBus:
    def __init__(self):
        self.tags = {}

    def set_run_h(self, hours):
        self.tags[RUN_H_TOPIC] = json.dumps({"value": hours, "ts": "x"})

    def latest_raw(self, t):
        return self.tags.get(t)

    def publish_json(self, *_a, **_k):
        pass


@pytest.fixture
def mon():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    bus = FakeBus()
    m = EquipmentMonitor(db, bus=None, buses={"L1": bus})
    m.fake_bus = bus
    return m


@pytest.fixture
def run_h_mode(monkeypatch):
    monkeypatch.setattr(E, "CIP_TRIGGER", "run_h")
    monkeypatch.setattr(E, "CIP_RUN_H_INTERVAL", 1.5)


def test_resolve_defaults_to_rf_and_accepts_run_h():
    assert E.resolve_cip_trigger(None, None) == ("rf", None)
    assert E.resolve_cip_trigger("RF", "3") == ("rf", None)
    assert E.resolve_cip_trigger("run_h", "2.5") == ("run_h", 2.5)


@pytest.mark.parametrize("trigger,interval", [
    ("run_h", None), ("run_h", ""), ("run_h", "x"), ("run_h", "0"), ("run_h", "-1"),
    ("runh", "2"),
])
def test_invalid_settings_fall_back_to_rf_and_say_so(caplog, trigger, interval):
    with caplog.at_level(logging.ERROR, logger="vla.equipment"):
        assert E.resolve_cip_trigger(trigger, interval) == ("rf", None)
    assert "using rf" in caplog.text


def test_rf_mode_ignores_run_hours(mon):
    mon.fake_bus.set_run_h(50.0)
    st = mon.cip_status(COOK)
    assert st["trigger"] == "rf" and st["dirty"] is False and st["run_h_since_cip"] is None
    mon.set_fouling(COOK, E.CIP_RF_LIMIT)
    st = mon.cip_status(COOK)
    assert st["dirty"] is True and st["reason"] == "vervuiling"


def test_run_h_mode_cleans_at_the_interval_and_ignores_rf(mon, run_h_mode):
    mon.set_fouling(COOK, E.CIP_RF_LIMIT * 3)        # would trigger in rf mode
    mon.fake_bus.set_run_h(1.4)
    st = mon.cip_status(COOK)
    assert st["dirty"] is False and st["run_h_since_cip"] == 1.4 and st["run_h_limit"] == 1.5
    mon.fake_bus.set_run_h(1.5)
    st = mon.cip_status(COOK)
    assert st["dirty"] is True and st["reason"] == "draaiuren"


def test_stale_counter_after_cip_does_not_start_another_cip(mon, run_h_mode):
    mon.fake_bus.set_run_h(1.6)
    assert mon.is_dirty(COOK)
    mon.perform_cip(COOK, operator_id="OP", reason="draaiuren")
    # factory CIP still running: the bus shows the old value, also a bit higher
    for v in (1.6, 1.7):
        mon.fake_bus.set_run_h(v)
        assert mon.cip_status(COOK)["run_h_since_cip"] == 0.0
        assert not mon.is_dirty(COOK)
    # factory CIP done: the counter resets, the mark clears, counting starts again
    mon.fake_bus.set_run_h(0.1)
    assert mon.cip_status(COOK)["run_h_since_cip"] == 0.1
    mon.fake_bus.set_run_h(1.6)
    st = mon.cip_status(COOK)
    assert st["run_h_since_cip"] == 1.6 and st["dirty"] is True


def test_cip_with_counter_near_zero_sets_no_mark(mon, run_h_mode):
    mon.fake_bus.set_run_h(0.0)
    mon.perform_cip(COOK, operator_id="OP", reason="productwissel")
    assert mon.ensure_meta(COOK)["run_h_at_cip"] is None
    mon.fake_bus.set_run_h(1.5)
    assert mon.is_dirty(COOK)


def test_run_h_mode_without_a_reading_keeps_the_batch_safety_net(mon, run_h_mode):
    st = mon.cip_status(COOK)
    assert st["run_h_since_cip"] is None and st["dirty"] is False
    mon.db.dw_equipment_meta.update_one({"equipment_id": COOK}, {"$set": {"dirty": True}})
    st = mon.cip_status(COOK)
    assert st["dirty"] is True and st["reason"] == "batches"


def test_run_hours_only_count_for_the_cook_unit(mon, run_h_mode):
    mon.fake_bus.set_run_h(9.0)
    assert mon.cip_status(COOK)["dirty"] is True
    assert mon.cip_status("filler-01")["dirty"] is False
