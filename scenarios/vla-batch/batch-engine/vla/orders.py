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
                     due_date: Optional[str] = None) -> dict:
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
            "status": M.ORDER_OPEN,
            "created_at": _iso(),
        }
        self.db.dw_orders.insert_one(doc)
        self._event(order_id, "order_created", {"recipe_id": recipe_id,
                                                "target_qty_L": float(target_qty_L)})
        self.publish_status(doc)
        return dict(doc)

    def get_order(self, order_id: str) -> Optional[dict]:
        return self.db.dw_orders.find_one({"order_id": order_id})

    def order_progress(self, order_id: str) -> dict:
        batches = self.db.dw_batches.find({"order_id": order_id})
        batch_ids = [b["batch_id"] for b in batches]
        produced = sum(p["packs"] for p in self.db.dw_production.find({})
                       if p["batch_id"] in batch_ids)
        return {
            "batched_L": sum(float(b.get("planned_L") or 0) for b in batches),
            "produced_packs": produced,
            "batch_ids": batch_ids,
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
