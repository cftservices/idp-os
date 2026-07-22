"""HandlingUnit flow (PR-35, light): filled packs -> pallet -> wrap -> HU label
-> cold-store putaway -> shipping. APPROVED-gate: only an APPROVED batch may
enter the warehouse (the Solve story extended to logistics). Deliberately NOT
covered (spec): palletizer simulation, WMS, real GS1 registration.

Runs POST-batch: HUs are created for COMPLETE batches, so this module does not
use BatchRunner._guard_bookable (that guard is for in-process bookings)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import model as M
from .scan import ScanRejected

log = logging.getLogger("vla.handling")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HandlingUnitManager:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ create

    def create_hu(self, batch_id: str, packs_count: int,
                  operator_id: Optional[str] = None) -> dict:
        batch = self.db.dw_batches.find_one({"batch_id": batch_id})
        if batch is None:
            self._reject(None, batch_id, "unknown", operator_id)
        if batch.get("verdict") != M.APPROVED:
            self._reject(batch_id, batch_id, "not_approved", operator_id)
        if int(packs_count) <= 0:
            self._reject(batch_id, str(packs_count), "invalid_qty", operator_id)
        already = sum(int(h["packs_count"]) for h in
                      self.db.dw_handling_units.find({"batch_id": batch_id}))
        if already + int(packs_count) > int(batch.get("packs_total", 0)):
            self._reject(batch_id, str(packs_count), "exceeds_production",
                         operator_id)
        row = {
            "hu_id": M.new_hu_id(),
            "batch_id": batch_id,
            "packs_count": int(packs_count),
            "location": None,
            "status": M.HU_WRAPPED,
            "operator_id": operator_id,
            "ts": _iso(),
        }
        self.db.dw_handling_units.insert_one(row)
        self._event(batch_id, "hu_scanned",
                    {"hu_id": row["hu_id"], "batch_id": batch_id,
                     "packs_count": int(packs_count), "operator_id": operator_id})
        return {k: v for k, v in row.items()}

    # ----------------------------------------------------------------- putaway

    def putaway(self, hu_id: str, operator_id: Optional[str] = None) -> dict:
        hu = self._hu_or_reject(hu_id, operator_id)
        if hu["status"] != M.HU_WRAPPED:
            self._reject(hu["batch_id"], hu_id, "wrong_status", operator_id)
        self.db.dw_handling_units.update_one(
            {"hu_id": hu_id},
            {"$set": {"location": M.LOC_COLDSTORE, "status": M.HU_AWAITING,
                      "ts": _iso()}})
        self._event(hu["batch_id"], "putaway_booked",
                    {"hu_id": hu_id, "location": M.LOC_COLDSTORE,
                     "operator_id": operator_id})
        return self.db.dw_handling_units.find_one({"hu_id": hu_id})

    # -------------------------------------------------------------------- ship

    def ship(self, hu_id: str, operator_id: Optional[str] = None) -> dict:
        hu = self._hu_or_reject(hu_id, operator_id)
        if hu["status"] != M.HU_AWAITING:
            self._reject(hu["batch_id"], hu_id, "wrong_status", operator_id)
        self.db.dw_handling_units.update_one(
            {"hu_id": hu_id},
            {"$set": {"location": M.LOC_EXPEDITION, "status": M.HU_SHIPPED,
                      "ts": _iso()}})
        self._event(hu["batch_id"], "hu_shipped",
                    {"hu_id": hu_id, "operator_id": operator_id})
        batch = self.db.dw_batches.find_one({"batch_id": hu["batch_id"]}) or {}
        hu_total = sum(int(h["packs_count"]) for h in
                       self.db.dw_handling_units.find({"batch_id": hu["batch_id"]}))
        packs_total = int(batch.get("packs_total", 0))
        if hu_total != packs_total:
            self._event(hu["batch_id"], "hu_packs_difference",
                        {"batch_id": hu["batch_id"], "packs_total": packs_total,
                         "hu_total": hu_total,
                         "difference": packs_total - hu_total})
        return self.db.dw_handling_units.find_one({"hu_id": hu_id})

    # ------------------------------------------------------------------- query

    def list_hus(self, batch_id: Optional[str] = None) -> list[dict]:
        query = {"batch_id": batch_id} if batch_id else {}
        return self.db.dw_handling_units.find(query)

    # ----------------------------------------------------------------- helpers

    def _hu_or_reject(self, hu_id: str, operator_id: Optional[str]) -> dict:
        hu = self.db.dw_handling_units.find_one({"hu_id": hu_id})
        if hu is None:
            self._reject(None, hu_id, "unknown", operator_id)
        return hu

    def _event(self, batch_id: Optional[str], event_type: str, payload: dict) -> None:
        self.db.dw_batch_events.insert_one({
            "batch_id": batch_id, "event_type": event_type,
            "payload": payload, "ts": _iso()})

    def _reject(self, batch_id: Optional[str], code: str, reason: str,
                operator_id: Optional[str]) -> None:
        self._event(batch_id, "scan_rejected",
                    {"code": code, "reason": reason, "operator_id": operator_id})
        raise ScanRejected(reason, f"scan rejected ({reason}): {code}")
