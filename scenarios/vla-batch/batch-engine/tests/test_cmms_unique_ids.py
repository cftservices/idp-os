"""Werkordernummers moeten uniek zijn, en een overgang raakt alleen zijn eigen werkorder.

Aanleiding (17-11-2026): in productie deelden een CBM-werkorder en een storingswerkorder
hetzelfde nummer. De update op `wo_id` raakte steeds het CBM-document, de storing kwam
nooit verder dan `reported`, de machine bleef `Down` en de autopilot wachtte vanaf
29-10 op reparatie. Deze tests bootsen die toestand na.
"""
import random
from datetime import datetime, timedelta

from vla import cmms as C
from vla.cmms import CmmsConnector
from vla.db import get_db, seed_recipes
from vla.equipment import EquipmentMonitor


def _setup():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    return db, mon, CmmsConnector(db, mon, rng=random.Random(7))


def test_breakdown_progresses_although_an_older_work_order_has_the_same_number():
    db, mon, cmms = _setup()
    wo = cmms.report_breakdown("process-tank-01", repair_s=60)
    # De kapotte toestand uit productie: een ouder CBM-document met hetzelfde nummer,
    # vóór de storing in de collectie (dus de eerste treffer van een update op wo_id).
    cbm = {**wo, "equipment_id": "cook-unit-01", "area": "Cook", "kind": "cbm",
           "priority": "P3", "source_ref": "CBM-X", "status": "done",
           "reported_at": "2026-10-27T06:03:30+00:00"}
    rest = db.dw_work_orders.find({})
    db.dw_work_orders.delete_many({})
    db.dw_work_orders.insert_one(cbm)
    for d in rest:
        db.dw_work_orders.insert_one(d)

    t0 = datetime.fromisoformat(wo["reported_at"])
    cmms.tick(t0 + timedelta(seconds=25))
    cmms.tick(t0 + timedelta(seconds=55))
    started = datetime.fromisoformat(cmms.list(equipment_id="process-tank-01")[0]["started_at"])
    cmms.tick(started + timedelta(seconds=61))

    assert cmms.list(equipment_id="process-tank-01")[0]["status"] == "done"
    assert not mon.is_down("process-tank-01")
    other = cmms.list(equipment_id="cook-unit-01")[0]
    assert other["status"] == "done" and other["kind"] == "cbm"
    assert len(other["history"]) == len(cbm["history"])  # niet overschreven


def test_new_work_order_never_reuses_an_existing_number(monkeypatch):
    db, mon, cmms = _setup()
    seq = iter(["a" * 32, "a" * 32, "b" * 32])

    class _U:
        def __init__(self, h):
            self.hex = h

    monkeypatch.setattr(C.uuid, "uuid4", lambda: _U(next(seq)))
    first = cmms.create("cook-unit-01", "cbm", "test", priority="P3")
    second = cmms.create("filler-01", "breakdown", "test", priority="P1")
    assert second["wo_id"] != first["wo_id"]
    assert len({w["wo_id"] for w in db.dw_work_orders.find({})}) == 2
