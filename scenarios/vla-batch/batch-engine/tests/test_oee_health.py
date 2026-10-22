import random
from datetime import datetime, timedelta, timezone

import pytest

from vla import kpi, model as M
from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.equipment import EquipmentMonitor, BASE_HEATUP_SEC

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def setup(n):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(46), equipment=mon)
    for _ in range(n):
        b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    return db, mon, runner


def test_oee_shape_and_cook_performance_drop():
    db, mon, runner = setup(2)
    rows = {r["equipment_id"]: r for r in mon.oee()}
    cook = rows["cook-unit-01"]
    assert set(cook) == {"equipment_id", "line", "availability", "performance",
                         "quality", "oee"}
    # avg heat-up after 2 batches = base*(1.15+1.30)/2 -> performance < 1
    expected_perf = round(BASE_HEATUP_SEC / (BASE_HEATUP_SEC * (1.15 + 1.30) / 2), 4)
    assert cook["performance"] == expected_perf
    assert rows["filler-01"]["performance"] == 1.0
    assert 0.0 <= cook["oee"] <= 1.0


def test_health_includes_trend_and_alerts():
    db, mon, runner = setup(3)  # 3rd batch fires the fouling alert
    h = {r["equipment_id"]: r for r in mon.health()}
    cook = h["cook-unit-01"]
    assert len(cook["heatup_trend"]) == 3
    assert len(cook["open_cbm_alerts"]) == 1
    assert h["filler-01"]["open_cbm_alerts"] == []


# ---------------------------------------------------------------------------
# Een definitie, twee endpoints.
#
# /oee (equipment.py) en /kpi/summary (kpi.py) delen de state-historie van
# dezelfde vijf procesdelen. Toen de indeling van die states op twee plaatsen
# stond, landde de Dirty-herclassificatie alleen in kpi.py en spraken de twee
# elkaar op het scherm tegen: cook-unit-01 stond op 0 % beschikbaarheid naast
# een KPI-tegel die 100 % benutting meldde. Deze tests falen zodra dat opnieuw
# uiteenloopt.
# ---------------------------------------------------------------------------

BLOK_MIN = 30


def _schrijf_historie(mon, equipment_id, states, minutes=BLOK_MIN):
    """Vervang de statehistorie door gelijke blokken, oplopend in de tijd."""
    start = datetime.now(timezone.utc) - timedelta(minutes=minutes * len(states))
    mon.db.dw_equipment_state.delete_many({"equipment_id": equipment_id})
    for i, state in enumerate(states):
        mon.db.dw_equipment_state.insert_one({
            "equipment_id": equipment_id,
            "state": state,
            "ts": (start + timedelta(minutes=minutes * i)).isoformat(),
        })


def _monitor():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    return EquipmentMonitor(db, bus=None)


def test_de_drie_verzamelingen_zijn_disjunct_en_dekkend():
    """Een state die in twee emmers valt, of in geen enkele, maakt elke som
    stilzwijgend fout."""
    assert not (M.PRODUCTIVE_STATES & M.NEUTRAL_STATES)
    assert not (M.PRODUCTIVE_STATES & M.DOWNTIME_STATES)
    assert not (M.NEUTRAL_STATES & M.DOWNTIME_STATES)

    alle = M.PRODUCTIVE_STATES | M.NEUTRAL_STATES | M.DOWNTIME_STATES
    assert alle == {"Running", "Dirty", "Idle", "Allocated", "Down", "Error"}


def test_kpi_leest_dezelfde_verzamelingen_als_het_model():
    """De aliassen in kpi.py mogen geen kopie zijn: een kopie kan opnieuw
    uiteenlopen zonder dat iets faalt."""
    assert kpi._DOWNTIME_STATES is M.DOWNTIME_STATES
    assert kpi._PRODUCTIVE_STATES is M.PRODUCTIVE_STATES
    assert kpi._NEUTRAL_STATES is M.NEUTRAL_STATES


def test_dirty_telt_niet_als_stilstand_in_oee():
    """De regressie zelf: een procesdeel dat alleen maar Dirty was, stond op
    0 % beschikbaarheid terwijl het gewoon doorkookte."""
    mon = _monitor()
    _schrijf_historie(mon, "cook-unit-01", ["Dirty", "Dirty", "Dirty"])
    running, down = mon._running_and_down_seconds("cook-unit-01")
    assert down == 0.0
    assert running > 0.0


def test_down_telt_wel_als_stilstand():
    """De andere kant op: een echte storing mag niet als productief wegvallen."""
    mon = _monitor()
    _schrijf_historie(mon, "cooler-01", ["Running", "Down"])
    running, down = mon._running_and_down_seconds("cooler-01")
    assert running > 0.0
    assert down > 0.0
    assert running == pytest.approx(down, rel=0.1)


def test_idle_valt_buiten_beide_emmers():
    """Neutraal is geen derde categorie in de noemer: stilstaan zonder werk
    hoort niet als onbeschikbaarheid te lezen."""
    mon = _monitor()
    _schrijf_historie(mon, "filler-01", ["Running", "Idle", "Allocated"])
    running, down = mon._running_and_down_seconds("filler-01")
    assert down == 0.0
    # Alleen het eerste blok telt; de twee neutrale blokken vallen weg.
    assert running == pytest.approx(BLOK_MIN * 60, rel=0.05)


def test_onbekende_state_telt_als_stilstand():
    """Een nieuwe state mag de beschikbaarheid niet stilzwijgend opblazen."""
    mon = _monitor()
    _schrijf_historie(mon, "process-tank-01", ["Running", "Verzonnen"])
    _, down = mon._running_and_down_seconds("process-tank-01")
    assert down > 0.0


def test_oee_en_kpi_delen_dezelfde_indeling():
    """Dezelfde historie, twee rekenpaden, een uitkomst.

    /oee rekent in seconden over intervallen; de kpi-kant classificeert per
    state. Vergelijkbaar te maken door de blokken even lang te houden: dan is de
    verhouding productief tegen stilstand in beide paden gelijk.
    """
    mon = _monitor()
    states = ["Running", "Dirty", "Down", "Idle", "Running"]
    _schrijf_historie(mon, "cook-unit-01", states)

    running, down = mon._running_and_down_seconds("cook-unit-01")
    beschikbaarheid = running / (running + down)

    rows = mon._history("cook-unit-01")
    productief = sum(1 for r in rows if r["state"] in kpi._PRODUCTIVE_STATES)
    stilstand = sum(1 for r in rows if r["state"] in kpi._DOWNTIME_STATES)
    verwacht = productief / (productief + stilstand)

    assert beschikbaarheid == pytest.approx(verwacht, rel=0.1)


def test_oee_quality_is_per_line():
    """Een HOLD op L2 zegt niets over de machines van L1. Plant-breed stond
    op beide lijnen dezelfde kwaliteit, waardoor niet te zien was welke lijn
    verloor."""
    mon = _monitor()
    mon.db.dw_batches.insert_one({"batch_id": "B-L1", "line": "L1", "state": M.COMPLETE,
                                  "verdict": M.APPROVED, "packs_total": 1000})
    mon.db.dw_batches.insert_one({"batch_id": "B-L2a", "line": "L2", "state": M.COMPLETE,
                                  "verdict": M.APPROVED, "packs_total": 1000})
    mon.db.dw_batches.insert_one({"batch_id": "B-L2b", "line": "L2", "state": M.COMPLETE,
                                  "verdict": M.HOLD, "packs_total": 1000})
    rows = {r["equipment_id"]: r for r in mon.oee()}
    assert rows["cook-unit-01"]["quality"] == 1.0
    assert rows["cook-unit-02"]["quality"] == 0.5
    assert rows["filler-02"]["line"] == "L2"
    only_l2 = mon.oee(line="L2")
    assert {r["quality"] for r in only_l2} == {0.5}


def test_throughput_is_per_line_and_in_process_time(monkeypatch):
    """De norm is 'scope: line' en in echte tijd; de demo draait versneld en
    heeft twee lijnen. Zonder omrekening las de tegel ~20x de norm."""
    mon = _monitor()
    db = mon.db
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=2)
    db.dw_batches.insert_one({"batch_id": "B-1", "line": "L1"})
    db.dw_batches.insert_one({"batch_id": "B-2", "line": "L2"})
    # without run times: falls back to elapsed time (2 h)
    for bid, packs in (("B-1", 30000), ("B-2", 10000)):
        db.dw_production.insert_one({"batch_id": bid, "packs": packs,
                                     "ts": (now - timedelta(minutes=30)).isoformat()})
    monkeypatch.setenv("KPI_TIME_SCALE", "1")
    value, meta = kpi._throughput_rate(db, start, now + timedelta(hours=10))
    # elapsed = 2 h, not 12 h: the future part of the window does not count
    assert meta["per_line"] == {"L1": pytest.approx(15000, rel=0.01),
                                "L2": pytest.approx(5000, rel=0.01)}
    assert value == pytest.approx(10000, rel=0.01)
    monkeypatch.setenv("KPI_TIME_SCALE", "10")
    value, meta = kpi._throughput_rate(db, start, now)
    assert value == pytest.approx(1000, rel=0.01) and meta["time_scale"] == 10


def test_throughput_divides_by_run_time_not_calendar_time(monkeypatch):
    """ISO 22400: TR = (GQ + RQ) / AOET. Een lijn die pas net draait, of een
    demo die 's nachts uit staat, mag niet onder de norm zakken door lege uren."""
    mon = _monitor()
    db = mon.db
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=3)
    t0 = now - timedelta(hours=2)
    # two L1 batches of 30 min each, 1000 packs each; one L2 batch of 15 min
    for bid, ln, s_min, dur, packs in (("A", "L1", 0, 30, 1000), ("B", "L1", 60, 30, 1000),
                                        ("C", "L2", 0, 15, 500)):
        st = t0 + timedelta(minutes=s_min)
        en = st + timedelta(minutes=dur)
        db.dw_batches.insert_one({"batch_id": bid, "line": ln, "started_at": st.isoformat(),
                                  "completed_at": en.isoformat()})
        db.dw_production.insert_one({"batch_id": bid, "packs": packs, "ts": en.isoformat()})
    monkeypatch.setenv("KPI_TIME_SCALE", "1")
    value, meta = kpi._throughput_rate(db, start, now)
    assert meta["per_line"] == {"L1": pytest.approx(2000, rel=0.01), "L2": pytest.approx(2000, rel=0.01)}
    assert meta["run_hours"]["L1"] == pytest.approx(1.0, rel=0.01)
    assert value == pytest.approx(2000, rel=0.01)
