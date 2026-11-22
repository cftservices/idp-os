"""milkdemo -> aios Continuous Improvement: push batch data into CI worksheets.

Runs inside the milkdemo network, reads the batch engine (read-only) and writes worksheets
into one aios workspace through its REST API with a workspace-scoped PAT. Push, not pull: the
two stacks stay apart (aios never joins this network, the batch engine never knows aios).

The batch engine prunes autopilot batches after 24 hours, so the history lives in aios: each
run appends the completed batches it has not seen yet (by batch_id) and rebuilds the daily KPI
worksheets from that history. Nothing in the batch engine is changed.

Worksheets per line (L1, L2):
  "Vla <line> · batches"                     one row per completed batch
  "Vla <line> · KPI-QUA-001 Defectpercentage" per day: rejected packs / filled packs
  "Vla <line> · KPI-PRD-005 First Pass Yield" per day: good packs of approved batches / filled packs
  "Vla <line> · KPI-PRD-001 OEE"              per day: the five OEE inputs (only with an ideal rate)

Once per UTC day (the first sync of the day) the sync also runs and stores the daily analyses
in aios: the KPI series of every KPI worksheet, an I-MR chart and a capability analysis against
the recipe spec on the viscosity of each line, and an I-MR chart on the peak cook temperature
(the cause, which signals before the viscosity does: first 8D, 2026-11-17). The I-MR limits come
from the first DAILY_BASE_N batches and are then held, so a later incident does not widen them.
A day that already has a run with the same analysis and parameters is skipped, so a restart does
not run it twice. SYNC_DAILY_RUNS=0 switches this off.

Pack counts follow the factory: packs_total counts GOOD packs only, filled = packs_total +
reject_count. OEE times are process hours: wall-clock hours times SYNC_TIME_SCALE (the demo runs
faster than real time, like the MES's KPI_TIME_SCALE). Planned hours assume the line is planned
around the clock from SYNC_FROM on (the autopilot runs continuously); the ideal rate per line
comes from SYNC_IDEAL_PACKS_PER_H ("L1:10300,L2:10300"), measured as the fault-free batch cycle
of the factory model. Without an ideal rate there is no OEE worksheet.

Environment: BATCH_ENGINE_URL, AIOS_URL, AIOS_WORKSPACE_ID, AIOS_PAT, SYNC_LINES (L1,L2),
SYNC_INTERVAL_S (900), SYNC_FROM (ISO time; batches completed earlier are left out, for
example the deterministic batches from before SIM_VARIATION was switched on). Run once with --once; --dry-run prints and writes nothing.
Standard library only.

Value chain (chain.py): every run also pushes hourly KPI measurements from the historian into the
value chain of the same workspace and runs detection there. It needs TD_USER and TD_PASS of its
own TDengine user (chain_sync) in ci-sync/.env; without them that step is off and says so once.
That user is a separate credential, not a privilege boundary: see the docstring of chain.py.
SYNC_CHAIN=0 switches it off.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any

import chain

log = logging.getLogger("ci-sync")

BATCH_COLUMNS: list[tuple[str, str]] = [
    ("batch_id", "text"),
    ("started_at", "text"),
    ("completed_at", "text"),
    ("recipe_id", "text"),
    ("peak_cook_temp_C", "numeric"),
    ("hold_elapsed_sec", "numeric"),
    ("cook_heatup_sec", "numeric"),
    ("end_viscosity_cP", "numeric"),
    ("spec_min_cP", "numeric"),
    ("spec_max_cP", "numeric"),
    ("packs_total", "numeric"),
    ("reject_count", "numeric"),
    ("verdict", "text"),
    ("fault", "text"),
]

KPIS = {
    "KPI-QUA-001": ("Defectpercentage", "units_defective", "units_inspected"),
    "KPI-PRD-005": ("First Pass Yield", "units_first_pass", "units_produced"),
}


# --------------------------------------------------------------------------- pure functions


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def batch_row(b: dict[str, Any]) -> dict[str, Any]:
    """One worksheet row from a batch detail document of the batch engine."""
    row: dict[str, Any] = {}
    for name, typ in BATCH_COLUMNS:
        v = b.get(name)
        row[name] = _num(v) if typ == "numeric" else (None if v is None else str(v))
    return row


def merge_rows(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Existing rows plus new batches, one row per batch_id, in completion order."""
    by_id: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for r in existing + new:
        if not r.get("batch_id"):
            continue
        old = by_id.get(r["batch_id"])
        if old is None:
            by_id[r["batch_id"]] = dict(r)
        else:  # a stored value is never rewritten; an empty stored cell is filled
            for k, v in r.items():
                if old.get(k) is None and v is not None:
                    old[k] = v
    return sorted(by_id.values(), key=lambda r: (r.get("completed_at") or "", r["batch_id"]))


def rows_to_columns(rows: list[dict[str, Any]],
                    extra: list[tuple[str, str]] | None = None) -> list[dict[str, Any]]:
    """The batch columns, plus columns a person added in aios (for example a stage column
    for before and after a repair). Those are kept; new rows get an empty cell there."""
    cols = list(BATCH_COLUMNS) + list(extra or [])
    return [{"name": n, "type": t, "values": [r.get(n) for r in rows]} for n, t in cols]


def extra_columns(columns: list[dict[str, Any]]) -> list[tuple[str, str]]:
    own = {n for n, _ in BATCH_COLUMNS}
    return [(c["name"], c["type"]) for c in columns if c["name"] not in own]


def columns_to_rows(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not columns:
        return []
    n = len(columns[0]["values"])
    return [{c["name"]: c["values"][i] for c in columns} for i in range(n)]


def daily_kpi(rows: list[dict[str, Any]], kpi_id: str) -> list[dict[str, Any]]:
    """Per day the KPI inputs, never the percentage. Day = date of completion (UTC)."""
    _, num_name, den_name = KPIS[kpi_id]
    days: "OrderedDict[str, list[float]]" = OrderedDict()
    for r in sorted(rows, key=lambda r: r.get("completed_at") or ""):
        day = (r.get("completed_at") or "")[:10]
        good, rejects = r.get("packs_total"), r.get("reject_count")
        if not day or good is None:
            continue
        acc = days.setdefault(day, [0.0, 0.0])
        rejects = rejects or 0.0
        if kpi_id == "KPI-QUA-001":
            acc[0] += rejects
        else:
            acc[0] += good if r.get("verdict") == "APPROVED" else 0.0
        acc[1] += good + rejects  # filled packs
    cols = [
        {"name": "periode", "type": "text", "values": list(days), "kpi_id": kpi_id, "rol": "periode"},
        {"name": num_name, "type": "numeric", "values": [v[0] for v in days.values()],
         "kpi_id": kpi_id, "rol": "teller"},
        {"name": den_name, "type": "numeric", "values": [v[1] for v in days.values()],
         "kpi_id": kpi_id, "rol": "noemer"},
    ]
    return cols


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _union_seconds(spans: list[tuple[datetime, datetime]]) -> float:
    total, cur_s, cur_e = 0.0, None, None
    for a, z in sorted(spans):
        if cur_e is None or a > cur_e:
            if cur_e is not None:
                total += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = a, z
        elif z > cur_e:
            cur_e = z
    if cur_e is not None:
        total += (cur_e - cur_s).total_seconds()
    return total


def daily_oee(rows: list[dict[str, Any]], since: str, now: datetime, scale: float,
              ideal_per_h: float) -> list[dict[str, Any]]:
    """Per day the five inputs of KPI-PRD-001, in process hours and packs."""
    start = _dt(since)
    days: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for r in sorted(rows, key=lambda r: r.get("completed_at") or ""):
        s_, e_ = _dt(r.get("started_at")), _dt(r.get("completed_at"))
        good, rejects = r.get("packs_total"), r.get("reject_count") or 0.0
        if not e_ or good is None:
            continue
        d = days.setdefault(e_.date().isoformat(), {"spans": [], "filled": 0.0, "good": 0.0, "last": e_})
        d["last"] = max(d["last"], e_)
        d["filled"] += good + rejects
        d["good"] += good if r.get("verdict") == "APPROVED" else 0.0
        if s_ and e_ > s_:
            d["spans"].append((s_, e_))
    periods, planned, run, ideal, good, produced = [], [], [], [], [], []
    for day, d in days.items():
        lo = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        hi = lo + timedelta(days=1)
        # A day still running ends at its last completed batch: a batch in progress is not
        # yet run time, so counting planned time up to "now" would understate availability.
        lo, hi = (max(lo, start) if start else lo), (d["last"] if hi > now else hi)
        plan_h = max(0.0, (hi - lo).total_seconds()) / 3600 * scale
        spans = [(max(a, lo), min(z, hi)) for a, z in d["spans"] if min(z, hi) > max(a, lo)]
        run_h = _union_seconds(spans) / 3600 * scale
        periods.append(day)
        planned.append(round(plan_h, 4))
        run.append(round(run_h, 4))
        ideal.append(round(run_h * ideal_per_h, 1))
        good.append(d["good"])
        produced.append(d["filled"])
    k = "KPI-PRD-001"
    return [
        {"name": "periode", "type": "text", "values": periods, "kpi_id": k, "rol": "periode"},
        {"name": "ideal_units_at_run", "type": "numeric", "values": ideal, "kpi_id": k},
        {"name": "planned_hours", "type": "numeric", "values": planned, "kpi_id": k},
        {"name": "run_hours", "type": "numeric", "values": run, "kpi_id": k},
        {"name": "units_good", "type": "numeric", "values": good, "kpi_id": k},
        {"name": "units_produced", "type": "numeric", "values": produced, "kpi_id": k},
    ]


# Limits of the daily I-MR charts: estimated from the first N batches since SYNC_FROM, then held.
DAILY_BASE_N = 20


def same_run(stored: dict[str, Any], analysis_id: str, params: dict[str, Any]) -> bool:
    """A stored analysis is the same daily run when its id matches and it has our parameters.

    The server stores the parameters with defaults filled in, so ours are a subset of them.
    """
    sp = stored.get("params") or {}
    return stored.get("analysis_id") == analysis_id and all(sp.get(k) == v for k, v in params.items())


def daily_runs(line: str, recipe_spec: str) -> list[tuple[str, str, dict[str, Any]]]:
    """The analyses run once a day per line: (worksheet name, analysis id, params)."""
    runs: list[tuple[str, str, dict[str, Any]]] = [
        (f"Vla {line} · {k} {t}", "kpi_series", {"kpi_id": k, "period_column": "periode"})
        for k, (t, _, _) in KPIS.items()]
    runs.append((f"Vla {line} · KPI-PRD-001 OEE", "kpi_series",
                 {"kpi_id": "KPI-PRD-001", "period_column": "periode"}))
    runs.append((f"Vla {line} · batches", "imr_chart",
                 {"column": "end_viscosity_cP", "label_column": "batch_id", "base_n": DAILY_BASE_N}))
    runs.append((f"Vla {line} · batches", "imr_chart",
                 {"column": "peak_cook_temp_C", "label_column": "batch_id", "base_n": DAILY_BASE_N}))
    runs.append((f"Vla {line} · batches", "capability_normal",
                 {"column": "end_viscosity_cP", "lsl": 150, "usl": 300, "spec_source": recipe_spec}))
    return runs


RECIPE_SPEC = ("recipe spec 150-300 cP (spec_min_cP / spec_max_cP of the recipe in the milkdemo MES; "
               "also columns of this worksheet)")


def parse_ideal(spec: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in (spec or "").split(","):
        if ":" in part:
            line, v = part.split(":", 1)
            out[line.strip()] = float(v)
    return out


# --------------------------------------------------------------------------- HTTP


class Http:
    def __init__(self, base: str, token: str | None = None, timeout: float = 30.0) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:500].decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from e
        return json.loads(raw) if raw else None


# --------------------------------------------------------------------------- sync


class Sync:
    def __init__(self, engine: Http, aios: Http, workspace_id: str, lines: list[str],
                 dry_run: bool = False, since: str = "", scale: float = 1.0,
                 ideal: dict[str, float] | None = None, now=None) -> None:
        self.since = since
        self.scale = scale
        self.ideal = ideal or {}
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.engine = engine
        self.aios = aios
        self.ci = f"/api/v1/workspaces/{workspace_id}/ci"
        self.lines = lines
        self.dry_run = dry_run

    def _sheets(self) -> dict[str, dict[str, Any]]:
        return {s["name"]: s for s in self.aios.call("GET", f"{self.ci}/worksheets")}

    def _write(self, sheets: dict[str, dict[str, Any]], name: str, columns: list[dict[str, Any]]) -> str:
        if not columns or not columns[0]["values"]:
            return "empty"
        meta = sheets.get(name)
        if self.dry_run:
            return f"dry-run ({len(columns[0]['values'])} rows)"
        if meta is None:
            self.aios.call("POST", f"{self.ci}/worksheets", {"name": name, "columns": columns})
            return "created"
        current = self.aios.call("GET", f"{self.ci}/worksheets/{meta['id']}")
        if [(c["name"], c["values"]) for c in current["data"]] == [(c["name"], c["values"]) for c in columns]:
            return "unchanged"
        self.aios.call("PUT", f"{self.ci}/worksheets/{meta['id']}",
                       {"name": name, "columns": columns, "version": current["version"]})
        return f"updated to v{current['version'] + 1}"

    def run_daily(self) -> dict[str, str]:
        """Once per UTC day: run and store the daily analyses, unless today's run exists."""
        today = self.now().date().isoformat()
        sheets = self._sheets()
        report: dict[str, str] = {}
        for line in self.lines:
            for name, analysis_id, params in daily_runs(line, RECIPE_SPEC):
                key = f"daily {name} / {analysis_id}" + (f" {params['column']}" if "column" in params else "")
                meta = sheets.get(name)
                if meta is None:
                    report[key] = "no worksheet"
                    continue
                done = self.aios.call("GET", f"{self.ci}/analyses?worksheet_id={meta['id']}&limit=50")
                if any(same_run(a, analysis_id, params) and (a.get("created_at") or "")[:10] == today
                       and a.get("channel") == "api" for a in done):
                    report[key] = "already ran today"
                    continue
                if self.dry_run:
                    report[key] = "dry-run"
                    continue
                try:
                    self.aios.call("POST", f"{self.ci}/analyses",
                                   {"worksheet_id": meta["id"], "analysis_id": analysis_id, "params": params})
                    report[key] = "ran"
                except RuntimeError as e:  # for example too few points yet; try again tomorrow
                    report[key] = f"not run: {e}"
        return report

    def run_once(self) -> dict[str, str]:
        sheets = self._sheets()
        report: dict[str, str] = {}
        for line in self.lines:
            name = f"Vla {line} · batches"
            existing: list[dict[str, Any]] = []
            extra: list[tuple[str, str]] = []
            if name in sheets:
                data = self.aios.call("GET", f"{self.ci}/worksheets/{sheets[name]['id']}")["data"]
                existing, extra = columns_to_rows(data), extra_columns(data)
            known = {r["batch_id"] for r in existing}
            complete = {r["batch_id"] for r in existing if r.get("started_at")}
            listed = self.engine.call("GET", f"/batches?line={line}")
            new = [batch_row(self.engine.call("GET", f"/batches/{b['batch_id']}"))
                   for b in listed if b.get("state") == "COMPLETE" and b["batch_id"] not in complete
                   and (b.get("completed_at") or "") >= self.since]
            rows = merge_rows(existing, new)
            fresh = len([r for r in new if r["batch_id"] not in known])
            report[name] = f"{self._write(sheets, name, rows_to_columns(rows, extra))}, {fresh} new"
            for kpi_id, (title, _, _) in KPIS.items():
                kname = f"Vla {line} · {kpi_id} {title}"
                report[kname] = self._write(sheets, kname, daily_kpi(rows, kpi_id))
            oname = f"Vla {line} · KPI-PRD-001 OEE"
            if line in self.ideal:
                report[oname] = self._write(sheets, oname, daily_oee(
                    rows, self.since, self.now(), self.scale, self.ideal[line]))
            else:
                report[oname] = "skipped: no ideal rate for this line"
        return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="run one sync and exit")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = os.environ
    missing = [k for k in ("AIOS_URL", "AIOS_WORKSPACE_ID", "AIOS_PAT") if not env.get(k)]
    if missing:
        log.error("missing environment: %s", ", ".join(missing))
        return 2
    sync = Sync(Http(env.get("BATCH_ENGINE_URL", "http://vla-batch-engine:8000/api/v1")),
                Http(env["AIOS_URL"].rstrip("/"), env["AIOS_PAT"]),
                env["AIOS_WORKSPACE_ID"],
                [x.strip() for x in env.get("SYNC_LINES", "L1,L2").split(",") if x.strip()],
                dry_run=args.dry_run, since=env.get("SYNC_FROM", ""),
                scale=float(env.get("SYNC_TIME_SCALE", "1")),
                ideal=parse_ideal(env.get("SYNC_IDEAL_PACKS_PER_H", "")))
    value_chain = make_chain(env, args.dry_run)
    interval = float(env.get("SYNC_INTERVAL_S", "900"))
    while True:
        try:
            for name, outcome in sync.run_once().items():
                log.info("%s: %s", name, outcome)
            if env.get("SYNC_DAILY_RUNS", "1") == "1":
                for name, outcome in sync.run_daily().items():
                    if outcome != "already ran today":
                        log.info("%s: %s", name, outcome)
        except Exception as e:  # keep running; the next run tries again
            log.error("sync failed: %s", e)
            if args.once:
                return 1
        if value_chain is not None:
            try:  # apart from the CI sync: one failing never stops the other
                for name, outcome in value_chain.run_once().items():
                    if outcome != "no new closed hours":
                        log.info("chain %s: %s", name, outcome)
            except Exception as e:
                log.error("chain sync failed: %s", e)
                if args.once:
                    return 1
        if args.once:
            return 0
        time.sleep(interval)


def make_chain(env, dry_run: bool) -> "chain.ChainSync | None":
    if env.get("SYNC_CHAIN", "1") != "1":
        return None
    if not (env.get("TD_USER") and env.get("TD_PASS")):
        log.warning("chain step off: TD_USER and TD_PASS (TDengine user chain_sync) missing in ci-sync/.env")
        return None
    since = env.get("SYNC_CHAIN_FROM") or env.get("SYNC_FROM") or "2026-11-17T00:00:00+00:00"
    return chain.ChainSync(
        chain.Td(env.get("TD_URL", "http://vla-tdengine:6041"), env["TD_USER"], env["TD_PASS"]),
        Http(env["AIOS_URL"].rstrip("/"), env["AIOS_PAT"]), env["AIOS_WORKSPACE_ID"],
        [x.strip() for x in env.get("SYNC_LINES", "L1,L2").split(",") if x.strip()],
        db=env.get("TD_DB", "idp"), cost=chain.load_cost(env.get("FACTORY_MODEL", "/model/isa95-vla.json")),
        since=datetime.fromisoformat(since), dry_run=dry_run)


if __name__ == "__main__":
    sys.exit(main())
