"""The viscosity spec is a band (150-300 cP): out of spec on EITHER side is a critical
quality fail and rejects the batch. Before 2026-11-20 only the lower limit did; a batch
above 300 cP got a failed sample and ended HOLD instead."""
import random

import pytest

from vla import model as M
from vla.batches import BatchRunner
from vla.db import get_db, seed_recipes

TELEM = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
         "packs_total": 4980, "reject_count": 20,
         "dose_actuals": {"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "cocoa": 100.0}}


def _run(end_visc: float):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(4))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    recipe = M.get_recipe("chocolate-vla-1L")
    runner._finalize(b["batch_id"], recipe, 88.0, 300.0, None, end_visc, 4980, 20,
                     telemetry=TELEM)
    return db, db.dw_batches.find_one({"batch_id": b["batch_id"]})


@pytest.mark.parametrize("visc,verdict", [
    (140.0, M.REJECTED),     # below the band: the original Solve
    (260.0, M.APPROVED),     # a good batch
    (300.0, M.APPROVED),     # the limit itself is in spec
    (320.0, M.REJECTED),     # above the band: too thick is out of spec too
])
def test_viscosity_outside_the_band_rejects_on_both_sides(visc, verdict):
    _db, batch = _run(visc)
    assert batch["verdict"] == verdict


def test_too_thick_raises_the_same_critical_alarm_as_too_thin():
    db, batch = _run(320.0)
    alarms = [a for a in db.dw_alarms.find({"batch_id": batch["batch_id"]})
              if a["alarm_type"] == "viscosity_out_of_spec"]
    assert len(alarms) == 1 and alarms[0]["severity"] == M.CRITICAL
    assert "above" in alarms[0]["message"]
