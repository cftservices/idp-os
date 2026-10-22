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


# Milestones for the order timeline. The rest (dose_booked, phase_mirrored,
# stock_mutation, ...) is process detail that would drown the chain.
TIMELINE_EVENTS = {
    "order_created", "order_scanned", "order_running", "order_closed",
    "batch_created", "batch_started", "setpoints_pushed", "filling_done",
    "sample_taken", "verdict_set", "batch_complete", "batch_discarded",
    "hu_scanned", "putaway_booked", "forklift_task", "hu_shipped",
}
TIMELINE_MAX = 600


def _pallets(db, bid: str, events: list[dict]) -> list[dict]:
    """Every pallet of the batch with who moved it. Live HUs first; pallets
    whose HU was already dropped are rebuilt from their batch events."""
    out: dict[str, dict] = {}
    for h in db.dw_handling_units.find({"batch_id": bid}):
        out[h["hu_id"]] = {"hu_id": h["hu_id"], "status": h.get("status"),
                           "packs": h.get("packs_count"), "slot": h.get("slot"),
                           "putaway_by": h.get("putaway_by"), "loaded_by": h.get("loaded_by"),
                           "truck_id": None, "wrapped_at": h.get("created_at"),
                           "stored_at": None, "shipped_at": None}
    for e in events:
        p = e.get("payload") or {}
        hid = p.get("hu_id")
        if not hid:
            continue
        r = out.setdefault(hid, {"hu_id": hid, "status": None, "packs": None, "slot": None,
                                 "putaway_by": None, "loaded_by": None, "truck_id": None,
                                 "wrapped_at": None, "stored_at": None, "shipped_at": None})
        kind = e.get("event_type")
        if kind == "hu_scanned":
            r["wrapped_at"] = r["wrapped_at"] or e.get("ts")
            r["packs"] = r["packs"] or p.get("packs_count")
        elif kind == "putaway_booked":
            r["stored_at"] = e.get("ts")
            if r["slot"] is None:
                r["slot"] = p.get("slot")
        elif kind == "hu_shipped":
            r["shipped_at"] = e.get("ts")
            r["status"] = r["status"] or M.HU_SHIPPED
        elif kind == "forklift_task":
            if p.get("task") == "putaway":
                r["putaway_by"] = r["putaway_by"] or p.get("forklift_id")
            else:
                r["loaded_by"] = r["loaded_by"] or p.get("forklift_id")
                r["truck_id"] = p.get("truck_id")
    return sorted(out.values(), key=lambda r: r["wrapped_at"] or r["hu_id"])


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

    timeline = [e for e in db.dw_batch_events.find({"order_id": order_id})
                if e.get("event_type") in TIMELINE_EVENTS]
    rows = []
    for b in batches:
        bid = b["batch_id"]
        bev = [e for e in db.dw_batch_events.find({"batch_id": bid})
               if e.get("event_type") in TIMELINE_EVENTS]
        timeline += bev
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
            "pallet_list": _pallets(db, bid, bev),
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
    for x in preps:
        for h in x.get("history") or []:
            timeline.append({"batch_id": x.get("batch_id"), "event_type": "prep_" + str(h.get("state")),
                             "payload": {"prep_id": x.get("prep_id")}, "ts": h.get("ts")})
    for r in rows:
        sh = r["shipment"] or {}
        if sh.get("eta") and r["step"] == "afgeleverd":
            timeline.append({"batch_id": r["batch_id"], "event_type": "delivered",
                             "payload": {"truck_id": sh.get("truck_id"),
                                         "customer": sh.get("customer"),
                                         "destination": sh.get("destination")},
                             "ts": sh.get("eta")})
    timeline.sort(key=lambda e: e.get("ts") or "")
    events = [{"ts": e.get("ts"), "batch_id": e.get("batch_id"),
               "type": e.get("event_type"), "payload": e.get("payload") or {}}
              for e in timeline[-TIMELINE_MAX:]]
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
        "events": events,
        "events_truncated": len(timeline) > TIMELINE_MAX,
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
