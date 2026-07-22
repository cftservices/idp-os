import random
import pytest

from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner

TELEM_BAD = {"fault": "cook_undertemp", "magnitude": 0.6,
             "hold_elapsed_sec": 300.0, "packs_total": 4900, "reject_count": 100}


def test_alarm_ack_flow():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(44))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    runner.start_batch(b["batch_id"], telemetry=TELEM_BAD)
    alarm = db.dw_alarms.find({"batch_id": b["batch_id"]})[0]
    assert alarm["alarm_id"].startswith("A-")
    acked = runner.ack_alarm(alarm["alarm_id"], operator_id="OP-7")
    assert acked["acknowledged"] is True and acked["ack_by"] == "OP-7"
    with pytest.raises(ValueError, match="unknown"):
        runner.ack_alarm("A-DOESNOTEXIST", operator_id="OP-7")


def test_verdict_ack_idempotent_and_gated():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(45))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    with pytest.raises(ValueError):
        runner.ack_verdict(b["batch_id"], operator_id="OP-7")  # not COMPLETE yet
    runner.start_batch(b["batch_id"], telemetry=TELEM_BAD)
    a1 = runner.ack_verdict(b["batch_id"], operator_id="OP-7")
    a2 = runner.ack_verdict(b["batch_id"], operator_id="OP-8")
    assert a1["verdict_ack"]["operator_id"] == "OP-7"
    assert a2["verdict_ack"]["operator_id"] == "OP-7"  # idempotent
