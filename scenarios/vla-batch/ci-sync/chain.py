"""milkdemo -> aios value chain: hourly KPI measurements from the historian.

Reads idp.kpi_input in the vla TDengine (read-only user) and pushes one measurement per closed
hour per line into the value chain of the same aios workspace as the CI sync, then runs the
catalogue's detection rules there. Push, not pull, like sync.py.

Why kpi_input and not idp.kpi: idp.kpi holds the ROLLING value over the last 8 hours every 5
minutes, so two neighbouring points share 95 % of their data. The value chain's proof test
treats measurements as independent; overlapping windows would make its standard error far too
small and "prove" a saving that is not there. kpi_input holds the formula inputs as increments
per 5-minute bucket, so the exact KPI over one hour is sum(numerator) / sum(denominator) over
that hour, and two hours share nothing.

KPIs (both have a value chain rule in the BVIP catalogue):
  KPI-PRD-002 Unplanned downtime %   sum(downtime_hours) / sum(planned_hours) * 100   DR-004
  KPI-ENE-001 Energy per unit kWh    sum(energy_kwh) / sum(units_produced)             DR-003

Times: a measurement is stamped with the START of its wall-clock hour (UTC). The demo runs
SYNC_TIME_SCALE times faster than real time, so one wall-clock hour holds about 15 process
hours and the rules' windows (DR-004 a week, DR-003 a day) hold 15 times as much process time.
An hour counts only when it closed at least LAG ago (the writer rewrites its last two buckets)
and when at least MIN_BUCKETS of its twelve 5-minute buckets are present.

Value inputs come from the same historian over the last VALUE_DAYS days (measured) and from the
cost_model of the factory model (synthetic demo assumptions, marked "assumed"). An input that
cannot be derived is left out, so aios answers insufficient_data instead of a number.

Idempotent without a second administration: per KPI only hours after its latest measurement in
aios (current_at) are sent. Environment: TD_URL, TD_DB, TD_USER, TD_PASS, FACTORY_MODEL,
SYNC_CHAIN_FROM (defaults to SYNC_FROM). Standard library only.

The TDengine user (chain_sync) is a separate credential, NOT a privilege boundary. Measured on
2026-11-22 on the VPS (TDengine OSS 3.3.6): with only "grant read on idp.*", and with sysinfo 0
and createdb 0, that user could still insert into a table of root, drop it and create a
database. So the only thing that keeps this sync read-only is Td.query below, which refuses any
statement that is not a single SELECT. The separate user still pays off: it can be revoked
without touching the root password of the engine and Grafana.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("ci-sync.chain")

# catalogue id -> numerator input, denominator input, factor, department, how it is measured
CHAIN_KPIS: dict[str, dict[str, Any]] = {
    "KPI-PRD-002": {"num": "downtime_hours", "den": "planned_hours", "factor": 100.0,
                    "department": "Production",
                    "method": "Per wall-clock hour: sum(downtime_hours) / sum(planned_hours) * 100 from "
                              "idp.kpi_input in the vla historian (process hours)."},
    "KPI-ENE-001": {"num": "energy_kwh", "den": "units_produced", "factor": 1.0,
                    "department": "Energy",
                    "method": "Per wall-clock hour: sum(energy_kwh) / sum(units_produced) from idp.kpi_input "
                              "in the vla historian (gas to the boiler plus electricity, filled packs)."},
}
# inputs read only for the value inputs, over the last VALUE_DAYS days
VALUE_INPUTS = {("KPI-PRD-001", "units_produced"), ("KPI-PRD-001", "units_good"), ("KPI-PRD-001", "run_hours"),
                ("KPI-PRD-001", "planned_hours"), ("KPI-ENE-001", "energy_kwh"), ("KPI-ENE-002", "energy_costs")}

SOURCE = "demo: vla historian idp.kpi_input (simulated plant)"   # "demo" marks fictional data in aios
MIN_BUCKETS = 6          # of the twelve 5-minute buckets in an hour
LAG = timedelta(minutes=15)
VALUE_DAYS = 7
HOURS_PER_YEAR = 8760.0
DECIMALS = 4             # kpi_measurements.value is Numeric(14, 4)
HOUR_MS = 3_600_000
COST_SOURCE = "cost_model of the vla factory model (synthetic demo assumption)"


# --------------------------------------------------------------------------- pure functions


def scope(line: str) -> str:
    return f"Vla {line}"


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def from_ms(v: int) -> datetime:
    return datetime.fromtimestamp(v / 1000, tz=timezone.utc)


def closed_until(now: datetime) -> datetime:
    """Start of the first hour that is not yet closed long enough ago to be final."""
    return (now - LAG).replace(minute=0, second=0, microsecond=0)


def next_hour(current_at: str | None, default: datetime) -> datetime:
    """First hour to send: the hour after the latest measurement in aios, else the default."""
    if not current_at:
        return default.replace(minute=0, second=0, microsecond=0)
    t = datetime.fromisoformat(current_at.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def hourly_values(rows: list[list[Any]], kpi_id: str) -> list[tuple[datetime, float]]:
    """Rows [t_ms, kpi_id, input, n_buckets, sum] -> [(hour start, KPI value)], oldest first.

    An hour without the numerator, with a denominator of 0 or less, or with fewer than
    MIN_BUCKETS buckets for either input gives no value (never 0)."""
    spec = CHAIN_KPIS[kpi_id]
    hours: dict[int, dict[str, tuple[int, float]]] = {}
    for t, kid, name, n, v in rows:
        if kid == kpi_id and name in (spec["num"], spec["den"]) and v is not None:
            hours.setdefault(int(t), {})[name] = (int(n), float(v))
    out = []
    for t in sorted(hours):
        got = hours[t]
        if spec["num"] not in got or spec["den"] not in got:
            continue
        (n_num, num), (n_den, den) = got[spec["num"]], got[spec["den"]]
        if den <= 0 or min(n_num, n_den) < MIN_BUCKETS:
            continue
        out.append((from_ms(t), round(num / den * spec["factor"], DECIMALS)))
    return out


def totals(rows: list[list[Any]]) -> dict[tuple[str, str], float]:
    """Rows [kpi_id, input, sum] -> {(kpi_id, input): sum}."""
    return {(k, name): float(v) for k, name, v in rows if v is not None}


def _iv(value: float, source: str, confidence: str) -> dict[str, Any]:
    return {"value": round(value, 6), "source": source, "confidence": confidence}


def value_inputs(kpi_id: str, tot: dict[tuple[str, str], float], cost: dict[str, Any]) -> dict[str, Any]:
    """The value model's inputs for one line, from the historian and the cost model. Missing stays missing."""
    def ratio(a: tuple[str, str], b: tuple[str, str]) -> float | None:
        x, y = tot.get(a), tot.get(b)
        return x / y if x is not None and y and y > 0 else None

    period = f"vla historian, last {VALUE_DAYS} days"
    out: dict[str, Any] = {}
    if kpi_id == "KPI-ENE-001":
        per_h = ratio(("KPI-PRD-001", "units_produced"), ("KPI-PRD-001", "planned_hours"))
        if per_h is not None:
            out["units_per_year"] = _iv(per_h * HOURS_PER_YEAR,
                                        f"{period}: filled packs per planned process hour x 8760", "measured")
        price = ratio(("KPI-ENE-002", "energy_costs"), ("KPI-ENE-001", "energy_kwh"))
        if price is not None:
            out["energy_price"] = _iv(price, f"{period}: energy costs / kWh, i.e. the gas and electricity prices "
                                             f"of the {COST_SOURCE} on the measured mix", "assumed")
    elif kpi_id == "KPI-PRD-002":
        out["planned_hours_per_year"] = _iv(HOURS_PER_YEAR, "the demo plans each line around the clock (autopilot)",
                                            "validated")
        rate = ratio(("KPI-PRD-001", "units_good"), ("KPI-PRD-001", "run_hours"))
        if rate is not None:
            out["good_rate"] = _iv(rate, f"{period}: good packs per run hour", "measured")
        sale, value = cost.get("sales_price_per_pack"), cost.get("value_per_pack")
        if isinstance(sale, (int, float)) and isinstance(value, (int, float)) and sale > value:
            out["contribution_margin"] = _iv(sale - value, f"sales_price_per_pack - value_per_pack, {COST_SOURCE}",
                                             "assumed")
    return out


def inputs_changed(stored: dict[str, Any], new: dict[str, Any], tolerance: float = 0.01) -> bool:
    """True when a key appears or disappears, or a value moved more than the tolerance (relative)."""
    if set(stored) != set(new):
        return True
    for k, v in new.items():
        a, b = (stored.get(k) or {}).get("value"), v["value"]
        if a is None or abs(float(a) - b) > tolerance * max(abs(b), 1e-12):
            return True
    return False


def hourly_sql(db: str, line: str, start: datetime, end: datetime) -> str:
    ids = ", ".join(f"'{k}'" for k in CHAIN_KPIS)
    return (f"select cast(_wstart as bigint) as t, kpi_id, `input`, count(*) as n, sum(`value`) as v "
            f"from {db}.kpi_input where `line` = '{line}' and kpi_id in ({ids}) "
            f"and ts >= {ms(start)} and ts < {ms(end)} partition by kpi_id, `input` interval(1h) order by t")


def totals_sql(db: str, line: str, start: datetime, end: datetime) -> str:
    ids = ", ".join(sorted({f"'{k}'" for k, _ in VALUE_INPUTS}))
    return (f"select kpi_id, `input`, sum(`value`) as v from {db}.kpi_input where `line` = '{line}' "
            f"and kpi_id in ({ids}) and ts >= {ms(start)} and ts < {ms(end)} partition by kpi_id, `input`")


def check_select(sql: str) -> None:
    """Refuse anything but one SELECT statement (no second statement after a semicolon)."""
    body = sql.strip().rstrip(";")
    if not body.lower().startswith("select ") or ";" in body:
        raise ValueError("chain sync sends a single SELECT to TDengine, nothing else")


def load_cost(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            cost = json.load(f).get("cost_model")
        return cost if isinstance(cost, dict) else {}
    except (OSError, ValueError) as e:
        log.warning("no cost model from %s (%s): margin and price inputs stay missing", path, e)
        return {}


# --------------------------------------------------------------------------- IO


class Td:
    """TDengine REST, read only by construction: one SELECT per call, nothing else.

    TDengine does not enforce the user's read grant here (see the module docstring), so this
    check is the boundary. Lines are plain identifiers from our own config, never user input."""

    def __init__(self, url: str, user: str, password: str, timeout: float = 30.0) -> None:
        self.url = url.rstrip("/") + "/rest/sql"
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.timeout = timeout

    def query(self, sql: str) -> list[list[Any]]:
        check_select(sql)
        req = urllib.request.Request(self.url, data=sql.encode(), method="POST")
        req.add_header("Authorization", self.auth)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"tdengine -> {e.code}: {e.read()[:300].decode(errors='replace')}") from e
        if body.get("code") != 0:
            raise RuntimeError(f"tdengine code {body.get('code')}: {body.get('desc')}")
        return body.get("data") or []


class ChainSync:
    def __init__(self, td: Td, aios, workspace_id: str, lines: list[str], db: str = "idp",
                 cost: dict[str, Any] | None = None, since: datetime | None = None,
                 dry_run: bool = False, now=None) -> None:
        self.td, self.aios, self.db, self.lines = td, aios, db, lines
        self.base = f"/api/v1/workspaces/{workspace_id}/chain"
        self.cost = cost or {}
        self.since = since or datetime(2026, 11, 17, tzinfo=timezone.utc)
        self.dry_run = dry_run
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.inputs_day = ""

    def _kpis(self) -> dict[tuple[str, str], dict[str, Any]]:
        # archived ones too: a KPI someone archived in aios is left alone, never quietly recreated
        return {(k.get("catalogue_kpi_id"), k.get("scope")): k
                for k in self.aios.call("GET", f"{self.base}/kpis?archived=true")}

    def _ensure(self, known: dict, kpi_id: str, line: str, inputs: dict[str, Any]) -> dict[str, Any] | None:
        k = known.get((kpi_id, scope(line)))
        if k is not None or self.dry_run:
            return k
        spec = CHAIN_KPIS[kpi_id]
        k = self.aios.call("POST", f"{self.base}/kpis", {
            "catalogue_kpi_id": kpi_id, "scope": scope(line), "department": spec["department"],
            "frequency": "hourly", "measurement_method": spec["method"], "value_inputs": inputs})
        k = self.aios.call("PATCH", f"{self.base}/kpis/{k['id']}", {"name": f"{k['name']} · {scope(line)}"})
        known[(kpi_id, scope(line))] = k
        return k

    def _line_inputs(self, line: str, end: datetime) -> dict[str, dict[str, Any]]:
        tot = totals(self.td.query(totals_sql(self.db, line, end - timedelta(days=VALUE_DAYS), end)))
        return {kid: value_inputs(kid, tot, self.cost) for kid in CHAIN_KPIS}

    def run_once(self) -> dict[str, str]:
        now = self.now()
        end = closed_until(now)
        today = now.date().isoformat()
        refresh = self.inputs_day != today
        known = self._kpis()
        report: dict[str, str] = {}
        added = 0
        for line in self.lines:
            inputs = self._line_inputs(line, end)
            ks = {kid: self._ensure(known, kid, line, inputs[kid]) for kid in CHAIN_KPIS}
            active = [k for k in ks.values() if not (k or {}).get("archived")]
            start = min((next_hour((k or {}).get("current_at"), self.since) for k in active), default=end)
            rows = self.td.query(hourly_sql(self.db, line, start, end)) if start < end else []
            for kid, k in ks.items():
                name = f"{kid} · {scope(line)}"
                if k is not None and k.get("archived"):
                    report[name] = "archived in aios, skipped"
                    continue
                first = next_hour((k or {}).get("current_at"), self.since)
                pts = [{"at": t.isoformat(), "value": v, "source": SOURCE}
                       for t, v in hourly_values(rows, kid) if t >= first]
                notes = []
                if k is not None and refresh and not self.dry_run and inputs_changed(k.get("value_inputs") or {}, inputs[kid]):
                    self.aios.call("PATCH", f"{self.base}/kpis/{k['id']}", {"value_inputs": inputs[kid]})
                    notes.append("value inputs updated")
                if not pts:
                    report[name] = ", ".join(["no new closed hours", *notes])
                    continue
                if self.dry_run or k is None:
                    report[name] = f"dry-run: {len(pts)} hours from {pts[0]['at']}"
                    continue
                self.aios.call("POST", f"{self.base}/kpis/{k['id']}/measurements", {"items": pts})
                added += len(pts)
                report[name] = ", ".join([f"{len(pts)} hours added, last {pts[-1]['at']}", *notes])
        if not self.dry_run:
            self.inputs_day = today
        if added:
            found = self.aios.call("POST", f"{self.base}/detect")
            report["detection"] = (f"{len(found.get('created') or [])} new opportunities, "
                                   f"{found.get('already_known', 0)} already known")
        return report
