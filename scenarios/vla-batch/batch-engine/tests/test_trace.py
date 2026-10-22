from datetime import timedelta

from vla import model as M
from vla.autopilot import OPERATOR
from vla.logistics_sim import LogisticsSim
from vla.trace import order_trace
from vla import warehouse as W

from tests.test_warehouse import T0, _run, _setup


def _link(db, bid, oid="PO-T1", customer="Zuivelhandel Noord"):
    db.dw_orders.insert_one({"order_id": oid, "customer": customer, "status": M.ORDER_RUNNING,
                             "target_qty_L": 1000, "recipe_id": "chocolate-vla-1L"})
    db.dw_batches.update_one({"batch_id": bid}, {"$set": {"order_id": oid}})
    return oid


def test_trace_follows_order_to_delivery_and_survives_hu_drop(monkeypatch):
    db, sim, (bid,) = _setup()
    oid = _link(db, bid)
    logi = LogisticsSim(db, OPERATOR)
    sim.logistics = logi
    t = _run(sim, db, 90, monkey=monkeypatch)
    tr = order_trace(db, oid, t, logi.shipments(t))
    (row,) = tr["batches"]
    assert row["step"] == "koelcel" and row["pallets"] == 3
    assert tr["customer"] == "Zuivelhandel Noord"

    t = _run(sim, db, W.STORE_DWELL_S + 30, start=t, monkey=monkeypatch)
    (row,) = order_trace(db, oid, t, logi.shipments(t))["batches"]
    assert row["step"] == "onderweg"
    assert row["shipment"]["customer"] == "Zuivelhandel Noord"
    assert db.dw_shipments.count_documents({"batch_id": bid}) == 1

    # shipped HUs are dropped; the stored shipment keeps the chain intact
    t = _run(sim, db, W.KEEP_SHIPPED_S + 60, start=t, tick=30, monkey=monkeypatch)
    assert not list(db.dw_handling_units.find({"batch_id": bid}))
    tr = order_trace(db, oid, t, logi.shipments(t))
    (row,) = tr["batches"]
    assert row["step"] == "afgeleverd" and row["pallets"] == 3
    assert tr["totals"]["trucks_delivered"] == 1 and tr["totals"]["packs_delivered"] == 1000
    assert db.dw_shipments.count_documents({}) == 1


def test_trace_unknown_order_is_none():
    db, _, _ = _setup()
    assert order_trace(db, "PO-NOPE", T0) is None
