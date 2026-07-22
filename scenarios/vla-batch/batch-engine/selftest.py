"""Offline self-test for the Vla batch-engine. No broker / no Mongo required.

Verifies:
  1. model + recipe seed (chocolate-vla-1L doses + scaling)
  2. viscosity physics (normal -> in-spec, cook_undertemp -> out-of-spec)
  3. BatchRunner end-to-end normal run -> APPROVED (doses booked, samples, packs)
  4. BatchRunner cook_undertemp run -> low viscosity -> HOLD/REJECTED (Solve)
  5. JSON report assembles (header + doses + peak_temp/hold/viscosity + packs + verdict)
  6. PDF report renders to %PDF bytes
  7. `import app` succeeds without a broker + verdict-rule assertion
  8. OPC-UA control path is offline-safe (no factory -> status, no exception)
  9. orders + scan-flow end-to-end (fase 1: gate/label/weigh/report/close)
  10. stop rule (close w/o production) + scan rejection
  11. HU flow e2e (fase 2: wrap/putaway/ship, APPROVED-gate, report traceability)

Run: python selftest.py   (exit 0 = all pass)
"""

from __future__ import annotations

import random
import sys

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


# --- 1. model + recipe seed ---
try:
    from vla import model as M

    r = M.get_recipe("chocolate-vla-1L")
    dmap = {d.material_id: d.qty_target for d in r.doses}
    seed_ok = (
        r is not None
        and dmap == {"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "cocoa": 100.0}
        and r.cook_setpoint_C == 88.0 and r.hold_sec == 300.0
        and r.cool_target_C == 22.0
        and r.spec_min_cP == 150.0 and r.spec_max_cP == 300.0
    )
    # scale to 2500 L (half basis) -> milk 2500
    scaled = {d.material_id: d.qty_target for d in r.scaled_doses(2500)}
    scale_ok = abs(scaled["milk"] - 2500.0) < 1e-6 and abs(scaled["cocoa"] - 50.0) < 1e-6
    check("1. recipe seed chocolate-vla-1L + dose scaling",
          seed_ok and scale_ok,
          f"doses={dmap} scaled(2500L).milk={scaled['milk']}")
except Exception as e:
    import traceback
    check("1. model + recipe seed", False, f"exception: {e}\n{traceback.format_exc()}")


# --- 2. viscosity physics ---
try:
    from vla.model import physics_viscosity

    normal = physics_viscosity(88.0, 300.0, 300.0)          # full gelatinisation ~260
    undertemp = physics_viscosity(72.0, 300.0, 300.0)       # low peak -> low viscosity
    phys_ok = (
        abs(normal - 260.0) < 1e-6            # 30 + 1.0*230 = 260
        and 150.0 <= normal <= 300.0          # in-spec
        and undertemp < 150.0                 # out-of-spec (below spec_min)
    )
    check("2. viscosity physics (normal in-spec, undertemp out-of-spec)",
          phys_ok, f"normal={normal} cP undertemp(72C)={undertemp} cP")
except Exception as e:
    check("2. viscosity physics", False, f"exception: {e}")


# --- 3. normal run -> APPROVED ---
try:
    from vla.db import get_db, seed_recipes
    from vla.batches import BatchRunner

    db = get_db()  # in-memory (no MONGO_URL)
    seed_recipes(db)
    runner = BatchRunner(db, bus=None, rng=random.Random(42))

    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    bid = b["batch_id"]
    # normal telemetry: full cook + hold, clean doses, packs
    telem_ok = {
        "peak_cook_temp_C": 88.0,
        "hold_elapsed_sec": 300.0,
        "packs_total": 4980,
        "reject_count": 20,
        "dose_actuals": {"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "cocoa": 100.0},
    }
    res = runner.start_batch(bid, telemetry=telem_ok)
    doses_booked = all(d["qty_actual"] is not None for d in res["doses"])
    n_samples = len(res["samples"])
    approved = res["verdict"] == "APPROVED"
    state_ok = res["state"] == "COMPLETE"
    visc = res["end_viscosity_cP"]
    check("3. normal run end-to-end -> APPROVED",
          approved and state_ok and doses_booked and n_samples == 4
          and res["packs_total"] == 4980 and 150.0 <= visc <= 300.0,
          f"verdict={res['verdict']} state={res['state']} visc={visc} "
          f"packs={res['packs_total']} samples={n_samples} doses_booked={doses_booked}")
except Exception as e:
    import traceback
    check("3. normal run -> APPROVED", False,
          f"exception: {e}\n{traceback.format_exc()}")


# --- 4. cook_undertemp -> low viscosity -> HOLD/REJECTED (Solve) ---
try:
    db2 = get_db()
    seed_recipes(db2)
    runner2 = BatchRunner(db2, bus=None, rng=random.Random(7))
    b2 = runner2.create_batch("chocolate-vla-1L", planned_L=5000)
    bid2 = b2["batch_id"]
    # cook_undertemp magnitude 0.6 -> peak ≈ 70 + 0.4*18 = 77.2 C -> visc < 150
    telem_bad = {"fault": "cook_undertemp", "magnitude": 0.6,
                 "hold_elapsed_sec": 300.0, "packs_total": 4900, "reject_count": 100}
    res2 = runner2.start_batch(bid2, telemetry=telem_bad)
    visc2 = res2["end_viscosity_cP"]
    verdict2 = res2["verdict"]
    crit = any(a["severity"] == "Critical" for a in res2["alarms"])
    # verdict-rule: end_viscosity < spec_min -> REJECTED (or HOLD if not critical)
    ok = (
        visc2 < 150.0
        and verdict2 in ("REJECTED", "HOLD")
        and crit  # out-of-spec viscosity raised a CRITICAL Solve alarm
        and res2["critical_alarm_during_batch"]
    )
    check("4. cook_undertemp -> low viscosity -> HOLD/REJECTED (Solve)",
          ok, f"peak={res2['peak_cook_temp_C']}C visc={visc2}cP verdict={verdict2} "
              f"critical_alarm={crit}")
except Exception as e:
    import traceback
    check("4. cook_undertemp -> Solve", False,
          f"exception: {e}\n{traceback.format_exc()}")


# --- 5. JSON report ---
try:
    from vla.report import render_json

    rep = render_json(runner.get_batch(bid))
    rep_ok = (
        rep["header"]["batch_id"] == bid
        and len(rep["doses"]) == 4
        and rep["cook"]["peak_cook_temp_C"] is not None
        and rep["cook"]["hold_sec"] == 300.0
        and rep["quality"]["end_viscosity_cP"] is not None
        and "packs_total" in rep["packs"]
        and rep["verdict"] == "APPROVED"
    )
    check("5. JSON report (header+doses+peak/hold/viscosity+packs+verdict)",
          rep_ok, f"batch={rep['header']['batch_id']} doses={len(rep['doses'])} "
                  f"verdict={rep['verdict']}")
except Exception as e:
    import traceback
    check("5. JSON report", False, f"exception: {e}\n{traceback.format_exc()}")


# --- 6. PDF report ---
try:
    from vla.report import render_pdf

    pdf = render_pdf(runner.get_batch(bid))
    pdf_ok = isinstance(pdf, (bytes, bytearray)) and pdf[:4] == b"%PDF" and len(pdf) > 800
    check("6. PDF report renders (%PDF via reportlab)",
          pdf_ok, f"bytes={len(pdf)} magic={pdf[:4]!r}")
except Exception as e:
    import traceback
    check("6. PDF report", False, f"exception: {e}\n{traceback.format_exc()}")


# --- 7. import app + verdict-rule assertion ---
try:
    import app as _app  # noqa: F401
    # verdict-rule cross-check: out-of-spec viscosity must NOT be APPROVED
    bad_batch = runner2.get_batch(bid2)
    rule_ok = (
        hasattr(_app, "app")
        and bad_batch["verdict"] != "APPROVED"
        and bad_batch["end_viscosity_cP"] < bad_batch["spec_min_cP"]
    )
    check("7. import app + verdict-rule (out-of-spec != APPROVED)",
          rule_ok, f"app_ok={hasattr(_app, 'app')} bad_verdict={bad_batch['verdict']}")
except Exception as e:
    import traceback
    check("7. import app + verdict-rule", False,
          f"exception: {e}\n{traceback.format_exc()}")


# --- 8. OPC-UA control path is offline-safe (no factory -> no exception) ---
try:
    from vla.opcua_control import OpcuaControl

    # point at an unreachable endpoint, tiny timeout, no retries -> fast no-op
    ctl = OpcuaControl(url="opc.tcp://127.0.0.1:1/DairyWorks",
                       connect_timeout_s=0.3, retries=0, backoff_s=0.0)
    r_start = ctl.start_batch("chocolate-vla-1L")
    r_sp = ctl.set_setpoint("cook.setpoint_C", 88.0)
    r_sample = ctl.take_sample("viscosity")
    r_fault = ctl.inject_fault("cook_undertemp", 0.6)
    r_clear = ctl.clear_fault()
    r_stop = ctl.stop()
    all_returned_status = all(
        isinstance(r, dict) and r.get("connected") is False and r.get("accepted") is False
        for r in (r_start, r_sp, r_sample, r_fault, r_clear, r_stop)
    )

    # a full batch run WITH a (dead) control still completes + verdicts APPROVED
    db3 = get_db()
    seed_recipes(db3)
    runner3 = BatchRunner(db3, bus=None, control=ctl, rng=random.Random(11))
    b3 = runner3.create_batch("chocolate-vla-1L", planned_L=5000)
    res3 = runner3.start_batch(b3["batch_id"], telemetry={
        "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
        "packs_total": 4990, "reject_count": 10,
    })
    run_ok = res3["state"] == "COMPLETE" and res3["verdict"] == "APPROVED"

    check("8. OPC-UA control offline-safe (no factory -> status, no exception)",
          all_returned_status and run_ok,
          f"start.connected={r_start.get('connected')} run_state={res3['state']} "
          f"run_verdict={res3['verdict']}")
except Exception as e:
    import traceback
    check("8. OPC-UA control offline-safe", False,
          f"exception: {e}\n{traceback.format_exc()}")


# --- 9. orders + scan-flow end-to-end (fase 1) ---
try:
    from vla.orders import OrderManager
    from vla.scan import ScanFlow, ScanRejected

    db9 = get_db()
    seed_recipes(db9)
    orders9 = OrderManager(db9, bus=None)
    runner9 = BatchRunner(db9, bus=None, rng=random.Random(21), orders=orders9)
    flow9 = ScanFlow(db9, runner9, orders9)

    o9 = orders9.create_order("chocolate-vla-1L", target_qty_L=5000)
    b9 = runner9.create_batch("chocolate-vla-1L", planned_L=5000,
                              order_id=o9["order_id"])
    gate = flow9.scan_order(o9["order_id"], operator_id="OP-7")
    flow9.scan_label(b9["batch_id"], "cocoa", lot_no="L-1", operator_id="OP-7")
    flow9.weigh(b9["batch_id"], "cocoa", total=True, lot_no="L-1",
                operator_id="OP-7")
    flow9.scan_report(b9["batch_id"], operator_id="OP-7")
    runner9.start_batch(b9["batch_id"], telemetry={
        "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
        "packs_total": 4980, "reject_count": 20,
        "dose_actuals": {"milk": 5000.0, "sugar": 500.0, "starch": 250.0}})
    closed = orders9.close_order(o9["order_id"])
    dose9 = db9.dw_doses.find_one({"batch_id": b9["batch_id"],
                                   "material_id": "cocoa"})
    check("9. orders + scan-flow e2e (gate/label/weigh/report/close)",
          gate["ok"] and closed["status"] == "DONE"
          and dose9["qty_actual"] == 100.0 and dose9["operator_id"] == "OP-7",
          f"order={closed['status']} cocoa_actual={dose9['qty_actual']}")
except Exception as e:
    import traceback
    check("9. orders + scan-flow e2e", False,
          f"exception: {e}\n{traceback.format_exc()}")


# --- 10. stop rule + scan rejections ---
try:
    db10 = get_db()
    seed_recipes(db10)
    orders10 = OrderManager(db10, bus=None)
    runner10 = BatchRunner(db10, bus=None, rng=random.Random(22), orders=orders10)
    flow10 = ScanFlow(db10, runner10, orders10)
    o10 = orders10.create_order("chocolate-vla-1L", target_qty_L=5000)
    try:
        orders10.close_order(o10["order_id"])
        stop_rule_ok = False
    except ValueError:
        stop_rule_ok = True
    try:
        flow10.scan_order("PO-NOPE", operator_id="OP-7")
        reject_ok = False
    except ScanRejected as ex:
        reject_ok = ex.reason == "unknown"
    check("10. stop rule (close w/o production) + scan rejection",
          stop_rule_ok and reject_ok,
          f"stop_rule={stop_rule_ok} reject={reject_ok}")
except Exception as e:
    import traceback
    check("10. stop rule + rejections", False,
          f"exception: {e}\n{traceback.format_exc()}")


# --- 11. HU flow e2e (PR-35): APPROVED-gate + wrap/putaway/ship + traceability ---
try:
    from vla.handling import HandlingUnitManager

    db11 = get_db()
    seed_recipes(db11)
    runner11 = BatchRunner(db11, bus=None, rng=random.Random(33))
    b11 = runner11.create_batch("chocolate-vla-1L", planned_L=5000)
    r11 = runner11.start_batch(b11["batch_id"], telemetry={
        "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
        "packs_total": 4980, "reject_count": 20,
        "dose_actuals": {"milk": 5000.0, "sugar": 500.0,
                         "starch": 250.0, "cocoa": 100.0}})
    hum11 = HandlingUnitManager(db11)
    hu11 = hum11.create_hu(b11["batch_id"], 2400, operator_id="OP-7")
    hum11.putaway(hu11["hu_id"]); hum11.ship(hu11["hu_id"])
    shipped = db11.dw_handling_units.find_one({"hu_id": hu11["hu_id"]})
    # gate: a REJECTED batch may not enter the warehouse
    db11.dw_batches.update_one({"batch_id": b11["batch_id"]},
                               {"$set": {"verdict": "REJECTED"}})
    try:
        from vla.scan import ScanRejected
        hum11.create_hu(b11["batch_id"], 100)
        gate_ok = False
    except ScanRejected as ex:
        gate_ok = ex.reason == "not_approved"
    rep11 = render_json(runner11.get_batch(b11["batch_id"]))
    check("11. HU flow e2e (wrap/putaway/ship + APPROVED-gate + report)",
          r11["verdict"] == "APPROVED" and shipped["status"] == "shipped"
          and gate_ok and len(rep11["handling_units"]) == 1,
          f"hu={hu11['hu_id']} shipped={shipped['status']} gate_ok={gate_ok}")
except Exception as e:
    import traceback
    check("11. HU flow e2e", False, f"exception: {e}\n{traceback.format_exc()}")


# --- 12. CBM + CIP-gate e2e (fase 3): alert op batch 3, Dirty op 4, CIP herstelt ---
try:
    from vla.equipment import EquipmentMonitor, DIRTY_AFTER_BATCHES

    db12 = get_db()
    seed_recipes(db12)
    mon12 = EquipmentMonitor(db12, bus=None)
    runner12 = BatchRunner(db12, bus=None, rng=random.Random(50), equipment=mon12)
    for _ in range(DIRTY_AFTER_BATCHES):
        b12 = runner12.create_batch("chocolate-vla-1L", planned_L=5000)
        runner12.start_batch(b12["batch_id"], telemetry={
            "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20})
    alert_ok = len(mon12.open_alerts("cook-unit-01")) == 1
    dirty_ok = mon12.is_dirty("cook-unit-01")
    try:
        runner12.create_batch("chocolate-vla-1L", planned_L=5000)
        gate_ok = False
    except ValueError:
        gate_ok = True
    mon12.perform_cip("cook-unit-01", operator_id="OP-7")
    after = runner12.create_batch("chocolate-vla-1L", planned_L=5000)
    check("12. CBM alert + Dirty gate + CIP recovery",
          alert_ok and dirty_ok and gate_ok and after["state"] == "IDLE",
          f"alert={alert_ok} dirty={dirty_ok} gate={gate_ok}")
except Exception as e:
    import traceback
    check("12. CBM + CIP gate", False, f"exception: {e}\n{traceback.format_exc()}")


# --- 13. OEE + EBR + periode-rapport (fase 3) ---
try:
    from vla.period_reports import assemble_period_report, render_period_pdf

    # start batch 5 FIRST: CIP cleared the heat-up history, so OEE performance
    # is only < 1.0 again once this batch's heat-up (base*1.15) is recorded
    runner12.start_batch(after["batch_id"], telemetry={
        "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
        "packs_total": 4980, "reject_count": 20})
    oee_rows = {r["equipment_id"]: r for r in mon12.oee()}
    cook_perf_ok = oee_rows["cook-unit-01"]["performance"] < 1.0
    runner12.ack_verdict(after["batch_id"], operator_id="OP-7")
    from vla.report import render_json
    ebr = render_json(runner12.get_batch(after["batch_id"]))
    ebr_ok = (ebr["report_type"].startswith("Electronic Batch Record")
              and ebr["verdict_ack"]["operator_id"] == "OP-7")
    prep = assemble_period_report(db12, days=7)
    pdf_ok = render_period_pdf(prep)[:4] == b"%PDF"
    check("13. OEE performance-drop + EBR + periode-rapport",
          cook_perf_ok and ebr_ok and prep["batches_total"] == 5 and pdf_ok,
          f"perf={oee_rows['cook-unit-01']['performance']} ebr={ebr_ok} "
          f"batches={prep['batches_total']}")
except Exception as e:
    import traceback
    check("13. OEE + EBR + periode-rapport", False,
          f"exception: {e}\n{traceback.format_exc()}")


# --- report ---
print("\n=== batch-engine selftest ===")
all_pass = True
for name, ok, detail in results:
    tag = PASS if ok else FAIL
    all_pass = all_pass and ok
    print(f"[{tag}] {name}")
    if detail:
        print(f"       {detail}")
print("=" * 34)
print("RESULT:", "ALL PASS" if all_pass else "FAILURES PRESENT")
sys.exit(0 if all_pass else 1)
