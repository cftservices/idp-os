"""Demo logistics around the warehouse flow: shipments, forklifts, inbound milk.

Everything here is `demo-sim`: the engine has no TMS, no forklift telemetry
and no gate system. The 3D twin needs those numbers to tell a full story, so
this module derives them from what the warehouse simulation really did and
labels them as simulated in the API (`source: "demo-sim"`).

  shipments  one per autopilot batch with pallets in the cold store; derived
             from the HU statuses on every request, so it never drifts from
             the rack. Customer and destination are fictitious and stable per
             batch (anonymisation rule: no real company names).
  forklifts  HT-01 puts away, HT-02 loads trucks. Each move drains the
             battery; below CHARGE_BELOW_PCT a truck goes to the charger and
             the other one takes over its work. Trips reset at midnight.
  inbound    one supplier vehicle per raw material, ordered when its silo
             drops below half (en_route -> unloading -> gone): a milk tanker,
             bulk trucks for sugar and starch, a pallet truck for cocoa.
             While it unloads, the load is booked in chunks as a real goods
             receipt, capped at the silo capacity, so the silo in the twin
             rises with the vehicle.

Silo capacity is 4x the reorder level, the same full mark the twin uses
(ui/lib/twin/derive.ts, siloFill). Without these receipts consumption only
ever subtracts and the demo drives every ingredient negative.

All state is in memory: a restart starts with full batteries and no tanker
on the road, which is fine for a demo and avoids a collection that nobody
else reads. Stock itself lives in dw_materials and survives a restart."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import model as M

SOURCE = "demo-sim"

# Fictitious customers and destinations, with a drive time in demo minutes
# (compressed: a delivery must finish before shipped HUs are dropped, 30 min).
CUSTOMERS = ["Zuivelhandel Noord", "Versmarkt Groep", "Koelketen Midden",
             "Dessertgroothandel West", "Supermarktcentrale Oost"]
DESTINATIONS = [("Zwolle", 8), ("Utrecht", 12), ("Eindhoven", 16),
                ("Groningen", 18), ("Rotterdam", 14), ("Arnhem", 10)]

BATTERY_PER_MOVE_PCT = 1.6
BATTERY_IDLE_PER_MIN_PCT = 0.05
CHARGE_PER_MIN_PCT = 4.0
CHARGE_BELOW_PCT = 25.0
CHARGE_UNTIL_PCT = 95.0

TANKER_EN_ROUTE_S = 3 * 60
TANKER_UNLOAD_S = 5 * 60
TANKER_LITRES = 28_000
TANKER_ORDER_BELOW = 0.5        # fraction of silo capacity
TANKER_CHUNK_KG = 2_500.0       # one receipt per chunk, not one per tick
# The twin animates the departure from next_at - this; keep equal to
# TANKER_GONE_S in ui/lib/twin/flow.ts.
TANKER_GONE_S = 480
SILO_CAPACITY_X_REORDER = 4.0
MILK_ID = "milk"
MILK_DENSITY_KG_L = 1.03

# Supplier vehicle per raw material. Milk is fixed at a 28,000 L tanker; the
# dry goods bring 1.25x their silo capacity (booking stops when the silo is
# full). Unknown materials get the pallet truck.
VEHICLES = {
    "milk":   {"kind": "melktankwagen", "vehicle": "tanker", "prefix": "MLK", "unload_s": TANKER_UNLOAD_S},
    "sugar":  {"kind": "bulkwagen suiker", "vehicle": "bulk", "prefix": "SUI", "unload_s": 200},
    "starch": {"kind": "bulkwagen zetmeel", "vehicle": "bulk", "prefix": "ZTM", "unload_s": 200},
    "cocoa":  {"kind": "vrachtwagen cacao", "vehicle": "pallet", "prefix": "CAC", "unload_s": 160},
}
DEFAULT_VEHICLE = {"kind": "vrachtwagen", "vehicle": "pallet", "prefix": "INK", "unload_s": 160}
DRY_LOAD_X_CAPACITY = 1.25
CHUNKS_PER_LOAD = 8


def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _pick(seq, key: str):
    h = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)
    return seq[h % len(seq)]


class Forklift:
    def __init__(self, fid: str, role: str, battery: float):
        self.id = fid
        self.role = role
        self.battery = battery
        self.charging = False
        self.trips_today = 0
        self.last_task: Optional[str] = None
        self.last_move: Optional[datetime] = None

    def snapshot(self) -> dict:
        state = "charging" if self.charging else (
            "busy" if self.last_move is not None else "idle")
        return {"id": self.id, "role": self.role, "state": state,
                "battery_pct": round(self.battery, 1),
                "trips_today": self.trips_today, "last_task": self.last_task,
                "last_move": self.last_move.isoformat() if self.last_move else None}


class LogisticsSim:
    def __init__(self, db, operator_id: str, events=None):
        self.db = db
        self.operator_id = operator_id
        self.events = events or (lambda *_: None)
        self.forklifts = {"putaway": Forklift("HT-01", "putaway", 88.0),
                          "loader": Forklift("HT-02", "loader", 64.0)}
        self._clock: Optional[datetime] = None
        self._day: Optional[str] = None
        self._deliveries: dict[str, dict] = {}   # material_id -> current/last vehicle
        self._ordered: dict[str, int] = {}

    # ------------------------------------------------------------- forklifts

    def _advance(self, now: datetime) -> None:
        if self._clock is not None:
            minutes = max(0.0, (now - self._clock).total_seconds() / 60.0)
            for f in self.forklifts.values():
                if f.charging:
                    f.battery = min(100.0, f.battery + CHARGE_PER_MIN_PCT * minutes)
                    if f.battery >= CHARGE_UNTIL_PCT:
                        f.charging = False
                else:
                    f.battery = max(0.0, f.battery - BATTERY_IDLE_PER_MIN_PCT * minutes)
                if f.last_move is not None and (now - f.last_move).total_seconds() > 20:
                    f.last_move = None
        self._clock = now
        day = now.date().isoformat()
        if self._day != day:
            self._day = day
            for f in self.forklifts.values():
                f.trips_today = 0

    def _worker(self, role: str) -> Forklift:
        """The truck that does this role now; the other covers while it charges."""
        own = self.forklifts[role]
        if not own.charging:
            return own
        other = self.forklifts["loader" if role == "putaway" else "putaway"]
        return other if not other.charging else own

    def record(self, moved: list[str], now: Optional[datetime] = None) -> None:
        """Called with what WarehouseSim.step() did ("putaway HU-..", "ship HU-..")."""
        now = now or datetime.now(timezone.utc)
        self._advance(now)
        for m in moved:
            kind, _, hu = m.partition(" ")
            role = {"putaway": "putaway", "ship": "loader"}.get(kind)
            if role is None:
                continue
            f = self._worker(role)
            f.battery = max(0.0, f.battery - BATTERY_PER_MOVE_PCT)
            f.trips_today += 1
            f.last_task = m
            f.last_move = now
            if f.battery < CHARGE_BELOW_PCT:
                f.charging = True
        if self.db is not None:
            self._supply(now)

    # ---------------------------------------------------------------- supply

    def _receive(self, material_id: str, qty: float, lot_no: str) -> None:
        if qty <= 0:
            return
        from . import inventory  # local: inventory has no reason to know us
        inventory.receive(self.db, self.events, material_id, round(qty, 4),
                          lot_no=lot_no, operator_id=self.operator_id)

    def _supply(self, now: datetime) -> None:
        for m in self.db.dw_materials.find({}):
            mid = m.get("material_id")
            reorder = float(m.get("reorder_level") or 0)
            if mid == M.FINISHED_GOOD_ID or reorder <= 0:
                continue
            cap = reorder * SILO_CAPACITY_X_REORDER
            stock = float(m.get("stock_qty") or 0)
            self._deliver(now, mid, m.get("uom") or "kg", stock, cap,
                          float(m.get("density_kg_L") or MILK_DENSITY_KG_L))

    def _deliver(self, now: datetime, mid: str, uom: str, stock: float,
                 cap: float, density: float) -> None:
        t = self._deliveries.get(mid)
        if t is None or t["stage"] == "gone":
            if stock >= cap * TANKER_ORDER_BELOW:
                return
            spec = VEHICLES.get(mid, DEFAULT_VEHICLE)
            n = self._ordered[mid] = self._ordered.get(mid, 0) + 1
            milk = mid == MILK_ID
            load = TANKER_LITRES * density if milk else cap * DRY_LOAD_X_CAPACITY
            self._deliveries[mid] = {
                "id": f"{spec['prefix']}-{(n * 7 + len(mid) * 131) % 900 + 100:03d}",
                "material_id": mid, "kind": spec["kind"], "vehicle": spec["vehicle"],
                "uom": uom, "litres": TANKER_LITRES if milk else 0,
                "unload_s": spec["unload_s"], "stage": "en_route",
                "arrive_at": now + timedelta(seconds=TANKER_EN_ROUTE_S),
                "load_kg": load,
                "chunk": TANKER_CHUNK_KG if milk else load / CHUNKS_PER_LOAD,
                "pumped_kg": 0.0, "booked_kg": 0.0}
            return
        if t["stage"] == "en_route":
            if now < t["arrive_at"]:
                return
            t["stage"] = "unloading"
            t["unload_end"] = t["arrive_at"] + timedelta(seconds=t["unload_s"])
        frac = min(1.0, (now - t["arrive_at"]).total_seconds() / t["unload_s"])
        due = t["load_kg"] * frac
        pending = due - t["pumped_kg"]
        full = stock >= cap - 1e-6
        if pending >= t["chunk"] or frac >= 1.0 or full:
            qty = max(0.0, min(pending, cap - stock))
            self._receive(mid, qty, t["id"])
            t["pumped_kg"] = due
            t["booked_kg"] += qty
        if frac >= 1.0 or full:
            t["stage"] = "gone"
            t["left_at"] = now

    # ------------------------------------------------------------- shipments

    def shipments(self, now: datetime) -> list[dict]:
        by_batch: dict[str, list[dict]] = {}
        for h in self.db.dw_handling_units.find({}):
            if h.get("operator_id") != self.operator_id:
                continue
            if h["status"] in (M.HU_AWAITING, M.HU_SHIPPED):
                by_batch.setdefault(h["batch_id"], []).append(h)
        out = []
        for batch_id, rows in by_batch.items():
            shipped = [h for h in rows if h["status"] == M.HU_SHIPPED]
            city, drive_min = _pick(DESTINATIONS, batch_id + ":dest")
            first_ts = min(_parse(h["ts"]) or now for h in rows)
            loaded_at = max((_parse(h["ts"]) or now for h in shipped), default=None)
            eta = None
            if not shipped:
                stage = "order"
            elif len(shipped) < len(rows):
                stage = "loading"
            else:
                arrive = loaded_at + timedelta(minutes=drive_min)
                stage = "en_route" if now < arrive else "delivered"
                eta = arrive.isoformat()
            suffix = batch_id.split("-")[-1][-4:].upper()
            out.append({
                "shipment_id": f"SHP-{suffix}",
                "truck_id": f"TRK-{int(hashlib.sha1(batch_id.encode()).hexdigest()[:4], 16) % 90 + 10}",
                "batch_id": batch_id,
                "customer": _pick(CUSTOMERS, batch_id + ":cust"),
                "destination": city,
                "drive_min": drive_min,
                "stage": stage,
                "pallets": len(rows),
                "pallets_loaded": len(shipped),
                "packs": sum(int(h.get("packs_count") or 0) for h in rows),
                "ordered_at": first_ts.isoformat(),
                "loaded_at": loaded_at.isoformat() if loaded_at and stage != "loading" else None,
                "eta": eta,
            })
        rank = {"loading": 0, "en_route": 1, "order": 2, "delivered": 3}
        out.sort(key=lambda s: (rank[s["stage"]], s["ordered_at"]))
        return out

    # --------------------------------------------------------------- inbound

    @staticmethod
    def _view(t: dict, now: datetime) -> dict:
        """next_at: arrival while en route, end of unloading while unloading,
        and left_at + TANKER_GONE_S once gone (the twin's departure clock)."""
        if t["stage"] == "en_route":
            nxt, pct = t["arrive_at"], 0.0
        elif t["stage"] == "unloading":
            nxt = t["unload_end"]
            pct = min(100.0, max(0.0, (now - t["arrive_at"]).total_seconds()
                                 / t["unload_s"] * 100))
        else:
            nxt, pct = t["left_at"] + timedelta(seconds=TANKER_GONE_S), 100.0
        return {"id": t["id"], "kind": t["kind"], "vehicle": t["vehicle"],
                "material_id": t["material_id"], "stage": t["stage"],
                "litres": t["litres"], "load_qty": round(t["load_kg"], 1),
                "uom": t["uom"], "unloaded_pct": round(pct, 1),
                "received_kg": round(t["booked_kg"], 1),
                "next_at": nxt.isoformat()}

    def inbound(self, now: datetime) -> Optional[dict]:
        """The current (or last) milk tanker; None until the first one is ordered."""
        t = self._deliveries.get(MILK_ID)
        return self._view(t, now) if t else None

    def inbounds(self, now: datetime) -> list[dict]:
        """Every supplier vehicle that was ordered, one (the latest) per material."""
        return [self._view(t, now) for t in self._deliveries.values()]

    # -------------------------------------------------------------- snapshot

    def snapshot(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        self._advance(now)
        return {"source": SOURCE, "ts": now.isoformat(),
                "shipments": self.shipments(now),
                "forklifts": [f.snapshot() for f in self.forklifts.values()],
                "inbound": self.inbound(now),
                "inbounds": self.inbounds(now)}
