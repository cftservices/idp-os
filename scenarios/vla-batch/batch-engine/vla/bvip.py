"""The 29 industrial BVIP KPIs, computed from MES data of the vla demo.

One function, `inputs(snapshot, line, start, end)`, derives every formula input over a time
window. The same function serves two purposes:

  * the rolling KPI value (window = the last SHIFT_HOURS), and
  * additive increments per 5-minute bucket (window = the bucket), written to TDengine so that
    Grafana can evaluate the catalogue formula exactly over any period: sum the inputs, then
    apply the formula. That works for a week although Mongo keeps only 24 hours of batches.

For that to hold, every additive input must be additive over disjoint windows: batch quantities
are counted at their completion time, durations are clipped to the window. Hours are process
hours (wall clock x KPI_TIME_SCALE), like the rest of the KPI layer.

The formula is the catalogue's, evaluated by a small arithmetic evaluator (numbers, names,
+ - * / and parentheses). Nothing here invents a value: an input without a source is missing,
and a KPI with a missing input is UNSET or NIET_AANTOONBAAR, never 0.

Line "PLANT" is the sum of the inputs of L1 and L2, which is the weighted plant value the
catalogue asks for ("per lijn dan gewogen").
"""
from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import model as M
from .bvip_map import BVIP_MAP, DEELS, NA

CATALOGUE_PATH = Path(__file__).with_name("bvip_catalogue.json")
PLANT = "PLANT"
LINES = ("L1", "L2")
STATUS_OK, STATUS_DEELS, STATUS_UNSET, STATUS_NA = "OK", "DEELS", "UNSET", "NIET_AANTOONBAAR"
PACK_L = 1.0  # all recipes are 1 L packs (model.pack_size_L)


def load_catalogue(path: Path = CATALOGUE_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["kpis"]


# ------------------------------------------------------------------ configuration


HOURS_PER_YEAR = 8760.0
REPEAT_WINDOW_H = 168.0   # KPI-MNT-003: same machine + same cause within 7 process days


@dataclass(frozen=True)
class Config:
    time_scale: float = 1.0
    ideal_packs_per_h: dict = field(default_factory=dict)   # line -> packs per process hour
    value_per_pack: Optional[float] = None
    rework_cost_per_batch: Optional[float] = None
    density_kg_L: Optional[float] = None
    labour_cost_per_hour: Optional[float] = None
    parts_cost_per_breakdown: dict = field(default_factory=dict)   # machine role -> EUR
    asset_replacement_value: dict = field(default_factory=dict)    # machine role -> EUR
    # fase 3: energy, water, CIP, pasteuriser (demo assumptions from the factory model)
    gas_price_per_kwh: Optional[float] = None
    electricity_price_per_kwh: Optional[float] = None
    water_price_per_m3: Optional[float] = None
    caustic_price_per_kg: Optional[float] = None
    acid_price_per_kg: Optional[float] = None
    cip_labour_hours: Optional[float] = None
    sales_price_per_pack: Optional[float] = None
    co2_kg_per_kwh: dict = field(default_factory=dict)    # gas / electricity
    product_cp: float = 3.85
    pasteuriser_ref_flow: float = 11.0
    pasteuriser_area: float = 14.0


def _parse_ideal(text: str) -> dict:
    out = {}
    for part in (text or "").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                continue
    return out


def config_from_env(model: Optional[dict] = None) -> Config:
    from .kpi import _time_scale, load_cost_model, load_factory_model
    model = model if model is not None else load_factory_model()
    cost = load_cost_model(model) or {}
    return Config(
        time_scale=_time_scale(),
        ideal_packs_per_h=_parse_ideal(os.environ.get("KPI_IDEAL_PACKS_PER_H", "")),
        value_per_pack=cost.get("value_per_pack"),
        rework_cost_per_batch=cost.get("rework_cost_per_batch"),
        density_kg_L=(model or {}).get("product_density_kg_L"),
        labour_cost_per_hour=cost.get("labour_cost_per_hour"),
        parts_cost_per_breakdown=cost.get("parts_cost_per_breakdown") or {},
        asset_replacement_value=cost.get("asset_replacement_value") or {},
        gas_price_per_kwh=cost.get("gas_price_per_kwh"),
        electricity_price_per_kwh=cost.get("electricity_price_per_kwh"),
        water_price_per_m3=cost.get("water_price_per_m3"),
        caustic_price_per_kg=cost.get("caustic_price_per_kg"),
        acid_price_per_kg=cost.get("acid_price_per_kg"),
        cip_labour_hours=cost.get("cip_labour_hours"),
        sales_price_per_pack=cost.get("sales_price_per_pack"),
        co2_kg_per_kwh=cost.get("co2_kg_per_kwh") or {},
        product_cp=float(((model or {}).get("process_constants") or {}).get(
            "product_cp_kj_kg_k", 3.85)),
        pasteuriser_ref_flow=float(((model or {}).get("process_constants") or {}).get(
            "pasteuriser_ref_flow_kg_s", 11.0)),
        pasteuriser_area=float(((model or {}).get("process_constants") or {}).get(
            "pasteuriser_area_m2", 14.0)),
    )


# ------------------------------------------------------------------ snapshot


def _ts(v) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def _f(v) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Snapshot:
    """Everything the KPIs need, read once per tick and grouped for fast windowing."""
    now: datetime
    batches: dict           # line -> list of batch dicts with _s/_e parsed
    data_start: dict        # line -> earliest batch start (planned time starts there)
    intervals: dict         # equipment_id -> list of (t0, t1, state)
    breakdowns: dict        # equipment_id -> breakdown work orders {t, cause, repair_s, done}
    orders: dict            # line -> list of (due, done_at or None, status)
    dosed_kg: dict          # batch_id -> kg actually dosed
    cips: dict              # equipment_id -> sorted list of CIP times
    readings: dict = field(default_factory=dict)   # line -> meter readings (sorted)
    pasteur: dict = field(default_factory=dict)    # line -> pasteuriser samples (sorted)
    cip_cycles: dict = field(default_factory=dict)  # line -> finished CIP cycles


def take_snapshot(db, now: Optional[datetime] = None) -> Snapshot:
    now = now or datetime.now(timezone.utc)
    batches: dict = {ln: [] for ln in LINES}
    start: dict = {}
    for b in db.dw_batches.find({}):
        s, e = _ts(b.get("started_at")), _ts(b.get("completed_at"))
        ln = M.line_of_doc(b)
        if ln not in batches or s is None:
            continue
        batches[ln].append({**b, "_s": s, "_e": e})
        start[ln] = min(start.get(ln, s), s)

    rows: dict = {}
    for r in db.dw_equipment_state.find({}):
        t = _ts(r.get("ts"))
        if t is not None and r.get("equipment_id"):
            rows.setdefault(r["equipment_id"], []).append((t, r.get("state")))
    intervals = {}
    for eq, hist in rows.items():
        hist.sort(key=lambda x: x[0])
        intervals[eq] = [(t, hist[i + 1][0] if i + 1 < len(hist) else now, st)
                         for i, (t, st) in enumerate(hist)]

    breakdowns: dict = {}
    for w in db.dw_work_orders.find({}):
        t = _ts(w.get("reported_at"))
        if w.get("kind") == "breakdown" and t is not None:
            breakdowns.setdefault(w.get("equipment_id"), []).append(
                {"t": t, "cause": w.get("cause"), "repair_s": w.get("repair_s"),
                 "done": _ts(w.get("done_at"))})
    for v in breakdowns.values():
        v.sort(key=lambda w: w["t"])

    orders: dict = {ln: [] for ln in LINES}
    for o in db.dw_orders.find({}):
        due = _ts(o.get("due_date"))
        ln = M.line_of_doc(o)
        if due is not None and ln in orders:
            orders[ln].append((due, _ts(o.get("completed_at")), o.get("status")))

    dosed: dict = {}
    for d in db.dw_doses.find({}):
        if d.get("qty_actual") is not None:
            dosed[d.get("batch_id")] = dosed.get(d.get("batch_id"), 0.0) + _f(d["qty_actual"])

    cips: dict = {}
    for ev in db.dw_batch_events.find({"event_type": "cip_performed"}):
        t = _ts(ev.get("ts"))
        eq = (ev.get("payload") or {}).get("equipment_id")
        if t is not None and eq:
            cips.setdefault(eq, []).append(t)
    for v in cips.values():
        v.sort()

    def by_line(coll: str, keep=lambda r: True) -> dict:
        out: dict = {ln: [] for ln in LINES}
        for r in db.collection(coll).find({}):
            t = _ts(r.get("ts"))
            if t is not None and r.get("line") in out and keep(r):
                out[r["line"]].append({**r, "_t": t})
        for v in out.values():
            v.sort(key=lambda r: r["_t"])
        return out

    return Snapshot(now, batches, start, intervals, breakdowns, orders, dosed, cips,
                    readings=by_line("dw_meter_readings"),
                    pasteur=by_line("dw_pasteur_samples"),
                    cip_cycles=by_line("dw_cip_cycles", lambda r: not r.get("baseline")))


# ------------------------------------------------------------------ inputs


def _overlap(a0, a1, b0, b1) -> float:
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0.0, (hi - lo).total_seconds())


def _union(spans) -> float:
    total, cs, ce = 0.0, None, None
    for a, z in sorted(spans):
        if ce is None or a > ce:
            if ce is not None:
                total += (ce - cs).total_seconds()
            cs, ce = a, z
        elif z > ce:
            ce = z
    if ce is not None:
        total += (ce - cs).total_seconds()
    return total


def _clip(spans, lo, hi):
    return [(max(a, lo), min(z, hi)) for a, z in spans if min(z, hi) > max(a, lo)]


def inputs(snap: Snapshot, line: str, start: datetime, end: datetime, cfg: Config) -> dict:
    """Formula inputs of every computable KPI over [start, end) for one line or PLANT.

    A value is None when it has no source in this window (for example no ideal rate set);
    0.0 means it was measured and was zero.
    """
    if line == PLANT:
        parts = [inputs(snap, ln, start, end, cfg) for ln in LINES]
        out = {}
        for k in parts[0]:
            vals = [p[k] for p in parts]
            out[k] = None if any(v is None for v in vals) else sum(vals)
        return out

    hi = min(end, snap.now)
    lo = max(start, snap.data_start.get(line, hi))
    scale_h = cfg.time_scale / 3600.0
    eqs = M.LINES[line].equipment_ids

    done = [b for b in snap.batches.get(line, []) if b["_e"] is not None and start <= b["_e"] < end]
    good = sum(_f(b.get("packs_total")) for b in done)
    rejects = sum(_f(b.get("reject_count")) for b in done)
    filled = good + rejects
    approved = sum(_f(b.get("packs_total")) for b in done if b.get("verdict") == M.APPROVED)
    held = [b for b in done if b.get("verdict") in (M.HOLD, M.REJECTED)]
    blocked = sum(_f(b.get("packs_total")) for b in held)

    spans = [(b["_s"], b["_e"]) for b in snap.batches.get(line, []) if b["_e"] is not None]
    run_h = _union(_clip(spans, lo, hi)) * scale_h if hi > lo else 0.0
    plan_h = max(0.0, (hi - lo).total_seconds()) * scale_h

    down_spans, operating = [], 0.0
    for eq in eqs:
        for t0, t1, st in snap.intervals.get(eq, []):
            if st in M.DOWNTIME_STATES:
                down_spans.append((t0, t1))
            elif st in M.PRODUCTIVE_STATES:
                operating += _overlap(t0, t1, lo, hi) if hi > lo else 0.0
    down_h = _union(_clip(down_spans, lo, hi)) * scale_h if hi > lo else 0.0
    role_of = {eq: role for role, eq in M.LINES[line].equipment.items()}
    failures = repeats = 0
    maint_eur: Optional[float] = 0.0
    repeat_wall = timedelta(hours=REPEAT_WINDOW_H / max(cfg.time_scale, 1e-9))
    for eq in eqs:
        hist = snap.breakdowns.get(eq, [])
        for i, w in enumerate(hist):
            if not start <= w["t"] < end:
                continue
            failures += 1
            if any(p["cause"] == w["cause"] and w["t"] - p["t"] <= repeat_wall
                   for p in hist[:i]):
                repeats += 1
            parts = cfg.parts_cost_per_breakdown.get(role_of[eq])
            if cfg.labour_cost_per_hour is None or parts is None or maint_eur is None:
                maint_eur = None
            else:
                maint_eur += _f(w["repair_s"]) * scale_h * cfg.labour_cost_per_hour + float(parts)
    # The replacement value is a stock, not a flow: per window its share of a year, so the
    # summed buckets give maintenance cost as an annual percentage, as the catalogue means it.
    rv = [cfg.asset_replacement_value.get(role_of[eq]) for eq in eqs]
    rv_share = None if any(v is None for v in rv) else sum(rv) * plan_h / HOURS_PER_YEAR

    ordered = sorted(snap.batches.get(line, []), key=lambda b: b["_s"])
    changeover_h = 0.0
    for prev, nxt in zip(ordered, ordered[1:]):
        if (start <= nxt["_s"] < end and prev["_e"] is not None
                and prev.get("recipe_id") != nxt.get("recipe_id")):
            changeover_h += max(0.0, (nxt["_s"] - prev["_e"]).total_seconds()) * scale_h

    due = [(d, c, s) for d, c, s in snap.orders.get(line, []) if start <= d < end and d <= snap.now]
    on_time = sum(1 for d, c, s in due if s == M.ORDER_DONE and c is not None and c <= d)

    ideal = cfg.ideal_packs_per_h.get(line)
    dens = cfg.density_kg_L
    raw_kg = sum(snap.dosed_kg.get(b.get("batch_id"), 0.0) for b in done)
    meter = counter_steps(snap.readings.get(line, []), start, end)
    gas, elec = meter["heat_kwh_total"], meter["electricity_kwh_total"]

    def priced(*pairs):
        if any(price is None for _q, price in pairs) or meter["n"] == 0:
            return None
        return sum(q * price for q, price in pairs)
    co2 = cfg.co2_kg_per_kwh
    return {
        "units_produced": filled,
        "units_good": approved,
        "units_first_pass": approved,
        "units_defective": rejects,
        "units_inspected": filled,
        "run_hours": run_h,
        "planned_hours": plan_h,
        "downtime_hours": down_h,
        "ideal_units_at_run": run_h * ideal if ideal else None,
        "ideal_units": plan_h * ideal if ideal else None,
        "orders_on_time": float(on_time),
        "orders_planned": float(len(due)),
        "operating_hours": operating * scale_h,
        "failures": float(failures),
        "repeat_failures": float(repeats),
        "maintenance_costs": maint_eur,
        "asset_replacement_value": rv_share,
        "changeover_hours": changeover_h,
        "scrap_cost": rejects * cfg.value_per_pack if cfg.value_per_pack is not None else None,
        "rework_cost": (len(held) * cfg.rework_cost_per_batch
                        if cfg.rework_cost_per_batch is not None else None),
        "waste_kg": (rejects + blocked) * PACK_L * dens if dens else None,
        "raw_material_kg": raw_kg,
        # fase 3: meters. Without any reading in the window there is no measurement
        # (None), which is different from a measured zero consumption.
        "energy_kwh": gas + elec if meter["n"] else None,
        "water_l": meter["water_L_total"] if meter["n"] else None,
        "energy_costs": priced((gas, cfg.gas_price_per_kwh),
                               (elec, cfg.electricity_price_per_kwh)),
        "co2_kg": priced((gas, co2.get("gas")), (elec, co2.get("electricity"))),
        "revenue": (approved * cfg.sales_price_per_pack
                    if cfg.sales_price_per_pack is not None else None),
    }


METER_KPIS = {"KPI-ENE-001", "KPI-ENE-002", "KPI-ENE-003", "KPI-ENE-004"}


def meter_cover_start(snap: Snapshot, line: str) -> Optional[datetime]:
    """From when the meters of `line` (both lines for PLANT) were read; None = never."""
    lines = LINES if line == PLANT else (line,)
    firsts = [snap.readings[ln][0]["_t"] for ln in lines if snap.readings.get(ln)]
    return max(firsts) if len(firsts) == len(lines) else None


def counter_steps(readings: list[dict], start: datetime, end: datetime) -> dict:
    """Consumption per counter over [start, end): the sum of positive steps between
    consecutive readings, each step counted at the time of its later reading. A
    counter that went down (factory restart) counts its new value from zero."""
    out = {c: 0.0 for c in ("heat_kwh_total", "electricity_kwh_total", "steam_kg_total",
                            "water_L_total")}
    n = 0
    for a, b in zip(readings, readings[1:]):
        if not start <= b["_t"] < end:
            continue
        n += 1
        for c in out:
            if a.get(c) is None or b.get(c) is None:
                continue   # a counter not yet known is no consumption
            va, vb = _f(a.get(c)), _f(b.get(c))
            out[c] += vb - va if vb >= va else vb
    out["n"] = n
    return out


def pac_values(snap: Snapshot, line: str, start: datetime, end: datetime, cfg: Config) -> dict:
    """KPI-PAC-100..106 over [start, end): means over the pasteuriser samples in the
    window (all taken while milk flowed, so in steady operation) and over the CIP
    cycles that finished in it. None when there is no sample or cycle."""
    from .meters import U_CLEAN_RUN_H, u_value
    lines = LINES if line == PLANT else (line,)
    duty, us, rfs, dps, regen = [], [], [], [], []
    for ln in lines:
        rows = snap.pasteur.get(ln, [])
        # U_clean per cleaning cycle: the first samples after run_h dropped back
        cycle, clean = 0, {}
        cycles = []
        for i, r in enumerate(rows):
            if i and r["run_h_since_cip"] < rows[i - 1]["run_h_since_cip"]:
                cycle += 1
            cycles.append(cycle)
            if r["run_h_since_cip"] <= U_CLEAN_RUN_H and r.get("u"):
                clean.setdefault(cycle, []).append(r["u"])
        for r, cyc in zip(rows, cycles):
            if not start <= r["_t"] < end or _f(r.get("product_flow_kg_s")) <= 1.0:
                continue
            flow = _f(r["product_flow_kg_s"])
            duty.append(flow * cfg.product_cp * (_f(r["hold_t_C"]) - _f(r["regen_t_out_C"])))
            u = r.get("u") or u_value(r, cfg.product_cp, cfg.pasteuriser_area)
            if u:
                us.append(u)
                if clean.get(cyc):
                    u_clean = sum(clean[cyc]) / len(clean[cyc])
                    rfs.append(max(0.0, 1.0 / u - 1.0 / u_clean))
            dps.append(_f(r["dp_bar"]) * (cfg.pasteuriser_ref_flow / flow) ** 2)
            span = _f(r["hold_t_C"]) - _f(r["cold_t_in_C"])
            if span > 0:
                regen.append((_f(r["regen_t_out_C"]) - _f(r["cold_t_in_C"])) / span * 100.0)
    cycles = [c for ln in lines for c in snap.cip_cycles.get(ln, []) if start <= c["_t"] < end]
    run_h = [_f(c.get("past_run_h")) for c in cycles if c.get("past_run_h") is not None]
    costs = []
    prices = (cfg.caustic_price_per_kg, cfg.acid_price_per_kg, cfg.water_price_per_m3,
              cfg.gas_price_per_kwh, cfg.electricity_price_per_kwh, cfg.cip_labour_hours,
              cfg.labour_cost_per_hour)
    if all(p is not None for p in prices):
        for c in cycles:
            costs.append(_f(c.get("caustic_kg")) * cfg.caustic_price_per_kg
                         + _f(c.get("acid_kg")) * cfg.acid_price_per_kg
                         + _f(c.get("water_L")) / 1000.0 * cfg.water_price_per_m3
                         + _f(c.get("heat_kwh")) * cfg.gas_price_per_kwh
                         + _f(c.get("electricity_kwh")) * cfg.electricity_price_per_kwh
                         + cfg.cip_labour_hours * cfg.labour_cost_per_hour)

    def mean(xs):
        return sum(xs) / len(xs) if xs else None
    return {"KPI-PAC-100": (mean(duty), len(duty)), "KPI-PAC-101": (mean(us), len(us)),
            "KPI-PAC-102": (mean(rfs), len(rfs)), "KPI-PAC-103": (mean(dps), len(dps)),
            "KPI-PAC-104": (mean(regen), len(regen)),
            "KPI-PAC-105": (mean(run_h), len(run_h)), "KPI-PAC-106": (mean(costs), len(costs))}


# ------------------------------------------------------------------ formula


_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
            ast.USub, ast.UAdd, ast.Constant, ast.Name, ast.Load)


def evaluate(formula: str, values: dict) -> Optional[float]:
    """Catalogue formula over `values`. None when an input is None or a division is by zero."""
    tree = ast.parse(formula, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"formula not supported here: {formula!r}")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.Name):
            v = values.get(n.id)
            if v is None:
                raise _Missing(n.id)
            return float(v)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        a, b = ev(n.left), ev(n.right)
        if isinstance(n.op, ast.Add):
            return a + b
        if isinstance(n.op, ast.Sub):
            return a - b
        if isinstance(n.op, ast.Mult):
            return a * b
        if b == 0:
            raise _Missing("/0")
        return a / b

    try:
        return ev(tree)
    except _Missing:
        return None


class _Missing(Exception):
    pass


def kpi_values(snap: Snapshot, line: str, start: datetime, end: datetime, cfg: Config,
               catalogue: list[dict]) -> dict:
    """{kpi_id: {"value", "status", "inputs"}} for all 29 KPIs over [start, end)."""
    vals = inputs(snap, line, start, end, cfg)
    # A meter KPI is a ratio of metered consumption to production. Production must cover
    # the same time as the meters, or consumption is spread over packs nobody metered
    # (the meters started on 2026-11-19 while the window reached 8 h back).
    cover = meter_cover_start(snap, line)
    vals_m = None if cover is None else (
        vals if cover <= start else inputs(snap, line, max(start, cover), end, cfg))
    pac = None
    out = {}
    for k in catalogue:
        kid, spec = k["id"], BVIP_MAP[k["id"]]
        names = [i["naam"] for i in k["inputs"]]
        if kid in METER_KPIS and spec["status"] != NA:
            mv = vals_m or {}
            value = evaluate(k["formule"], {n: mv.get(n) for n in names}) if vals_m else None
            ok = STATUS_DEELS if spec["status"] == DEELS else STATUS_OK
            out[kid] = {"value": value, "status": ok if value is not None else STATUS_UNSET,
                        "inputs": {n: mv.get(n) for n in names}}
            continue
        if spec["status"] == NA:
            out[kid] = {"value": None, "status": STATUS_NA, "inputs": {}}
            continue
        if kid.startswith("KPI-PAC-"):
            if pac is None:
                pac = pac_values(snap, line, start, end, cfg)
            value, n = pac[kid]
            ok = STATUS_DEELS if spec["status"] == DEELS else STATUS_OK
            out[kid] = {"value": value, "status": ok if value is not None else STATUS_UNSET,
                        "inputs": {"n": float(n)}}
            continue
        missing = set(spec.get("ontbreekt") or [])
        used = {n: (0.0 if n in missing else vals.get(n)) for n in names}
        value = evaluate(k["formule"], used)
        if value is None:
            status = STATUS_UNSET
        else:
            status = STATUS_DEELS if spec["status"] == DEELS else STATUS_OK
        out[kid] = {"value": value, "status": status,
                    "inputs": {n: vals.get(n) for n in names if n not in missing}}
    return out


def additive_kpis(catalogue: list[dict]) -> list[dict]:
    """KPIs whose inputs are written per bucket: computable and additive."""
    return [k for k in catalogue if BVIP_MAP[k["id"]]["status"] != NA
            and BVIP_MAP[k["id"]].get("additief")]


def floor_bucket(t: datetime, step_s: int) -> datetime:
    epoch = int(t.timestamp())
    return datetime.fromtimestamp(epoch - epoch % step_s, tz=timezone.utc)


def window(end: datetime, hours: float) -> tuple[datetime, datetime]:
    return end - timedelta(hours=hours), end
