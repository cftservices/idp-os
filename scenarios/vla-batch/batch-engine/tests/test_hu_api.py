from fastapi.testclient import TestClient

import app as appmod


def test_hu_endpoints_roundtrip_and_gate():
    with TestClient(appmod.app) as c:
        r = c.post("/api/v1/batches",
                   json={"recipe_id": "chocolate-vla-1L", "planned_L": 5000})
        bid = r.json()["batch_id"]
        # AUTO_START default runs the batch to COMPLETE synchronously offline;
        # verdict is APPROVED with fabricated healthy telemetry
        b = c.get(f"/api/v1/batches/{bid}").json()
        assert b["verdict"] == "APPROVED"
        r2 = c.post("/api/v1/hu", json={"batch_id": bid, "packs_count": 1200,
                                        "operator_id": "OP-7"})
        assert r2.status_code == 200
        hu_id = r2.json()["hu_id"]
        assert len(hu_id) == 18 and hu_id.startswith("80")
        r3 = c.post(f"/api/v1/hu/{hu_id}/putaway", json={"operator_id": "OP-7"})
        assert r3.json()["status"] == "awaiting_shipment"
        r4 = c.post(f"/api/v1/hu/{hu_id}/ship", json={"operator_id": "OP-7"})
        assert r4.json()["status"] == "shipped"
        r5 = c.get(f"/api/v1/hu?batch_id={bid}")
        assert len(r5.json()) == 1
        # gate: unknown HU -> 404 with reason
        r6 = c.post("/api/v1/hu/800000000000000000/putaway", json={})
        assert r6.status_code == 404
        assert r6.json()["detail"]["reason"] == "unknown"
