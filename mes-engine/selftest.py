"""Offline self-test for mes-engine. No broker / no Mongo required.

Verifies:
  1. SSCC build + validate (check digit correct)
  2. model loads + recipe-explode scales bom to planned_qty
  3. OrderRunner runs an order end-to-end in pure-sim mode (consumptions, HUs,
     samples, verdict) + a forced-fault Solve scenario -> REJECTED
  4. EBR HTML renders for that order
  5. `import app` succeeds without a broker

Run: python selftest.py   (exit 0 = all pass)
"""

from __future__ import annotations

import random
import sys

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))


# --- 1. SSCC ---
try:
    from mes.sscc import build_sscc, validate_sscc, gs1_check_digit

    sscc = build_sscc("80", 123456)
    ok = (len(sscc) == 18 and sscc.isdigit() and validate_sscc(sscc))
    # tamper the check digit -> must fail
    bad = sscc[:-1] + str((int(sscc[-1]) + 1) % 10)
    tamper_fails = not validate_sscc(bad)
    # known-value cross-check: recompute check digit independently
    manual = gs1_check_digit(sscc[:-1])
    check("1. SSCC build+validate (18-digit, valid check digit)",
          ok and tamper_fails and manual == int(sscc[-1]),
          f"sscc={sscc} check={sscc[-1]} recomputed={manual} tamper_rejected={tamper_fails}")
except Exception as e:
    check("1. SSCC build+validate", False, f"exception: {e}")


# --- 2. model + recipe-explode ---
try:
    from mes.model import load_model
    from mes.db import get_db
    from mes.orders import OrderRunner

    model = load_model()
    n_recipes = len(model.recipes())
    n_units = len(model.units())
    area = model.area_of("pasteurizer-01")

    db = get_db()  # in-memory (no MONGO_URL)
    runner = OrderRunner(model, db, bus=None, rng=random.Random(42))

    # R-YOG basis 1000 kg; MILK qty_per_parent_item=900. At planned 2000 -> ~1800.
    order = runner.create_order("R-YOG", 2000)
    oid = order["order"]["order_id"]
    job_bom = db.dw_job_bom.find({"order_id": oid})
    milk_row = next(r for r in job_bom if r["material_id"] == "MILK")
    scaled_ok = abs(milk_row["qty_target"] - 1800.0) < 1e-6
    # tolerances scaled too (tol_min 890 -> 1780)
    tol_ok = abs(milk_row["tol_min"] - 1780.0) < 1e-6
    check("2. model loads + recipe-explode scales BOM to planned_qty",
          n_recipes >= 4 and n_units >= 10 and area == "Processing"
          and scaled_ok and tol_ok,
          f"recipes={n_recipes} units={n_units} area(pasteurizer)={area} "
          f"MILK target={milk_row['qty_target']} tol_min={milk_row['tol_min']}")
except Exception as e:
    import traceback
    check("2. model + recipe-explode", False, f"exception: {e}\n{traceback.format_exc()}")


# --- 3. OrderRunner end-to-end (pure-sim) ---
try:
    runner.run_order(oid)
    bundle = runner.get_order(oid)
    o = bundle["order"]
    cons = bundle["consumptions"]
    hus = bundle["handling_units"]
    samples = bundle["samples"]
    all_sscc_valid = all(validate_sscc(h["sscc_code"]) for h in hus)
    verdict_ok = o["verdict"] in ("APPROVED", "HOLD", "REJECTED", "PENDING")
    check("3a. OrderRunner runs end-to-end (consumptions+HUs+samples+verdict)",
          o["status"] == "CLOSED" and len(cons) >= 1 and len(hus) >= 1
          and len(samples) >= 1 and all_sscc_valid and verdict_ok,
          f"status={o['status']} cons={len(cons)} HUs={len(hus)} "
          f"samples={len(samples)} verdict={o['verdict']} all_sscc_valid={all_sscc_valid}")

    # forced-fault Solve scenario -> CRITICAL -> REJECTED
    db2 = get_db()
    runner2 = OrderRunner(model, db2, bus=None, rng=random.Random(7))
    o2 = runner2.create_order("R-MILK", 1000)
    oid2 = o2["order"]["order_id"]
    runner2.run_order(oid2, inject_fault={"unit": "pasteurizer-01", "htst_temp_C": 70.5})
    b2 = runner2.get_order(oid2)
    crit_alarm = any(a["severity"] == "Critical" for a in b2["alarms"])
    check("3b. Solve fault (HTST<71.5) -> CRITICAL alarm + REJECTED verdict",
          crit_alarm and b2["order"]["verdict"] == "REJECTED"
          and b2["order"]["critical_alarm_during_batch"],
          f"critical_alarm={crit_alarm} verdict={b2['order']['verdict']}")
except Exception as e:
    import traceback
    check("3. OrderRunner end-to-end", False, f"exception: {e}\n{traceback.format_exc()}")


# --- 4. EBR HTML ---
try:
    from mes.ebr import assemble_ebr, render_html

    ebr = assemble_ebr(runner.get_order(oid), model)
    html = render_html(ebr)
    ebr_ok = (
        "Electronic Batch Record" in html
        and oid in html
        and "Critical Alarm During Batch" in html
        and len(html) > 500
    )
    check("4. EBR HTML renders for the order", ebr_ok,
          f"html_len={len(html)} contains_order_id={oid in html}")
except Exception as e:
    check("4. EBR HTML render", False, f"exception: {e}")


# --- 5. import app without a broker ---
try:
    import app as _app  # noqa: F401
    check("5. `import app` succeeds without a broker", hasattr(_app, "app"),
          "FastAPI app object present")
except Exception as e:
    import traceback
    check("5. import app", False, f"exception: {e}\n{traceback.format_exc()}")


# --- report ---
print("\n=== mes-engine selftest ===")
all_pass = True
for name, ok, detail in results:
    tag = PASS if ok else FAIL
    all_pass = all_pass and ok
    print(f"[{tag}] {name}")
    if detail:
        print(f"       {detail}")
print("=" * 32)
print("RESULT:", "ALL PASS" if all_pass else "FAILURES PRESENT")
sys.exit(0 if all_pass else 1)
