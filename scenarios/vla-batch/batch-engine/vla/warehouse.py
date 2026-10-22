"""Demo warehouse flow for autopilot batches: palletise -> putaway -> ship.

Without this the cold store stays empty: HUs are only created by an operator
in the shopfloor screen. The simulation books through HandlingUnitManager, so
the same APPROVED gate, events and checks apply as for a manual booking. It
paces itself on the wall clock and moves at most one HU per stage per tick,
so the rack fills and drains gradually instead of jumping:

  wrap     per line one pallet every WRAP_EVERY_S from the oldest recently
           completed autopilot batch of that line (each line has its own
           conveyor and palletiser), until its packs are on pallets;
  putaway  per line the oldest wrapped HU after WRAP_DWELL_S, into a free
           rack slot (each line has its own putaway forklift);
  ship     a batch whose pallets have all been in the cold store for
           STORE_DWELL_S is loaded one HU per SHIP_EVERY_S (one truck at a
           time); a nearly full rack ships the oldest batch earlier.

The rack slot is stored on the HU (`slot`), so a pallet keeps its place in
the 3D twin when others leave. Shipped autopilot HUs are dropped after
KEEP_SHIPPED_S, which keeps GET /hu small (~700 batches a day)."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import model as M

log = logging.getLogger("vla.warehouse")

PACKS_PER_PALLET = 480      # 1 L packs: 12 per tray, 40 trays per pallet
RACK_SLOTS = 192            # must match RACK_SLOTS in ui/lib/twin/layout.ts
WRAP_EVERY_S = 9.0
WRAP_DWELL_S = 5.0
PUTAWAY_EVERY_S = 9.0
STORE_DWELL_S = 12 * 60.0
SHIP_EVERY_S = 6.0
LOOKBACK_S = 15 * 60.0      # older completed batches are not palletised
# Must exceed LOOKBACK_S: once its HUs are dropped a batch looks unpalletised
# again, so it must have left the lookback window by then. Two hours so a
# shipment stays visible as "delivered" after its drive time (was 30 min:
# the delivery history vanished almost as soon as it arrived).
KEEP_SHIPPED_S = 2 * 60 * 60.0
MAX_AT_PALLETISER = 4       # per line
ZONE_SLOTS = 96             # first half of the rack belongs to L1 (ui: RACK_ZONE_SLOTS)
LINES = ("L1", "L2")
PRESSURE_FREE_SLOTS = 12


def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class WarehouseSim:
    def __init__(self, db, handling, operator_id: str,
                 rng: Optional[random.Random] = None, logistics=None):
        self.db = db
        self.handling = handling
        self.operator_id = operator_id
        self.rng = rng or random.Random()
        self.logistics = logistics
        self._last: dict[str, datetime] = {}

    def _due(self, stage: str, now: datetime, every_s: float) -> bool:
        last = self._last.get(stage)
        return last is None or (now - last).total_seconds() >= every_s

    def _mark(self, stage: str, now: datetime) -> None:
        self._last[stage] = now

    def step(self, now: Optional[datetime] = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        hus = [h for h in self.db.dw_handling_units.find({})
               if h.get("operator_id") == self.operator_id]
        done = []
        stages = [self._ship]
        stages += [lambda h, n, ln=ln: self._putaway(h, n, ln) for ln in LINES]
        stages += [lambda h, n, ln=ln: self._wrap(h, n, ln) for ln in LINES]
        for stage in stages:
            r = stage(hus, now)
            if r:
                done.append(r)
                hus = [h for h in self.db.dw_handling_units.find({})
                       if h.get("operator_id") == self.operator_id]
        self._drop_shipped(hus, now)
        if self.logistics is not None:
            self.logistics.record(done, now)
        return done

    # ---------------------------------------------------------------- stages

    @staticmethod
    def _line(hu: dict) -> str:
        return hu.get("line") or M.DEFAULT_LINE

    def _wrap(self, hus: list[dict], now: datetime, line: str = M.DEFAULT_LINE) -> Optional[str]:
        if not self._due(f"wrap:{line}", now, WRAP_EVERY_S):
            return None
        if sum(1 for h in hus if h["status"] == M.HU_WRAPPED
               and self._line(h) == line) >= MAX_AT_PALLETISER:
            return None
        cutoff = now - timedelta(seconds=LOOKBACK_S)
        candidates = []
        for b in self.db.dw_batches.find({"created_by": self.operator_id}):
            done_at = _parse(b.get("completed_at"))
            if b.get("state") != M.COMPLETE or b.get("verdict") != M.APPROVED \
                    or done_at is None or done_at < cutoff or M.line_of_doc(b) != line:
                continue
            on_pallets = sum(int(h["packs_count"]) for h in
                             self.db.dw_handling_units.find({"batch_id": b["batch_id"]}))
            left = int(b.get("packs_total") or 0) - on_pallets
            if left > 0:
                candidates.append((done_at, b["batch_id"], left))
        if not candidates:
            return None
        _, batch_id, left = min(candidates)
        hu = self.handling.create_hu(batch_id, min(PACKS_PER_PALLET, left),
                                     self.operator_id)
        self._mark(f"wrap:{line}", now)
        return f"wrap {hu['hu_id']} {line}"

    def _putaway(self, hus: list[dict], now: datetime, line: str = M.DEFAULT_LINE) -> Optional[str]:
        if not self._due(f"putaway:{line}", now, PUTAWAY_EVERY_S):
            return None
        wrapped = sorted((h for h in hus if h["status"] == M.HU_WRAPPED
                          and self._line(h) == line
                          and (_parse(h["ts"]) or now) <= now - timedelta(seconds=WRAP_DWELL_S)),
                         key=lambda h: (h["ts"], h["hu_id"]))
        if not wrapped:
            return None
        used = {h.get("slot") for h in hus if h["status"] == M.HU_AWAITING}
        free = [s for s in range(RACK_SLOTS) if s not in used]
        if not free:
            return None
        # Zones: L1 fills the southern rows (slots 0..ZONE_SLOTS-1), L2 the
        # northern rows next to its palletiser; a full zone spills over.
        zone = [s for s in free if (s < ZONE_SLOTS) == (line == M.DEFAULT_LINE)]
        hu = wrapped[0]
        self.handling.putaway(hu["hu_id"], self.operator_id,
                              slot=self.rng.choice(zone or free))
        self._mark(f"putaway:{line}", now)
        return f"putaway {hu['hu_id']} {line}"

    def _ship(self, hus: list[dict], now: datetime) -> Optional[str]:
        if not self._due("ship", now, SHIP_EVERY_S):
            return None
        stored = [h for h in hus if h["status"] == M.HU_AWAITING]
        if not stored:
            return None
        by_batch: dict[str, list[dict]] = {}
        for h in stored:
            by_batch.setdefault(h["batch_id"], []).append(h)
        # finish the truck that is already loading before starting another
        loading = [b for b in by_batch
                   if any(h["status"] == M.HU_SHIPPED and h["batch_id"] == b for h in hus)]
        if loading:
            batch_id = min(loading, key=lambda b: min(h["ts"] for h in by_batch[b]))
        else:
            pressure = RACK_SLOTS - len(stored) <= PRESSURE_FREE_SLOTS
            ready = []
            for b, rows in by_batch.items():
                if any(h["status"] == M.HU_WRAPPED and h["batch_id"] == b for h in hus):
                    continue  # pallets of this batch still at the palletiser
                if not self._fully_palletised(b):
                    continue
                newest = max(_parse(h["ts"]) or now for h in rows)
                if pressure or (now - newest).total_seconds() >= STORE_DWELL_S:
                    ready.append((min(h["ts"] for h in rows), b))
            if not ready:
                return None
            batch_id = min(ready)[1]
        hu = min(by_batch[batch_id], key=lambda h: (h["ts"], h["hu_id"]))
        self.handling.ship(hu["hu_id"], self.operator_id)
        self._mark("ship", now)
        return f"ship {hu['hu_id']}"

    # --------------------------------------------------------------- helpers

    def _fully_palletised(self, batch_id: str) -> bool:
        b = self.db.dw_batches.find_one({"batch_id": batch_id}) or {}
        on = sum(int(h["packs_count"]) for h in
                 self.db.dw_handling_units.find({"batch_id": batch_id}))
        return on >= int(b.get("packs_total") or 0)

    def _drop_shipped(self, hus: list[dict], now: datetime) -> None:
        cutoff = now - timedelta(seconds=KEEP_SHIPPED_S)
        for h in hus:
            ts = _parse(h["ts"])
            if h["status"] == M.HU_SHIPPED and ts is not None and ts < cutoff:
                self.db.dw_handling_units.delete_many({"hu_id": h["hu_id"]})
