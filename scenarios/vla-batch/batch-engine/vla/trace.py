"""Order trace: one order from intake to the customer's dock.

order -> batches (production, QA) -> pallets (HU) -> truck -> customer.

Live trucks come from LogisticsSim.shipments(); departed ones are also kept in
dw_shipments because the shipped HUs (and so the derived shipment) are dropped
after a couple of hours. The live view wins when both exist."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from vla import model as M

# One step per batch, in flow order; the UI draws them as a chain.
STEPS = ["gepland", "productie", "palletiseren", "koelcel", "laden", "onderweg", "afgeleverd"]


def _parse(ts) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except (TypeError, ValueError):
        return None


def _stage(ship: Optional[dict], now: datetime) -> Optional[str]:
    if not ship:
        return None
    if ship.get("stage") in ("order", "loading"):
        return ship["stage"]
    eta = _parse(ship.get("eta"))
    return "delivered" if eta and now >= eta else "en_route"


def _step(batch: dict, hus: dict, stage: Optional[str]) -> str:
    if stage == "delivered":
        return "afgeleverd"
    if stage == "en_route":
        return "onderweg"
    if stage == "loading":
        return "laden"
    if hus.get(M.HU_AWAITING) or hus.get(M.HU_STORED):
        return "koelcel"
    if hus.get(M.HU_WRAPPED):
        return "palletiseren"
    if batch.get("state") in (None, M.IDLE):
        return "gepland"
    return "productie"


def order_trace(db, order_id: str, now: datetime,
                live_shipments: Optional[list[dict]] = None) -> Optional[dict]:
    order = db.dw_orders.find_one({"order_id": order_id})
    if order is None:
        return None
    batches = sorted(db.dw_batches.find({"order_id": order_id}),
                     key=lambda b: b.get("created_at") or "")
    ids = {b["batch_id"] for b in batches}
    stored = {s["batch_id"]: s for s in db.dw_shipments.find({}) if s.get("batch_id") in ids}
    live = {s["batch_id"]: s for s in (live_shipments or []) if s.get("batch_id") in ids}
    hus: dict[str, dict[str, int]] = {}
    for h in db.dw_handling_units.find({}):
        if h.get("batch_id") in ids:
            c = hus.setdefault(h["batch_id"], {})
            c[h["status"]] = c.get(h["status"], 0) + 1
    # Voorbereiding (afwegen + premix) hoort bij de batch die hem verbruikte;
    # sets die nog niet verbruikt zijn tonen we op orderniveau.
    preps = [x for x in db.dw_prep_orders.find({})
             if x.get("order_id") == order_id or x.get("batch_id") in ids]
    prep_by_batch = {x["batch_id"]: x for x in preps if x.get("batch_id")}

    def _prep(x):
        return None if not x else {
            "prep_id": x.get("prep_id"), "state": x.get("state"),
            "weigh_station": x.get("weigh_station"),
            "premix_tank": x.get("premix_tank"),
            "created_at": x.get("created_at"), "state_changed_at": x.get("state_changed_at"),
            "history": x.get("history") or [],
            "components": x.get("components") or []}

    rows = []
    for b in batches:
        bid = b["batch_id"]
        ship = live.get(bid) or stored.get(bid)
        stage = _stage(ship, now)
        counts = hus.get(bid, {})
        pallets = sum(counts.values()) or (ship or {}).get("pallets") or 0
        rows.append({
            "batch_id": bid, "state": b.get("state"), "verdict": b.get("verdict"),
            "planned_L": b.get("planned_L"), "packs": b.get("packs_total"),
            "created_at": b.get("created_at"), "started_at": b.get("started_at"),
            "completed_at": b.get("completed_at"),
            "pallets": pallets, "hu": counts, "line": M.line_of_doc(b),
            "prep": _prep(prep_by_batch.get(bid)),
            "step": _step(b, counts, stage),
            "shipment": None if not ship else {
                "shipment_id": ship.get("shipment_id"), "truck_id": ship.get("truck_id"),
                "customer": ship.get("customer"), "destination": ship.get("destination"),
                "stage": stage, "pallets": ship.get("pallets"),
                "pallets_loaded": ship.get("pallets_loaded", ship.get("pallets")),
                "packs": ship.get("packs"), "loaded_at": ship.get("loaded_at"),
                "eta": ship.get("eta"),
            },
        })

    def n(step):
        return sum(1 for r in rows if r["step"] == step)

    delivered = [r for r in rows if r["step"] == "afgeleverd"]
    return {
        "order_id": order_id, "customer": order.get("customer"),
        "status": order.get("status"), "recipe_id": order.get("recipe_id"),
        "line": M.line_of_recipe(order.get("recipe_id")) if order.get("recipe_id") else None,
        "product_name": M.get_recipe(order["recipe_id"]).product_name
        if order.get("recipe_id") in M.RECIPES else None,
        "prep_pending": [_prep(x) for x in preps if not x.get("batch_id")],
        "target_qty_L": order.get("target_qty_L"), "due_date": order.get("due_date"),
        "created_at": order.get("created_at"), "completed_at": order.get("completed_at"),
        "steps": STEPS,
        "batches": rows,
        "totals": {
            "batches": len(rows),
            "pallets": sum(r["pallets"] for r in rows),
            "packs": sum(int(r["packs"] or 0) for r in rows),
            "trucks_en_route": n("onderweg"),
            "trucks_delivered": len(delivered),
            "packs_delivered": sum(int((r["shipment"] or {}).get("packs") or 0)
                                   for r in delivered),
            "per_step": {s: n(s) for s in STEPS},
        },
    }
