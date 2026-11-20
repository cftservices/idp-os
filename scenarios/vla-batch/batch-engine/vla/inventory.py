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


def block(db, events: Events, material_id: str, qty: float,
          batch_id: Optional[str], kind: str) -> Optional[float]:
    """Take finished goods of a HOLD (kind "quarantine") or REJECTED (kind "scrap")
    batch out of available stock again.

    The packs were booked as production at fill time, before the verdict. A
    batch that is not APPROVED is never palletised or shipped, so without this
    the stock of finished goods only ever grows.
    """
    if kind not in ("quarantine", "scrap"):
        raise ValueError(f"unknown block kind {kind!r}")
    return _mutate(db, events, material_id, -abs(float(qty)), batch_id, kind)


def receive(db, events: Events, material_id: str, qty: float,
            lot_no: Optional[str] = None,
            operator_id: Optional[str] = None) -> Optional[float]:
    """Goods receipt: raw material delivered into stock.

    Without this there is no way back up — consumption only ever subtracts, so
    a demo that keeps running drives every ingredient negative. Booked as its
    own `kind` so a receipt is distinguishable from produced finished goods.
    """
    qty = abs(float(qty))
    if qty <= 0:
        raise ValueError("receipt qty must be > 0")
    if db.dw_materials.find_one({"material_id": material_id}) is None:
        raise ValueError(f"unknown material_id {material_id!r}")
    after = _mutate(db, events, material_id, qty, None, "receipt")
    events(None, "goods_receipt", {"material_id": material_id, "qty": qty,
                                   "lot_no": lot_no, "operator_id": operator_id,
                                   "stock_qty": after})
    return after
