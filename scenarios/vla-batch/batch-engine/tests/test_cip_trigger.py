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


def test_resolve_defaults_to_rf_and_accepts_run_h_within_haccp():
    assert E.resolve_cip_trigger(None, None) == ("rf", None)
    assert E.resolve_cip_trigger("RF", "3") == ("rf", None)
    assert E.resolve_cip_trigger("run_h", "2.5", haccp_h=4.0) == ("run_h", 2.5)
    assert E.resolve_cip_trigger("run_h", "4", haccp_h=4.0) == ("run_h", 4.0)   # on the limit


@pytest.mark.parametrize("trigger,interval,haccp", [
    ("run_h", None, 4.0), ("run_h", "", 4.0), ("run_h", "x", 4.0), ("run_h", "0", 4.0),
    ("run_h", "-1", 4.0), ("runh", "2", 4.0),
    ("run_h", "2.5", None),     # no HACCP limit known: no interval (ADR-030)
    ("run_h", "4.01", 4.0),     # never beyond the HACCP limit
])
def test_invalid_or_unsafe_settings_fall_back_to_rf_and_say_so(caplog, trigger, interval, haccp):
    with caplog.at_level(logging.ERROR, logger="vla.equipment"):
        assert E.resolve_cip_trigger(trigger, interval, haccp_h=haccp) == ("rf", None)
    assert "using rf" in caplog.text


def test_haccp_limit_is_the_strictest_line_and_tolerates_junk():
    assert E.haccp_limit_h({"food_safety": {"haccp_max_run_h": {"L1": 4.0, "L2": 3.5}}}) == 3.5
    assert E.haccp_limit_h({"food_safety": {"haccp_max_run_h": 2}}) == 2.0
    assert E.haccp_limit_h({"food_safety": {"haccp_max_run_h": {"L1": "x", "L2": 0}}}) is None
    assert E.haccp_limit_h({}) is None and E.haccp_limit_h(None) is None


def test_the_real_factory_model_has_a_haccp_limit(monkeypatch):
    model = E.__file__.rsplit("batch-engine", 1)[0] + "factory-model/isa95-vla.json"
    monkeypatch.setenv("FACTORY_MODEL", model)
    assert E._model_haccp_limit_h() == 4.0


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


# ── per line (CIP_TRIGGER_LINES, 24-11-2026) ────────────────────────────

COOK_L2 = E.M.LINES["L2"].eq("cook")


class TwoLineBus(FakeBus):
    def __init__(self, line):
        super().__init__()
        self.topic = topic(line, "Receiving", "pasteurizer", "run_h_since_cip")

    def set_run_h(self, hours):
        self.tags[self.topic] = json.dumps({"value": hours, "ts": "x"})


@pytest.fixture
def two_lines(monkeypatch):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    b1, b2 = TwoLineBus("L1"), TwoLineBus("L2")
    m = EquipmentMonitor(db, bus=None, buses={"L1": b1, "L2": b2})
    monkeypatch.setattr(E, "CIP_TRIGGER", "run_h")
    monkeypatch.setattr(E, "CIP_RUN_H_INTERVAL", 3.0)
    monkeypatch.setattr(E, "RUN_H_UNITS", E.resolve_trigger_lines("L1"))
    return m, b1, b2


def test_trigger_lines_default_to_every_line_and_reject_unknown_codes(caplog):
    assert E.resolve_trigger_lines(None) == E.resolve_trigger_lines("") == frozenset(E.COOK_UNITS)
    assert E.resolve_trigger_lines("L1") == {COOK}
    with caplog.at_level(logging.ERROR):
        assert E.resolve_trigger_lines("L1, L3") == {COOK}
    assert "L3" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        assert E.resolve_trigger_lines("l1") == frozenset()     # case matters: say so, do not guess
    assert "selects no line" in caplog.text


def test_only_the_selected_line_cleans_on_run_hours_the_other_stays_on_rf(two_lines):
    m, b1, b2 = two_lines
    b1.set_run_h(2.9)
    b2.set_run_h(2.9)
    m.set_fouling(COOK, E.CIP_RF_LIMIT * 3)          # L1 ignores Rf now
    m.set_fouling(COOK_L2, E.CIP_RF_LIMIT * 3)       # L2 still cleans on Rf
    s1, s2 = m.cip_status(COOK), m.cip_status(COOK_L2)
    assert s1["trigger"] == "run_h" and s1["dirty"] is False and s1["run_h_limit"] == 3.0
    assert s2["trigger"] == "rf" and s2["dirty"] is True and s2["reason"] == "vervuiling"
    assert s2["run_h_since_cip"] is None and s2["run_h_limit"] is None
    b1.set_run_h(3.0)
    assert m.cip_status(COOK)["dirty"] is True


def test_a_cip_on_an_rf_line_sets_no_run_hour_mark(two_lines):
    m, b1, b2 = two_lines
    b2.set_run_h(1.6)                                 # a reading exists, but L2 is an rf line
    m.perform_cip(COOK_L2, operator_id="OP", reason="vervuiling")
    assert m.ensure_meta(COOK_L2).get("run_h_at_cip") is None
    b1.set_run_h(3.1)                                 # the run_h line still gets its mark
    m.perform_cip(COOK, operator_id="OP", reason="draaiuren")
    assert m.ensure_meta(COOK)["run_h_at_cip"] == 3.1


# ── safety net cuts the interval (25-11-2026) ───────────────────────────

def _run_to_dirty(mon, monkeypatch, batches=4):
    monkeypatch.setattr(E, "DIRTY_AFTER_BATCHES", batches)
    for i in range(batches):
        mon.record_batch_completed(f"B{i}", 100.0, equipment_id=COOK)
    ev = mon.db.dw_batch_events.find_one({"event_type": "equipment_dirty"})
    return ev["payload"]


def test_a_safety_net_that_fires_before_the_interval_says_so_with_the_batches_needed(
        mon, run_h_mode, monkeypatch, caplog):
    mon.fake_bus.set_run_h(0.6)                       # 4 batches, 0.6 of 1.5 run h
    with caplog.at_level(logging.ERROR, logger="vla.equipment"):
        p = _run_to_dirty(mon, monkeypatch)
    assert p["interval_cut"] is True and p["run_h_since_cip"] == 0.6 and p["run_h_limit"] == 1.5
    assert p["batches_needed"] == 10                  # ceil(4 * 1.5 / 0.6)
    assert "cut the run-hour interval" in caplog.text


def test_a_safety_net_after_the_interval_is_not_a_cut(mon, run_h_mode, monkeypatch, caplog):
    mon.fake_bus.set_run_h(1.6)
    with caplog.at_level(logging.ERROR, logger="vla.equipment"):
        p = _run_to_dirty(mon, monkeypatch)
    assert "interval_cut" not in p and "cut the run-hour" not in caplog.text


def test_an_rf_line_never_reports_a_cut(mon, monkeypatch, caplog):
    mon.fake_bus.set_run_h(0.6)
    with caplog.at_level(logging.ERROR, logger="vla.equipment"):
        p = _run_to_dirty(mon, monkeypatch)
    assert "interval_cut" not in p and "cut the run-hour" not in caplog.text
