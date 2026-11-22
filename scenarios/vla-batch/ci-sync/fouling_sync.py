"""milkdemo -> aios Fouling Intelligence: buckets per cleaning cycle and CIP costs (BVIP UC-01/UC-02).

Reads the two stables the batch engine writes (vla/fouling_history.py):

  idp.fouling    one row per 0.25 run hours of one cleaning cycle (complete buckets only)
  idp.cip_cycle  one row per finished CIP, cost split in cash and labour

and pushes per line, into the value chain of the same workspace as chain.py:

  KPI-PAC-104 recovery %          per bucket, with the cycle context the schedule analysis needs
  KPI-PAC-102 fouling resistance  per bucket, same context (and the input of DR-007)
  KPI-PAC-105 run hours           per finished CIP
  KPI-PAC-106 cleaning cost cash  per finished CIP (labour in the context: it only counts as money
                                  when the hours really go away)

then runs detection and POST /chain/fouling/analyse. The buckets are final when the engine writes
them, so "new" is exact: every row after the latest measurement in aios (current_at), not per hour.

Value inputs (on KPI-PAC-104, for the analysis) come from the factory model, all synthetic demo
assumptions, and one measured value: run hours per year = pasteuriser run hours per process hour
over the CIPs in the last VALUE_DAYS days x 8760 (the demo runs SYNC_TIME_SCALE times faster than
real time). KPI-PAC-102 gets the critical value 0.10 m2K/kW of UC-02 as its own threshold.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import chain as C

log = logging.getLogger("ci-sync.fouling")

FOULING_KPIS = {
    "KPI-PAC-104": {"department": "Receiving", "method": "Per 0.25 run hours of one cleaning cycle: flow-weighted "
                    "(regen_t_out - cold_t_in) / (hold_t - cold_t_in) x 100 (vla engine, idp.fouling)."},
    "KPI-PAC-102": {"department": "Receiving", "critical": 0.10,
                    "description": "Critical 0.10 m2K/kW from BVIP UC-02 (illustrative, to be calibrated per exchanger).",
                    "method": "Per 0.25 run hours of one cleaning cycle: mean of 1/U - 1/U_clean, U_clean from the "
                              "first samples after the CIP (vla engine, idp.fouling)."},
    "KPI-PAC-105": {"department": "Receiving", "method": "Pasteuriser run hours of each finished cleaning cycle (idp.cip_cycle)."},
    "KPI-PAC-106": {"department": "Receiving", "method": "Cash cost of each finished cleaning cycle: chemicals, water and "
                    "energy at the cost_model prices; labour in the context (idp.cip_cycle)."},
}
SOURCE = "demo: vla historian idp.fouling (simulated plant)"
CIP_SOURCE = "demo: vla historian idp.cip_cycle (simulated plant)"
MODEL = "factory model of the vla demo (synthetic demo assumption)"


def after(current_at: str | None, default: datetime) -> datetime:
    """The first instant after the latest measurement in aios (the buckets are final, so exact)."""
    if not current_at:
        return default
    t = datetime.fromisoformat(current_at.replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)) + timedelta(milliseconds=1)


def fouling_sql(db: str, line: str, start: datetime, end: datetime) -> str:
    return (f"select cast(ts as bigint), run_h, rf, recovery_pct, flow_kg_s, throughput_kg, hold_t_c, cold_t_in_c, "
            f"cycle_start, exchanger from {db}.fouling where `line` = '{line}' and ts >= {C.ms(start)} "
            f"and ts < {C.ms(end)} order by ts")


def cip_sql(db: str, line: str, start: datetime, end: datetime) -> str:
    return (f"select cast(ts as bigint), run_h, cash_eur, labour_eur, exchanger from {db}.cip_cycle "
            f"where `line` = '{line}' and ts >= {C.ms(start)} and ts < {C.ms(end)} order by ts")


def bucket_points(rows: list[list[Any]]) -> dict[str, list[dict[str, Any]]]:
    """Rows of idp.fouling -> measurements for KPI-PAC-104 and KPI-PAC-102, each with the cycle context."""
    out: dict[str, list[dict[str, Any]]] = {"KPI-PAC-104": [], "KPI-PAC-102": []}
    for t, run_h, rf, rec, flow, kg, hold, cold, cycle_start, exchanger in rows:
        ctx = {"cycle_start": C.from_ms(int(cycle_start)).isoformat(), "run_h": run_h, "throughput_kg": kg,
               "hold_t_c": hold, "cold_t_in_c": cold, "flow_kg_s": flow, "exchanger": exchanger}
        at = C.from_ms(int(t)).isoformat()
        if rec is not None:
            out["KPI-PAC-104"].append({"at": at, "value": round(float(rec), 4), "source": SOURCE, "context": ctx})
        if rf is not None:
            out["KPI-PAC-102"].append({"at": at, "value": round(float(rf), 4), "source": SOURCE, "context": ctx})
    return out


def cip_points(rows: list[list[Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"KPI-PAC-105": [], "KPI-PAC-106": []}
    for t, run_h, cash, labour, exchanger in rows:
        at = C.from_ms(int(t)).isoformat()
        if run_h is not None:
            out["KPI-PAC-105"].append({"at": at, "value": round(float(run_h), 4), "source": CIP_SOURCE,
                                       "context": {"exchanger": exchanger}})
        if cash is not None:   # without prices the cost is unknown, never 0
            out["KPI-PAC-106"].append({"at": at, "value": round(float(cash), 4), "source": CIP_SOURCE,
                                       "context": {"exchanger": exchanger, "labour_eur": labour}})
    return out


def run_hours_per_year(cip_rows: list[list[Any]], scale: float) -> float | None:
    """Pasteuriser run hours per process hour between the first and last CIP, x 8760."""
    if len(cip_rows) < 2 or scale <= 0:
        return None
    t0, t1 = int(cip_rows[0][0]), int(cip_rows[-1][0])
    process_h = (t1 - t0) / 3_600_000 * scale
    run = sum(float(r[1]) for r in cip_rows[1:] if r[1] is not None)
    return run / process_h * C.HOURS_PER_YEAR if process_h > 0 else None


def value_inputs(model: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """Inputs of the schedule analysis from the factory model, plus its HACCP limits per line.

    Missing stays missing (aios then says insufficient_data)."""
    pc, cost = model.get("process_constants") or {}, model.get("cost_model") or {}
    haccp = (model.get("food_safety") or {}).get("haccp_max_run_h")
    out: dict[str, Any] = {}
    if isinstance(pc.get("product_cp_kj_kg_k"), (int, float)):
        out["cp_kj_kg_k"] = C._iv(pc["product_cp_kj_kg_k"], f"process_constants, {MODEL}", "assumed")
    if isinstance(pc.get("boiler_efficiency"), (int, float)):
        out["boiler_efficiency"] = C._iv(pc["boiler_efficiency"], f"process_constants, {MODEL}", "assumed")
    if isinstance(cost.get("gas_price_per_kwh"), (int, float)):
        out["thermal_price"] = C._iv(cost["gas_price_per_kwh"], f"gas price of the cost_model, {MODEL}", "assumed")
    return out, haccp


class FoulingSync:
    def __init__(self, td: C.Td, aios, workspace_id: str, lines: list[str], db: str = "idp",
                 model: dict[str, Any] | None = None, scale: float = 1.0, since: datetime | None = None,
                 dry_run: bool = False, now=None) -> None:
        self.td, self.aios, self.db, self.lines = td, aios, db, lines
        self.base = f"/api/v1/workspaces/{workspace_id}/chain"
        self.model = model or {}
        self.scale = scale
        self.since = since or datetime(2026, 11, 17, tzinfo=timezone.utc)
        self.dry_run = dry_run
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.inputs_day = ""

    def _inputs(self, line: str, end: datetime) -> dict[str, Any]:
        cips = self.td.query(cip_sql(self.db, line, end - timedelta(days=C.VALUE_DAYS), end))
        base, haccp = value_inputs(self.model)
        rhy = run_hours_per_year(cips, self.scale)
        if rhy is not None:
            base["run_hours_per_year"] = C._iv(rhy, f"vla historian, last {C.VALUE_DAYS} days: pasteuriser run hours "
                                                    f"per process hour x 8760 (time scale {self.scale:g})", "measured")
        if isinstance(haccp, dict) and isinstance(haccp.get(line), (int, float)):
            base["haccp_max_run_h"] = C._iv(haccp[line], f"food_safety.haccp_max_run_h, {MODEL}; in a real plant the "
                                                        f"quality manager's HACCP plan", "assumed")
        return base

    def _ensure(self, known: dict, kid: str, line: str, inputs: dict[str, Any]) -> dict[str, Any] | None:
        k = known.get((kid, C.scope(line)))
        if k is not None or self.dry_run:
            return k
        spec = FOULING_KPIS[kid]
        body = {"catalogue_kpi_id": kid, "scope": C.scope(line), "department": spec["department"],
                "frequency": "per run-time bucket" if kid in ("KPI-PAC-104", "KPI-PAC-102") else "per cleaning cycle",
                "measurement_method": spec["method"]}
        for f in ("critical", "description"):
            if f in spec:
                body[f] = spec[f]
        if kid == "KPI-PAC-104":
            body["value_inputs"] = inputs
        k = self.aios.call("POST", f"{self.base}/kpis", body)
        k = self.aios.call("PATCH", f"{self.base}/kpis/{k['id']}", {"name": f"{k['name']} · {C.scope(line)}"})
        known[(kid, C.scope(line))] = k
        return k

    def run_once(self) -> dict[str, str]:
        now = self.now()
        today = now.date().isoformat()
        refresh = self.inputs_day != today
        known = {(k.get("catalogue_kpi_id"), k.get("scope")): k
                 for k in self.aios.call("GET", f"{self.base}/kpis?archived=true")}
        report: dict[str, str] = {}
        added = 0
        for line in self.lines:
            inputs = self._inputs(line, now)
            ks = {kid: self._ensure(known, kid, line, inputs) for kid in FOULING_KPIS}
            first = {kid: after((k or {}).get("current_at"), self.since) for kid, k in ks.items()}
            b0 = min(first["KPI-PAC-104"], first["KPI-PAC-102"])
            c0 = min(first["KPI-PAC-105"], first["KPI-PAC-106"])
            pts = {**bucket_points(self.td.query(fouling_sql(self.db, line, b0, now))),
                   **cip_points(self.td.query(cip_sql(self.db, line, c0, now)))}
            for kid, k in ks.items():
                name = f"{kid} · {C.scope(line)}"
                if k is not None and k.get("archived"):
                    report[name] = "archived in aios, skipped"
                    continue
                new = [p for p in pts[kid] if datetime.fromisoformat(p["at"]) >= first[kid]]
                notes = []
                if (kid == "KPI-PAC-104" and k is not None and refresh and not self.dry_run
                        and C.inputs_changed(k.get("value_inputs") or {}, inputs)):
                    self.aios.call("PATCH", f"{self.base}/kpis/{k['id']}", {"value_inputs": inputs})
                    notes.append("value inputs updated")
                if not new:
                    report[name] = ", ".join(["nothing new", *notes])
                    continue
                if self.dry_run or k is None:
                    report[name] = f"dry-run: {len(new)} from {new[0]['at']}"
                    continue
                for i in range(0, len(new), 5000):
                    self.aios.call("POST", f"{self.base}/kpis/{k['id']}/measurements", {"items": new[i:i + 5000]})
                added += len(new)
                report[name] = ", ".join([f"{len(new)} added, last {new[-1]['at']}", *notes])
        if not self.dry_run:
            self.inputs_day = today
        if added:
            found = self.aios.call("POST", f"{self.base}/detect")
            report["fouling detection"] = f"{len(found.get('created') or [])} new opportunities"
            res = self.aios.call("POST", f"{self.base}/fouling/analyse").get("results") or []
            report["schedule analysis"] = "; ".join(
                f"{r['scope']}: {r['status']}" + (f", {r.get('action')} EUR {r.get('amount')}/yr, every "
                                                   f"{r.get('target_h')} instead of {r.get('current_h')} run h"
                                                   if r["status"] == "computed" else "") for r in res) or "no scopes"
        return report


def load_model(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError) as e:
        log.warning("no factory model from %s (%s): fouling value inputs stay missing", path, e)
        return {}
