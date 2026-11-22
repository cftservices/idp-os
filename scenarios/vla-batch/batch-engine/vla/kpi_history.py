"""KPI history: every 5 minutes a snapshot of the 29 BVIP KPIs into TDengine.

Without this the KPIs exist only on request (GET /kpi/summary), so Grafana could show a current
value but never a trend. Two super tables in the historian database:

  idp.kpi        (ts, value, status)  TAGS (kpi_id, engine_key, line, unit)
                 the rolling value over the last SHIFT_HOURS, at the end of each bucket
  idp.kpi_input  (ts, value)          TAGS (kpi_id, input, line, unit)
                 the formula inputs as increments per bucket [ts, ts+5 min), at event time,
                 only for additive KPIs. sum(inputs) then formula = the exact KPI over any
                 period, which is what the Grafana "over de gekozen periode" panels do.

Lines L1, L2 and PLANT. Status OK / DEELS / UNSET / NIET_AANTOONBAAR (value NULL).

Its own daemon thread, not the autopilot loop: the autopilot polls every 3 s and CPU steal on
the VPS already delays batch starts. One read of the collections per tick; the last two buckets
are written again each tick (late verdicts), and TDengine overwrites a row with the same
timestamp. At start it backfills what Mongo still holds (KPI_WRITER_BACKFILL_H, max 24).

Env: KPI_WRITER=1, KPI_WRITER_INTERVAL_S (300), KPI_WRITER_BACKFILL_H (24), KPI_WINDOW_H (8),
TD_URL / TD_DB / TD_USER / TD_PASS, KPI_IDEAL_PACKS_PER_H ("L1:10300,L2:10300").
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import bvip, fouling_history
from .bvip_map import BVIP_MAP
from .tdengine import TdClient, number, quote

log = logging.getLogger("vla.kpi_history")

SCHEMA = (
    "CREATE STABLE IF NOT EXISTS {db}.kpi (ts TIMESTAMP, `value` DOUBLE, `status` VARCHAR(20)) "
    "TAGS (kpi_id VARCHAR(16), engine_key VARCHAR(48), `line` VARCHAR(8), unit VARCHAR(16))",
    "CREATE STABLE IF NOT EXISTS {db}.kpi_input (ts TIMESTAMP, `value` DOUBLE) "
    "TAGS (kpi_id VARCHAR(16), `input` VARCHAR(40), `line` VARCHAR(8), unit VARCHAR(16))",
)
LINES = ("L1", "L2", bvip.PLANT)
FOULING_LINES = ("L1", "L2")     # one pasteuriser per line; PLANT is not an exchanger
FOULING_RECENT_H = 1.0           # each tick rewrites the fouling rows of the last hour
MAX_SQL_BYTES = 700_000


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_")


def kpi_table(kpi_id: str, line: str) -> str:
    return f"kpi_{_slug(line)}_{_slug(kpi_id.replace('KPI-', ''))}"


def input_table(kpi_id: str, line: str, name: str) -> str:
    return f"kpin_{_slug(line)}_{_slug(kpi_id.replace('KPI-', ''))}_{_slug(name)}"


def engine_key(kpi_id: str) -> str:
    d = BVIP_MAP[kpi_id].get("drempels", "")
    return d.split(":", 1)[1] if d.startswith("model:") else ""


class Rows:
    """Collects (table, using-clause, values) and renders multi-table INSERTs in chunks."""

    def __init__(self, db: str):
        self.db = db
        self.parts: list[str] = []

    def kpi(self, ts: datetime, kpi_id: str, line: str, unit: str, value, status: str) -> None:
        self.parts.append(
            f"{self.db}.{kpi_table(kpi_id, line)} USING {self.db}.kpi TAGS "
            f"({quote(kpi_id)}, {quote(engine_key(kpi_id))}, {quote(line)}, {quote(unit or '')}) "
            f"VALUES ({_ms(ts)}, {number(value)}, {quote(status)})")

    def input(self, ts: datetime, kpi_id: str, line: str, name: str, unit: str, value) -> None:
        self.parts.append(
            f"{self.db}.{input_table(kpi_id, line, name)} USING {self.db}.kpi_input TAGS "
            f"({quote(kpi_id)}, {quote(name)}, {quote(line)}, {quote(unit or '')}) "
            f"VALUES ({_ms(ts)}, {number(value)})")

    def statements(self) -> list[str]:
        out, cur, size = [], [], 0
        for p in self.parts:
            if cur and size + len(p) > MAX_SQL_BYTES:
                out.append("INSERT INTO " + " ".join(cur))
                cur, size = [], 0
            cur.append(p)
            size += len(p) + 1
        if cur:
            out.append("INSERT INTO " + " ".join(cur))
        return out


def build_rows(snap: bvip.Snapshot, bucket_starts: list[datetime], rolling_at: list[datetime],
               cfg: bvip.Config, catalogue: list[dict], step_s: int, window_h: float,
               db: str) -> Rows:
    rows = Rows(db)
    units = {k["id"]: k.get("eenheid") or "" for k in catalogue}
    additive = bvip.additive_kpis(catalogue)
    for b0 in bucket_starts:
        b1 = b0 + timedelta(seconds=step_s)
        for line in LINES:
            vals = bvip.inputs(snap, line, b0, b1, cfg)
            for k in additive:
                missing = set(BVIP_MAP[k["id"]].get("ontbreekt") or [])
                names = [i for i in k["inputs"] if i["naam"] not in missing]
                # A bucket where one input has no source (no meter reading yet, no ideal
                # rate) writes nothing for this KPI: otherwise the other inputs (packs)
                # are summed over time the first one never covered, and the ratio lies.
                if any(vals.get(i["naam"]) is None for i in names):
                    continue
                for i in names:
                    rows.input(b0, k["id"], line, i["naam"], i.get("eenheid"), vals.get(i["naam"]))
    for t in rolling_at:
        start, end = bvip.window(t, window_h)
        for line in LINES:
            for kid, r in bvip.kpi_values(snap, line, start, end, cfg, catalogue).items():
                rows.kpi(t, kid, line, units[kid], r["value"], r["status"])
    return rows


class KpiWriter(threading.Thread):
    def __init__(self, db, td: Optional[TdClient] = None,
                 cfg_factory: Optional[Callable[[], bvip.Config]] = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        super().__init__(name="vla-kpi-writer", daemon=True)
        self.db = db
        self.td = td or TdClient()
        self.cfg_factory = cfg_factory or bvip.config_from_env
        self.now = now
        self.step = int(_env_float("KPI_WRITER_INTERVAL_S", 300))
        self.backfill_h = min(24.0, _env_float("KPI_WRITER_BACKFILL_H", 24))
        self.window_h = _env_float("KPI_WINDOW_H", 8)
        self.fouling = os.environ.get("FOULING_WRITER", "1").strip().lower() in ("1", "true", "yes", "on")
        self.catalogue = bvip.load_catalogue()
        self.stop_event = threading.Event()
        self.status: dict = {"enabled": True, "interval_s": self.step, "schema": False,
                             "last_tick": None, "last_duration_s": None, "last_rows": None,
                             "last_error": None, "backfilled": False, "fouling": None}

    # -- one tick ----------------------------------------------------------------
    def ensure_schema(self) -> None:
        for s in SCHEMA + (fouling_history.SCHEMA if self.fouling else ()):
            self.td.sql(s.format(db=self.td.db))
        self.status["schema"] = True

    def write(self, bucket_starts: list[datetime], rolling_at: list[datetime],
              snap: Optional[bvip.Snapshot] = None, fouling_since: Optional[datetime] = None,
              fouling: bool = True) -> int:
        t0 = time.monotonic()
        snap = snap or bvip.take_snapshot(self.db, self.now())
        cfg = self.cfg_factory()
        rows = build_rows(snap, bucket_starts, rolling_at, cfg, self.catalogue,
                          self.step, self.window_h, self.td.db)
        if self.fouling and fouling:
            extra = fouling_history.parts(snap, cfg, self.td.db, FOULING_LINES, fouling_since)
            rows.parts.extend(extra)
            self.status["fouling"] = {"rows": len(extra), "since": fouling_since and fouling_since.isoformat()}
        for stmt in rows.statements():
            self.td.sql(stmt)
        n = len(rows.parts)
        self.status.update(last_tick=self.now().isoformat(), last_rows=n, last_error=None,
                           last_duration_s=round(time.monotonic() - t0, 2))
        return n

    def tick(self) -> int:
        end = bvip.floor_bucket(self.now(), self.step)
        prev = end - timedelta(seconds=self.step)
        return self.write([prev - timedelta(seconds=self.step), prev], [end],
                          fouling_since=self.now() - timedelta(hours=FOULING_RECENT_H))

    def backfill(self) -> int:
        end = bvip.floor_bucket(self.now(), self.step)
        n = int(self.backfill_h * 3600 // self.step)
        starts = [end - timedelta(seconds=self.step * i) for i in range(n, 0, -1)]
        total = 0
        snap = bvip.take_snapshot(self.db, self.now())  # one read of Mongo for the whole backfill
        # in slices, so one statement never gets too large; a short pause between slices keeps
        # the engine responsive on a VPS with CPU steal
        for i in range(0, len(starts), 24):
            sl = starts[i:i + 24]
            # all fouling rows Mongo still holds, once, with the first slice
            total += self.write(sl, [s + timedelta(seconds=self.step) for s in sl], snap,
                                fouling=(i == 0))
            if self.stop_event.wait(0.5):
                break
        self.status["backfilled"] = True
        log.info("KPI-historie: %d rijen teruggevuld over %.0f u", total, self.backfill_h)
        return total

    # -- thread ------------------------------------------------------------------
    def run(self) -> None:
        delay = 5.0
        while not self.stop_event.is_set():
            try:
                if not self.status["schema"]:
                    self.ensure_schema()
                if not self.status["backfilled"] and self.backfill_h > 0:
                    self.backfill()
                self.tick()
                delay = 5.0
            except Exception as e:  # noqa: BLE001 - the writer must never take the engine down
                self.status["last_error"] = str(e)[:300]
                log.warning("KPI-historie: %s", e)
                if self.stop_event.wait(delay):
                    return
                delay = min(300.0, delay * 2)
                continue
            now = self.now().timestamp()
            wait = self.step - (now % self.step) + 5  # just after the bucket boundary
            if self.stop_event.wait(wait):
                return


_writer: Optional[KpiWriter] = None


def start_if_enabled(db) -> Optional[KpiWriter]:
    global _writer
    if os.environ.get("KPI_WRITER", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    if _writer is None or not _writer.is_alive():
        _writer = KpiWriter(db)
        _writer.start()
        log.info("KPI-historie: schrijver gestart (elke %d s)", _writer.step)
    return _writer


def writer_status() -> dict:
    return dict(_writer.status) if _writer is not None else {"enabled": False}
