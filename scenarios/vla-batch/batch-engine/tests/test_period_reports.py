import random
import pytest

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner
from vla.equipment import EquipmentMonitor
from vla.period_reports import (assemble_period_report,
                                assemble_equipment_report,
                                render_period_pdf, render_equipment_pdf)

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}
TELEM_BAD = {"fault": "cook_undertemp", "magnitude": 0.6,
             "hold_elapsed_sec": 300.0, "packs_total": 4900, "reject_count": 100}


def test_period_and_equipment_reports():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(48), equipment=mon)
    for telem in (TELEM_OK, TELEM_OK, TELEM_BAD):
        b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=telem)
    rep = assemble_period_report(db, days=7)
    assert rep["batches_total"] == 3
    assert rep["batches_by_verdict"]["REJECTED"] + \
           rep["batches_by_verdict"]["HOLD"] == 1
    assert rep["hold_reject_ratio"] == round(1 / 3, 4)
    assert rep["yield_pct"] > 0
    assert render_period_pdf(rep)[:4] == b"%PDF"

    er = assemble_equipment_report(db, "cook-unit-01", days=30)
    assert er["equipment_id"] == "cook-unit-01"
    assert er["running_hours"] >= 0.0
    assert render_equipment_pdf(er)[:4] == b"%PDF"
    with pytest.raises(ValueError, match="unknown"):
        assemble_equipment_report(db, "toaster-9000", days=30)
