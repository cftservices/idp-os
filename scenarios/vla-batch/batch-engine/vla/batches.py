"""BatchRunner — the batch-centric MES core for the Vla demo.

Drives a batch through its lifecycle:
  IDLE -> DOSING -> COOKING -> COOLING -> FILLING -> COMPLETE

For each batch it:
  * creates the batch from a recipe, scaling doses to planned_L
  * pushes dose setpoints + cook/cool setpoints as SetSetpoint commands (MQTT)
  * starts the batch via the line-level StartBatch(recipeId) method (MQTT)
  * follows UNS telemetry (from the bus tag-cache) for state / peak cook temp /
    hold / viscosity / packs — and books dose actuals + samples from it
  * determines the verdict per §verdict-regel

Offline-first: with no broker/DB it fabricates the process values it would
otherwise read from the sim (a "pure-simulation" fallback), so the whole
lifecycle + verdict logic is exercised headless in selftest.

Telemetry can also be injected directly (dict of readings) so tests can force a
normal run (APPROVED) or a cook_undertemp / low-viscosity run (HOLD/REJECTED)
without a broker.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from . import model as M

log = logging.getLogger("vla.batches")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


# Map SetSetpoint dose-target short names -> recipe material ids.
_DOSE_TARGET = {m: f"dose.{m}" for m in M.DOSE_MATERIALS}

# QA sample plan (06-Model: dose_check | cook_temp | hold | viscosity) is booked
# inline: dose_check at end of DOSING, cook_temp + hold at cook capture,
# viscosity during COOLING (the Solve input).


class BatchRunner:
    """Creates and runs Vla batches against the recipe seed + factory model."""

    def __init__(self, db, bus=None, control=None,
                 rng: Optional[random.Random] = None):
        self.db = db
        self.bus = bus            # MQTT: telemetry READ + secondary Command fallback
        self.control = control    # OPC-UA: PRIMARY control (write/command) path
        self.rng = rng or random.Random()

    # ------------------------------------------------------------------ create

    def create_batch(self, recipe_id: str, planned_L: Optional[float] = None,
                     auto_start: bool = False) -> dict:
        recipe = M.get_recipe(recipe_id)
        if recipe is None:
            raise ValueError(f"unknown recipe_id {recipe_id!r}")
        planned_L = float(planned_L) if planned_L else recipe.basis_L
        if planned_L <= 0:
            raise ValueError("planned_L must be > 0")

        # Release-gate: check recipe status is "released"
        rec_doc = self.db.dw_recipes.find_one({"recipe_id": recipe_id})
        status = (rec_doc or {}).get("status", recipe.status)
        if status != "released":
            raise ValueError(f"recipe {recipe_id!r} is not released (status={status})")

        batch_id = f"B-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        scaled = recipe.scaled_doses(planned_L)

        batch_doc = {
            "batch_id": batch_id,
            "recipe_id": recipe_id,
            "product_name": recipe.product_name,
            "planned_L": planned_L,
            "state": M.IDLE,
            "verdict": None,
            "peak_cook_temp_C": None,
            "hold_sec": recipe.hold_sec,
            "hold_elapsed_sec": None,
            "end_viscosity_cP": None,
            "packs_total": 0,
            "reject_count": 0,
            "cook_setpoint_C": recipe.cook_setpoint_C,
            "cool_target_C": recipe.cool_target_C,
            "spec_min_cP": recipe.spec_min_cP,
            "spec_max_cP": recipe.spec_max_cP,
            "critical_alarm_during_batch": False,
            "created_at": _iso(),
            "started_at": None,
            "completed_at": None,
        }
        self.db.dw_batches.insert_one(batch_doc)

        # dose rows (targets; actuals booked during DOSING)
        for d in scaled:
            self.db.dw_doses.insert_one({
                "batch_id": batch_id,
                "material_id": d.material_id,
                "qty_target": d.qty_target,
                "qty_actual": None,
                "tol_min": d.tol_min,
                "tol_max": d.tol_max,
                "tol_pos_pct": d.tol_pos_pct,
                "tol_neg_pct": d.tol_neg_pct,
                "uom": d.uom,
                "in_tolerance": None,
                "source_equipment": None,
                "lot_no": None,
                "operator_id": None,
                "staged": [],
                "qty_prepared": 0.0,
            })

        self._event(batch_id, "batch_created",
                    {"recipe_id": recipe_id, "planned_L": planned_L})

        # push setpoints (MQTT no-op when offline)
        self._push_setpoints(batch_id, recipe, scaled)

        if auto_start:
            self.start_batch(batch_id)
        return self.get_batch(batch_id)

    def _set_setpoint(self, target: str, value: float) -> None:
        """PRIMARY: OPC-UA SetSetpoint on the factory. Secondary: MQTT Command."""
        if self.control is not None:
            self.control.set_setpoint(target, value)
        if self.bus is not None:
            self.bus.set_setpoint(target, value)

    def _push_setpoints(self, batch_id: str, recipe: M.Recipe,
                        scaled_doses: list[M.Dose]) -> None:
        for d in scaled_doses:
            tgt = _DOSE_TARGET.get(d.material_id)
            if tgt:
                self._set_setpoint(tgt, d.qty_target)
        self._set_setpoint("cook.setpoint_C", recipe.cook_setpoint_C)
        self._set_setpoint("cook.hold_sec", recipe.hold_sec)
        self._set_setpoint("cooler.target_C", recipe.cool_target_C)
        self._set_setpoint("mixing.agitator_rpm", recipe.agitator_rpm)
        self._event(batch_id, "setpoints_pushed", {
            "cook_setpoint_C": recipe.cook_setpoint_C,
            "hold_sec": recipe.hold_sec,
            "cool_target_C": recipe.cool_target_C,
        })

    # ------------------------------------------------------------------- start

    # live-follow tuning (factory: TIME_SCALE=6, hele batch ~2-3 min realtime)
    POLL_S = 2.0
    START_TIMEOUT_S = 30.0
    FOLLOW_TIMEOUT_S = 900.0

    def start_batch(self, batch_id: str,
                    telemetry: Optional[dict[str, Any]] = None) -> dict:
        """Send StartBatch and drive the batch to COMPLETE.

        LIVE mode (bus connected, no forced telemetry): StartBatch goes to the
        factory and a background thread FOLLOWS the real batch over the UNS —
        mirroring phases, tracking peak cook temp / hold, and reading the
        factory's end viscosity — until the factory reports COMPLETE. The call
        returns immediately with state DOSING; poll GET /batches/{id}.

        INSTANT mode (offline/selftest, or `telemetry` given): the lifecycle is
        completed synchronously with fabricated/forced values, e.g.
        {"peak_cook_temp_C":88, "hold_elapsed_sec":300, "packs_total":5000,
         "reject_count":20, "fault":"cook_undertemp", "magnitude":0.6}.
        """
        batch = self._raw_batch(batch_id)
        if batch is None:
            raise ValueError(f"unknown batch {batch_id!r}")
        if batch["state"] in (M.COMPLETE,):
            return self.get_batch(batch_id)

        recipe = M.get_recipe(batch["recipe_id"])
        live = (telemetry is None and self.bus is not None
                and getattr(self.bus, "connected", False))
        if live:
            fstate = self._bus_str("Batch", "state")
            if fstate in (M.DOSING, M.COOKING, M.COOLING, M.FILLING):
                raise ValueError("factory line is busy with another batch")

        started = _now()
        self._update(batch_id, {"state": M.DOSING, "started_at": _iso(started)})
        self._event(batch_id, "batch_started",
                    {"mode": "live" if live else "instant"})

        # PRIMARY: OPC-UA StartBatch on the factory. Secondary: MQTT Command.
        if self.control is not None:
            self.control.start_batch(batch["recipe_id"])
        if self.bus is not None:
            self.bus.start_batch(batch["recipe_id"])

        if live:
            threading.Thread(target=self._follow_live,
                             args=(batch_id, recipe),
                             name=f"vla-follow-{batch_id}", daemon=True).start()
            return self.get_batch(batch_id)

        # INSTANT: book doses + capture cook from telemetry/fabricated values.
        self._book_doses(batch_id, telemetry)
        self._update(batch_id, {"state": M.COOKING})
        peak_temp, hold_elapsed, fault, _mag = self._read_cook(recipe, telemetry)
        packs, rejects = self._read_packs(batch, telemetry)
        self._finalize(batch_id, recipe, peak_temp, hold_elapsed, fault,
                       end_visc=None, packs=packs, rejects=rejects)
        return self.get_batch(batch_id)

    # -------------------------------------------------------------- live follow

    def _bus_float(self, equipment: str, tag: str) -> Optional[float]:
        if self.bus is None:
            return None
        raw = self.bus.latest_value(equipment, tag)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _bus_str(self, equipment: str, tag: str) -> Optional[str]:
        if self.bus is None:
            return None
        raw = self.bus.latest_value(equipment, tag)
        return None if raw is None else str(raw)

    def _follow_live(self, batch_id: str, recipe: M.Recipe) -> None:
        """Follow the REAL factory batch over the UNS until COMPLETE, then
        finalize the MES batch from the observed telemetry (runs in a thread)."""
        try:
            peak: Optional[float] = None
            hold = 0.0
            mirrored = M.DOSING
            saw_running = False
            t0 = time.monotonic()

            while time.monotonic() - t0 < self.FOLLOW_TIMEOUT_S:
                st = self._bus_str("Batch", "state")

                tv = self._bus_float("cook-unit-01", "temp_C")
                if tv is not None:
                    peak = tv if peak is None else max(peak, tv)
                hv = self._bus_float("cook-unit-01", "hold_elapsed_sec")
                if hv is not None:
                    hold = max(hold, hv)

                if st in (M.DOSING, M.COOKING, M.COOLING, M.FILLING):
                    saw_running = True
                    if st != mirrored:
                        mirrored = st
                        self._update(batch_id, {"state": st})
                        self._event(batch_id, "phase_mirrored", {"state": st})
                elif st == M.COMPLETE and saw_running:
                    break
                elif st == M.IDLE and saw_running:
                    # factory aborted/stopped mid-batch — finalize with what we saw
                    self._event(batch_id, "factory_stopped_early", {})
                    break
                elif not saw_running and time.monotonic() - t0 > self.START_TIMEOUT_S:
                    log.warning("factory never left IDLE for %s — "
                                "falling back to instant simulation", batch_id)
                    self._event(batch_id, "factory_start_timeout", {})
                    batch = self._raw_batch(batch_id)
                    self._book_doses(batch_id, None)
                    p, h, fault, _m = self._read_cook(recipe, None)
                    packs, rejects = self._read_packs(batch, None)
                    self._finalize(batch_id, recipe, p, h, fault,
                                   end_visc=None, packs=packs, rejects=rejects)
                    return
                time.sleep(self.POLL_S)

            # --- finalize from the live tag-cache (post-batch values persist) ---
            self._book_doses(batch_id, None)
            if peak is None:
                peak = 20.0
            fault = None
            if peak < recipe.cook_setpoint_C - 5.0:
                fault = "cook_undertemp"
            end_visc = self._bus_float("cook-unit-01", "viscosity_cP")
            packs_f = self._bus_float("filler-01", "packs_total")
            rejects_f = self._bus_float("filler-01", "reject_count")
            batch = self._raw_batch(batch_id)
            if packs_f is None:
                packs, rejects = self._read_packs(batch, None)
            else:
                packs, rejects = int(packs_f), int(rejects_f or 0)
            self._finalize(batch_id, recipe, round(peak, 2), round(hold, 2),
                           fault, end_visc=end_visc, packs=packs, rejects=rejects)
        except Exception as e:  # pragma: no cover - defensive: thread must not die silent
            log.exception("live follow failed for %s", batch_id)
            try:
                self._event(batch_id, "live_follow_error", {"error": str(e)})
            except Exception:
                pass

    # ---------------------------------------------------------------- finalize

    def _finalize(self, batch_id: str, recipe: M.Recipe, peak_temp: float,
                  hold_elapsed: float, fault: Optional[str],
                  end_visc: Optional[float], packs: int, rejects: int) -> None:
        """Shared tail of the lifecycle: cook capture -> viscosity/Solve ->
        filling -> COMPLETE -> verdict. `end_visc` None = compute via physics."""
        self._update(batch_id, {
            "peak_cook_temp_C": peak_temp,
            "hold_elapsed_sec": hold_elapsed,
        })
        self._event(batch_id, "cook_captured",
                    {"peak_cook_temp_C": peak_temp, "hold_elapsed_sec": hold_elapsed,
                     "fault": fault})
        if fault == "cook_undertemp":
            self._alarm(batch_id, "cook-unit-01", "cook_undertemp", M.HIGH,
                        f"Peak cook temp capped at {peak_temp} C "
                        f"(setpoint {recipe.cook_setpoint_C} C)",
                        impact=True, resolved=False)

        # Book cook_temp and hold samples at end of COOKING
        self._take_sample(batch_id, "cook_temp", M.COOKING,
                          value=peak_temp, unit="C",
                          spec_min=recipe.cook_setpoint_C - 5.0,
                          spec_max=recipe.cook_setpoint_C + 5.0)
        self._take_sample(batch_id, "hold", M.COOKING,
                          value=hold_elapsed, unit="s",
                          spec_min=recipe.hold_sec * 0.95)

        # --- COOLING: viscosity (the Solve) ---
        self._update(batch_id, {"state": M.COOLING})
        if end_visc is None:
            end_visc = M.physics_viscosity(peak_temp, hold_elapsed, recipe.hold_sec)
        end_visc = round(float(end_visc), 1)
        self._update(batch_id, {"end_viscosity_cP": end_visc})
        self._event(batch_id, "viscosity_computed", {"end_viscosity_cP": end_visc})
        self._take_sample(batch_id, "viscosity", M.COOLING,
                          value=end_visc, unit="cP",
                          spec_min=recipe.spec_min_cP, spec_max=recipe.spec_max_cP)
        if end_visc < recipe.spec_min_cP:
            # SOLVE trigger: out-of-spec viscosity -> CRITICAL (hold/rework)
            self._alarm(batch_id, "cook-unit-01", "viscosity_out_of_spec", M.CRITICAL,
                        f"End viscosity {end_visc} cP below spec_min "
                        f"{recipe.spec_min_cP} cP — SOLVE: hold + rework",
                        impact=True, resolved=False)
            self._event(batch_id, "solve_viscosity", {"end_viscosity_cP": end_visc})

        # --- FILLING: packs ---
        self._update(batch_id, {"state": M.FILLING})
        self._update(batch_id, {"packs_total": packs, "reject_count": rejects})
        self._event(batch_id, "filling_done",
                    {"packs_total": packs, "reject_count": rejects})

        # --- COMPLETE ---
        self._update(batch_id, {"state": M.COMPLETE, "completed_at": _iso(_now())})
        self._event(batch_id, "batch_complete", {})

        # --- verdict ---
        verdict, crit = self._verdict(batch_id)
        self._update(batch_id, {
            "verdict": verdict,
            "critical_alarm_during_batch": crit,
        })
        self._event(batch_id, "verdict_set", {"verdict": verdict})

    # ------------------------------------------------------------------- doses

    def _book_doses(self, batch_id: str, telemetry: Optional[dict]) -> None:
        doses = self.db.dw_doses.find({"batch_id": batch_id})
        actuals = (telemetry or {}).get("dose_actuals", {})
        for line in doses:
            # Skip already-booked lines (scan-flow commits set qty_actual earlier)
            if line.get("qty_actual") is not None:
                continue
            target = float(line["qty_target"])
            if line["material_id"] in actuals:
                actual = float(actuals[line["material_id"]])
            else:
                # try bus tag: process-tank-01 dose_<m>_actual_kg
                actual = None
                if self.bus is not None:
                    raw = self.bus.latest_value(
                        "process-tank-01", f"dose_{line['material_id']}_actual_kg")
                    if raw is not None:
                        try:
                            actual = float(raw)
                        except (TypeError, ValueError):
                            actual = None
                if actual is None:
                    # fabricate: small variance around target
                    actual = target + self.rng.uniform(-0.004, 0.004) * target
            actual = round(actual, 4)
            in_tol = float(line["tol_min"]) <= actual <= float(line["tol_max"])
            self.db.dw_doses.update_one(
                {"batch_id": batch_id, "material_id": line["material_id"]},
                {"$set": {"qty_actual": actual, "in_tolerance": in_tol,
                          "source_equipment": line.get("source_equipment") or "dosing-unit",
                          "ts": _iso()}},
            )
            self._event(batch_id, "dose_booked", {
                "material_id": line["material_id"], "qty_actual": actual,
                "in_tolerance": in_tol,
            })
            if not in_tol:
                self._alarm(batch_id, "process-tank-01", "dose_out_of_tolerance",
                            M.MEDIUM,
                            f"{line['material_id']} dose {actual} kg out of tol "
                            f"({line['tol_min']}-{line['tol_max']})",
                            impact=True, resolved=False)
        # Book dose_check sample at end of DOSING
        rows = self.db.dw_doses.find({"batch_id": batch_id})
        all_in_tol = all(r.get("in_tolerance") is not False for r in rows)
        self._take_sample(batch_id, "dose_check", M.DOSING, ok=all_in_tol)

    # ------------------------------------------------------------------- cook

    def _read_cook(self, recipe: M.Recipe,
                   telemetry: Optional[dict]) -> tuple[float, float, Optional[str], float]:
        """Return (peak_cook_temp_C, hold_elapsed_sec, fault, magnitude)."""
        t = telemetry or {}
        fault = t.get("fault")
        magnitude = float(t.get("magnitude", 0.0) or 0.0)

        # explicit override wins
        if "peak_cook_temp_C" in t:
            peak = float(t["peak_cook_temp_C"])
        elif fault == "cook_undertemp":
            # §physics: capped peak ≈ 70 + (1-magnitude)*18
            peak = 70.0 + (1.0 - magnitude) * 18.0
        else:
            # try bus, else fabricate a healthy peak near setpoint
            peak = None
            if self.bus is not None:
                raw = self.bus.latest_value("cook-unit-01", "temp_C")
                if raw is not None:
                    try:
                        peak = float(raw)
                    except (TypeError, ValueError):
                        peak = None
            if peak is None:
                peak = round(recipe.cook_setpoint_C - self.rng.uniform(0.0, 0.6), 2)

        if "hold_elapsed_sec" in t:
            hold_elapsed = float(t["hold_elapsed_sec"])
        else:
            hold_elapsed = None
            if self.bus is not None:
                raw = self.bus.latest_value("cook-unit-01", "hold_elapsed_sec")
                if raw is not None:
                    try:
                        hold_elapsed = float(raw)
                    except (TypeError, ValueError):
                        hold_elapsed = None
            if hold_elapsed is None:
                # healthy: full hold reached
                hold_elapsed = float(recipe.hold_sec)

        return round(peak, 2), round(hold_elapsed, 2), fault, magnitude

    # ------------------------------------------------------------------- packs

    def _read_packs(self, batch: dict, telemetry: Optional[dict]) -> tuple[int, int]:
        t = telemetry or {}
        if "packs_total" in t:
            packs = int(t["packs_total"])
            rejects = int(t.get("reject_count", 0))
            return packs, rejects
        # try bus filler tags
        packs = None
        rejects = 0
        if self.bus is not None:
            raw = self.bus.latest_value("filler-01", "packs_total")
            if raw is not None:
                try:
                    packs = int(float(raw))
                except (TypeError, ValueError):
                    packs = None
            rj = self.bus.latest_value("filler-01", "reject_count")
            if rj is not None:
                try:
                    rejects = int(float(rj))
                except (TypeError, ValueError):
                    rejects = 0
        if packs is None:
            # fabricate: ~planned_L packs at 1 L/pack minus small reject rate
            total = max(1, int(round(float(batch["planned_L"]))))
            rejects = int(round(total * self.rng.uniform(0.002, 0.012)))
            packs = total - rejects
        return packs, rejects

    # ------------------------------------------------------------------ samples

    def _take_sample(self, batch_id: str, sample_type: str, phase: str,
                     value: Optional[float] = None, unit: Optional[str] = None,
                     spec_min: Optional[float] = None,
                     spec_max: Optional[float] = None,
                     ok: Optional[bool] = None,
                     operator_id: Optional[str] = None) -> dict:
        sample_id = f"S-{batch_id}-{uuid.uuid4().hex[:5].upper()}"
        status, result = "completed", "pass"
        if ok is not None:
            status, result = ("approved", "pass") if ok else ("failed", "fail")
        elif value is not None and (spec_min is not None or spec_max is not None):
            in_spec = ((spec_min is None or value >= spec_min)
                       and (spec_max is None or value <= spec_max))
            status, result = ("approved", "pass") if in_spec else ("failed", "fail")
        row = {
            "sample_id": sample_id,
            "batch_id": batch_id,
            "sample_type": sample_type,
            "phase": phase,
            "status": status,
            "result": result,
            "value": value,
            "unit": unit,
            "label_printed": True,
            "operator_id": operator_id,
            "ts": _iso(),
        }
        self.db.dw_samples.insert_one(row)
        self._event(batch_id, "sample_taken",
                    {"sample_type": sample_type, "status": status, "value": value})
        self._event(batch_id, "sample_label_printed",
                    {"sample_id": sample_id, "sample_type": sample_type})
        if self.control is not None:
            self.control.take_sample(sample_type)
        if self.bus is not None:
            self.bus.take_sample(sample_type)
        return row

    def take_sample(self, batch_id: str, sample_type: str,
                    operator_id: Optional[str] = None) -> dict:
        """Ad-hoc sample requested via POST /samples (contract §REST)."""
        if sample_type not in M.SAMPLE_TYPES:
            raise ValueError(f"unknown sample_type {sample_type!r} "
                             f"(allowed: {', '.join(M.SAMPLE_TYPES)})")
        batch = self._raw_batch(batch_id)
        phase = batch["state"] if batch else M.IDLE
        return self._take_sample(batch_id, sample_type, phase, operator_id=operator_id)

    # ---------------------------------------------------------------- verdict

    def _verdict(self, batch_id: str) -> tuple[str, bool]:
        """§verdict-regel:
          end_viscosity < spec_min OR unresolved CRITICAL -> REJECTED/HOLD
          afwijking (warning/out-of-tol/failed sample) -> HOLD
          alles OK -> APPROVED
          anders (pending sample) -> PENDING
        """
        batch = self._raw_batch(batch_id)
        alarms = self.db.dw_alarms.find({"batch_id": batch_id})
        samples = self.db.dw_samples.find({"batch_id": batch_id})
        doses = self.db.dw_doses.find({"batch_id": batch_id})

        end_visc = batch.get("end_viscosity_cP")
        spec_min = batch.get("spec_min_cP", M.SPEC_MIN_CP)

        unresolved_critical = any(
            a.get("severity") == M.CRITICAL and not a.get("resolved", False)
            for a in alarms
        )
        crit_during_batch = any(a.get("severity") == M.CRITICAL for a in alarms)
        out_of_spec_visc = end_visc is not None and end_visc < spec_min

        has_warning = any(
            a.get("severity") in (M.HIGH, M.MEDIUM) and not a.get("resolved", False)
            for a in alarms
        )
        out_of_tol = any(d.get("in_tolerance") is False for d in doses)
        failed_sample = any(s.get("status") == "failed" for s in samples)
        pending_sample = any(s.get("status") == "pending" for s in samples)

        if out_of_spec_visc or unresolved_critical:
            # out-of-spec viscosity is a critical quality fail -> REJECTED
            verdict = M.REJECTED
        elif has_warning or out_of_tol or failed_sample:
            verdict = M.HOLD
        elif pending_sample:
            verdict = M.PENDING
        else:
            verdict = M.APPROVED
        return verdict, crit_during_batch

    # ------------------------------------------------------------ persistence

    def _raw_batch(self, batch_id: str) -> Optional[dict]:
        return self.db.dw_batches.find_one({"batch_id": batch_id})

    def get_batch(self, batch_id: str) -> Optional[dict]:
        batch = self._raw_batch(batch_id)
        if batch is None:
            return None
        doses = self.db.dw_doses.find({"batch_id": batch_id})
        samples = self.db.dw_samples.find({"batch_id": batch_id})
        alarms = self.db.dw_alarms.find({"batch_id": batch_id})
        return {
            **batch,
            "doses": [{"material_id": d["material_id"],
                       "qty_target": d["qty_target"],
                       "qty_actual": d.get("qty_actual"),
                       "tol_min": d.get("tol_min"),
                       "tol_max": d.get("tol_max"),
                       "in_tolerance": d.get("in_tolerance"),
                       "uom": d.get("uom", "kg"),
                       "lot_no": d.get("lot_no"),
                       "source_equipment": d.get("source_equipment"),
                       "operator_id": d.get("operator_id")} for d in doses],
            "samples": samples,
            "alarms": alarms,
        }

    def list_batches(self) -> list[dict]:
        out = []
        for b in self.db.dw_batches.find({}):
            out.append({
                "batch_id": b["batch_id"],
                "recipe_id": b["recipe_id"],
                "product_name": b.get("product_name"),
                "state": b.get("state"),
                "started_at": b.get("started_at"),
                "verdict": b.get("verdict"),
                "packs_total": b.get("packs_total", 0),
            })
        return out

    def get_samples(self, batch_id: Optional[str] = None) -> list[dict]:
        query = {"batch_id": batch_id} if batch_id else {}
        return self.db.dw_samples.find(query)

    def _update(self, batch_id: str, fields: dict) -> None:
        self.db.dw_batches.update_one(
            {"batch_id": batch_id}, {"$set": fields})
        # mirror line-level Batch/Status to UNS
        if self.bus is not None and "state" in fields:
            self.bus.command("Batch", "state", value=fields["state"])

    def _event(self, batch_id: str, event_type: str, payload: dict) -> None:
        self.db.dw_batch_events.insert_one({
            "batch_id": batch_id,
            "event_type": event_type,
            "payload": payload,
            "ts": _iso(),
        })

    def _alarm(self, batch_id: str, equipment_id: str, alarm_type: str,
               severity: str, message: str, impact: bool, resolved: bool) -> None:
        self.db.dw_alarms.insert_one({
            "batch_id": batch_id,
            "equipment_id": equipment_id,
            "alarm_type": alarm_type,
            "severity": severity,
            "message": message,
            "acknowledged": False,
            "impact_on_batch": impact,
            "resolved": resolved,
            "ts": _iso(),
        })
