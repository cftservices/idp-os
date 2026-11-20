"""Scan-driven shop-floor flow (PR-34, FDS §B steps 0-6).

Demo translation: no physical scanner — the operator UI posts label/order
payloads. Every rejection is logged as a scan_rejected BatchEvent so the
UNS shows the full story.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from . import inventory, model as M

log = logging.getLogger("vla.scan")

RUNNING = (M.DOSING, M.COOKING, M.COOLING, M.FILLING)


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

    def scan_order(self, code: str, operator_id: Optional[str] = None) -> dict:
        """The gate. Answers with the batch to work on and the phase it is in:

        dosing     the batch is still bookable: weigh, sample, book production
        logistics  the batch is COMPLETE: only pallets, putaway and shipping.
                   A REJECTED batch also opens here on purpose, so the pallet
                   refusal ("not approved") is what the operator sees.
        None       an order without a batch yet

        A scanned ORDER code picks its open batch first, then its latest
        finished one. A closed order is refused; a finished batch scanned by
        its own code is not, because logistics happens after the order closes.

        line_busy  a batch that is not running yet, while another batch runs on
                   the same line (PR-34 step 0). Logistics and the running batch
                   itself always open; the other line never blocks.
        """
        order = self.orders.get_order(code) if self.orders else None
        batch = None
        if order is None:
            batch = self.db.dw_batches.find_one({"batch_id": code})
            if batch is None:
                self._reject(None, code, "unknown", operator_id)
            if batch.get("order_id") and self.orders:
                order = self.orders.get_order(batch["order_id"])
        else:
            if order["status"] == M.ORDER_DONE:
                self._reject(None, code, "not_active", operator_id)
            batch = self._batch_of_order(order["order_id"])
        if batch is not None and batch["state"] not in RUNNING + (M.COMPLETE,):
            active = self._running_on_line(M.line_of_doc(batch), batch["batch_id"])
            if active is not None:
                self._reject(active["batch_id"],
                             f"{code} (line {M.line_of_doc(batch)} runs {active['batch_id']})",
                             "line_busy", operator_id)
        batch_id = batch and batch.get("batch_id")
        order_id = (order and order.get("order_id")) or (batch and batch.get("order_id"))
        self._event(batch_id, "order_scanned",
                    {"code": code, "operator_id": operator_id, "order_id": order_id})
        phase = None
        if batch is not None:
            phase = "logistics" if batch["state"] == M.COMPLETE else "dosing"
        return {"ok": True, "order": order, "batch": batch,
                # flat fields for the shop-floor screen
                "order_id": order_id, "batch_id": batch_id, "phase": phase,
                "materials": self._materials(batch_id) if phase == "dosing" else []}

    def _running_on_line(self, line: str, other_than: str) -> Optional[dict]:
        return next((b for b in self.db.dw_batches.find({})
                     if b["state"] in RUNNING and b["batch_id"] != other_than
                     and M.line_of_doc(b) == line), None)

    def _batch_of_order(self, order_id: str) -> Optional[dict]:
        rows = sorted(self.db.dw_batches.find({"order_id": order_id}),
                      key=lambda b: b.get("created_at") or "")
        open_ = [b for b in rows if b["state"] != M.COMPLETE]
        return (open_ or rows or [None])[-1]

    def _materials(self, batch_id: str) -> list[dict]:
        out = []
        for d in self.db.dw_doses.find({"batch_id": batch_id}):
            target = float(d["qty_target"])
            prepared = float(d.get("qty_prepared") or 0.0)
            out.append({"material_id": d["material_id"], "qty_target": target,
                        "qty_staged": prepared if d.get("qty_prepared") is not None else None,
                        "remaining": round(max(0.0, target - prepared), 4),
                        "booked": d.get("qty_actual") is not None,
                        "tol_min": d.get("tol_min"), "tol_max": d.get("tol_max")})
        return out

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

    # ------------------------------------------- step 4: report scan = commit

    def scan_report(self, batch_id: str,
                    operator_id: Optional[str] = None) -> dict:
        self.runner._guard_bookable(batch_id)
        rows = self.db.dw_doses.find({"batch_id": batch_id})
        staged_rows = [r for r in rows
                       if float(r.get("qty_prepared") or 0.0) > 0
                       and r.get("qty_actual") is None]
        if not staged_rows:
            self._reject(batch_id, batch_id, "nothing_staged", operator_id)
        # All or nothing: check every staged material before booking any of them,
        # so a shortage never leaves a half-committed batch behind.
        short = []
        for r in staged_rows:
            mat = self.db.dw_materials.find_one({"material_id": r["material_id"]})
            if mat is None or mat.get("stock_qty") is None:
                continue
            need = round(float(r["qty_prepared"]), 4)
            have = float(mat["stock_qty"])
            if need > have:
                short.append(f"{r['material_id']} {need:g} kg, stock {have:g} kg")
        if short:
            self._reject(batch_id, "; ".join(short), "insufficient_stock", operator_id)
        booked = []
        for r in staged_rows:
            actual = round(float(r["qty_prepared"]), 4)
            in_tol = float(r["tol_min"]) <= actual <= float(r["tol_max"])
            last = (r.get("staged") or [{}])[-1]
            self.db.dw_doses.update_one(
                {"batch_id": batch_id, "material_id": r["material_id"]},
                {"$set": {"qty_actual": actual, "in_tolerance": in_tol,
                          "source_equipment": last.get("source_equipment", "scale-01"),
                          "operator_id": operator_id or r.get("operator_id"),
                          "ts": _iso()}})
            self._event(batch_id, "dose_booked",
                        {"material_id": r["material_id"], "qty_actual": actual,
                         "in_tolerance": in_tol, "lot_no": r.get("lot_no"),
                         "operator_id": operator_id})
            if not in_tol:
                self.runner._alarm(batch_id, "process-tank-01",
                                   "dose_out_of_tolerance", M.MEDIUM,
                                   f"{r['material_id']} dose {actual} kg out of tol "
                                   f"({r['tol_min']}-{r['tol_max']})",
                                   impact=True, resolved=False)
            inventory.consume(self.db, self._event, r["material_id"],
                              actual, batch_id)
            booked.append(r["material_id"])
        self._event(batch_id, "report_scanned",
                    {"operator_id": operator_id, "booked_materials": booked})
        return {"ok": True, "booked_materials": booked}

    # --------------------------------------- step 5: manual production booking

    def book_production(self, batch_id: str, packs: int,
                        operator_id: Optional[str] = None) -> dict:
        self.runner._guard_bookable(batch_id)
        if int(packs) <= 0:
            self._reject(batch_id, str(packs), "invalid_qty", operator_id)
        return self.runner._book_production(batch_id, int(packs), 0,
                                            source="operator_booking",
                                            operator_id=operator_id)

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
