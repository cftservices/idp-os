"""Werkorders binnen een tijdvenster: het Grafana-paneel toont het gekozen dashboardvenster.

Aanleiding (20-11-2026): het paneel liet maar twee dagen zien, omdat de API de laatste
100 werkorders gaf. Mongo had er 3.355 vanaf 22 oktober.
"""
import random

from vla.cmms import CmmsConnector
from vla.db import get_db, seed_recipes
from vla.equipment import EquipmentMonitor


def _cmms_with(reported: list[str]):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    cmms = CmmsConnector(db, EquipmentMonitor(db, bus=None), rng=random.Random(3))
    db.dw_work_orders.delete_many({})
    for i, ts in enumerate(reported):
        db.dw_work_orders.insert_one({"wo_id": f"WO-{i}", "equipment_id": "cook-unit-01",
                                      "line": "L1", "status": "done", "reported_at": ts})
    return cmms


def test_since_and_until_bound_the_window_and_accept_grafana_iso_with_z():
    cmms = _cmms_with(["2026-10-22T08:00:00+00:00", "2026-11-01T12:00:00+00:00",
                       "2026-11-19T22:00:00+00:00", "2026-11-20T09:00:00+00:00"])
    got = cmms.list(since="2026-10-31T00:00:00.000Z", until="2026-11-20T00:00:00.000Z",
                    limit=5000)
    assert [w["wo_id"] for w in got] == ["WO-2", "WO-1"]       # nieuwste eerst


def test_without_window_the_old_behaviour_stays():
    cmms = _cmms_with(["2026-10-22T08:00:00+00:00", "2026-11-20T09:00:00+00:00"])
    assert [w["wo_id"] for w in cmms.list(limit=1)] == ["WO-1"]


def test_unreadable_window_is_refused_not_ignored():
    cmms = _cmms_with(["2026-11-20T09:00:00+00:00"])
    try:
        cmms.list(since="gisteren")
    except ValueError as e:
        assert "since" in str(e)
    else:
        raise AssertionError("een onleesbaar venster mag niet stil alles teruggeven")
