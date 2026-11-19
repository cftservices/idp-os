"""Fase 3: meters, pasteuriser and CIP. The sampler against a fake bus, the energy and
heat-exchanger KPIs against hand-computed values, and the CIP gate in the autopilot."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from vla import bvip, meters, model as M
from vla.db import get_db, seed_recipes
from vla.equipment import CIP_RF_LIMIT, EquipmentMonitor

T0 = datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc)
CP, AREA = 3.85, 14.0
CAT = bvip.load_catalogue()
CFG = bvip.Config(time_scale=15.0, gas_price_per_kwh=0.06, electricity_price_per_kwh=0.18,
                  water_price_per_m3=2.5, caustic_price_per_kg=0.55, acid_price_per_kg=0.9,
                  cip_labour_hours=0.5, labour_cost_per_hour=80.0, sales_price_per_pack=1.0,
                  co2_kg_per_kwh={"gas": 0.2, "electricity": 0.4}, product_cp=CP,
                  pasteuriser_ref_flow=11.0, pasteuriser_area=AREA)


def iso(t):
    return t.isoformat()


def sample_for_u(u: float, run_h: float, flow: float = 11.0) -> dict:
    """A pasteuriser sample whose temperatures give exactly heat transfer coefficient u."""
    cold, hold, regen = 5.0, 75.0, 69.4
    duty = flow * CP * (hold - regen)
    dtw = duty / (10.0 * 4.18)
    need = duty / (u * AREA)
    lo, hi = max(hold, regen + dtw) + 1e-3, 200.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if meters.lmtd(mid - hold, mid - dtw - regen) < need:
            lo = mid
        else:
            hi = mid
    return {"product_flow_kg_s": flow, "cold_t_in_C": cold, "regen_t_out_C": regen,
            "hold_t_C": hold, "hot_t_in_C": hi, "hot_t_out_C": hi - dtw, "dp_bar": 0.9,
            "run_h_since_cip": run_h}


def test_u_value_recovers_the_coefficient_from_temperatures():
    for u in (3.8, 3.1):
        assert meters.u_value(sample_for_u(u, 0.1), CP, AREA) == pytest.approx(u, rel=1e-4)


class FakeBus:
    def __init__(self, line="L1"):
        self.line, self.vals = line, {}

    def set(self, area, base, tag, value):
        self.vals[meters.topic(self.line, area, base, tag)] = json.dumps({"value": value})

    def latest_raw(self, topic):
        return self.vals.get(topic)


def _sampler(bus):
    db = get_db(mongo_url=None)
    eq = EquipmentMonitor(db, bus=None)
    clock = {"t": T0}
    s = meters.MeterSampler(db, {"L1": bus}, eq, {"process_constants": {
        "product_cp_kj_kg_k": CP, "pasteuriser_area_m2": AREA}}, now=lambda: clock["t"])
    return db, eq, s, clock


def test_sampler_records_counters_cip_cycles_and_fouling():
    bus = FakeBus()
    db, eq, s, clock = _sampler(bus)
    for tag, v in (("heat_kwh_total", 100.0), ("electricity_kwh_total", 50.0),
                   ("steam_kg_total", 10.0)):
        bus.set("Utilities", "energy-meter", tag, v)
    bus.set("Utilities", "water-meter", "water_L_total", 0.0)
    bus.set("Utilities", "cip-station", "cip_count", 3)
    s.sample(mono=0)
    s.sample(mono=30)                                  # < 60 s: no second reading
    assert len(db.dw_meter_readings.find({"line": "L1"})) == 1
    cyc = db.dw_cip_cycles.find({"line": "L1"})
    assert len(cyc) == 1 and cyc[0]["baseline"] is True    # first sight is not a cycle

    bus.set("Utilities", "cip-station", "cip_count", 4)
    for k, v in (("water_L", 9000), ("caustic_kg", 15), ("acid_kg", 8), ("heat_kwh", 213),
                 ("electricity_kwh", 7.5), ("duration_s", 3600), ("past_run_h", 1.5)):
        bus.set("Utilities", "cip-station", f"last_{k}", v)
    clock["t"] = T0 + timedelta(minutes=2)
    s.sample(mono=120)
    new = [c for c in db.dw_cip_cycles.find({"line": "L1"}) if not c["baseline"]]
    assert len(new) == 1 and new[0]["water_L"] == 9000 and new[0]["past_run_h"] == 1.5
    assert len(db.dw_meter_readings.find({"line": "L1"})) == 2

    # pasteuriser: clean samples first, then fouled; Rf = 1/U - 1/U_clean
    for i, (u, run_h) in enumerate(((3.8, 0.05), (3.8, 0.10), (3.1, 1.4))):
        for tag, v in sample_for_u(u, run_h).items():
            bus.set("Receiving", "pasteurizer", tag, v)
        clock["t"] = T0 + timedelta(minutes=3 + i)
        s.sample(mono=200 + i)
    s.sample(mono=210)                                 # same run_h: no duplicate sample
    assert len(db.dw_pasteur_samples.find({"line": "L1"})) == 3
    rf = eq.cip_status(M.LINES["L1"].eq("cook"))["fouling_rf"]
    assert rf == pytest.approx(1 / 3.1 - 1 / 3.8, rel=1e-3)
    assert eq.cip_status(M.LINES["L1"].eq("cook"))["dirty"] is (rf >= CIP_RF_LIMIT)

    # after a CIP (run_h back to ~0) U_clean comes from THIS cycle, not an older one
    for i, (u, run_h) in enumerate(((3.5, 0.05), (3.0, 0.9))):
        for tag, v in sample_for_u(u, run_h).items():
            bus.set("Receiving", "pasteurizer", tag, v)
        clock["t"] = T0 + timedelta(minutes=7 + i)
        s.sample(mono=300 + i)
    assert eq.cip_status(M.LINES["L1"].eq("cook"))["fouling_rf"] == \
        pytest.approx(1 / 3.0 - 1 / 3.5, rel=1e-3)

    # factory restart: counter back to 0 is a new baseline, the next CIP still counts
    bus.set("Utilities", "cip-station", "cip_count", 0)
    clock["t"] = T0 + timedelta(minutes=10)
    s.sample(mono=400)
    bus.set("Utilities", "cip-station", "cip_count", 1)
    clock["t"] = T0 + timedelta(minutes=11)
    s.sample(mono=500)
    rows = sorted(db.dw_cip_cycles.find({"line": "L1"}), key=lambda r: r["ts"])
    assert [(r["cip_count"], r["baseline"]) for r in rows] == \
        [(3, True), (4, False), (0, True), (1, False)]


class FakeTd:
    """Historian with one stored value per topic (what the change-based link sent once)."""
    db = "idp"

    def __init__(self, values):
        self.values, self.queries = values, 0

    def sql(self, stmt):
        self.queries += 1
        for t, v in self.values.items():
            if f"`topic`='{t}'" in stmt:
                return {"code": 0, "data": [[v, None]]}
        return {"code": 0, "data": [[None, None]]}


def test_a_value_the_bus_never_saw_comes_from_the_historian():
    bus = FakeBus()
    for tag, v in sample_for_u(3.8, 0.05).items():
        if tag != "hold_t_C":              # constant 75 C: published once, before we started
            bus.set("Receiving", "pasteurizer", tag, v)
    db, eq, s, clock = _sampler(bus)
    s.td = FakeTd({meters.topic("L1", "Receiving", "pasteurizer", "hold_t_C"): 75.0})
    s.sample(mono=0)
    assert len(db.dw_pasteur_samples.find({"line": "L1"})) == 1
    first = s.td.queries
    s.sample(mono=5)
    assert s.td.queries == first          # cached: no new historian query per sample


def test_a_counter_that_appears_later_is_not_consumption():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    for m, water in ((0, None), (5, None), (10, 9000.0), (15, 9000.0)):
        db.dw_meter_readings.insert_one({"line": "L1", "ts": iso(T0 + timedelta(minutes=m)),
                                         "heat_kwh_total": 100.0 + m, "electricity_kwh_total": 50.0,
                                         "steam_kg_total": 0.0, "water_L_total": water})
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=20))
    v = bvip.inputs(snap, "L1", T0, T0 + timedelta(minutes=20), CFG)
    assert v["water_l"] == 0.0 and v["energy_kwh"] == pytest.approx(15.0)


def test_fouled_line_is_dirty_and_cip_clears_it():
    db = get_db(mongo_url=None)
    eq = EquipmentMonitor(db, bus=None)
    cook = M.LINES["L1"].eq("cook")
    eq.set_fouling(cook, CIP_RF_LIMIT - 0.001)
    assert eq.is_dirty(cook) is False
    eq.set_fouling(cook, CIP_RF_LIMIT + 0.001)
    st = eq.cip_status(cook)
    assert st["dirty"] is True and st["reason"] == "vervuiling"
    eq.perform_cip(cook, operator_id="t", reason="vervuiling")
    assert eq.is_dirty(cook) is False and eq.cip_status(cook)["fouling_rf"] is None


# ------------------------------------------------------------------ KPIs


def _db():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    return db


def test_energy_inputs_sum_positive_steps_and_survive_a_counter_reset():
    db = _db()
    rows = [(0, 1000, 500, 0), (5, 1100, 520, 0), (10, 1300, 560, 2000),
            (15, 40, 10, 0),              # factory restarted: counters back near zero
            (20, 90, 20, 0)]
    for m, gas, el, water in rows:
        db.dw_meter_readings.insert_one({"line": "L1", "ts": iso(T0 + timedelta(minutes=m)),
                                         "heat_kwh_total": gas, "electricity_kwh_total": el,
                                         "steam_kg_total": 0, "water_L_total": water})
    db.dw_batches.insert_one({"batch_id": "B1", "line": "L1", "recipe_id": "chocolate-vla-1L",
                              "started_at": iso(T0), "completed_at": iso(T0 + timedelta(minutes=8)),
                              "packs_total": 1000, "reject_count": 0, "verdict": M.APPROVED,
                              "state": M.COMPLETE})
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=30))
    v = bvip.inputs(snap, "L1", T0, T0 + timedelta(minutes=30), CFG)
    gas, el = 100 + 200 + 40 + 50, 20 + 40 + 10 + 10
    assert v["energy_kwh"] == pytest.approx(gas + el)
    assert v["water_l"] == pytest.approx(2000)
    assert v["energy_costs"] == pytest.approx(gas * 0.06 + el * 0.18)
    assert v["co2_kg"] == pytest.approx(gas * 0.2 + el * 0.4)
    assert v["revenue"] == pytest.approx(1000.0)
    k = bvip.kpi_values(snap, "L1", T0, T0 + timedelta(minutes=30), CFG, CAT)
    assert k["KPI-ENE-001"]["value"] == pytest.approx((gas + el) / 1000)
    assert k["KPI-ENE-001"]["status"] == "OK"


def test_energy_in_5_minute_buckets_adds_up_to_the_window():
    db = _db()
    gas = el = 0.0
    for m in range(0, 31):
        gas += 3.0 + (m % 4)
        el += 1.0 + (m % 3)
        if m == 17:
            gas, el = 2.0, 1.0           # restart in the middle
        db.dw_meter_readings.insert_one({"line": "L1", "ts": iso(T0 + timedelta(minutes=m)),
                                         "heat_kwh_total": gas, "electricity_kwh_total": el,
                                         "steam_kg_total": 0, "water_L_total": 0})
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=40))
    whole = bvip.inputs(snap, "L1", T0, T0 + timedelta(minutes=30), CFG)["energy_kwh"]
    parts = [bvip.inputs(snap, "L1", T0 + timedelta(minutes=m), T0 + timedelta(minutes=m + 5),
                         CFG)["energy_kwh"] for m in range(0, 30, 5)]
    assert sum(parts) == pytest.approx(whole) and whole > 0


def test_energy_per_pack_only_counts_packs_from_when_the_meters_ran():
    """The meters started at 08:30; packs filled before then must not dilute kWh/pack,
    neither in the rolling value nor in the per-bucket inputs Grafana sums."""
    from vla import kpi_history
    db = _db()
    for i, m in enumerate((0, 10, 20, 35, 45)):
        db.dw_batches.insert_one({"batch_id": f"B{i}", "line": "L1", "recipe_id": "chocolate-vla-1L",
                                  "started_at": iso(T0 + timedelta(minutes=m)),
                                  "completed_at": iso(T0 + timedelta(minutes=m + 5)),
                                  "packs_total": 1000, "reject_count": 0, "verdict": M.APPROVED,
                                  "state": M.COMPLETE})
    for j, m in enumerate(range(30, 55, 5)):
        db.dw_meter_readings.insert_one({"line": "L1", "ts": iso(T0 + timedelta(minutes=m)),
                                         "heat_kwh_total": 100.0 * j, "electricity_kwh_total": 0.0,
                                         "steam_kg_total": 0.0, "water_L_total": 0.0})
    end = T0 + timedelta(minutes=55)
    snap = bvip.take_snapshot(db, now=end)
    k = bvip.kpi_values(snap, "L1", T0, end, CFG, CAT)
    # 400 kWh metered after 08:30, and only the 2 batches completed after 08:30 count
    assert k["KPI-ENE-001"]["value"] == pytest.approx(400 / 2000)
    rows = kpi_history.build_rows(snap, [T0 + timedelta(minutes=m) for m in range(0, 55, 5)], [],
                                  CFG, CAT, 300, 8, "idp")
    ene_ts = sorted({p.split("VALUES (")[1].split(",")[0] for p in rows.parts if "_ene_001_" in p})
    first_ms = int((T0 + timedelta(minutes=35)).timestamp() * 1000)   # first bucket with a step
    assert ene_ts and min(int(t) for t in ene_ts) >= first_ms


def test_no_meter_reading_is_no_measurement_not_zero():
    db = _db()
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=30))
    v = bvip.inputs(snap, "L1", T0, T0 + timedelta(minutes=30), CFG)
    assert v["energy_kwh"] is None and v["water_l"] is None and v["energy_costs"] is None


def test_heat_exchanger_kpis_from_samples_and_cycles():
    db = _db()
    pts = [(1, 3.8, 0.05), (2, 3.8, 0.10), (3, 3.4, 0.8), (4, 3.1, 1.4),   # cycle 1
           (6, 3.8, 0.05), (7, 3.6, 0.5)]                                   # after a CIP
    for m, u, run_h in pts:
        s = sample_for_u(u, run_h)
        db.dw_pasteur_samples.insert_one({"line": "L1", "ts": iso(T0 + timedelta(minutes=m)),
                                          **s, "u": meters.u_value(s, CP, AREA)})
    db.dw_cip_cycles.insert_one({"line": "L1", "ts": iso(T0 + timedelta(minutes=5)),
                                 "cip_count": 1, "baseline": False, "water_L": 9000,
                                 "caustic_kg": 15, "acid_kg": 8, "heat_kwh": 200,
                                 "electricity_kwh": 7.5, "duration_s": 3600, "past_run_h": 1.5})
    db.dw_cip_cycles.insert_one({"line": "L1", "ts": iso(T0), "cip_count": 0, "baseline": True})
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=10))
    k = bvip.kpi_values(snap, "L1", T0, T0 + timedelta(minutes=10), CFG, CAT)
    us = [3.8, 3.8, 3.4, 3.1, 3.8, 3.6]
    assert k["KPI-PAC-101"]["value"] == pytest.approx(sum(us) / 6, rel=1e-3)
    rfs = [0, 0, 1 / 3.4 - 1 / 3.8, 1 / 3.1 - 1 / 3.8, 0, 1 / 3.6 - 1 / 3.8]
    assert k["KPI-PAC-102"]["value"] == pytest.approx(sum(rfs) / 6, rel=1e-3)
    assert k["KPI-PAC-104"]["value"] == pytest.approx((69.4 - 5) / (75 - 5) * 100)
    assert k["KPI-PAC-100"]["value"] == pytest.approx(11 * CP * (75 - 69.4))
    assert k["KPI-PAC-103"]["value"] == pytest.approx(0.9)
    assert k["KPI-PAC-105"] == {"value": 1.5, "status": "OK", "inputs": {"n": 1.0}}
    cost = 15 * 0.55 + 8 * 0.9 + 9 * 2.5 + 200 * 0.06 + 7.5 * 0.18 + 0.5 * 80
    assert k["KPI-PAC-106"]["value"] == pytest.approx(cost)
    assert k["KPI-PAC-106"]["status"] == "DEELS"


def test_pac_kpis_without_samples_are_unset():
    db = _db()
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=10))
    k = bvip.kpi_values(snap, "L1", T0, T0 + timedelta(minutes=10), CFG, CAT)
    assert all(k[f"KPI-PAC-10{i}"]["status"] == "UNSET" for i in range(7))


# ------------------------------------------------------------------ autopilot


class CipBus:
    connected = True

    def __init__(self):
        self.state = "COMPLETE"

    def latest_value(self, equipment, tag):
        return self.state if (equipment, tag) == ("Batch", "state") else None


class FakeControl:
    """Counts StartCip; every other OPC-UA method (StartBatch, SetSetpoint) accepts."""

    def __init__(self):
        self.calls = 0

    def start_cip(self):
        self.calls += 1
        return {"rc": 0}

    def __getattr__(self, name):
        return lambda *a, **k: {"rc": 0, "accepted": True}


def test_fouling_triggers_a_factory_cip_and_the_autopilot_waits_for_it():
    import random
    from vla.autopilot import Autopilot
    from vla.batches import BatchRunner
    from vla.orders import OrderManager
    db = _db()
    eq = EquipmentMonitor(db, bus=None)
    orders = OrderManager(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(7), orders=orders, equipment=eq)
    runner.control = FakeControl()
    bus = CipBus()
    pilot = Autopilot(db, runner, equipment=eq, orders=orders, bus=bus, gap_s=10,
                      prune_every_s=1e9)
    eq.set_fouling(M.LINES["L1"].eq("cook"), CIP_RF_LIMIT + 0.01)
    assert pilot.step(now=0) == "wait_gap"
    assert pilot.step(now=11) == "cip"
    assert runner.control.calls == 1 and pilot.status["last_cip_reason"] == "vervuiling"
    bus.state = "CIP"
    assert pilot.step(now=12) == "wait_cip"
    bus.state = "IDLE"
    assert pilot.step(now=13) == "wait_gap"
    assert pilot.step(now=24).startswith("started ")
