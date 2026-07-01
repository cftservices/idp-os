"""Headless self-test for the vla-factory batch physics (no OPC-UA, no MQTT).

Runs a full chocolate-vla batch through the state machine and asserts:
  1. state walks the six phases IDLE -> DOSING -> COOKING -> COOLING -> FILLING -> COMPLETE
  2. every dose_actual reaches its recipe setpoint
  3. end viscosity is IN-SPEC (150-300 cP) on a normal batch
  4. with a cook_undertemp fault, end viscosity is < 150 cP (out-of-spec -> Solve trigger)

Run:  python selftest.py         -> prints PASS/FAIL, exit 0 on PASS
"""

from __future__ import annotations

import sys

from physics import (
    VlaProcess, RECIPES, STATES,
    IDLE, DOSING, COOKING, COOLING, FILLING, COMPLETE,
)

RECIPE = "chocolate-vla-1L"
SPEC_MIN = RECIPES[RECIPE]["spec_min_cP"]
SPEC_MAX = RECIPES[RECIPE]["spec_max_cP"]
DT = 0.2
MAX_TICKS = 20000


def run_batch(fault: tuple[str, float] | None = None):
    """Run one batch to COMPLETE. Returns (process, states_seen, ticks)."""
    p = VlaProcess()
    states_seen: list[str] = [p.state]  # capture IDLE at rest, before start
    rc = p.start_batch(RECIPE, batch_id="SELFTEST-001")
    assert rc == 0, f"start_batch refused rc={rc}"
    if p.state != states_seen[-1]:
        states_seen.append(p.state)
    if fault is not None:
        frc = p.inject_fault(fault[0], fault[1])
        assert frc == 0, f"inject_fault refused rc={frc}"

    ticks = 0
    while p.state != COMPLETE and ticks < MAX_TICKS:
        p.tick(DT)
        if p.state != states_seen[-1]:
            states_seen.append(p.state)
        ticks += 1
    return p, states_seen, ticks


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    print("[selftest] vla-factory batch physics")
    all_ok = True

    # ---- 1. NORMAL batch ----------------------------------------------------
    print("\n-- normal batch --")
    p, states, ticks = run_batch()

    reached_complete = p.state == COMPLETE
    all_ok &= check("reaches COMPLETE", reached_complete, f"in {ticks} ticks (~{ticks*DT:.0f}s sim-real)")

    expected_order = [IDLE, DOSING, COOKING, COOLING, FILLING, COMPLETE]
    all_ok &= check("state walks all 6 phases in order", states == expected_order, " -> ".join(states))

    doses_ok = True
    dose_detail = []
    for mat, sp in p.dose_setpoint_kg.items():
        act = p.dose_actual_kg[mat]
        hit = abs(act - sp) < 0.5
        doses_ok &= hit
        dose_detail.append(f"{mat}={act:.0f}/{sp:.0f}")
    all_ok &= check("all doses reach setpoint", doses_ok, ", ".join(dose_detail))

    visc = p.viscosity_cP
    in_spec = SPEC_MIN <= visc <= SPEC_MAX
    all_ok &= check(f"end viscosity in-spec ({SPEC_MIN:.0f}-{SPEC_MAX:.0f} cP)",
                    in_spec, f"{visc:.1f} cP (peak_cook={p.peak_cook_temp_C:.1f}C, hold={p.hold_elapsed_sec:.0f}s)")

    packs_ok = p.packs_total > 0
    all_ok &= check("packs produced (1L each)", packs_ok, f"{p.packs_total} packs")

    # ---- 2. cook_undertemp FAULT -------------------------------------------
    print("\n-- cook_undertemp fault (magnitude 1.0) --")
    pf, states_f, ticks_f = run_batch(fault=("cook_undertemp", 1.0))

    fault_complete = pf.state == COMPLETE
    all_ok &= check("faulted batch still reaches COMPLETE", fault_complete, f"in {ticks_f} ticks")

    visc_f = pf.viscosity_cP
    below_spec = visc_f < SPEC_MIN
    all_ok &= check(f"faulted viscosity < {SPEC_MIN:.0f} cP (out-of-spec, Solve trigger)",
                    below_spec, f"{visc_f:.1f} cP (peak_cook={pf.peak_cook_temp_C:.1f}C)")

    # ---- verdict ------------------------------------------------------------
    print()
    if all_ok:
        print("[selftest] PASS")
        return 0
    print("[selftest] FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
