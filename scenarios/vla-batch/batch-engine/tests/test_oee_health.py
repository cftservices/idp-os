import random

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
    assert set(cook) == {"equipment_id", "availability", "performance",
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
