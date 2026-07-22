"""EquipmentMonitor — per-equipment meta, derived running-hours, and the CBM
fouling model (PR-17/18/29). The fouling model lives in the MES layer as a
documented substitution: the factory sim stays untouched; heat-up per batch is
measured (live) or fabricated (instant) and trended here."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import model as M

log = logging.getLogger("vla.equipment")

# CBM fouling model (PR-18) — single source of truth for the constants.
BASE_HEATUP_SEC = 120.0
HEATUP_INCREASE_PER_BATCH = 0.15
CBM_ALERT_FACTOR = 1.35
DIRTY_AFTER_BATCHES = 4

EQUIPMENT_IDS = ["receiving-tank-01", "process-tank-01", "cook-unit-01",
                 "cooler-01", "filler-01"]


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class EquipmentMonitor:
    def __init__(self, db, bus=None):
        self.db = db
        self.bus = bus

    # ------------------------------------------------------------------- meta

    def ensure_meta(self, equipment_id: str) -> dict:
        meta = self.db.dw_equipment_meta.find_one({"equipment_id": equipment_id})
        if meta is None:
            meta = {
                "equipment_id": equipment_id,
                "area": M.area_of(equipment_id),
                "batches_since_cip": 0,
                "dirty": False,
                "last_cip_at": None,
                "heatup_history": [],
            }
            self.db.dw_equipment_meta.insert_one(meta)
            meta = self.db.dw_equipment_meta.find_one(
                {"equipment_id": equipment_id})
        return meta

    # ---------------------------------------------------------- running hours

    def running_hours(self, equipment_id: str) -> float:
        rows = [r for r in self.db.dw_equipment_state.find(
            {"equipment_id": equipment_id})]
        total = 0.0
        for i, row in enumerate(rows):
            if row.get("state") != "Running":
                continue
            start = _parse(row["ts"])
            end = _parse(rows[i + 1]["ts"]) if i + 1 < len(rows) \
                else datetime.now(timezone.utc)
            total += max(0.0, (end - start).total_seconds())
        return round(total / 3600.0, 4)

    # ------------------------------------------------------------ UNS publish

    def on_state_change(self, equipment_id: str, state: str) -> None:
        if self.bus is None:
            return
        area = M.area_of(equipment_id)
        self.bus.publish_json(f"{area}/{equipment_id}/Status/state",
                              {"value": state, "ts": _iso()})
        self.bus.publish_json(f"{area}/{equipment_id}/Status/running_hours",
                              {"value": self.running_hours(equipment_id),
                               "unit": "h", "ts": _iso()})

    # --------------------------------------------------------------- snapshot

    def snapshot(self) -> list[dict]:
        out = []
        for eq in EQUIPMENT_IDS:
            meta = self.ensure_meta(eq)
            hist = self.db.dw_equipment_state.find({"equipment_id": eq})
            latest = hist[-1]["state"] if hist else "Idle"
            if meta.get("dirty"):
                latest = "Dirty"
            out.append({
                "equipment_id": eq,
                "area": meta["area"],
                "state": latest,
                "running_hours": self.running_hours(eq),
                "batches_since_cip": meta.get("batches_since_cip", 0),
                "dirty": bool(meta.get("dirty")),
                "last_cip_at": meta.get("last_cip_at"),
            })
        return out
