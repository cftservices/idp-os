"""Scan-driven shop-floor flow (PR-34, FDS §B steps 0-6).

Demo translation: no physical scanner — the operator UI posts label/order
payloads. Every rejection is logged as a scan_rejected BatchEvent so the
UNS shows the full story.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import model as M

log = logging.getLogger("vla.scan")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanRejected(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class ScanFlow:
    def __init__(self, db, runner, orders):
        self.db = db
        self.runner = runner
        self.orders = orders

    # ------------------------------------------------------------ step 0: gate

    def scan_order(self, code: str, operator_id: Optional[str] = None,
                   for_start: bool = False) -> dict:
        order = self.orders.get_order(code) if self.orders else None
        batch = None
        if order is None:
            batch = self.db.dw_batches.find_one({"batch_id": code})
            if batch is None:
                self._reject(None, code, "unknown", operator_id)
            if batch.get("order_id"):
                order = self.orders.get_order(batch["order_id"])
        if order is not None and order["status"] == M.ORDER_DONE:
            self._reject(batch and batch.get("batch_id"), code, "not_active",
                         operator_id)
        if batch is not None and batch["state"] == M.COMPLETE:
            self._reject(batch["batch_id"], code, "not_active", operator_id)
        if for_start:
            active = next((b for b in self.db.dw_batches.find({})
                           if b["state"] in (M.DOSING, M.COOKING, M.COOLING,
                                             M.FILLING)), None)
            if active is not None:
                self._reject(active["batch_id"], code, "line_busy", operator_id)
        self._event(batch and batch.get("batch_id"), "order_scanned",
                    {"code": code, "operator_id": operator_id,
                     "order_id": order and order.get("order_id")})
        return {"ok": True, "order": order, "batch": batch}

    # ------------------------------------------------------ step 2: label scan

    def scan_label(self, batch_id: str, material_id: str, lot_no: str,
                   operator_id: Optional[str] = None) -> dict:
        self.runner._guard_bookable(batch_id)
        dose = self.db.dw_doses.find_one({"batch_id": batch_id,
                                          "material_id": material_id})
        if dose is None:
            self.runner._alarm(batch_id, "process-tank-01",
                               "wrong_material_scanned", M.MEDIUM,
                               f"{material_id} is not in the recipe for {batch_id}",
                               impact=False, resolved=False)
            self._reject(batch_id, material_id, "wrong_material", operator_id)
        self._event(batch_id, "label_scanned",
                    {"material_id": material_id, "lot_no": lot_no,
                     "operator_id": operator_id})
        return self._guidance(batch_id, material_id, lot_no)

    # ------------------------------------------- step 3: weigh guidance + stage

    def weigh(self, batch_id: str, material_id: str,
              qty_kg: Optional[float] = None, lot_no: Optional[str] = None,
              source_equipment: str = "scale-01",
              operator_id: Optional[str] = None, total: bool = False) -> dict:
        self.runner._guard_bookable(batch_id)
        dose = self.db.dw_doses.find_one({"batch_id": batch_id,
                                          "material_id": material_id})
        if dose is None:
            self._reject(batch_id, material_id, "wrong_material", operator_id)
        mat = self.db.dw_materials.find_one({"material_id": material_id}) or {}
        prepared = float(dose.get("qty_prepared") or 0.0)
        target = float(dose["qty_target"])

        if total:
            qty = round(max(0.0, target - prepared), 4)
            if qty == 0.0:
                self._reject(batch_id, material_id, "nothing_remaining", operator_id)
        else:
            if qty_kg is None or float(qty_kg) <= 0:
                self._reject(batch_id, material_id, "invalid_qty", operator_id)
            qty = round(float(qty_kg), 4)

        if mat.get("whole_bag") and mat.get("bag_size_kg"):
            bags = qty / float(mat["bag_size_kg"])
            if abs(bags - round(bags)) > 1e-6:
                self._reject(batch_id, material_id, "not_whole_bags", operator_id)

        staged = list(dose.get("staged") or [])
        staged.append({"qty_kg": qty, "lot_no": lot_no,
                       "source_equipment": source_equipment,
                       "operator_id": operator_id, "ts": _iso(),
                       "total_action": bool(total)})
        new_prepared = round(prepared + qty, 4)
        self.db.dw_doses.update_one(
            {"batch_id": batch_id, "material_id": material_id},
            {"$set": {"staged": staged, "qty_prepared": new_prepared,
                      "lot_no": lot_no or dose.get("lot_no"),
                      "operator_id": operator_id or dose.get("operator_id")}})
        self._event(batch_id, "dose_staged",
                    {"material_id": material_id, "qty_kg": qty,
                     "qty_prepared": new_prepared, "total_action": bool(total)})
        if new_prepared > target and prepared <= target:
            self._event(batch_id, "overconsumption_booked",
                        {"material_id": material_id, "qty_prepared": new_prepared,
                         "qty_target": target, "operator_id": operator_id})
        return self._guidance(batch_id, material_id, lot_no)

    # ---------------------------------------------------------------- helpers

    def _guidance(self, batch_id: str, material_id: str,
                  lot_no: Optional[str]) -> dict:
        dose = self.db.dw_doses.find_one({"batch_id": batch_id,
                                          "material_id": material_id})
        mat = self.db.dw_materials.find_one({"material_id": material_id}) or {}
        prepared = float(dose.get("qty_prepared") or 0.0)
        target = float(dose["qty_target"])
        return {
            "material_id": material_id,
            "lot_no": lot_no or dose.get("lot_no"),
            "qty_target": target,
            "tol_min": dose.get("tol_min"),
            "tol_max": dose.get("tol_max"),
            "booked": prepared,
            "remaining": round(max(0.0, target - prepared), 4),
            "whole_bag": bool(mat.get("whole_bag")),
            "bag_size_kg": mat.get("bag_size_kg"),
        }

    def _event(self, batch_id: Optional[str], event_type: str, payload: dict) -> None:
        self.db.dw_batch_events.insert_one({
            "batch_id": batch_id, "event_type": event_type,
            "payload": payload, "ts": _iso()})

    def _reject(self, batch_id: Optional[str], code: str, reason: str,
                operator_id: Optional[str]) -> None:
        self._event(batch_id, "scan_rejected",
                    {"code": code, "reason": reason, "operator_id": operator_id})
        raise ScanRejected(reason, f"scan rejected ({reason}): {code}")
