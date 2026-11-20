import os
from fastapi.testclient import TestClient

import app as appmod


def client():
    return TestClient(appmod.app)


def test_create_batch_returns_dose_setpoints_and_telemetry_summary():
    with client() as c:
        r = c.post("/api/v1/batches",
                   json={"recipe_id": "chocolate-vla-1L", "planned_L": 5000})
        assert r.status_code == 200
        body = r.json()
        assert body["dose_setpoints"] == {
            "milk": 5000.0, "sugar": 500.0, "starch": 250.0, "cocoa": 100.0}
        r2 = c.get(f"/api/v1/batches/{body['batch_id']}")
        ts = r2.json()["telemetry_summary"]
        assert set(ts) == {"peak_cook_temp_C", "hold_elapsed_sec",
                           "end_viscosity_cP", "packs_total", "reject_count"}


def test_order_batch_refused_by_cip_gate_returns_structured_detail():
    """POST /orders/{id}/batches on a Dirty cook unit must answer 400 with a
    detail the dashboard can render as text AND as a CIP button."""
    with client() as c:
        mon = appmod.STATE["equipment"]
        mon.ensure_meta("cook-unit-01")
        mon.db.dw_equipment_meta.update_one(
            {"equipment_id": "cook-unit-01"},
            {"$set": {"dirty": True, "batches_since_cip": 4}})
        order = c.post("/api/v1/orders", json={
            "recipe_id": "chocolate-vla-1L", "target_qty_L": 5000}).json()
        r = c.post(f"/api/v1/orders/{order['order_id']}/batches",
                   json={"operator_id": "OP-7"})
        assert r.status_code == 400
        d = r.json()["detail"]
        assert d["reason"] == "cip_required"
        assert d["equipment_id"] == "cook-unit-01"
        assert d["batches_since_cip"] == 4 and d["limit"] == 4
        assert "Dirty" in d["message"]
        assert d["action"]["path"] == "/api/v1/equipment/cook-unit-01/cip"

        # and the offered action really unblocks the order
        assert c.post(d["action"]["path"], json={"operator_id": "OP-7"}
                      ).status_code == 200
        assert c.post(f"/api/v1/orders/{order['order_id']}/batches",
                      json={"operator_id": "OP-7"}).status_code == 200


def test_admin_command_new_contract():
    with client() as c:
        r = c.post("/api/v1/admin/command", json={
            "equipment_id": "cook-unit-01", "cmd": "setpoint",
            "params": {"target": "cook.setpoint_C", "value": 88.0}})
        assert r.status_code == 200 and r.json()["accepted"] is True
        r2 = c.post("/api/v1/admin/command", json={
            "equipment_id": "Batch", "cmd": "fault",
            "params": {"fault_id": "cook_undertemp", "magnitude": 0.6}})
        assert r2.status_code == 200
        r3 = c.post("/api/v1/admin/command", json={
            "equipment_id": "Batch", "cmd": "unknown-cmd"})
        assert r3.status_code == 400


def test_stop_refused_when_active_batch_has_no_production():
    with client() as c:
        # AUTO_START=0 so the batch stays bookless and we can control its state
        original_auto_start = os.environ.get("AUTO_START", "1")
        os.environ["AUTO_START"] = "0"
        try:
            r = c.post("/api/v1/batches",
                       json={"recipe_id": "chocolate-vla-1L", "planned_L": 5000})
            bid = r.json()["batch_id"]
            # force the batch into an active state without booking production
            appmod.STATE["runner"].db.dw_batches.update_one(
                {"batch_id": bid}, {"$set": {"state": "COOKING"}})
            r2 = c.post("/api/v1/admin/command",
                        json={"equipment_id": "Batch", "cmd": "stop"})
            assert r2.status_code == 409
            assert "no production booked" in r2.json()["detail"]
        finally:
            os.environ["AUTO_START"] = original_auto_start


def test_ack_all_route_acks_open_alarms_and_rejects_unknown_priority():
    with client() as c:
        assert c.post("/api/v1/alarms/ack-all",
                      json={"priority": "urgent"}).status_code == 400
        r = c.post("/api/v1/alarms/ack-all", json={"operator_id": "OP-7"})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"acknowledged", "alarm_ids"}
        assert body["acknowledged"] == len(body["alarm_ids"])
        assert c.get("/api/v1/alarms", params={"state": "open"}).json() == []


def test_admin_start_creates_a_mes_batch_not_a_raw_factory_run():
    """The SCADA Start button: the run must exist in the MES, with an order,
    and pass the CIP gate like any other batch."""
    with client() as c:
        assert c.post("/api/v1/equipment/cook-unit-01/cip",
                      json={"operator_id": "OP-7"}).status_code == 200
        r = c.post("/api/v1/admin/command", json={
            "equipment_id": "Batch", "cmd": "start",
            "params": {"line": "L1", "operator_id": "OP-7"}})
        assert r.status_code == 200, r.text
        started = r.json()["opcua"]
        bid = started["batch_id"]
        batch = c.get(f"/api/v1/batches/{bid}").json()
        assert batch["line"] == "L1" and batch["order_id"] == started["order_id"]
        assert batch["created_by"] == "OP-7"

        # the CIP gate applies to Start as well
        mon = appmod.STATE["equipment"]
        mon.db.dw_equipment_meta.update_one(
            {"equipment_id": "cook-unit-01"}, {"$set": {"dirty": True}})
        r2 = c.post("/api/v1/admin/command", json={
            "equipment_id": "Batch", "cmd": "start", "params": {"line": "L1"}})
        assert r2.status_code == 400
        assert r2.json()["detail"]["reason"] == "cip_required"
        c.post("/api/v1/equipment/cook-unit-01/cip", json={"operator_id": "OP-7"})
