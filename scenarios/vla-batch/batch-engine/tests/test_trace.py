from datetime import timedelta

from vla import model as M
from vla.autopilot import OPERATOR
from vla.logistics_sim import LogisticsSim
from vla.trace import order_trace
from vla.forklift_trace import forklift_history, hu_history
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


def test_every_pallet_names_its_forklifts_even_after_the_hu_is_dropped(monkeypatch):
    """Track & trace per pallet: wie zette hem weg, wie laadde hem, in welke
    truck. Moet blijven kloppen als de HU na KEEP_SHIPPED_S verdwenen is."""
    db, sim, (bid,) = _setup()
    oid = _link(db, bid)
    logi = LogisticsSim(db, OPERATOR)
    sim.logistics = logi
    t = _run(sim, db, W.STORE_DWELL_S + 120, monkey=monkeypatch)
    pallets = order_trace(db, oid, t, logi.shipments(t))["batches"][0]["pallet_list"]
    assert len(pallets) == 3
    ids = {f.id for f in logi.forklifts.values()}
    for p in pallets:
        assert p["putaway_by"] in ids and p["loaded_by"] in ids
        assert p["truck_id"] and p["truck_id"].startswith("TRK-")
        assert p["wrapped_at"] and p["stored_at"] and p["shipped_at"]
    # putaway on L1 is HT-01's job; loading is one of the loaders
    assert {p["putaway_by"] for p in pallets} == {"HT-01"}
    assert {p["loaded_by"] for p in pallets} <= {"HT-02", "HT-04"}

    t = _run(sim, db, W.KEEP_SHIPPED_S + 60, start=t, tick=30, monkey=monkeypatch)
    assert not list(db.dw_handling_units.find({"batch_id": bid}))
    tr = order_trace(db, oid, t, logi.shipments(t))
    again = tr["batches"][0]["pallet_list"]
    assert [(p["hu_id"], p["putaway_by"], p["loaded_by"]) for p in again] == \
        [(p["hu_id"], p["putaway_by"], p["loaded_by"]) for p in pallets]
    types = [e["type"] for e in tr["events"]]
    assert types.count("forklift_task") == 6
    assert "hu_scanned" in types and "hu_shipped" in types and "delivered" in types
    assert [e["ts"] for e in tr["events"]] == sorted(e["ts"] for e in tr["events"])

    h = hu_history(db, pallets[0]["hu_id"])
    assert h["order_id"] == oid and not h["live"]
    assert [e["type"] for e in h["events"]][0] == "hu_scanned"
    assert sum(1 for e in h["events"] if e["type"] == "forklift_task") == 2


def test_forklift_history_lists_the_orders_it_handled(monkeypatch):
    db, sim, (bid,) = _setup()
    oid = _link(db, bid)
    logi = LogisticsSim(db, OPERATOR)
    sim.logistics = logi
    t = _run(sim, db, W.STORE_DWELL_S + 120, monkey=monkeypatch)
    ht1 = forklift_history(db, "HT-01")
    assert ht1["totals"] == {"moves": 3, "putaway": 3, "loaded": 0, "orders": 1}
    (row,) = ht1["orders"]
    assert row["order_id"] == oid and row["customer"] == "Zuivelhandel Noord"
    assert row["batches"] == [bid] and row["putaway"] == 3
    assert ht1["recent"][0]["order_id"] == oid
    loaded = sum(forklift_history(db, f)["totals"]["loaded"] for f in ("HT-02", "HT-04"))
    assert loaded == 3
    loader = next(f for f in ("HT-02", "HT-04") if forklift_history(db, f)["totals"]["loaded"])
    assert forklift_history(db, loader)["orders"][0]["trucks"][0].startswith("TRK-")
    assert forklift_history(db, "HT-99")["totals"]["moves"] == 0
    assert hu_history(db, "HU-NOPE") is None
