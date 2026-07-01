"""OrderRunner — the order-centric MES core.

Drives a work order through its recipe.process_path:
  NEW -> RELEASED -> STARTED -> COMPLETED -> CLOSED

For each order it:
  * explodes the recipe BOM into a job_bom scaled to planned_qty
  * books Consumption rows (qty_actual within tolerance, small variance,
    occasional out-of-tolerance)
  * commands the relevant units over MQTT (Start) — no-op if broker absent
  * books Production + one HandlingUnit per pallet, with an SSCC per HU
  * schedules Samples per phase from the model's sample_types
  * records BatchEvents, computes OEE, raises BatchAlarms
  * applies the batch-verdict rule
  * if a fault drives pasteurizer HTST_temp_C < 71.5 -> CRITICAL alarm (Solve)

Runs fully offline (pure-simulation mode): with no broker/db it fabricates the
process values it would otherwise read from the sim.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timedelta, timezone

from .oee import oee as compute_oee
from .sscc import build_sscc

log = logging.getLogger("mes.orders")

# Order lifecycle states
NEW, RELEASED, STARTED, COMPLETED, CLOSED = (
    "NEW", "RELEASED", "STARTED", "COMPLETED", "CLOSED",
)

# Verdicts
APPROVED, HOLD, REJECTED, PENDING = "APPROVED", "HOLD", "REJECTED", "PENDING"

# Phase mapping: which process_path units belong to which ISA-88 phase.
PACK_UNITS = {"fill-line-01", "fill-line-02", "palletizer-01", "warehouse-01"}
PREP_UNITS = {"weigh-station-01", "pallet-buffer-01"}

# Packaging assumptions (from factory model: ~42 packs/pallet, 1 L packs).
PACKS_PER_PALLET = 42
DEFAULT_PACK_SIZE_L = 1.0
HTST_CRITICAL_LOW = 71.5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class OrderRunner:
    """Creates and runs work orders against the DairyWorks factory model."""

    def __init__(self, model, db, bus=None, rng: random.Random | None = None):
        self.model = model
        self.db = db
        self.bus = bus
        self.rng = rng or random.Random()

    # ------------------------------------------------------------------ create

    def create_order(self, recipe_id: str, planned_qty: float) -> dict:
        recipe = self.model.recipe(recipe_id)
        if recipe is None:
            raise ValueError(f"unknown recipe_id {recipe_id!r}")
        planned_qty = float(planned_qty)
        if planned_qty <= 0:
            raise ValueError("planned_qty must be > 0")

        order_id = f"WO-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        routing = self._routing(recipe)
        order = {
            "order_id": order_id,
            "recipe_id": recipe_id,
            "product_id": recipe.get("product_id"),
            "product_name": recipe.get("product_name"),
            "routing": routing,
            "process_path": recipe.get("process_path", []),
            "status": NEW,
            "planned_qty": planned_qty,
            "progress_pct": 0,
            "verdict": None,
            "critical_alarm_during_batch": False,
            "created_at": _iso(_now()),
            "started_at": None,
            "completed_at": None,
            "closed_at": None,
        }
        self.db.dw_work_orders.insert_one(order)
        self._explode_bom(order, recipe)
        self._event(order_id, "order_created", {"recipe_id": recipe_id, "planned_qty": planned_qty})
        return self.get_order(order_id)

    def _routing(self, recipe: dict) -> list[str]:
        path = recipe.get("process_path", [])
        phases = []
        if any(u in PREP_UNITS for u in path) or recipe.get("prep_stages", 1) >= 1:
            phases.append("Preparation")
        phases.append("Processing")
        phases.append("Packaging")
        return phases

    def _explode_bom(self, order: dict, recipe: dict) -> None:
        """Scale recipe BOM (per basis_kg) to planned_qty -> dw_job_bom rows."""
        basis = float(recipe.get("basis_kg", 1000)) or 1000.0
        scale = order["planned_qty"] / basis
        rows = []
        for item in recipe.get("bom", []):
            if item.get("bom_pos", 0) == 0:
                continue  # position 0 is the finished product, not an ingredient
            qty_target = float(item["qty_per_parent_item"]) * scale
            tol_min = item.get("tol_min")
            tol_max = item.get("tol_max")
            rows.append({
                "order_id": order["order_id"],
                "recipe_id": recipe["recipe_id"],
                "bom_pos": item["bom_pos"],
                "material_id": item["material_id"],
                "qty_per_parent_item": item["qty_per_parent_item"],
                "qty_target": round(qty_target, 4),
                "tol_min": round(tol_min * scale, 4) if tol_min is not None else None,
                "tol_max": round(tol_max * scale, 4) if tol_max is not None else None,
                "full_bag": item.get("full_bag", False),
                "uom": "kg",
            })
        if rows:
            self.db.dw_job_bom.insert_many(rows)

    # ------------------------------------------------------------------- run

    def run_order(self, order_id: str, inject_fault: dict | None = None) -> dict:
        """Drive an order end-to-end. inject_fault e.g. {'unit':'pasteurizer-01',
        'htst_temp_C': 70.8} forces a Solve/CRITICAL scenario."""
        order = self._raw_order(order_id)
        if order is None:
            raise ValueError(f"unknown order {order_id!r}")
        if order["status"] in (COMPLETED, CLOSED):
            return self.get_order(order_id)

        self._set_status(order_id, RELEASED)
        self._event(order_id, "order_released", {})

        started = _now()
        self._update_order(order_id, {"status": STARTED, "started_at": _iso(started)})
        self._event(order_id, "prep_started", {})

        recipe = self.model.recipe(order["recipe_id"])
        path = order["process_path"]

        # Command every real unit in the path to Start (no-op offline).
        for unit in path:
            if self.bus is not None:
                self.bus.command(unit, "Start", "1")

        # --- Preparation: book consumptions from the exploded job_bom ---
        consumptions = self._book_consumptions(order_id)
        self._event(order_id, "prep_completed", {"lines": len(consumptions)})

        # --- Processing: pasteurizer Solve check + phase samples ---
        alarms = self._run_processing(order_id, path, inject_fault)
        self._event(order_id, "processing_completed", {"alarms": len(alarms)})

        # --- Packaging: production, HUs, SSCC ---
        prod = self._run_packaging(order_id, order, recipe)
        self._event(order_id, "packaging_completed", prod)

        # --- Samples across phases ---
        samples = self._schedule_samples(order_id, path)

        # --- OEE ---
        oee_row = self._record_oee(order_id, path, prod)

        completed = _now()
        self._update_order(order_id, {
            "status": COMPLETED,
            "progress_pct": 100,
            "completed_at": _iso(completed),
        })
        self._event(order_id, "order_completed", {})

        # --- Verdict ---
        verdict, crit = self._verdict(order_id)
        self._update_order(order_id, {
            "verdict": verdict,
            "critical_alarm_during_batch": crit,
            "status": CLOSED,
            "closed_at": _iso(_now()),
        })
        self._event(order_id, "order_closed", {"verdict": verdict})

        if self.bus is not None:
            self.bus.emit_event("Order", {"order_id": order_id, "verdict": verdict, "status": CLOSED})

        return self.get_order(order_id)

    # ------------------------------------------------------------- consumptions

    def _book_consumptions(self, order_id: str) -> list[dict]:
        job_bom = self.db.dw_job_bom.find({"order_id": order_id})
        rows = []
        for line in job_bom:
            target = float(line["qty_target"])
            tol_min = line.get("tol_min")
            tol_max = line.get("tol_max")

            # small variance; ~8% chance of an out-of-tolerance draw
            variance = self.rng.uniform(-0.006, 0.006) * target
            actual = target + variance
            out_of_tol = False
            if tol_min is not None and tol_max is not None and self.rng.random() < 0.08:
                if self.rng.random() < 0.5:
                    actual = tol_min - abs(variance) - 0.01 * max(target, 1)
                else:
                    actual = tol_max + abs(variance) + 0.01 * max(target, 1)
                out_of_tol = True

            if tol_min is not None and tol_max is not None:
                out_of_tol = not (tol_min <= actual <= tol_max)

            source = "hopper" if line.get("full_bag") else "tank"
            row = {
                "order_id": order_id,
                "material_id": line["material_id"],
                "bom_pos": line["bom_pos"],
                "qty_target": round(target, 4),
                "qty_actual": round(actual, 4),
                "qty_extra": round(max(0.0, actual - target), 4),
                "uom": "kg",
                "source": source,
                "operator_id": "OP-SIM",
                "in_tolerance": not out_of_tol,
                "ts": _iso(_now()),
            }
            self.db.dw_item_cons.insert_one(row)
            rows.append(row)
            self._event(order_id, "consume_booked", {
                "material_id": line["material_id"],
                "qty_actual": row["qty_actual"],
                "in_tolerance": row["in_tolerance"],
            })
            if out_of_tol:
                self._alarm(
                    order_id, "weigh-station-01", "out_of_tolerance", "Medium",
                    f"{line['material_id']} out of tolerance: {row['qty_actual']} kg "
                    f"(target {row['qty_target']}, tol {tol_min}-{tol_max})",
                    impact=True, resolved=False,
                )
        return rows

    # -------------------------------------------------------------- processing

    def _run_processing(self, order_id: str, path: list[str], inject_fault: dict | None) -> list[dict]:
        alarms = []
        if "pasteurizer-01" not in path:
            return alarms

        # Read HTST temp from the sim if available, else simulate a healthy value.
        htst = None
        if self.bus is not None:
            raw = self.bus.latest_tag("pasteurizer-01", "HTST_temp_C")
            if raw is not None:
                try:
                    htst = float(raw)
                except (TypeError, ValueError):
                    htst = None

        forced = None
        if inject_fault and inject_fault.get("unit") == "pasteurizer-01":
            forced = inject_fault.get("htst_temp_C")
            if forced is None and inject_fault.get("magnitude"):
                # magnitude in [0..1] drops temp below the critical low
                forced = HTST_CRITICAL_LOW - float(inject_fault["magnitude"]) * 3.0

        if forced is not None:
            htst = float(forced)
        elif htst is None:
            htst = round(self.rng.uniform(72.2, 73.6), 2)  # healthy sim default

        self._event(order_id, "htst_measured", {"HTST_temp_C": htst})

        if htst < HTST_CRITICAL_LOW:
            # SOLVE-HTST: auto-divert + audit + CRITICAL batch alarm
            if self.bus is not None:
                self.bus.command("pasteurizer-01", "Fault/Inject",
                                 {"fault": "htst_low", "magnitude": 0.5})
            self._alarm(
                order_id, "pasteurizer-01", "HTST_under_pasteurization", "Critical",
                f"HTST {htst} C below {HTST_CRITICAL_LOW} C — auto-divert + audit (SOLVE-HTST)",
                impact=True, resolved=False, divert=True,
            )
            self._event(order_id, "solve_htst_divert", {"HTST_temp_C": htst, "divert_valve_status": 1})
            alarms.append({"type": "HTST_under_pasteurization", "severity": "Critical"})

        return alarms

    # -------------------------------------------------------------- packaging

    def _run_packaging(self, order_id: str, order: dict, recipe: dict) -> dict:
        planned_qty = float(order["planned_qty"])
        pack_size = DEFAULT_PACK_SIZE_L
        total_packs = max(1, int(round(planned_qty / pack_size)))
        reject_rate = self.rng.uniform(0.002, 0.012)
        reject_count = int(round(total_packs * reject_rate))
        good_packs = total_packs - reject_count

        # Book aggregate production
        prod = {
            "order_id": order_id,
            "item_id": order["product_id"],
            "lot_no": order_id,
            "sublot_no": "1",
            "qty_produced": good_packs,
            "reject_count": reject_count,
            "grade": "A",
            "entity": order["product_name"],
            "uom": "pack",
            "pack_size_L": pack_size,
            "ts": _iso(_now()),
        }
        self.db.dw_item_prod.insert_one(prod)
        self._event(order_id, "produce_booked", {
            "qty_produced": good_packs, "reject_count": reject_count,
        })

        # HUs: one per pallet
        n_pallets = max(1, -(-good_packs // PACKS_PER_PALLET))  # ceil
        prefix = self.model.sscc_prefix()
        prod_date = _now()
        expiry = prod_date + timedelta(days=14)
        remaining = good_packs
        hu_count = 0
        for seq in range(1, n_pallets + 1):
            packs_on_pallet = min(PACKS_PER_PALLET, remaining)
            remaining -= packs_on_pallet
            serial = f"{abs(hash(order_id)) % 1000:03d}{seq:03d}"
            sscc = build_sscc(prefix, serial)
            hu_id = f"HU-{order_id}-{seq:03d}"
            hu = {
                "hu_id": hu_id,
                "order_id": order_id,
                "material_id": order["product_id"],
                "pack_count": packs_on_pallet,
                "pallet_seq": seq,
                "production_date": _iso(prod_date),
                "expiry_date": _iso(expiry),
                "batch_id": order_id,
                "sscc_code": sscc,
                "status": "COMPLETE",
                "ts": _iso(_now()),
            }
            self.db.dw_handling_units.insert_one(hu)
            self.db.dw_sscc.insert_one({
                "sscc_code": sscc, "hu_id": hu_id, "order_id": order_id,
                "pallet_seq": seq, "ts": _iso(_now()),
            })
            hu_count += 1
            self._event(order_id, "hu_created", {"hu_id": hu_id, "sscc": sscc, "packs": packs_on_pallet})
            if self.bus is not None:
                self.bus.emit_event("HU", {"hu_id": hu_id, "sscc": sscc, "order_id": order_id})

        return {
            "total_packs": total_packs,
            "good_packs": good_packs,
            "reject_count": reject_count,
            "pallets": hu_count,
        }

    # ---------------------------------------------------------------- samples

    def _schedule_samples(self, order_id: str, path: list[str]) -> list[dict]:
        samples = []
        for st in self.model.sample_types():
            phase = st.get("phase")
            # Skip processing-only phases the order doesn't traverse (best effort)
            sample_id = f"S-{order_id}-{uuid.uuid4().hex[:5].upper()}"
            # ~90% completed/approved, small chance pending/failed
            r = self.rng.random()
            if r < 0.85:
                status, result = "approved", "pass"
            elif r < 0.95:
                status, result = "completed", "pass"
            elif r < 0.98:
                status, result = "pending", None
            else:
                status, result = "failed", "fail"
            row = {
                "sample_id": sample_id,
                "order_id": order_id,
                "sample_type": st.get("type"),
                "phase": phase,
                "location": st.get("location"),
                "status": status,
                "result": result,
                "ts": _iso(_now()),
            }
            self.db.dw_samples.insert_one(row)
            samples.append(row)
            self._event(order_id, "sample_created", {"sample_type": st.get("type"), "status": status})
            if status == "failed":
                self._alarm(
                    order_id, "fill-line-01", "sample_failed", "High",
                    f"Sample {st.get('type')} failed QA", impact=True, resolved=False,
                )
        return samples

    # ------------------------------------------------------------------- oee

    def _record_oee(self, order_id: str, path: list[str], prod: dict) -> dict:
        targets = self.model.oee_targets()
        availability = self.rng.uniform(0.90, 0.98)
        performance = self.rng.uniform(0.92, 0.99)
        total = prod.get("total_packs", 1) or 1
        quality = (prod.get("good_packs", total)) / total
        result = compute_oee(availability, performance, quality)
        row = {
            "order_id": order_id,
            "equipment_id": "line",
            **result,
            "target_oee_pct": targets.get("oee_pct"),
            "window_start": None,
            "window_end": _iso(_now()),
            "ts": _iso(_now()),
        }
        self.db.dw_oee.insert_one(row)
        self._event(order_id, "oee_computed", {"oee_pct": result["oee_pct"]})
        if self.bus is not None:
            self.bus.emit_event("OEE", {"order_id": order_id, "oee_pct": result["oee_pct"]})
        return row

    # ---------------------------------------------------------------- verdict

    def _verdict(self, order_id: str) -> tuple[str, bool]:
        alarms = self.db.dw_batch_alarms.find({"order_id": order_id})
        samples = self.db.dw_samples.find({"order_id": order_id})
        consumptions = self.db.dw_item_cons.find({"order_id": order_id})

        unresolved_critical = any(
            a.get("severity") == "Critical" and not a.get("resolved", False)
            for a in alarms
        )
        crit_during_batch = any(a.get("severity") == "Critical" for a in alarms)

        has_warning = any(
            a.get("severity") in ("High", "Medium") and not a.get("resolved", False)
            for a in alarms
        )
        out_of_tol = any(not c.get("in_tolerance", True) for c in consumptions)
        failed_sample = any(s.get("status") == "failed" for s in samples)
        pending_sample = any(s.get("status") == "pending" for s in samples)

        if unresolved_critical:
            verdict = REJECTED
        elif has_warning or out_of_tol or failed_sample:
            verdict = HOLD
        elif pending_sample:
            verdict = PENDING
        else:
            verdict = APPROVED
        return verdict, crit_during_batch

    # ------------------------------------------------------------ persistence

    def _raw_order(self, order_id: str) -> dict | None:
        return self.db.dw_work_orders.find_one({"order_id": order_id})

    def get_order(self, order_id: str) -> dict | None:
        order = self._raw_order(order_id)
        if order is None:
            return None
        return {
            "order": order,
            "job_bom": self.db.dw_job_bom.find({"order_id": order_id}),
            "consumptions": self.db.dw_item_cons.find({"order_id": order_id}),
            "productions": self.db.dw_item_prod.find({"order_id": order_id}),
            "handling_units": self.db.dw_handling_units.find({"order_id": order_id}),
            "samples": self.db.dw_samples.find({"order_id": order_id}),
            "alarms": self.db.dw_batch_alarms.find({"order_id": order_id}),
            "oee": self.db.dw_oee.find({"order_id": order_id}),
        }

    def list_orders(self) -> list[dict]:
        return self.db.dw_work_orders.find({})

    def _set_status(self, order_id: str, status: str) -> None:
        self._update_order(order_id, {"status": status})

    def _update_order(self, order_id: str, fields: dict) -> None:
        self.db.dw_work_orders.update_one({"order_id": order_id}, {"$set": fields})

    def _event(self, order_id: str, event_type: str, payload: dict) -> None:
        self.db.dw_batch_events.insert_one({
            "order_id": order_id,
            "event_type": event_type,
            "payload": payload,
            "ts": _iso(_now()),
        })

    def _alarm(self, order_id: str, equipment_id: str, alarm_type: str, severity: str,
               message: str, impact: bool, resolved: bool, divert: bool = False) -> None:
        self.db.dw_batch_alarms.insert_one({
            "order_id": order_id,
            "equipment_id": equipment_id,
            "alarm_type": alarm_type,
            "severity": severity,
            "message": message,
            "acknowledged": False,
            "impact_on_batch": impact,
            "resolved": resolved,
            "divert": divert,
            "ts": _iso(_now()),
        })
        if self.bus is not None:
            self.bus.emit_event("Alarm", {
                "order_id": order_id, "equipment_id": equipment_id,
                "alarm_type": alarm_type, "severity": severity,
            })
