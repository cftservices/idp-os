"""Inventory mutations (PR-27): stock on the material master, moved by
consumptions (-) and productions (+). Below-reorder-level fires a
stock_below_threshold event once per crossing."""

from __future__ import annotations

from typing import Callable, Optional

Events = Callable[[Optional[str], str, dict], None]


def _mutate(db, events: Events, material_id: str, delta: float,
            batch_id: Optional[str], kind: str) -> Optional[float]:
    mat = db.dw_materials.find_one({"material_id": material_id})
    if mat is None:
        return None
    before = float(mat.get("stock_qty", 0.0))
    after = round(before + delta, 4)
    db.dw_materials.update_one({"material_id": material_id},
                               {"$set": {"stock_qty": after}})
    events(batch_id, "stock_mutation",
           {"material_id": material_id, "delta": delta, "stock_qty": after,
            "kind": kind})
    level = float(mat.get("reorder_level", 0.0))
    if level > 0 and after < level <= before:
        events(batch_id, "stock_below_threshold",
               {"material_id": material_id, "stock_qty": after,
                "reorder_level": level})
    return after


def consume(db, events: Events, material_id: str, qty: float,
            batch_id: Optional[str]) -> Optional[float]:
    return _mutate(db, events, material_id, -abs(float(qty)), batch_id, "consumption")


def produce(db, events: Events, material_id: str, qty: float,
            batch_id: Optional[str]) -> Optional[float]:
    return _mutate(db, events, material_id, abs(float(qty)), batch_id, "production")
