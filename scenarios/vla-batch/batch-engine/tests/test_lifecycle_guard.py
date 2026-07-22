import random
import pytest

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


def test_no_sample_booking_on_complete_batch():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(2))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    with pytest.raises(ValueError, match="COMPLETE"):
        runner.take_sample(b["batch_id"], "viscosity")
