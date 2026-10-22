"""Grip op je fabriek via de datalaag: context, kwaliteit, stilstand,
onderhoud, euro's per machine, oorzaak en de read-only MCP-ingang."""

import random
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app as appmod
from vla import datalayer as DL, mcp as MCP, model as M, rca
from vla.autopilot import Autopilot
from vla.batches import BatchRunner
from vla.cmms import CmmsConnector, reliability
from vla.db import get_db, seed_recipes
from vla.equipment import EquipmentMonitor
from vla.losses import losses_per_equipment

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}


class FakeBus:
    def __init__(self, rows, connected=True):
        self.rows = rows
        self.connected = connected
        self.published = []

    def snapshot_verbose(self):
        return dict(self.rows)

    def publish_json(self, topic, payload):
        self.published.append((topic, payload))
        return True


def _bus_with(eq, age=1.0, quality="GOOD"):
    ln = M.get_line(M.line_of_equipment(eq))
    pre = f"{ln.uns_root}/{M.area_of(eq)}/{eq}/Status/"
    return {pre + "temperature_C": {"value": 80.0, "quality": quality,
                                    "age_s": age, "retained": False, "unit": "C"},
            pre + "state": {"value": "Running", "quality": "GOOD",
                            "age_s": age, "retained": False}}


def setup(n=0):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(46), equipment=mon)
    for _ in range(n):
        b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=TELEM_OK)
    return db, mon, runner


# ------------------------------------------------------------------ datalaag

def test_context_has_isa95_path_raw_source_and_quality():
    db, mon, _ = setup()
    buses = {"L1": FakeBus(_bus_with("cook-unit-01")), "L2": FakeBus({})}
    ctx = DL.context("cook-unit-01", buses, db, mon, None)
    assert [p["level"] for p in ctx["isa95_path"]] == \
        ["Enterprise", "Site", "Line", "Area", "Equipment"]
    temp = next(t for t in ctx["topics"] if t["tag"] == "temperature_C")
    assert temp["source"] == "OPC-UA"
    assert temp["raw"]["node_id"].endswith(".cook-unit-01.temperature_C")
    state = next(t for t in ctx["topics"] if t["tag"] == "state")
    assert state["source"] == "MES" and state["raw"] is None
    assert ctx["quality"] == "GOOD"


def test_stale_and_dropout_make_quality_visible():
    buses = {"L1": FakeBus(_bus_with("cook-unit-01", age=500)), "L2": FakeBus({})}
    q = {e["equipment_id"]: e for e in DL.quality(buses)["equipment"]}
    assert q["cook-unit-01"]["quality"] == "STALE"
    assert q["filler-01"]["quality"] == "NO_DATA"
    fresh = {"L1": FakeBus(_bus_with("cook-unit-01")), "L2": FakeBus({})}
    DL.set_dropout("cook-unit-01", 60)
    try:
        q = {e["equipment_id"]: e for e in DL.quality(fresh)["equipment"]}
        # Alleen de sensorwaarde valt uit; de MES-toestand blijft GOOD.
        assert q["cook-unit-01"]["counts"] == {"GOOD": 1, "STALE": 0, "BAD": 1}
        assert q["cook-unit-01"]["quality"] == "BAD"
    finally:
        DL.clear_dropout()
    assert DL.active_dropouts() == {}


def test_layer_map_lists_sources_and_services():
    db, _, _ = setup()
    buses = {"L1": FakeBus(_bus_with("cook-unit-01")), "L2": FakeBus({}, False)}
    m = DL.layer_map(buses, db, {"cmms": True, "agent": True, "logistics": False})
    ids = {s["id"] for s in m["sources"]}
    assert {"opcua-l1", "opcua-l2", "mes", "warehouse"} <= ids
    l1 = next(s for s in m["sources"] if s["id"] == "opcua-l1")
    assert l1["topics"] == 2 and l1["good"] == 2 and l1["status"] == "ok"
    svc = {s["id"]: s["status"] for s in m["services"]}
    assert svc["cmms"] == "ok" and svc["documents"] == "ok"
    # Geen bron- of brokernaam in wat de UI toont.
    text = str(m).lower()
    for word in ("uns", "monstermq", "mqtt"):
        assert word not in text


# ------------------------------------------------------------------ stilstand

def test_breakdown_writes_downtime_and_autopilot_waits_for_repair():
    db, mon, runner = setup(1)
    cmms = CmmsConnector(db, mon, rng=random.Random(1))
    wo = cmms.report_breakdown("cook-unit-01", repair_s=60)
    assert mon.is_down("cook-unit-01")
    assert mon.down_info("cook-unit-01")["work_order_id"] == wo["wo_id"]
    snap = {r["equipment_id"]: r for r in mon.snapshot("L1")}
    assert snap["cook-unit-01"]["state"] == "Down"
    pilot = Autopilot(db, runner, equipment=mon, line="L1",
                      recipe_id="chocolate-vla-1L", gap_s=0)
    assert pilot.step().startswith("wait_repair")
    # Een tweede melding op dezelfde machine geeft dezelfde werkorder.
    assert cmms.report_breakdown("cook-unit-01")["wo_id"] == wo["wo_id"]


def test_work_order_lifecycle_releases_machine_and_gives_mttr():
    db, mon, _ = setup(1)
    cmms = CmmsConnector(db, mon, rng=random.Random(2))
    wo = cmms.report_breakdown("filler-01", repair_s=60)
    t0 = datetime.fromisoformat(wo["reported_at"])
    cmms.tick(t0 + timedelta(seconds=25))
    w = cmms.list(equipment_id="filler-01")[0]
    assert w["status"] == "assigned" and w["technician"]["id"]
    cmms.tick(t0 + timedelta(seconds=55))
    assert cmms.list(equipment_id="filler-01")[0]["status"] == "in_progress"
    started = datetime.fromisoformat(cmms.list(equipment_id="filler-01")[0]["started_at"])
    cmms.tick(started + timedelta(seconds=61))
    w = cmms.list(equipment_id="filler-01")[0]
    assert w["status"] == "done"
    assert not mon.is_down("filler-01")
    rel = {r["equipment_id"]: r for r in reliability(db, mon, "L1")["equipment"]}
    f = rel["filler-01"]
    assert f["failures"] == 1 and f["open_failures"] == 0
    assert f["mttr_min"] is not None and f["mttr_min"] >= 0  # wandklok, zie ADET
    assert f["adet_h"] >= 0  # wandklok: in de test slechts microseconden


def test_cbm_alert_gets_preventive_work_order_closed_by_cip():
    db, mon, _ = setup(3)  # derde batch geeft het fouling-alert
    cmms = CmmsConnector(db, mon)
    cmms.tick()
    cbm = [w for w in cmms.list() if w["kind"] == "cbm"]
    assert len(cbm) == 1 and cbm[0]["status"] == "reported"
    assert not mon.is_down("cook-unit-01")  # preventief zet niets stil
    mon.perform_cip("cook-unit-01", operator_id="OP-1")
    cmms.tick()
    assert cmms.list(equipment_id="cook-unit-01")[0]["status"] == "done"


def test_work_order_status_published_on_line_bus():
    db, mon, _ = setup()
    bus = FakeBus({})
    cmms = CmmsConnector(db, mon, buses={"L1": bus, "L2": FakeBus({})})
    cmms.report_breakdown("cooler-01", repair_s=60)
    topics = [t for t, _ in bus.published]
    assert any(t.endswith("cooler-01/Status/work_order") for t in topics)
    assert any(t.endswith("cooler-01/Status/maintenance_state") for t in topics)


# ------------------------------------------------------------------ euro's

def test_losses_per_equipment_attributes_scrap_and_downtime():
    db, mon, _ = setup(2)
    mon.set_down("process-tank-01", "test")
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    end = datetime.now(timezone.utc) + timedelta(seconds=5)
    cost = {"currency": "EUR", "value_per_pack": 0.62,
            "cost_per_downtime_hour": 450, "cost_per_kg_material": {},
            "rework_cost_per_batch": 620}
    out = losses_per_equipment(db, mon, start, end, cost, line="L1")
    rows = {r["equipment_id"]: r for r in out["equipment"]}
    scrap = [i for i in rows["filler-01"]["items"] if i["category"] == "scrap"]
    assert scrap and scrap[0]["amount"] == round(40 * 0.62, 2)
    assert {"category": "material_overdose",
            "reason": "cost_per_kg_material ontbreekt"} in out["omitted"]
    assert out["currency"] == "EUR"


def test_losses_without_cost_model_is_not_zero():
    db, mon, _ = setup(1)
    out = losses_per_equipment(db, mon, datetime.now(timezone.utc) - timedelta(hours=1),
                               datetime.now(timezone.utc), None)
    assert out["total_causal"] is None and out["omitted"]


# ------------------------------------------------------------------ oorzaak

def test_cause_chain_for_cbm_alert():
    db, mon, _ = setup(3)
    alert = mon.open_alerts("cook-unit-01")[0]
    out = rca.cause(db, alert["alert_id"], cost_model={"rework_cost_per_batch": 620})
    steps = [c["step"] for c in out["chain"]]
    assert steps[0] == "fouling" and "heatup" in steps and "action" in steps
    assert out["focus_equipment"] == "cook-unit-01"
    assert len(out["signals"]["heatup_trend"]) == 3
    assert rca.cause(db, "A-NOPE") is None


# ------------------------------------------------------------------ MCP

def test_mcp_registry_refuses_write_tools():
    reg = MCP.ToolRegistry()
    with pytest.raises(ValueError):
        reg.register("set_setpoint", "x", {}, lambda a: None, read_only=False)


def test_mcp_protocol_basics():
    reg = MCP.ToolRegistry()
    reg.register("echo", "echo", {"type": "object"}, lambda a: {"got": a})
    init = MCP.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {}}, reg)
    assert init["result"]["capabilities"]["tools"]
    assert MCP.handle({"jsonrpc": "2.0", "method": "notifications/initialized"},
                      reg) is None
    lst = MCP.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, reg)
    assert lst["result"]["tools"][0]["annotations"]["readOnlyHint"] is True
    call = MCP.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "echo", "arguments": {"x": 1}}}, reg)
    assert call["result"]["structuredContent"] == {"got": {"x": 1}}
    bad = MCP.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                      "params": {"name": "nope"}}, reg)
    assert bad["error"]["code"] == -32602


# ------------------------------------------------------------------ API

def test_api_datalayer_cmms_mcp_end_to_end():
    with TestClient(appmod.app) as c:
        m = c.get("/api/v1/datalayer/map")
        assert m.status_code == 200 and m.json()["datalayer"]["name"] == "De datalaag"
        ctx = c.get("/api/v1/datalayer/context/cook-unit-02")
        assert ctx.status_code == 200 and ctx.json()["line"] == "L2"
        assert c.get("/api/v1/datalayer/context/nope").status_code == 404
        assert c.post("/api/v1/datalayer/dropout/filler-01?seconds=30").status_code == 200
        assert c.delete("/api/v1/datalayer/dropout/all").json()["cleared"] == 1

        b = c.post("/api/v1/equipment/cooler-02/breakdown", json={"repair_s": 60})
        assert b.status_code == 200 and b.json()["kind"] == "breakdown"
        wos = c.get("/api/v1/cmms/work-orders?open_only=true").json()
        assert any(w["equipment_id"] == "cooler-02" for w in wos["work_orders"])
        rel = c.get("/api/v1/reliability?line=L2").json()
        cooler = next(r for r in rel["equipment"] if r["equipment_id"] == "cooler-02")
        assert cooler["failures"] == 1 and cooler["down"]
        assert c.get("/api/v1/losses/equipment?hours=2").status_code == 200

        tools = c.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1,
                                            "method": "tools/list"}).json()
        names = {t["name"] for t in tools["result"]["tools"]}
        assert {"get_context", "get_losses", "list_work_orders",
                "get_quality", "get_reliability"} <= names
        r = c.post("/api/v1/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "list_work_orders",
                       "arguments": {"equipment_id": "cooler-02"}}}).json()
        assert r["result"]["structuredContent"]["work_orders"][0]["equipment_id"] \
            == "cooler-02"
        assert c.post("/api/v1/mcp", json={"jsonrpc": "2.0",
                                           "method": "notifications/initialized"}
                      ).status_code == 202

        ops = c.get("/api/v1/dataops").json()
        assert {r["id"] for r in ops["rules"]} >= {"DQ-01", "DQ-02", "DQ-03"}
        assert c.get("/api/v1/system/resources").json()["available"] is False
        assert c.get("/api/v1/alarms/A-NOPE/cause").status_code == 404


def test_ai_summary_runs_in_background_and_is_cached(monkeypatch):
    import time
    from vla import rca as R
    calls = []

    def fake(facts, url, model, timeout):
        calls.append(facts)
        return {"model": model, "text": "Vervuiling gaf een te lage viscositeit."}

    monkeypatch.setattr(R, "_ai_summary", fake)
    R._ai_cache.clear()
    out = {"alarm": {"equipment_id": "cook-unit-01", "message": "visc laag"},
           "chain": [{"step": "fouling", "label": "Vervuiling", "evidence": "x"},
                     {"step": "work_order", "label": "WO-1", "evidence": "a"},
                     {"step": "work_order", "label": "WO-2", "evidence": "b"}]}
    first = R.ai_summary_async(out, "http://x", "m")
    assert first["pending"] is True and first["text"] is None
    for _ in range(50):
        if R._ai_cache:
            break
        time.sleep(0.02)
    second = R.ai_summary_async(out, "http://x", "m")
    assert second["text"].startswith("Vervuiling")
    assert len(calls) == 1 and "WO-2" not in calls[0]
