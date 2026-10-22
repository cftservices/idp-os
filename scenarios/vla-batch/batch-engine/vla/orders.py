"""OrderManager — production orders for the Vla demo (PR-24).

Order lifecycle OPEN -> RUNNING -> DONE maps onto the batch FSM (FDS mapping
table). Multiple batches per order; progress = batched_L vs target_qty_L and
produced packs. Status + progress are mirrored to the UNS under
DairyWorks/Vla/Orders/{order_id}/Status/*.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import model as M

log = logging.getLogger("vla.orders")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderManager:
    def __init__(self, db, bus=None):
        self.db = db
        self.bus = bus

    def create_order(self, recipe_id: str, target_qty_L: float,
                     due_date: Optional[str] = None,
                     customer: Optional[str] = None) -> dict:
        if M.get_recipe(recipe_id) is None:
            raise ValueError(f"unknown recipe_id {recipe_id!r}")
        if float(target_qty_L) <= 0:
            raise ValueError("target_qty_L must be > 0")
        order_id = f"PO-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        doc = {
            "order_id": order_id,
            "recipe_id": recipe_id,
            "target_qty_L": float(target_qty_L),
            "due_date": due_date,
            "customer": customer,
            "status": M.ORDER_OPEN,
            "created_at": _iso(),
        }
        self.db.dw_orders.insert_one(doc)
        self._event(order_id, "order_created", {"recipe_id": recipe_id,
                                                "target_qty_L": float(target_qty_L),
                                                "customer": customer})
        self.publish_status(doc)
        return dict(doc)

    def get_order(self, order_id: str) -> Optional[dict]:
        return self.db.dw_orders.find_one({"order_id": order_id})

    def order_progress(self, order_id: str) -> dict:
        """Planned vs produced for one order.

        NOTE `batched_L` is the sum of the batches' *planned* litres, not output.
        `produced_L` (packs x pack_size_L) is the actual yield — show both, they
        differ as soon as a batch under-fills.
        """
        order = self.get_order(order_id) or {}
        batches = self.db.dw_batches.find({"order_id": order_id})
        batch_ids = [b["batch_id"] for b in batches]
        bookings = [p for p in self.db.dw_production.find({})
                    if p["batch_id"] in batch_ids]
        produced = sum(p.get("packs") or 0 for p in bookings)
        produced_L = sum((p.get("packs") or 0) * float(p.get("pack_size_L") or 1)
                         for p in bookings)
        rejects = sum(p.get("reject_count") or 0 for p in bookings)
        target = float(order.get("target_qty_L") or 0)
        return {
            "batched_L": sum(float(b.get("planned_L") or 0) for b in batches),
            "produced_packs": produced,
            "produced_L": produced_L,
            "reject_total": rejects,
            "remaining_L": max(0.0, target - produced_L) if target else 0.0,
            "progress_pct": round(100.0 * produced_L / target, 1) if target else 0.0,
            "batches_count": len(batch_ids),
            "batch_ids": batch_ids,
        }

    def order_consumption(self, order_id: str) -> list[dict]:
        """Ingredient use for one order: planned vs actual kg per material.

        Rolled up from dw_doses over the order's batches. Deliberately not from
        dw_batch_events(payload.material_id): nested queries work on Mongo but
        the in-memory fallback only matches top-level keys, so that would break
        silently offline.
        """
        batch_ids = set(b["batch_id"] for b in
                        self.db.dw_batches.find({"order_id": order_id}))
        if not batch_ids:
            return []
        names = {m["material_id"]: m for m in self.db.dw_materials.find({})}
        agg: dict[str, dict] = {}
        for d in self.db.dw_doses.find({}):
            if d.get("batch_id") not in batch_ids:
                continue
            mid = d.get("material_id")
            row = agg.setdefault(mid, {
                "material_id": mid,
                "name": (names.get(mid) or {}).get("name", mid),
                "uom": d.get("uom") or (names.get(mid) or {}).get("uom", "kg"),
                "qty_target": 0.0, "qty_actual": 0.0,
                "doses_count": 0, "out_of_tolerance": 0,
            })
            row["qty_target"] += float(d.get("qty_target") or 0)
            row["qty_actual"] += float(d.get("qty_actual") or 0)
            row["doses_count"] += 1
            if d.get("in_tolerance") is False:
                row["out_of_tolerance"] += 1
        out = []
        for row in agg.values():
            row["delta"] = round(row["qty_actual"] - row["qty_target"], 3)
            row["delta_pct"] = (round(100.0 * row["delta"] / row["qty_target"], 2)
                                if row["qty_target"] else 0.0)
            out.append(row)
        return sorted(out, key=lambda r: -r["qty_target"])

    def order_detail(self, order_id: str) -> Optional[dict]:
        """Everything the order-detail panel needs, in one request."""
        order = self.get_order(order_id)
        if order is None:
            return None
        batches = [{
            "batch_id": b.get("batch_id"),
            "state": b.get("state"),
            "verdict": b.get("verdict"),
            "planned_L": b.get("planned_L"),
            "packs_total": b.get("packs_total"),
            "reject_count": b.get("reject_count"),
            "created_at": b.get("created_at"),
            "started_at": b.get("started_at"),
            "completed_at": b.get("completed_at"),
        } for b in self.db.dw_batches.find({"order_id": order_id})]
        batches.sort(key=lambda b: b.get("created_at") or "")
        return {
            **order,
            "progress": self.order_progress(order_id),
            "batches": batches,
            "consumption": self.order_consumption(order_id),
        }

    def list_orders(self) -> list[dict]:
        return [{**o, "progress": self.order_progress(o["order_id"])}
                for o in self.db.dw_orders.find({})]

    def mark_running(self, order_id: str) -> None:
        order = self.get_order(order_id)
        if order and order["status"] == M.ORDER_OPEN:
            self.db.dw_orders.update_one({"order_id": order_id},
                                         {"$set": {"status": M.ORDER_RUNNING}})
            self._event(order_id, "order_running", {})
            self.publish_status({**order, "status": M.ORDER_RUNNING})

    def close_order(self, order_id: str) -> dict:
        order = self.get_order(order_id)
        if order is None:
            raise ValueError(f"unknown order {order_id!r}")
        if order["status"] == M.ORDER_DONE:
            return order
        prog = self.order_progress(order_id)
        if prog["produced_packs"] == 0:
            raise ValueError(f"order {order_id} has no production booked "
                             "— close refused (PR-34 stop rule)")
        self.db.dw_orders.update_one({"order_id": order_id},
                                     {"$set": {"status": M.ORDER_DONE,
                                               "completed_at": _iso()}})
        self._event(order_id, "order_closed", {"produced_packs": prog["produced_packs"]})
        out = self.get_order(order_id)
        self.publish_status(out)
        return out

    def publish_status(self, order: dict) -> None:
        if self.bus is None:
            return
        oid = order["order_id"]
        self.bus.publish_json(f"Orders/{oid}/Status/status",
                              {"value": order["status"], "ts": _iso()})
        prog = self.order_progress(oid)
        self.bus.publish_json(f"Orders/{oid}/Status/progress", {
            "target_qty_L": order.get("target_qty_L"),
            "batched_L": prog["batched_L"],
            "produced_packs": prog["produced_packs"],
            "ts": _iso(),
        })

    def _event(self, order_id: str, event_type: str, payload: dict) -> None:
        self.db.dw_batch_events.insert_one({
            "batch_id": None, "order_id": order_id,
            "event_type": event_type, "payload": payload, "ts": _iso(),
        })
