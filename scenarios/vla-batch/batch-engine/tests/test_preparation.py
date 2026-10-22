from datetime import datetime, timedelta, timezone

import pytest

from vla import model as M
from vla import preparation as P
from vla.db import get_db
from vla.trace import order_trace

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def _prep():
    return P.PrepManager(get_db(mongo_url=None))


def test_plan_rejects_recipe_of_other_line():
    pm = _prep()
    with pytest.raises(ValueError):
        pm.plan("L1", "vanilla-vla-1L")
    doc = pm.plan("L2", "vanilla-vla-1L", now=T0)
    assert doc["weigh_station"] == "weigh-station-02"
    assert doc["premix_tank"] == "premix-tank-02"
    assert all(c["material_id"] != "milk" for c in doc["components"])


def test_states_walk_weigh_premix_ready_consumed():
    pm = _prep()
    a = pm.plan("L2", "banana-vla-1L", now=T0)["prep_id"]
    b = pm.plan("L2", "banana-vla-1L", now=T0)["prep_id"]
    pm.tick(T0)
    assert pm.get(a)["state"] == P.AFWEGEN
    assert pm.get(b)["state"] == P.GEPLAND  # one weigh station per line
    pm.tick(T0 + timedelta(seconds=P.WEIGH_S + 1))
    assert pm.get(a)["state"] == P.PREMIX
    assert pm.get(b)["state"] == P.AFWEGEN
    assert pm.ready("L2", "banana-vla-1L") is None
    pm.tick(T0 + timedelta(seconds=P.WEIGH_S + P.PREMIX_S + 2))
    assert pm.ready("L2", "banana-vla-1L")["prep_id"] == a
    pm.consume(a, "VB-X1")
    assert pm.get(a)["state"] == P.VERBRUIKT
    assert pm.for_batch("VB-X1")["prep_id"] == a


def test_lines_do_not_share_stations():
    pm = _prep()
    pm.plan("L1", "chocolate-vla-1L", now=T0)
    pm.plan("L2", "caramel-vla-1L", now=T0)
    pm.tick(T0)
    busy = {s["station_id"] for s in pm.stations() if s["state"] == "bezig"}
    assert busy == {"weigh-station-01", "weigh-station-02"}


def test_ensure_ahead_drops_planned_sets_of_other_recipe():
    pm = _prep()
    pm.ensure_ahead("L2", "vanilla-vla-1L")
    assert len(pm.list(line="L2")) == P.AHEAD
    pm.ensure_ahead("L2", "strawberry-vla-1L")
    left = pm.list(line="L2")
    assert {d["recipe_id"] for d in left} == {"strawberry-vla-1L"}
    assert len(left) == P.AHEAD


def test_changeover_cip_only_light_after_dark():
    assert M.changeover_needs_cip("chocolate-vla-1L", "vanilla-vla-1L")
    assert M.changeover_needs_cip("strawberry-vla-1L", "banana-vla-1L")
    assert not M.changeover_needs_cip("vanilla-vla-1L", "strawberry-vla-1L")
    assert not M.changeover_needs_cip("caramel-vla-1L", "caramel-vla-1L")


def test_trace_shows_prep_of_batch():
    db = get_db(mongo_url=None)
    pm = P.PrepManager(db)
    db.dw_orders.insert_one({"order_id": "PO-P1", "status": M.ORDER_RUNNING,
                             "recipe_id": "caramel-vla-1L", "target_qty_L": 1000})
    db.dw_batches.insert_one({"batch_id": "VB-P1", "order_id": "PO-P1", "line": "L2",
                              "recipe_id": "caramel-vla-1L", "state": M.COMPLETE})
    used = pm.plan("L2", "caramel-vla-1L", order_id="PO-P1", now=T0)
    pm.plan("L2", "caramel-vla-1L", order_id="PO-P1", now=T0)
    pm.consume(used["prep_id"], "VB-P1")
    tr = order_trace(db, "PO-P1", T0)
    assert tr["line"] == "L2"
    assert tr["batches"][0]["line"] == "L2"
    assert tr["batches"][0]["prep"]["prep_id"] == used["prep_id"]
    assert len(tr["prep_pending"]) == 1
