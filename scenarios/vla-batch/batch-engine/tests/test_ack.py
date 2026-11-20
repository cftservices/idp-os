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


def _bad_batches(runner, n):
    for _ in range(n):
        b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=TELEM_BAD)


def test_ack_all_open_acks_only_open_alarms_in_the_selection():
    from datetime import datetime, timedelta, timezone
    from vla import alarms as A
    from vla.equipment import CipRequired, EquipmentMonitor

    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(46), equipment=mon)
    try:
        _bad_batches(runner, 2)
    except CipRequired:
        mon.perform_cip("cook-unit-01", operator_id="t")
        _bad_batches(runner, 1)
    # Een alarm van L2 en een al bevestigd alarm, om de selectie te toetsen.
    first = db.dw_alarms.find({})[0]
    db.dw_alarms.insert_one({**{k: v for k, v in first.items() if k != "_id"},
                             "alarm_id": "A-L2TEST", "equipment_id": "cook-unit-02",
                             "line": "L2"})
    db.dw_alarms.insert_one({**{k: v for k, v in first.items() if k != "_id"},
                             "alarm_id": "A-SHELVED"})
    A.shelve(db, "A-SHELVED", "WO-1",
             (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "t")
    runner.ack_alarm(first["alarm_id"], operator_id="OP-1")

    open_l1 = [a["alarm_id"] for a in A.list_alarms(db, state="open", limit=10_000)
               if a.get("line") == "L1"]
    assert open_l1, "de fixture hoort open L1-alarmen op te leveren"

    out = runner.ack_all_open(operator_id="OP-9", line="L1")
    assert sorted(out["alarm_ids"]) == sorted(open_l1)
    assert out["acknowledged"] == len(open_l1)

    by_id = {a["alarm_id"]: a for a in db.dw_alarms.find({})}
    assert all(by_id[i]["ack_by"] == "OP-9" for i in open_l1)
    assert by_id[first["alarm_id"]]["ack_by"] == "OP-1"      # al bevestigd: ongemoeid
    assert not by_id["A-L2TEST"]["acknowledged"]              # andere lijn: ongemoeid
    assert not by_id["A-SHELVED"]["acknowledged"]             # geparkeerd: ongemoeid
    events = [e for e in db.dw_batch_events.find({"event_type": "alarm_acknowledged"})
              if e["payload"].get("operator_id") == "OP-9"]
    assert len(events) == len(open_l1)                        # elk alarm een eigen spoor

    again = runner.ack_all_open(operator_id="OP-9", line="L1")
    assert again["acknowledged"] == 0                         # idempotent

    rest = runner.ack_all_open(operator_id="OP-9")
    assert rest["alarm_ids"] == ["A-L2TEST"]
