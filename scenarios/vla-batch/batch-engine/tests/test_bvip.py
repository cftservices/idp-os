"""BVIP KPI layer: mapping completeness, derivations against hand-computed fixtures, and the
TDengine writer's SQL. No TDengine, no Mongo: the in-memory store and a fake client."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from vla import bvip, kpi_history, model as M
from vla.bvip_map import BVIP_MAP, DEELS, NA, OK
from vla.db import get_db, seed_recipes

T0 = datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc)
ROLES = ("receiving", "process", "cook", "cooler", "filler")
CFG = bvip.Config(time_scale=15.0, ideal_packs_per_h={"L1": 10000.0, "L2": 10000.0},
                  value_per_pack=0.5, rework_cost_per_batch=600.0, density_kg_L=1.1,
                  labour_cost_per_hour=80.0,
                  parts_cost_per_breakdown={r: 100.0 for r in ROLES},
                  asset_replacement_value={r: 87600.0 for r in ROLES})
CAT = bvip.load_catalogue()


def iso(t):
    return t.isoformat()


# ------------------------------------------------------------------ mapping


def test_catalogue_has_the_29_industrial_kpis_and_each_is_mapped():
    ids = [k["id"] for k in CAT]
    assert len(ids) == 29 and len(set(ids)) == 29
    assert set(ids) == set(BVIP_MAP)
    assert {k["domein"] for k in CAT} == {"PRD", "QUA", "MNT", "ENE", "PAC"}


def test_every_kpi_without_full_source_names_what_is_missing():
    for k in CAT:
        spec = BVIP_MAP[k["id"]]
        assert spec["status"] in (OK, DEELS, NA)
        assert spec.get("bron"), k["id"]
        if spec["status"] == NA:
            assert spec.get("ontbreekt"), f"{k['id']} niet aantoonbaar zonder ontbrekende input"
            names = {i["naam"] for i in k["inputs"]}
            assert set(spec["ontbreekt"]) <= names, k["id"]
        if spec["status"] == DEELS:
            assert spec.get("ontbreekt") or spec.get("let_op"), k["id"]


def test_additive_formulas_only_use_arithmetic():
    for k in bvip.additive_kpis(CAT):
        # must parse with the restricted evaluator (no functions), or Grafana SQL breaks too
        bvip.evaluate(k["formule"], {i["naam"]: 1.0 for i in k["inputs"]})


# ------------------------------------------------------------------ derivations


def _db():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    return db


def _batch(db, bid, line, start_min, dur_min, good, rejects, verdict, recipe=None):
    s = T0 + timedelta(minutes=start_min)
    db.dw_batches.insert_one({
        "batch_id": bid, "line": line, "recipe_id": recipe or M.LINES[line].recipes[0],
        "started_at": iso(s), "completed_at": iso(s + timedelta(minutes=dur_min)),
        "packs_total": good, "reject_count": rejects, "verdict": verdict, "state": M.COMPLETE})


def _state(db, eq, minute, state):
    db.dw_equipment_state.insert_one({"equipment_id": eq, "state": state,
                                      "ts": iso(T0 + timedelta(minutes=minute))})


def test_inputs_and_kpis_over_a_window_match_hand_computation():
    db = _db()
    _batch(db, "B1", "L1", 0, 10, 900, 100, M.APPROVED)
    _batch(db, "B2", "L1", 5, 10, 800, 200, M.REJECTED)   # overlaps B1: run time is the union
    _batch(db, "B3", "L1", 30, 10, 950, 50, M.APPROVED)
    cook = M.LINES["L1"].eq("cook")
    _state(db, cook, 0, "Running")
    _state(db, cook, 20, "Down")
    _state(db, cook, 26, "Running")
    db.dw_work_orders.insert_one({"wo_id": "W1", "equipment_id": cook, "kind": "breakdown",
                                  "reported_at": iso(T0 + timedelta(minutes=20)), "status": "done"})
    db.dw_doses.insert_one({"batch_id": "B1", "material_id": "milk", "qty_actual": 1000.0})
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=60))
    v = bvip.inputs(snap, "L1", T0, T0 + timedelta(minutes=60), CFG)

    assert v["units_produced"] == 3000 and v["units_good"] == 1850 and v["units_defective"] == 350
    assert v["run_hours"] == pytest.approx((15 + 10) / 60 * 15)     # 0-15 and 30-40, x15
    assert v["planned_hours"] == pytest.approx(60 / 60 * 15)
    assert v["downtime_hours"] == pytest.approx(6 / 60 * 15)
    assert v["operating_hours"] == pytest.approx((20 + 34) / 60 * 15)
    assert v["failures"] == 1
    assert v["scrap_cost"] == pytest.approx(350 * 0.5)
    assert v["rework_cost"] == pytest.approx(600.0)
    assert v["waste_kg"] == pytest.approx((350 + 800) * 1.1)
    assert v["raw_material_kg"] == pytest.approx(1000.0)

    k = bvip.kpi_values(snap, "L1", T0, T0 + timedelta(minutes=60), CFG, CAT)
    run, plan = 25 / 60 * 15, 15.0
    assert k["KPI-PRD-001"]["value"] == pytest.approx(
        (run / plan) * (3000 / (run * 10000)) * (1850 / 3000) * 100)
    assert k["KPI-PRD-001"]["status"] == "OK"
    assert k["KPI-PRD-002"]["value"] == pytest.approx(1.5 / 15 * 100)
    assert k["KPI-QUA-001"]["value"] == pytest.approx(350 / 3000 * 100)
    assert k["KPI-MNT-001"]["value"] == pytest.approx(13.5)
    assert k["KPI-QUA-002"]["status"] == "DEELS"
    assert k["KPI-QUA-002"]["value"] == pytest.approx(175 + 600)
    assert k["KPI-ENE-001"] == {"value": None, "status": "NIET_AANTOONBAAR", "inputs": {}}


def test_no_failure_gives_no_mtbf_and_no_ideal_rate_gives_no_oee():
    db = _db()
    _batch(db, "B1", "L1", 0, 10, 900, 100, M.APPROVED)
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=30))
    cfg = bvip.Config(time_scale=15.0)
    k = bvip.kpi_values(snap, "L1", T0, T0 + timedelta(minutes=30), cfg, CAT)
    assert k["KPI-MNT-001"]["status"] == "UNSET" and k["KPI-MNT-001"]["value"] is None
    assert k["KPI-PRD-001"]["status"] == "UNSET"
    assert k["KPI-PRD-005"]["status"] == "OK"


def test_bucket_inputs_add_up_to_the_window():
    """The property the Grafana period panels rely on."""
    db = _db()
    for i in range(6):
        _batch(db, f"B{i}", "L1", i * 7, 6, 900, 100, M.APPROVED if i != 3 else M.REJECTED)
        _batch(db, f"C{i}", "L2", i * 7 + 2, 6, 950, 50, M.APPROVED)
    for eq in M.LINES["L1"].equipment_ids:
        _state(db, eq, 0, "Running")
    _state(db, M.LINES["L1"].eq("filler"), 13, "Down")
    _state(db, M.LINES["L1"].eq("filler"), 22, "Running")
    end = T0 + timedelta(minutes=45)
    snap = bvip.take_snapshot(db, now=end)
    whole = bvip.inputs(snap, bvip.PLANT, T0, end, CFG)
    parts = [bvip.inputs(snap, bvip.PLANT, T0 + timedelta(minutes=m),
                         T0 + timedelta(minutes=m + 5), CFG) for m in range(0, 45, 5)]
    for name, total in whole.items():
        assert sum(p[name] for p in parts) == pytest.approx(total), name


def test_a_batch_counts_in_the_bucket_where_it_completes():
    """Its verdict is only known at completion; counting it at the start would put a
    REJECTED batch in a bucket that was already written as good."""
    db = _db()
    _batch(db, "B1", "L1", 3, 4, 900, 100, M.REJECTED)       # 08:03 -> 08:07
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=10))
    first = bvip.inputs(snap, "L1", T0, T0 + timedelta(minutes=5), CFG)
    second = bvip.inputs(snap, "L1", T0 + timedelta(minutes=5), T0 + timedelta(minutes=10), CFG)
    assert first["units_produced"] == 0 and second["units_produced"] == 1000
    assert first["run_hours"] == pytest.approx(2 / 60 * 15)   # time is still clipped per bucket
    assert second["run_hours"] == pytest.approx(2 / 60 * 15)


def test_changeover_is_the_gap_before_a_batch_with_another_recipe():
    db = _db()
    r = M.LINES["L2"].recipes
    _batch(db, "C1", "L2", 0, 10, 900, 100, M.APPROVED, recipe=r[0])
    _batch(db, "C2", "L2", 12, 10, 900, 100, M.APPROVED, recipe=r[0])   # same recipe: no changeover
    _batch(db, "C3", "L2", 30, 10, 900, 100, M.APPROVED, recipe=r[1])   # 22 -> 30: 8 min
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=60))
    v = bvip.inputs(snap, "L2", T0, T0 + timedelta(minutes=60), CFG)
    assert v["changeover_hours"] == pytest.approx(8 / 60 * 15)
    k = bvip.kpi_values(snap, "L2", T0, T0 + timedelta(minutes=60), CFG, CAT)
    assert k["KPI-PRD-007"] == {"value": pytest.approx(2.0), "status": "OK",
                                "inputs": {"changeover_hours": pytest.approx(2.0)}}


def test_repeat_failures_and_maintenance_cost():
    db = _db()
    _batch(db, "B1", "L1", 0, 10, 900, 100, M.APPROVED)
    cook, filler = M.LINES["L1"].eq("cook"), M.LINES["L1"].eq("filler")

    def wo(eq, minute, cause, repair_s=240):
        db.dw_work_orders.insert_one({"wo_id": f"W{eq}{minute}", "equipment_id": eq,
                                      "kind": "breakdown", "cause": cause, "repair_s": repair_s,
                                      "reported_at": iso(T0 + timedelta(minutes=minute))})
    wo(cook, 5, "klep")
    wo(cook, 20, "klep")        # same machine, same cause, 15 min later: a repeat
    wo(cook, 30, "voeler")      # other cause: not a repeat
    wo(filler, 40, "klep")      # other machine: not a repeat
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=60))
    v = bvip.inputs(snap, "L1", T0, T0 + timedelta(minutes=60), CFG)
    assert (v["failures"], v["repeat_failures"]) == (4.0, 1.0)
    # 4 x (240 s x 15 / 3600 h x 80 EUR/h + 100 EUR parts)
    assert v["maintenance_costs"] == pytest.approx(4 * (1.0 * 80 + 100))
    # 5 machines x 87600 EUR x 15 process hours / 8760
    assert v["asset_replacement_value"] == pytest.approx(5 * 87600 * 15 / 8760)
    k = bvip.kpi_values(snap, "L1", T0, T0 + timedelta(minutes=60), CFG, CAT)
    assert k["KPI-MNT-003"]["value"] == pytest.approx(25.0)
    assert k["KPI-MNT-002"]["status"] == "DEELS"
    assert k["KPI-MNT-002"]["value"] == pytest.approx(720 / 750 * 100)


def test_maintenance_cost_without_cost_model_is_unset_not_zero():
    db = _db()
    _batch(db, "B1", "L1", 0, 10, 900, 100, M.APPROVED)
    db.dw_work_orders.insert_one({"wo_id": "W1", "equipment_id": M.LINES["L1"].eq("cook"),
                                  "kind": "breakdown", "cause": "x", "repair_s": 240,
                                  "reported_at": iso(T0 + timedelta(minutes=5))})
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=30))
    k = bvip.kpi_values(snap, "L1", T0, T0 + timedelta(minutes=30), bvip.Config(time_scale=15.0), CAT)
    assert k["KPI-MNT-004"] == {"value": None, "status": "UNSET",
                                "inputs": {"maintenance_costs": None, "units_produced": 1000.0}}


def test_orders_on_time_only_counts_orders_whose_due_date_passed():
    db = _db()
    _batch(db, "B1", "L1", 0, 10, 900, 100, M.APPROVED)
    rid = M.LINES["L1"].recipes[0]
    db.dw_orders.insert_one({"order_id": "O1", "recipe_id": rid, "status": M.ORDER_DONE,
                             "due_date": iso(T0 + timedelta(minutes=20)),
                             "completed_at": iso(T0 + timedelta(minutes=15))})
    db.dw_orders.insert_one({"order_id": "O2", "recipe_id": rid, "status": M.ORDER_DONE,
                             "due_date": iso(T0 + timedelta(minutes=25)),
                             "completed_at": iso(T0 + timedelta(minutes=40))})
    db.dw_orders.insert_one({"order_id": "O3", "recipe_id": rid, "status": "RUNNING",
                             "due_date": iso(T0 + timedelta(minutes=90))})   # not due yet
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=60))
    v = bvip.inputs(snap, "L1", T0, T0 + timedelta(hours=2), CFG)
    assert (v["orders_on_time"], v["orders_planned"]) == (1.0, 2.0)


def test_run_length_between_cip_uses_productive_time_of_the_cook_unit():
    db = _db()
    _batch(db, "B1", "L1", 0, 10, 900, 100, M.APPROVED)
    cook = M.LINES["L1"].eq("cook")
    _state(db, cook, 0, "Running")
    _state(db, cook, 40, "Idle")
    for m in (0, 40, 60):
        db.dw_batch_events.insert_one({"batch_id": None, "event_type": "cip_performed",
                                       "payload": {"equipment_id": cook},
                                       "ts": iso(T0 + timedelta(minutes=m))})
    snap = bvip.take_snapshot(db, now=T0 + timedelta(minutes=70))
    cycles = bvip.run_hours_between_cip(snap, "L1", T0, T0 + timedelta(minutes=70), CFG)
    assert cycles == pytest.approx([40 / 60 * 15, 0.0])


def test_evaluator_rejects_function_calls_and_handles_zero_division():
    with pytest.raises(ValueError):
        bvip.evaluate("__import__('os')", {})
    assert bvip.evaluate("a / b * 100", {"a": 1.0, "b": 0.0}) is None
    assert bvip.evaluate("a / b * 100", {"a": 1.0, "b": None}) is None
    assert bvip.evaluate("a + b", {"a": 1.0, "b": 2.0}) == 3.0


# ------------------------------------------------------------------ writer


class FakeTd:
    db = "idp"

    def __init__(self, fail_first: int = 0):
        self.statements: list[str] = []
        self.fail = fail_first

    def sql(self, s):
        if self.fail:
            self.fail -= 1
            raise OSError("connection refused")
        self.statements.append(s)
        return {"code": 0}


def test_writer_creates_schema_and_writes_null_for_not_demonstrable():
    db = _db()
    _batch(db, "B1", "L1", 0, 10, 900, 100, M.APPROVED)
    td = FakeTd()
    w = kpi_history.KpiWriter(db, td=td, cfg_factory=lambda: CFG,
                              now=lambda: T0 + timedelta(minutes=17))
    w.ensure_schema()
    assert any("CREATE STABLE IF NOT EXISTS idp.kpi " in s for s in td.statements)
    n = w.tick()
    sql = " ".join(td.statements)
    assert n > 0 and sql.count("INSERT INTO") >= 1
    assert "kpi_l1_ene_001 USING idp.kpi TAGS ('KPI-ENE-001'" in sql
    end_ms = int((T0 + timedelta(minutes=15)).timestamp() * 1000)     # 08:15 bucket end
    assert f"VALUES ({end_ms}, NULL, 'NIET_AANTOONBAAR')" in sql
    assert "kpi_plant_prd_001" in sql and "kpin_l1_prd_001_run_hours" in sql
    assert "other_quality_cost" not in sql       # a missing input is never written as 0
    assert "throughput_rate" in sql              # engine_key tag for model thresholds


def test_writer_survives_tdengine_being_down():
    db = _db()
    w = kpi_history.KpiWriter(db, td=FakeTd(fail_first=10), cfg_factory=lambda: CFG,
                              now=lambda: T0)
    with pytest.raises(OSError):
        w.ensure_schema()
    assert w.status["schema"] is False


def test_statements_are_chunked(monkeypatch):
    monkeypatch.setattr(kpi_history, "MAX_SQL_BYTES", 20_000)
    rows = kpi_history.Rows("idp")
    for i in range(1000):
        rows.input(T0, "KPI-PRD-001", "L1", f"x{i}", "h", 1.0)
    stmts = rows.statements()
    assert len(stmts) > 1 and all(len(s) <= 20_000 + 500 for s in stmts)
    assert sum(s.count(" USING ") for s in stmts) == 1000
