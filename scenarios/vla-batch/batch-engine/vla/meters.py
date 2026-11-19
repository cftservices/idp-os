"""Meters, intake pasteuriser and CIP cycles: from the bus into Mongo (fase 3).

The factory publishes counters (gas, electricity, steam, water), the pasteuriser
signals and a summary of every finished CIP. KPIs need them per time window, and
the bus only holds the latest value, so this sampler keeps a short history:

  dw_meter_readings   per line every 60 s the four counters
  dw_pasteur_samples  per line every 5 s while milk flows through the pasteuriser
  dw_cip_cycles       per line one row per finished CIP (counter cip_count went up)

and from the pasteuriser samples it computes the fouling resistance the way an
engineer would, from measured temperatures and flow, not by reading the
simulation's own Rf:  U = duty / (A x LMTD),  Rf = 1/U - 1/U_clean, with U_clean
the U of the first samples after the last CIP. That Rf goes to the cook unit's
meta (equipment.set_fouling), where the CIP gate uses it.

Kept 48 hours (METERS_KEEP_H). A factory restart resets its counters to zero; the
KPI layer only sums positive steps, so a reset is never a negative consumption.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import model as M

log = logging.getLogger("vla.meters")

SAMPLE_S = 5.0
READ_EVERY_S = 60.0
KEEP_H = float(os.environ.get("METERS_KEEP_H", "48") or 48)
U_CLEAN_RUN_H = 0.15   # samples within this many pasteuriser hours after a CIP define U_clean

COUNTERS = ("heat_kwh_total", "electricity_kwh_total", "steam_kg_total", "water_L_total")
PAST_TAGS = ("product_flow_kg_s", "cold_t_in_C", "regen_t_out_C", "hold_t_C",
             "hot_t_in_C", "hot_t_out_C", "dp_bar", "run_h_since_cip")
CIP_LAST = ("water_L", "caustic_kg", "acid_kg", "heat_kwh", "electricity_kwh",
            "duration_s", "past_run_h")


def lmtd(dt1: float, dt2: float) -> Optional[float]:
    if dt1 <= 0 or dt2 <= 0:
        return None
    if abs(dt1 - dt2) < 1e-6:
        return dt1
    return (dt1 - dt2) / math.log(dt1 / dt2)


def u_value(s: dict, cp: float, area: float) -> Optional[float]:
    """Overall heat transfer coefficient of the heating section from one sample."""
    d = lmtd(s["hot_t_in_C"] - s["hold_t_C"], s["hot_t_out_C"] - s["regen_t_out_C"])
    if not d or area <= 0:
        return None
    duty = s["product_flow_kg_s"] * cp * (s["hold_t_C"] - s["regen_t_out_C"])
    return duty / (area * d) if duty > 0 else None


def suffix(line: str) -> str:
    """'01' for L1, '02' for L2: the equipment numbering of the line."""
    return M.LINES[line].eq("cook")[-2:]


def topic(line: str, area: str, base: str, tag: str) -> str:
    return f"{M.LINES[line].uns_root}/{area}/{base}-{suffix(line)}/Status/{tag}"


def pasteuriser_constants(model: Optional[dict]) -> tuple[float, float]:
    p = ((model or {}).get("process_constants") or {})
    return float(p.get("product_cp_kj_kg_k", 3.85)), float(p.get("pasteuriser_area_m2", 14.0))


class MeterSampler(threading.Thread):
    def __init__(self, db, buses: dict, equipment=None, model: Optional[dict] = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc), td=None):
        super().__init__(name="vla-meters", daemon=True)
        self.db, self.buses, self.equipment, self.now = db, buses, equipment, now
        self.td = td
        self.cp, self.area = pasteuriser_constants(model)
        self.stop_event = threading.Event()
        self._last_read: dict = {}
        self._last_run_h: dict = {}
        self._last_prune = 0.0
        self._hist: dict = {}          # topic -> (value, monotonic time of the lookup)
        self.status: dict = {"enabled": True, "last_error": None, "fouling_rf": {}}

    # -- bus ---------------------------------------------------------------------
    def _historian(self, t: str):
        """Last value of a topic from TDengine, cached 10 minutes.

        The OPC-UA link publishes on change only, so a value that does not move
        (holding temperature 75 C, the CIP counter between cleanings, the water
        meter before the first CIP) reaches the bus once, and an engine started
        after that moment never sees it. The historian stored that one message."""
        if self.td is None:
            return None
        hit = self._hist.get(t)
        if hit is not None and time.monotonic() - hit[1] < 600:
            return hit[0]
        val = None
        try:
            r = self.td.sql(f"select last(`value`), last(`valuestr`) from "
                            f"{self.td.db}.telemetry where `topic`='{t}'")
            row = (r.get("data") or [[None, None]])[0]
            val = row[0] if row[0] is not None else row[1]
        except Exception as e:  # noqa: BLE001 - historian down: just no fallback
            log.debug("meters: historian fallback for %s failed: %s", t, e)
        self._hist[t] = (val, time.monotonic())
        return val

    def _val(self, line: str, area: str, base: str, tag: str):
        bus = self.buses.get(line)
        t = topic(line, area, base, tag)
        raw = bus.latest_raw(t) if bus is not None else None
        if raw is None:
            v = self._historian(t)
            try:
                return None if v is None else float(v)
            except (TypeError, ValueError):
                return None
        try:
            obj = json.loads(raw)
            v = obj.get("value", raw) if isinstance(obj, dict) else obj
        except (TypeError, ValueError):
            v = raw
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # -- one pass ----------------------------------------------------------------
    def sample(self, mono: Optional[float] = None) -> None:
        mono = time.monotonic() if mono is None else mono
        ts = self.now().isoformat()
        for line in M.LINES:
            if mono - self._last_read.get(line, -1e9) >= READ_EVERY_S:
                vals = {t: self._val(line, "Utilities", "energy-meter" if t != "water_L_total"
                                     else "water-meter", t) for t in COUNTERS}
                # store what is there; a counter still unknown is None, and the KPI
                # layer never turns None -> value into consumption
                if any(v is not None for v in vals.values()):
                    self.db.dw_meter_readings.insert_one({"line": line, "ts": ts, **vals})
                    self._last_read[line] = mono
            self._cip(line, ts)
            self._pasteuriser(line, ts)

    def _cip(self, line: str, ts: str) -> None:
        count = self._val(line, "Utilities", "cip-station", "cip_count")
        if count is None:
            return
        # compare with the LATEST row, not the maximum: after a factory restart the
        # counter starts again at 0, and every later CIP must still count as new
        rows = sorted(self.db.dw_cip_cycles.find({"line": line}), key=lambda r: r["ts"])
        known = rows[-1]["cip_count"] if rows else None
        if known is None:
            # first sight of this line: remember the count, do not invent a cycle
            self.db.dw_cip_cycles.insert_one({"line": line, "ts": ts, "cip_count": count,
                                              "baseline": True})
            return
        if count != known:   # up = a new cycle; down = the factory restarted
            row = {"line": line, "ts": ts, "cip_count": count, "baseline": count < known}
            if count > known:
                row.update({k: self._val(line, "Utilities", "cip-station", f"last_{k}")
                            for k in CIP_LAST})
            self.db.dw_cip_cycles.insert_one(row)

    def _pasteuriser(self, line: str, ts: str) -> None:
        s = {t: self._val(line, "Receiving", "pasteurizer", t) for t in PAST_TAGS}
        if any(v is None for v in s.values()) or s["product_flow_kg_s"] <= 1.0:
            return
        if self._last_run_h.get(line) == s["run_h_since_cip"]:
            return   # nothing new since the previous sample
        self._last_run_h[line] = s["run_h_since_cip"]
        u = u_value(s, self.cp, self.area)
        self.db.dw_pasteur_samples.insert_one({"line": line, "ts": ts, **s, "u": u})
        rf = self.fouling(line)
        self.status["fouling_rf"][line] = rf
        if self.equipment is not None and rf is not None:
            self.equipment.set_fouling(M.LINES[line].eq("cook"), rf)

    def fouling(self, line: str) -> Optional[float]:
        """Rf of the newest sample against U_clean of the current cleaning cycle."""
        rows = sorted(self.db.dw_pasteur_samples.find({"line": line}), key=lambda r: r["ts"])
        if not rows or rows[-1].get("u") is None:
            return None
        start = self._cycle_start(rows)
        clean = [r["u"] for r in rows if r.get("u") and r["run_h_since_cip"] <= U_CLEAN_RUN_H
                 and r["ts"] >= start]
        if not clean:
            return None
        u_clean = sum(clean) / len(clean)
        return max(0.0, 1.0 / rows[-1]["u"] - 1.0 / u_clean)

    @staticmethod
    def _cycle_start(rows: list[dict]) -> str:
        """Timestamp of the first sample of the current cleaning cycle: where run_h
        last dropped (a CIP resets it to zero)."""
        start = rows[0]["ts"]
        for prev, cur in zip(rows, rows[1:]):
            if cur["run_h_since_cip"] < prev["run_h_since_cip"]:
                start = cur["ts"]
        return start

    def prune(self) -> None:
        cutoff = (self.now() - timedelta(hours=KEEP_H)).isoformat()
        for line in M.LINES:
            for coll in ("dw_meter_readings", "dw_pasteur_samples", "dw_cip_cycles"):
                self.db.collection(coll).delete_older_than("ts", cutoff, {"line": line})

    # -- thread ------------------------------------------------------------------
    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.sample()
                if time.monotonic() - self._last_prune > 3600:
                    self._last_prune = time.monotonic()
                    self.prune()
                self.status["last_error"] = None
            except Exception as e:  # noqa: BLE001 - never take the engine down
                self.status["last_error"] = str(e)[:300]
                log.warning("meters: %s", e)
            self.stop_event.wait(SAMPLE_S)


_sampler: Optional[MeterSampler] = None


def start_if_enabled(db, buses: dict, equipment=None, model: Optional[dict] = None):
    global _sampler
    if os.environ.get("METERS", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    if _sampler is None or not _sampler.is_alive():
        from .tdengine import TdClient
        _sampler = MeterSampler(db, buses, equipment, model, td=TdClient(timeout=5.0))
        _sampler.start()
        log.info("meters: bemonstering gestart (elke %.0f s)", SAMPLE_S)
    return _sampler


def sampler_status() -> dict:
    return dict(_sampler.status) if _sampler is not None else {"enabled": False}
