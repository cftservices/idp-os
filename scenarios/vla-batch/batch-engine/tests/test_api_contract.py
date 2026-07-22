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
