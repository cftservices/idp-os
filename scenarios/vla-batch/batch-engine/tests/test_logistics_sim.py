from datetime import datetime, timedelta, timezone

from vla import logistics_sim as LS
from vla import model as M
from vla.autopilot import OPERATOR
from vla.logistics_sim import LogisticsSim

from tests.test_warehouse import T0, _hus, _run, _setup


def test_shipment_moves_order_loading_en_route_delivered(monkeypatch):
    db, sim, (bid,) = _setup()
    logi = LogisticsSim(db, OPERATOR)
    sim.logistics = logi
    t = _run(sim, db, 90, monkey=monkeypatch)
    (s,) = logi.shipments(t)
    assert s["stage"] == "order" and s["pallets"] == 3
    assert s["shipment_id"].startswith("SHP-") and s["truck_id"].startswith("TRK-")
    assert s["customer"] in LS.CUSTOMERS
    # stop halfway through loading: one HU shipped
    t = _run(sim, db, LS_DWELL(), start=t, monkey=monkeypatch)
    stages = {x["stage"] for x in logi.shipments(t)}
    assert stages <= {"loading", "en_route"}
    t = _run(sim, db, 30, start=t, monkey=monkeypatch)
    (s,) = logi.shipments(t)
    assert s["stage"] == "en_route" and s["eta"] and s["pallets_loaded"] == 3
    later = datetime.fromisoformat(s["eta"]) + timedelta(seconds=1)
    assert logi.shipments(later)[0]["stage"] == "delivered"


def LS_DWELL():
    from vla import warehouse as W
    return W.STORE_DWELL_S + 3


def test_shipment_customer_is_stable_per_batch(monkeypatch):
    db, sim, _ = _setup()
    logi = LogisticsSim(db, OPERATOR)
    t = _run(sim, db, 90, monkey=monkeypatch)
    a = logi.shipments(t)[0]
    b = logi.shipments(t + timedelta(minutes=1))[0]
    assert (a["customer"], a["destination"], a["truck_id"]) == \
        (b["customer"], b["destination"], b["truck_id"])


def test_forklift_charges_and_other_takes_over():
    logi = LogisticsSim(db=None, operator_id=OPERATOR)
    ht1 = logi.forklifts["putaway"]
    ht1.battery = LS.CHARGE_BELOW_PCT + LS.BATTERY_PER_MOVE_PCT / 2
    logi.record(["putaway HU-1"], T0)
    assert ht1.charging and ht1.trips_today == 1
    logi.record(["putaway HU-2"], T0 + timedelta(seconds=10))
    assert ht1.trips_today == 1
    assert logi.forklifts["loader"].trips_today == 1
    # charges back up and returns to work
    logi.record([], T0 + timedelta(minutes=30))
    assert not ht1.charging and ht1.battery >= LS.CHARGE_UNTIL_PCT
    logi.record(["putaway HU-3"], T0 + timedelta(minutes=31))
    assert ht1.trips_today == 2


def test_forklifts_stagger_charging_and_never_starve():
    """Op de echte cadans (~10 bewegingen per minuut) stonden beide trucks
    voorgoed op ~0 % aan de lader. Nu nooit tegelijk aan de lader zolang er
    één boven de kritieke grens zit, en geen van beide loopt leeg."""
    logi = LogisticsSim(db=None, operator_id=OPERATOR)
    t = T0
    both = 0
    for i in range(8 * 60 * 10):          # eight hours, a move every 6 s
        t += timedelta(seconds=6)
        logi.record([("putaway" if i % 2 else "ship") + f" HU-{i}"], t)
        fl = list(logi.forklifts.values())
        both += all(f.charging for f in fl)
        assert min(f.battery for f in fl) > LS.CHARGE_CRITICAL_PCT
    assert both == 0


def test_second_truck_waits_for_the_charger():
    logi = LogisticsSim(db=None, operator_id=OPERATOR)
    ht1, ht2 = logi.forklifts["putaway"], logi.forklifts["loader"]
    ht1.charging, ht1.battery = True, 30.0
    ht2.battery = LS.CHARGE_BELOW_PCT - 1
    logi.record(["ship HU-1"], T0)
    assert not ht2.charging            # waits: one charger, HT-01 is on it
    ht2.battery = LS.CHARGE_CRITICAL_PCT - 1
    logi.record(["ship HU-2"], T0 + timedelta(seconds=5))
    assert ht2.charging                # critical: goes anyway


def test_shipment_customer_comes_from_the_customer_order(monkeypatch):
    db, sim, (bid,) = _setup()
    db.dw_orders.insert_one({"order_id": "PO-K1", "customer": "Koelketen Midden",
                             "status": "RUNNING"})
    db.dw_batches.update_one({"batch_id": bid}, {"$set": {"order_id": "PO-K1"}})
    logi = LogisticsSim(db, OPERATOR)
    t = _run(sim, db, 90, monkey=monkeypatch)
    (s,) = logi.shipments(t)
    assert s["customer"] == "Koelketen Midden" and s["order_id"] == "PO-K1"
    assert (s["destination"], s["drive_min"]) == LS.destination_of("Koelketen Midden")


def test_trips_reset_at_midnight():
    logi = LogisticsSim(db=None, operator_id=OPERATOR)
    late = datetime(2026, 10, 6, 23, 59, tzinfo=timezone.utc)
    logi.record(["ship HU-1"], late)
    assert logi.forklifts["loader"].trips_today == 1
    logi.record([], late + timedelta(minutes=2))
    assert logi.forklifts["loader"].trips_today == 0


def _milk(db) -> dict:
    return db.dw_materials.find_one({"material_id": LS.MILK_ID})


def _set_stock(db, mid: str, qty: float) -> None:
    db.dw_materials.update_one({"material_id": mid}, {"$set": {"stock_qty": qty}})


def test_tanker_is_ordered_unloads_into_the_silo_and_leaves():
    db, _, _ = _setup()
    logi = LogisticsSim(db, OPERATOR)
    assert logi.inbound(T0) is None
    reorder = _milk(db)["reorder_level"]
    cap = reorder * LS.SILO_CAPACITY_X_REORDER
    _set_stock(db, LS.MILK_ID, cap * 0.6)
    logi.record([], T0)
    assert logi.inbound(T0) is None          # above the order mark: no tanker
    _set_stock(db, LS.MILK_ID, 2000.0)
    logi.record([], T0)
    i = logi.inbound(T0)
    assert i["stage"] == "en_route" and i["kind"] == "melktankwagen"
    t = T0 + timedelta(seconds=LS.TANKER_EN_ROUTE_S)
    logi.record([], t)
    assert logi.inbound(t)["stage"] == "unloading"
    assert _milk(db)["stock_qty"] == 2000.0  # nothing pumped yet
    t += timedelta(seconds=LS.TANKER_UNLOAD_S / 3)
    logi.record([], t)
    mid = logi.inbound(t)
    assert mid["stage"] == "unloading" and 0 < mid["unloaded_pct"] < 100
    assert _milk(db)["stock_qty"] > 2000.0   # the silo rises while it unloads
    for _ in range(20):
        t += timedelta(seconds=30)
        logi.record([], t)
    end = logi.inbound(t)
    assert end["stage"] == "gone"
    assert _milk(db)["stock_qty"] <= cap     # never above the silo capacity
    assert end["received_kg"] == round(_milk(db)["stock_qty"] - 2000.0, 1)
    left = datetime.fromisoformat(end["next_at"]) - timedelta(seconds=LS.TANKER_GONE_S)
    assert left <= t


def test_receipts_are_booked_as_goods_receipts():
    db, _, _ = _setup()
    seen = []
    logi = LogisticsSim(db, OPERATOR, events=lambda b, kind, p: seen.append((kind, p)))
    _set_stock(db, LS.MILK_ID, 1000.0)
    logi.record([], T0)
    logi.record([], T0 + timedelta(seconds=LS.TANKER_EN_ROUTE_S + LS.TANKER_UNLOAD_S))
    receipts = [p for k, p in seen if k == "goods_receipt" and p["material_id"] == "milk"]
    assert receipts and receipts[0]["lot_no"].startswith("MLK-")


def test_every_raw_material_gets_its_own_supplier_truck():
    db, _, _ = _setup()
    logi = LogisticsSim(db, OPERATOR)
    sugar = db.dw_materials.find_one({"material_id": "sugar"})
    cap = sugar["reorder_level"] * LS.SILO_CAPACITY_X_REORDER
    for mid in ("milk", "sugar", "starch", "cocoa"):
        _set_stock(db, mid, 0.0)
    logi.record([], T0)
    vehicles = {i["material_id"]: i for i in logi.inbounds(T0)}
    assert set(vehicles) == {"milk", "sugar", "starch", "cocoa"}
    assert vehicles["sugar"]["vehicle"] == "bulk" and vehicles["cocoa"]["vehicle"] == "pallet"
    assert all(i["stage"] == "en_route" for i in vehicles.values())
    assert db.dw_materials.find_one({"material_id": "sugar"})["stock_qty"] == 0.0
    t = T0 + timedelta(seconds=LS.TANKER_EN_ROUTE_S)
    for _ in range(20):
        t += timedelta(seconds=30)
        logi.record([], t)
    assert db.dw_materials.find_one({"material_id": "sugar"})["stock_qty"] == cap
    assert {i["stage"] for i in logi.inbounds(t)} == {"gone"}
    assert logi.snapshot(t)["inbounds"]


def test_no_supply_without_db():
    logi = LogisticsSim(db=None, operator_id=OPERATOR)
    logi.record([], T0)
    assert logi.inbound(T0) is None


def test_snapshot_is_labelled_demo_sim(monkeypatch):
    db, sim, _ = _setup()
    logi = LogisticsSim(db, OPERATOR)
    snap = logi.snapshot(T0)
    assert snap["source"] == "demo-sim"
    assert {f["id"] for f in snap["forklifts"]} == {"HT-01", "HT-02"}
    assert snap["inbound"] is None or snap["inbound"]["kind"] == "melktankwagen"
    assert _hus(db, M.HU_SHIPPED) == []
