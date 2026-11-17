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
  "Vla <line> · KPI-PRD-005 First Pass Yield" per day: packs of approved batches minus their
                                              rejects / all filled packs

Environment: BATCH_ENGINE_URL, AIOS_URL, AIOS_WORKSPACE_ID, AIOS_PAT, SYNC_LINES (L1,L2),
SYNC_INTERVAL_S (900), SYNC_FROM (ISO time; batches completed earlier are left out, for
example the deterministic batches from before SIM_VARIATION was switched on). Run once with --once; --dry-run prints and writes nothing.
Standard library only.
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
from typing import Any

log = logging.getLogger("ci-sync")

BATCH_COLUMNS: list[tuple[str, str]] = [
    ("batch_id", "text"),
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
        if r.get("batch_id"):
            by_id.setdefault(r["batch_id"], r)
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
        packs, rejects = r.get("packs_total"), r.get("reject_count")
        if not day or packs is None:
            continue
        acc = days.setdefault(day, [0.0, 0.0])
        rejects = rejects or 0.0
        if kpi_id == "KPI-QUA-001":
            acc[0] += rejects
        else:
            acc[0] += (packs - rejects) if r.get("verdict") == "APPROVED" else 0.0
        acc[1] += packs
    cols = [
        {"name": "periode", "type": "text", "values": list(days), "kpi_id": kpi_id, "rol": "periode"},
        {"name": num_name, "type": "numeric", "values": [v[0] for v in days.values()],
         "kpi_id": kpi_id, "rol": "teller"},
        {"name": den_name, "type": "numeric", "values": [v[1] for v in days.values()],
         "kpi_id": kpi_id, "rol": "noemer"},
    ]
    return cols


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
                 dry_run: bool = False, since: str = "") -> None:
        self.since = since
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
            listed = self.engine.call("GET", f"/batches?line={line}")
            new = [batch_row(self.engine.call("GET", f"/batches/{b['batch_id']}"))
                   for b in listed if b.get("state") == "COMPLETE" and b["batch_id"] not in known
                   and (b.get("completed_at") or "") >= self.since]
            rows = merge_rows(existing, new)
            report[name] = f"{self._write(sheets, name, rows_to_columns(rows, extra))}, {len(new)} new"
            for kpi_id, (title, _, _) in KPIS.items():
                kname = f"Vla {line} · {kpi_id} {title}"
                report[kname] = self._write(sheets, kname, daily_kpi(rows, kpi_id))
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
                dry_run=args.dry_run, since=env.get("SYNC_FROM", ""))
    interval = float(env.get("SYNC_INTERVAL_S", "900"))
    while True:
        try:
            for name, outcome in sync.run_once().items():
                log.info("%s: %s", name, outcome)
        except Exception as e:  # keep running; the next run tries again
            log.error("sync failed: %s", e)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
