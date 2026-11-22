"""Fouling history: per pasteuriser and cleaning cycle, into TDengine (Fouling Intelligence).

The pasteuriser samples (every 5 s while milk flows) live 48 hours in Mongo; the rolling
KPI-PAC-10x values in idp.kpi mix several cycles in one window. Fouling is a property of ONE
cleaning cycle against run time, so this writer keeps exactly that, for good:

  idp.fouling    one row per BUCKET_H run hours of one cycle, ts = last sample of the bucket
                 (run_h, rf, u, recovery_pct, dp_norm, flow_kg_s, throughput_kg, hold_t_c,
                 cold_t_in_c, n, cycle_start)  TAGS (line, exchanger)
  idp.cip_cycle  one row per finished CIP, ts = end of the CIP
                 (run_h, cash_eur, labour_eur, water_l, caustic_kg, acid_kg, heat_kwh,
                 electricity_kwh)  TAGS (line, exchanger)

Same physics and prices as the KPIs, by construction: U per sample from meters.u_value, the
cycle and U_clean from bvip.cycle_numbers / bvip.clean_u, the CIP cost from bvip.cip_cost.

Rules:
- Only COMPLETE buckets: a bucket is written once a later sample of the same cycle lies in a
  later bucket, or the cycle has ended. A running bucket would be rewritten with other values.
- Only cycles seen from their start: a cycle whose first sample in Mongo is past U_CLEAN_RUN_H
  has no U_clean (bvip.clean_u), so no Rf; it is left out entirely rather than written half.
- throughput_kg is mean flow x bucket length in process seconds (run_h is process time).
- Recovery is flow-weighted over the bucket, the way BVIP VM-FOULING-ENERGY asks for it.

TDengine overwrites a row with the same timestamp, so writing a bucket twice is harmless.
Runs inside the KPI writer's tick on the same snapshot (one Mongo read), see kpi_history.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from . import bvip
from .meters import suffix, u_value
from .tdengine import number, quote

BUCKET_H = 0.25

SCHEMA = (
    "CREATE STABLE IF NOT EXISTS {db}.fouling (ts TIMESTAMP, run_h DOUBLE, rf DOUBLE, u DOUBLE, "
    "recovery_pct DOUBLE, dp_norm DOUBLE, flow_kg_s DOUBLE, throughput_kg DOUBLE, hold_t_c DOUBLE, "
    "cold_t_in_c DOUBLE, n INT, cycle_start BIGINT) TAGS (`line` VARCHAR(8), exchanger VARCHAR(32))",
    "CREATE STABLE IF NOT EXISTS {db}.cip_cycle (ts TIMESTAMP, run_h DOUBLE, cash_eur DOUBLE, "
    "labour_eur DOUBLE, water_l DOUBLE, caustic_kg DOUBLE, acid_kg DOUBLE, heat_kwh DOUBLE, "
    "electricity_kwh DOUBLE) TAGS (`line` VARCHAR(8), exchanger VARCHAR(32))",
)


def exchanger(line: str) -> str:
    """The equipment id of the intake pasteuriser of the line (pasteurizer-01 for L1)."""
    return f"pasteurizer-{suffix(line)}"


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def buckets(rows: list, cfg: bvip.Config, bucket_h: float = BUCKET_H) -> list:
    """Complete run-time buckets per cleaning cycle from the sorted samples of one line."""
    cycles = bvip.cycle_numbers(rows)
    clean = bvip.clean_u(rows, cycles)
    first: dict = {}
    for r, c in zip(rows, cycles):
        first.setdefault(c, r)
    last_cycle = cycles[-1] if cycles else None
    groups: dict = {}
    for r, c in zip(rows, cycles):
        if _f(r.get("product_flow_kg_s")) <= 1.0:
            continue
        if c not in clean:   # run_h only rises in a cycle: no clean samples = not seen from its start
            continue
        groups.setdefault((c, int(r["run_h_since_cip"] // bucket_h)), []).append(r)
    # the newest bucket of the running cycle is still filling
    if last_cycle is not None:
        running = [b for (c, b) in groups if c == last_cycle]
        if running:
            groups.pop((last_cycle, max(running)), None)
    out = []
    for (c, b), rs in sorted(groups.items(), key=lambda kv: kv[1][-1]["_t"]):
        flows = [_f(r["product_flow_kg_s"]) for r in rs]
        us = [r.get("u") or u_value(r, cfg.product_cp, cfg.pasteuriser_area) for r in rs]
        rec_w = rec = 0.0
        for r, fl in zip(rs, flows):
            span = _f(r["hold_t_C"]) - _f(r["cold_t_in_C"])
            if span > 0:
                rec += fl * (_f(r["regen_t_out_C"]) - _f(r["cold_t_in_C"])) / span * 100.0
                rec_w += fl
        good_u = [u for u in us if u]
        mean_flow = sum(flows) / len(flows)
        out.append({
            "ts": rs[-1]["_t"], "cycle_start": first[c]["_t"], "run_h": (b + 0.5) * bucket_h,
            "rf": (sum(max(0.0, 1.0 / u - 1.0 / clean[c]) for u in good_u) / len(good_u)) if good_u else None,
            "u": sum(good_u) / len(good_u) if good_u else None,
            "recovery_pct": rec / rec_w if rec_w else None,
            "dp_norm": sum(_f(r["dp_bar"]) * (cfg.pasteuriser_ref_flow / fl) ** 2
                           for r, fl in zip(rs, flows)) / len(rs),
            "flow_kg_s": mean_flow, "throughput_kg": mean_flow * bucket_h * 3600.0,
            "hold_t_c": sum(_f(r["hold_t_C"]) for r in rs) / len(rs),
            "cold_t_in_c": sum(_f(r["cold_t_in_C"]) for r in rs) / len(rs), "n": len(rs)})
    return out


def cip_rows(cycles: list, cfg: bvip.Config) -> list:
    """One row per finished CIP with its cost split in cash and labour."""
    out = []
    for c in cycles:
        if c.get("baseline") or c.get("past_run_h") is None:
            continue
        cost = bvip.cip_cost(c, cfg)
        out.append({"ts": c["_t"], "run_h": _f(c["past_run_h"]),
                    "cash_eur": None if cost is None else cost[0],
                    "labour_eur": None if cost is None else cost[1],
                    "water_l": _f(c.get("water_L")), "caustic_kg": _f(c.get("caustic_kg")),
                    "acid_kg": _f(c.get("acid_kg")), "heat_kwh": _f(c.get("heat_kwh")),
                    "electricity_kwh": _f(c.get("electricity_kwh"))})
    return out


FOULING_COLS = ("run_h", "rf", "u", "recovery_pct", "dp_norm", "flow_kg_s", "throughput_kg",
                "hold_t_c", "cold_t_in_c")
CIP_COLS = ("run_h", "cash_eur", "labour_eur", "water_l", "caustic_kg", "acid_kg", "heat_kwh",
            "electricity_kwh")


def parts(snap: bvip.Snapshot, cfg: bvip.Config, db: str, lines: tuple,
          since: Optional[datetime] = None) -> list:
    """INSERT parts in the kpi_history.Rows format, for rows with ts >= since (all without since)."""
    out = []
    for line in lines:
        ex = exchanger(line)
        tags = f"TAGS ({quote(line)}, {quote(ex)})"
        for b in buckets(snap.pasteur.get(line, []), cfg):
            if since is not None and b["ts"] < since:
                continue
            vals = ", ".join([str(_ms(b["ts"]))] + [number(b[k]) for k in FOULING_COLS]
                             + [str(int(b["n"])), str(_ms(b["cycle_start"]))])
            out.append(f"{db}.foul_{line.lower()} USING {db}.fouling {tags} VALUES ({vals})")
        for c in cip_rows(snap.cip_cycles.get(line, []), cfg):
            if since is not None and c["ts"] < since:
                continue
            vals = ", ".join([str(_ms(c["ts"]))] + [number(c[k]) for k in CIP_COLS])
            out.append(f"{db}.cip_{line.lower()} USING {db}.cip_cycle {tags} VALUES ({vals})")
    return out
