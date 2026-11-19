"""Headless self-test for the vla-factory batch physics (no OPC-UA, no MQTT).

Runs a full chocolate-vla batch through the state machine and asserts:
  1. state walks the six phases IDLE -> DOSING -> COOKING -> COOLING -> FILLING -> COMPLETE
  2. every dose_actual reaches its recipe setpoint
  3. end viscosity is IN-SPEC (150-300 cP) on a normal batch
  4. with a cook_undertemp fault, end viscosity is < 150 cP (out-of-spec -> Solve trigger)
  5. the raw-milk silo actually EMPTIES when milk is dosed, and refills

Run:  python selftest.py         -> prints PASS/FAIL, exit 0 on PASS
"""

from __future__ import annotations

import sys

from physics import (
    VlaProcess, RECIPES, STATES,
    IDLE, DOSING, COOKING, COOLING, FILLING, COMPLETE,
    MILK_DENSITY_KG_L, RECEIVING_CAPACITY_L, RECEIVING_REFILL_BELOW_L,
)

RECIPE = "chocolate-vla-1L"
SPEC_MIN = RECIPES[RECIPE]["spec_min_cP"]
SPEC_MAX = RECIPES[RECIPE]["spec_max_cP"]
DT = 0.2
MAX_TICKS = 20000


def run_batch(fault: tuple[str, float] | None = None):
    """Run one batch to COMPLETE.

    Returns (process, states_seen, ticks, phase_ticks) where phase_ticks maps
    each state to the number of ticks spent in it -- multiply by DT for the
    real-time duration of that phase.
    """
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
    phase_ticks: dict[str, int] = {}
    while p.state != COMPLETE and ticks < MAX_TICKS:
        phase_ticks[p.state] = phase_ticks.get(p.state, 0) + 1
        p.tick(DT)
        if p.state != states_seen[-1]:
            states_seen.append(p.state)
        ticks += 1
    return p, states_seen, ticks, phase_ticks


def print_phase_timing(phase_ticks: dict[str, int]) -> None:
    """Print the real-time duration of each phase -- the demo-tempo check."""
    print("  phase timing (real seconds, DT=%.1f):" % DT)
    for st in (DOSING, COOKING, COOLING, FILLING):
        n = phase_ticks.get(st, 0)
        print(f"    {st:<9} {n:>5} ticks  {n * DT:>6.1f}s")
    total = sum(phase_ticks.values()) * DT
    print(f"    {'TOTAL':<9} {'':>5}         {total:>6.1f}s")


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    print("[selftest] vla-factory batch physics")
    all_ok = True

    # ---- 1. NORMAL batch ----------------------------------------------------
    print("\n-- normal batch --")
    p, states, ticks, phase_ticks = run_batch()

    reached_complete = p.state == COMPLETE
    all_ok &= check("reaches COMPLETE", reached_complete, f"in {ticks} ticks (~{ticks*DT:.0f}s sim-real)")
    print_phase_timing(phase_ticks)

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
    pf, states_f, ticks_f, _ = run_batch(fault=("cook_undertemp", 1.0))

    fault_complete = pf.state == COMPLETE
    all_ok &= check("faulted batch still reaches COMPLETE", fault_complete, f"in {ticks_f} ticks")

    visc_f = pf.viscosity_cP
    below_spec = visc_f < SPEC_MIN
    all_ok &= check(f"faulted viscosity < {SPEC_MIN:.0f} cP (out-of-spec, Solve trigger)",
                    below_spec, f"{visc_f:.1f} cP (peak_cook={pf.peak_cook_temp_C:.1f}C)")

    # ---- 3. RAW-MILK SILO ---------------------------------------------------
    # This one exists because the drain was multiplied by 0.0: the line read
    # `receiving_level_L - budget * 0.0`, so 5000 kg of milk per batch came out
    # of nowhere and the silo sat at its start value forever. That is also why
    # the tag never reached the UNS -- a change-based coupling never publishes a
    # constant -- and why the receiving tank was blank on every screen.
    print("\n-- raw-milk silo --")
    p3 = VlaProcess()
    start_L = p3.receiving_level_L
    p3, _, _, _ = run_batch()
    drawn_L = start_L - p3.receiving_level_L
    expected_L = p3.dose_actual_kg["milk"] / MILK_DENSITY_KG_L

    all_ok &= check("silo drains when milk is dosed", drawn_L > 0,
                    f"{drawn_L:.0f} L")
    all_ok &= check("drain matches the milk actually dosed",
                    abs(drawn_L - expected_L) < 1.0,
                    f"{drawn_L:.1f} L vs {expected_L:.1f} L expected")

    # Only milk comes from this silo; sugar, starch and cocoa are dry
    # ingredients from another stream. Draining the full dosed mass would empty
    # it 17 percent too fast.
    total_kg = sum(p3.dose_actual_kg.values())
    all_ok &= check("only milk is drawn, not the whole dosed mass",
                    drawn_L < total_kg / MILK_DENSITY_KG_L - 1.0,
                    f"{drawn_L:.0f} L of {total_kg:.0f} kg total dosed")

    # Keep running: below the threshold a tanker arrives and the silo refills,
    # otherwise the demo starves after two batches.
    p4 = VlaProcess()
    for i in range(6):
        p4.start_batch(RECIPE, batch_id=f"SELFTEST-SILO-{i}")
        t = 0
        while p4.state != COMPLETE and t < MAX_TICKS:
            p4.tick(DT)
            t += 1
    all_ok &= check("silo never starves over six batches", p4.receiving_level_L > 0,
                    f"{p4.receiving_level_L:.0f} L left")
    all_ok &= check("silo stays within capacity",
                    p4.receiving_level_L <= RECEIVING_CAPACITY_L + 0.5,
                    f"{p4.receiving_level_L:.0f} of {RECEIVING_CAPACITY_L:.0f} L")

    # The whole point for the UI: both tags have to MOVE, otherwise they are
    # never published and the tank shows dashes.
    p5 = VlaProcess()
    levels, temps = set(), set()
    p5.start_batch(RECIPE, batch_id="SELFTEST-SILO-MOVE")
    t = 0
    while p5.state != COMPLETE and t < MAX_TICKS:
        p5.tick(DT)
        levels.add(round(p5.receiving_level_L, 1))
        temps.add(round(p5.receiving_temp_C, 2))
        t += 1
    all_ok &= check("level_L changes during a batch", len(levels) > 5,
                    f"{len(levels)} distinct values")
    all_ok &= check("temp_C changes during a batch", len(temps) > 1,
                    f"{len(temps)} distinct values")

    # ---- natural variation (SIM_VARIATION) ---------------------------------
    # Twelve batches with the seeded variation on: the viscosity must vary (or a
    # control chart has nothing to show), stay in spec without a fault, and be
    # reproducible from the seed. A fault must still push a batch out of spec.
    def visc_series(seed: int, n: int = 12) -> list[float]:
        pv = VlaProcess(variation=True, seed=seed)
        out = []
        for i in range(n):
            pv.start_batch(RECIPE, batch_id=f"SELFTEST-VAR-{i}")
            t = 0
            while pv.state != COMPLETE and t < MAX_TICKS:
                pv.tick(DT)
                t += 1
            out.append(round(pv.viscosity_cP, 2))
        return out

    var_a, var_b = visc_series(46), visc_series(46)
    spread = max(var_a) - min(var_a)
    all_ok &= check("viscosity varies batch to batch", spread > 2.0,
                    f"min {min(var_a):.1f}, max {max(var_a):.1f} cP")
    all_ok &= check("varied batches stay in spec",
                    all(SPEC_MIN <= v <= SPEC_MAX for v in var_a), "")
    all_ok &= check("variation is reproducible from the seed", var_a == var_b, "")
    pf = VlaProcess(variation=True, seed=46)
    pf.start_batch(RECIPE, batch_id="SELFTEST-VAR-FAULT")
    pf.inject_fault("cook_undertemp", 0.6)
    t = 0
    while pf.state != COMPLETE and t < MAX_TICKS:
        pf.tick(DT)
        t += 1
    all_ok &= check("fault still out of spec with variation on", pf.viscosity_cP < SPEC_MIN,
                    f"{pf.viscosity_cP:.1f} cP")

    # Fouling rate: with variation on, the number of batches until the CIP limit differs
    # per cleaning cycle (no metronome), stays plausible, and comes back from the seed.
    def batches_to_limit(seed: int, cycles: int = 8, run_h: float = 0.126,
                         limit: float = 0.06) -> list[int]:
        pv = VlaProcess(variation=True, seed=seed)
        out = []
        for _ in range(cycles):
            n = 0
            while pv._past_rf() < limit and n < 100:
                pv._draw_foul_batch()
                pv._foul(run_h)
                n += 1
            out.append(n)
            pv._clean()
        return out

    foul_a, foul_b = batches_to_limit(46), batches_to_limit(46)
    flat = VlaProcess(variation=False)
    n_flat = 0
    while flat._past_rf() < 0.06:
        flat._draw_foul_batch()
        flat._foul(0.126)
        n_flat += 1
    all_ok &= check("fouling rate varies per cleaning cycle", len(set(foul_a)) >= 3,
                    f"batches to Rf 0.06: {foul_a} (without variation {n_flat})")
    all_ok &= check("fouling cycles stay plausible (7 to 20 batches)",
                    all(7 <= n <= 20 for n in foul_a), "")
    all_ok &= check("fouling spread is reproducible from the seed", foul_a == foul_b, "")
    all_ok &= check("without variation the fouling curve is unchanged", n_flat == 13,
                    f"{n_flat} batches")

    # ---- utilities, intake pasteuriser, CIP (fase 3) ------------------------
    print("\n-- utilities, pasteur en CIP --")
    import physics as P
    pu = VlaProcess(variation=False)
    readings, firsts, cooking_gas = [], [], None
    for i in range(12):
        pu.start_batch(RECIPE, batch_id=f"SELFTEST-U-{i}")
        hot_first = None
        t = 0
        while pu.state != COMPLETE and t < MAX_TICKS:
            st, gas0, peak0 = pu.state, pu.heat_kwh_total, pu.cook_temp_C
            pu.tick(DT)
            t += 1
            if i == 0 and st == COOKING:
                cooking_gas = (cooking_gas or 0.0) + (pu.heat_kwh_total - gas0)
            if i == 0 and st == DOSING and pu.state == COOKING:
                cook_start_C = pu.cook_temp_C
            if pu.past_flow_kg_s > 0 and hot_first is None:
                hot_first = (pu.past_hot_in_C, pu.past_cold_in_C, pu.past_regen_out_C,
                             pu.past_hold_C)
            readings.append((pu.heat_kwh_total, pu.electricity_kwh_total, pu.water_L_total))
        firsts.append(hot_first)
        if i == 0:
            mass = sum(pu.dose_actual_kg.values())
            expect = (mass * P.CP_PRODUCT_KJ_KG_K * (pu.peak_cook_temp_C - cook_start_C)
                      + P.COOK_HOLD_LOSS_KW * pu.hold_elapsed_sec) / 3600.0 / P.BOILER_EFF
    all_ok &= check("cook energy = m x cp x dT (+ hold loss) / boiler eff, within 5%",
                    abs(cooking_gas - expect) <= 0.05 * expect,
                    f"{cooking_gas:.1f} vs {expect:.1f} kWh")
    hot_in, cold, regen, hold = firsts[0]
    eff = (regen - cold) / (hold - cold)
    all_ok &= check("heat recovery between 80 and 95 %", 0.80 <= eff <= 0.95, f"{eff * 100:.1f} %")
    all_ok &= check("fouling: hot water runs hotter after 12 batches",
                    firsts[-1][0] > firsts[0][0] + 1.0,
                    f"{firsts[0][0]:.1f} -> {firsts[-1][0]:.1f} C")
    rf12 = pu._past_rf()
    all_ok &= check("fouling reaches the CIP limit around 12 batches", 0.05 <= rf12 <= 0.07,
                    f"Rf {rf12:.4f} m2K/kW")
    all_ok &= check("pasteuriser has no flow outside dosing", pu.past_flow_kg_s == 0.0, "")

    pu.start_batch(RECIPE, batch_id="SELFTEST-U-BUSY")
    all_ok &= check("CIP refused while a batch runs", pu.start_cip() == 1, "")
    while pu.state != COMPLETE:
        pu.tick(DT)
    water0 = pu.water_L_total
    all_ok &= check("CIP starts from COMPLETE", pu.start_cip() == 0, pu.state)
    t = 0
    while pu.state == P.CIP and t < MAX_TICKS:
        pu.tick(DT)
        t += 1
        readings.append((pu.heat_kwh_total, pu.electricity_kwh_total, pu.water_L_total))
    last = pu.cip_last
    all_ok &= check("CIP takes 60 process minutes and returns to IDLE",
                    abs(last["duration_s"] - 3600.0) < 1.0 and pu.state == IDLE,
                    f"{last['duration_s']:.0f} s, {pu.state}")
    all_ok &= check("CIP uses 9 m3 water, 15 kg caustic, 8 kg acid",
                    abs(pu.water_L_total - water0 - 9000) < 1 and last["caustic_kg"] == 15.0
                    and last["acid_kg"] == 8.0, f"{last}")
    all_ok &= check("CIP removes the fouling", pu._past_rf() == 0.0 and pu.cip_count == 1, "")
    mono = all(b[j] >= a[j] for a, b in zip(readings, readings[1:]) for j in range(3))
    all_ok &= check("meter totals never go down", mono, f"{len(readings)} readings")
    pu.start_batch(RECIPE, batch_id="SELFTEST-U-CLEAN")
    while pu.past_flow_kg_s == 0.0:
        pu.tick(DT)
    # U from the PUBLISHED signals, the way the engine computes it (KPI-PAC-101)
    duty = pu.past_flow_kg_s * P.CP_PRODUCT_KJ_KG_K * (pu.past_hold_C - pu.past_regen_out_C)
    u = duty / (P.PAST_AREA_M2 * P.lmtd(pu.past_hot_in_C - pu.past_hold_C,
                                        pu.past_hot_out_C - pu.past_regen_out_C))
    all_ok &= check("after CIP the U computed from the signals is back at clean",
                    abs(u - P.PAST_U_CLEAN) / P.PAST_U_CLEAN < 0.02,
                    f"U {u:.2f} vs clean {P.PAST_U_CLEAN} kW/m2/K")

    # ---- verdict ------------------------------------------------------------
    print()
    if all_ok:
        print("[selftest] PASS")
        return 0
    print("[selftest] FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
