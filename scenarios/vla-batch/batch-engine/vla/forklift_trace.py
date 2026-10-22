"""Track & trace from the pallet and the forklift side.

The order trace (trace.py) walks order -> batch -> pallet -> truck. These two
views walk the other way: which events did one pallet see, and which orders
did one forklift handle. Both read the forklift_task batch events that
LogisticsSim books per move, because shipped HUs are dropped after a couple of
hours and the batch events stay as long as the batch."""
from __future__ import annotations

from typing import Optional

HU_EVENTS = ("hu_scanned", "putaway_booked", "forklift_task", "hu_shipped",
             "hu_packs_difference", "scan_rejected")


def hu_history(db, hu_id: str) -> Optional[dict]:
    events = sorted((e for e in db.dw_batch_events.find({"payload.hu_id": hu_id})
                     if e.get("event_type") in HU_EVENTS),
                    key=lambda e: e.get("ts") or "")
    hu = db.dw_handling_units.find_one({"hu_id": hu_id})
    if hu is None and not events:
        return None
    bid = (hu or {}).get("batch_id") or next(
        (e.get("batch_id") for e in events if e.get("batch_id")), None)
    batch = db.dw_batches.find_one({"batch_id": bid}) if bid else None
    return {
        "hu_id": hu_id, "batch_id": bid,
        "order_id": (batch or {}).get("order_id"),
        "status": (hu or {}).get("status"), "slot": (hu or {}).get("slot"),
        "putaway_by": (hu or {}).get("putaway_by"), "loaded_by": (hu or {}).get("loaded_by"),
        "live": hu is not None,
        "events": [{"ts": e.get("ts"), "type": e.get("event_type"),
                    "payload": e.get("payload") or {}} for e in events],
    }


def forklift_history(db, forklift_id: str, limit: int = 50) -> dict:
    """Per order: how many pallets this truck put away / loaded, and when.
    Plus its most recent moves (newest first)."""
    moves = sorted(db.dw_batch_events.find({"event_type": "forklift_task",
                                            "payload.forklift_id": forklift_id}),
                   key=lambda e: e.get("ts") or "", reverse=True)
    batches: dict[str, dict] = {}
    orders: dict[str, dict] = {}
    customers = {s.get("batch_id"): s.get("customer") for s in db.dw_shipments.find({})}
    for e in moves:
        bid = e.get("batch_id")
        if bid not in batches:
            b = db.dw_batches.find_one({"batch_id": bid}) or {}
            oid = b.get("order_id")
            o = (db.dw_orders.find_one({"order_id": oid}) if oid else None) or {}
            batches[bid] = {"order_id": oid, "recipe_id": b.get("recipe_id") or o.get("recipe_id"),
                            "customer": o.get("customer") or customers.get(bid)}
        info = batches[bid]
        key = info["order_id"] or f"batch:{bid}"
        p = e.get("payload") or {}
        row = orders.setdefault(key, {
            "order_id": info["order_id"], "customer": info["customer"],
            "recipe_id": info["recipe_id"], "batches": set(),
            "putaway": 0, "loaded": 0, "trucks": set(),
            "first_ts": e.get("ts"), "last_ts": e.get("ts")})
        row["batches"].add(bid)
        if p.get("task") == "putaway":
            row["putaway"] += 1
        else:
            row["loaded"] += 1
            if p.get("truck_id"):
                row["trucks"].add(p["truck_id"])
        ts = e.get("ts") or ""
        row["first_ts"] = min(row["first_ts"] or ts, ts)
        row["last_ts"] = max(row["last_ts"] or ts, ts)
    per_order = sorted(({**r, "batches": sorted(r["batches"]), "trucks": sorted(r["trucks"])}
                        for r in orders.values()),
                       key=lambda r: r["last_ts"] or "", reverse=True)
    recent = []
    for e in moves[:max(0, limit)]:
        p = e.get("payload") or {}
        recent.append({"ts": e.get("ts"), "task": p.get("task"), "hu_id": p.get("hu_id"),
                       "batch_id": e.get("batch_id"),
                       "order_id": batches.get(e.get("batch_id"), {}).get("order_id"),
                       "line": p.get("line"), "slot": p.get("slot"),
                       "truck_id": p.get("truck_id")})
    return {
        "forklift_id": forklift_id,
        "totals": {"moves": len(moves),
                   "putaway": sum(r["putaway"] for r in per_order),
                   "loaded": sum(r["loaded"] for r in per_order),
                   "orders": sum(1 for r in per_order if r["order_id"])},
        "orders": per_order,
        "recent": recent,
    }
