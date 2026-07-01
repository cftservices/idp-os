"""Vla batch-process model — the internal physics of the DairyWorks chocolate-vla line.

Pure Python, no external dependencies. This module IS the process model behind the
"black box with SCADA buttons": one batch state machine drives dosing, mixing,
cooking, cooling and filling, with a viscosity (gelatinisation) physics that is the
"Solve" of the demo.

State machine:
    IDLE -> DOSING -> COOKING -> COOLING -> FILLING -> COMPLETE

The OPC-UA server (server.py) reads a flat snapshot via read() and drives the process
via start_batch()/stop()/set_setpoint()/take_sample()/inject_fault()/clear_fault().

Contract references (vla-build-contract.md):
  - Recipe chocolate-vla-1L: milk 5000, sugar 500, starch 250, cocoa 100 kg;
    cook_setpoint_C 88, hold_sec 300, cool_target_C 22; spec 150-300 cP.
  - Viscosity physics (the Solve):
        g = clamp((peak_temp-70)/(88-70),0,1) * clamp(hold_elapsed/hold_sec,0,1)
        end_viscosity_cP = 30 + g*230
  - Fault cook_undertemp caps the peak cook temperature -> low g -> viscosity < 150.
  - Demo time: a full batch runs in ~2-4 minutes (time acceleration).
"""

from __future__ import annotations

import time


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --- batch states ---
IDLE = "IDLE"
DOSING = "DOSING"
COOKING = "COOKING"
COOLING = "COOLING"
FILLING = "FILLING"
COMPLETE = "COMPLETE"

STATES = [IDLE, DOSING, COOKING, COOLING, FILLING, COMPLETE]

# --- recipe library (contract: chocolate-vla-1L) ---
RECIPES = {
    "chocolate-vla-1L": {
        "product_name": "Chocolate Vla 1L",
        "base_L": 5000.0,
        "doses_kg": {"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "cocoa": 100.0},
        "cook_setpoint_C": 88.0,
        "hold_sec": 300.0,
        "cool_target_C": 22.0,
        "spec_min_cP": 150.0,
        "spec_max_cP": 300.0,
    },
}

# --- viscosity physics constants (LOCK, from contract) ---
GEL_TEMP_MIN = 70.0        # below this no gelatinisation
GEL_TEMP_REF = 88.0        # full-temp reference (== recipe cook setpoint)
VISC_BASE_cP = 30.0        # raw (ungelatinised) viscosity
VISC_GAIN_cP = 230.0       # gelatinisation contribution -> ~260 cP at full gel

# --- demo-time acceleration ---
# Real cook hold_sec is 300s. To keep a full batch to ~2-4 min real time we run the
# process clock much faster than wall time. TIME_SCALE seconds of process time pass
# per second of tick dt. With hold_sec=300 and TIME_SCALE=6 the cook hold alone is
# ~50s, plus dosing/heat-up/cooling/filling -> ~2.5-3.5 min total.
TIME_SCALE = 6.0

# process rates (in *process* seconds)
DOSE_RATE_KG_S = 400.0     # kg dosed per process-second (total across materials)
HEAT_RATE_C_S = 1.2        # cook heat-up degrees C per process-second
COOL_RATE_C_S = 1.5        # cooling degrees C per process-second
FILL_RATE_PACKS_S = 60.0   # 1L packs filled per process-second
AGITATOR_DEFAULT_RPM = 60.0

AMBIENT_C = 20.0
PACK_SIZE_L = 1.0


class VlaProcess:
    """One production line running one batch at a time through the 6-state lifecycle."""

    def __init__(self) -> None:
        # control / lifecycle
        self.state: str = IDLE
        self.batch_id: str = ""
        self.active_recipe: str = ""
        self.recipe: dict | None = None
        self.sim_time_s: float = 0.0

        # --- receiving-tank-01 ---
        self.receiving_level_L: float = 8000.0
        self.receiving_temp_C: float = 6.0
        self.fat_setpoint_pct: float = 3.5

        # --- process-tank-01 (mixing) ---
        self.mix_level_L: float = 0.0
        self.mix_temp_C: float = AMBIENT_C
        self.agitator_rpm: float = 0.0
        self.agitator_setpoint_rpm: float = AGITATOR_DEFAULT_RPM
        self.dose_setpoint_kg: dict[str, float] = {"milk": 0.0, "sugar": 0.0, "starch": 0.0, "cocoa": 0.0}
        self.dose_actual_kg: dict[str, float] = {"milk": 0.0, "sugar": 0.0, "starch": 0.0, "cocoa": 0.0}
        self.phase: str = "idle"  # human-readable sub-phase string tag

        # --- cook-unit-01 ---
        self.cook_temp_C: float = AMBIENT_C
        self.cook_setpoint_C: float = 88.0
        self.hold_sec: float = 300.0
        self.hold_elapsed_sec: float = 0.0
        self.peak_cook_temp_C: float = 0.0
        self.viscosity_cP: float = VISC_BASE_cP

        # --- cooler-01 ---
        self.cool_temp_C: float = AMBIENT_C
        self.cool_target_C: float = 22.0

        # --- filler-01 ---
        self.packs_total: int = 0
        self.reject_count: int = 0
        self.pack_size_L: float = PACK_SIZE_L

        # --- faults ---
        # active_fault: {"id": str, "magnitude": float}
        self.active_fault: dict | None = None
        self._cook_temp_cap_C: float | None = None  # from cook_undertemp fault

        # --- samples / events ---
        self.samples: list[dict] = []
        self.events: list[dict] = []

        self._batch_seq = 0

    # ------------------------------------------------------------------ control
    def start_batch(self, recipe_id: str, batch_id: str | None = None) -> int:
        """Begin a new batch. Returns 0 on OK, >0 on refusal."""
        if self.state not in (IDLE, COMPLETE):
            self._log_event("start_refused", {"reason": "busy", "state": self.state})
            return 1
        recipe = RECIPES.get(recipe_id)
        if recipe is None:
            self._log_event("start_refused", {"reason": "unknown_recipe", "recipe_id": recipe_id})
            return 2

        self._batch_seq += 1
        self.batch_id = batch_id or self._make_batch_id()
        self.active_recipe = recipe_id
        self.recipe = recipe

        # load recipe setpoints
        self.dose_setpoint_kg = dict(recipe["doses_kg"])
        self.dose_actual_kg = {k: 0.0 for k in self.dose_setpoint_kg}
        self.cook_setpoint_C = float(recipe["cook_setpoint_C"])
        self.hold_sec = float(recipe["hold_sec"])
        self.cool_target_C = float(recipe["cool_target_C"])
        self.agitator_setpoint_rpm = AGITATOR_DEFAULT_RPM

        # reset per-batch process state
        self.mix_level_L = 0.0
        self.mix_temp_C = AMBIENT_C
        self.cook_temp_C = AMBIENT_C
        self.cool_temp_C = AMBIENT_C
        self.hold_elapsed_sec = 0.0
        self.peak_cook_temp_C = 0.0
        self.viscosity_cP = VISC_BASE_cP
        self.packs_total = 0
        self.reject_count = 0
        self.samples = []

        self.state = DOSING
        self.phase = "dosing"
        self._log_event("batch_started", {"batch_id": self.batch_id, "recipe_id": recipe_id})
        return 0

    def stop(self) -> int:
        """Abort the running batch -> back to IDLE."""
        if self.state in (IDLE, COMPLETE):
            return 1
        self._log_event("batch_stopped", {"batch_id": self.batch_id, "state": self.state})
        self.state = IDLE
        self.phase = "idle"
        self.agitator_rpm = 0.0
        return 0

    def set_setpoint(self, target: str, value: float) -> int:
        """Adjust a writable setpoint. target per contract §OPC-UA SetSetpoint."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 3
        t = (target or "").strip()
        if t == "cook.setpoint_C":
            self.cook_setpoint_C = value
        elif t == "cook.hold_sec":
            self.hold_sec = value
        elif t == "cooler.target_C":
            self.cool_target_C = value
        elif t == "mixing.agitator_rpm":
            self.agitator_setpoint_rpm = value
        elif t == "dose.milk":
            self.dose_setpoint_kg["milk"] = value
        elif t == "dose.sugar":
            self.dose_setpoint_kg["sugar"] = value
        elif t == "dose.starch":
            self.dose_setpoint_kg["starch"] = value
        elif t == "dose.cocoa":
            self.dose_setpoint_kg["cocoa"] = value
        elif t == "receiving.fat":
            self.fat_setpoint_pct = value
        else:
            return 4
        self._log_event("setpoint_changed", {"target": t, "value": value})
        return 0

    def take_sample(self, sample_type: str) -> int:
        """Grab a lab-sample snapshot of the current process values."""
        sample = {
            "sample_type": sample_type or "process",
            "batch_id": self.batch_id,
            "state": self.state,
            "sim_time_s": round(self.sim_time_s, 1),
            "cook_temp_C": round(self.cook_temp_C, 2),
            "peak_cook_temp_C": round(self.peak_cook_temp_C, 2),
            "viscosity_cP": round(self.viscosity_cP, 1),
            "mix_temp_C": round(self.mix_temp_C, 2),
        }
        self.samples.append(sample)
        self._log_event("sample_taken", {"sample_type": sample["sample_type"]})
        return 0

    def inject_fault(self, fault_id: str, magnitude: float) -> int:
        """Inject a process fault. magnitude in [0,1] (severity)."""
        try:
            magnitude = float(magnitude)
        except (TypeError, ValueError):
            magnitude = 1.0
        magnitude = clamp(magnitude, 0.0, 1.0)
        fid = (fault_id or "").strip()

        if fid == "cook_undertemp":
            # Cap the achievable peak cook temperature.
            #   magnitude 0 -> cap = 88 (no effect)
            #   magnitude 1 -> cap = 70 (no gelatinisation at all)
            # Contract: cap on 70 + (1-magnitude)*18 C.
            self._cook_temp_cap_C = GEL_TEMP_MIN + (1.0 - magnitude) * (GEL_TEMP_REF - GEL_TEMP_MIN)
        elif fid == "agitator_slow":
            pass  # applied in tick via magnitude
        elif fid == "dose_off":
            pass  # applied in tick via magnitude
        else:
            return 5

        self.active_fault = {"id": fid, "magnitude": magnitude}
        self._log_event("fault_injected", {"fault_id": fid, "magnitude": magnitude})
        return 0

    def clear_fault(self) -> int:
        if self.active_fault is None:
            return 1
        self._log_event("fault_cleared", {"fault_id": self.active_fault.get("id")})
        self.active_fault = None
        self._cook_temp_cap_C = None
        return 0

    # ------------------------------------------------------------------ tick
    def tick(self, dt: float) -> None:
        """Advance the process by dt real seconds (scaled to TIME_SCALE process-seconds)."""
        pdt = dt * TIME_SCALE
        self.sim_time_s += pdt

        # agitator tracks setpoint while a batch is active; slow-fault reduces it
        if self.state in (DOSING, COOKING, COOLING, FILLING):
            target_rpm = self.agitator_setpoint_rpm
            if self.active_fault and self.active_fault["id"] == "agitator_slow":
                target_rpm *= (1.0 - self.active_fault["magnitude"])
            self.agitator_rpm += (target_rpm - self.agitator_rpm) * clamp(pdt * 0.5, 0, 1)
        else:
            self.agitator_rpm += (0.0 - self.agitator_rpm) * clamp(pdt * 0.5, 0, 1)

        if self.state == DOSING:
            self._tick_dosing(pdt)
        elif self.state == COOKING:
            self._tick_cooking(pdt)
        elif self.state == COOLING:
            self._tick_cooling(pdt)
        elif self.state == FILLING:
            self._tick_filling(pdt)
        # IDLE / COMPLETE: nothing to advance

    def _tick_dosing(self, pdt: float) -> None:
        self.phase = "dosing"
        # dose_off fault reduces the actually-delivered fraction of one material (milk)
        remaining = False
        # distribute dosing budget across materials proportionally to their setpoints
        total_sp = sum(self.dose_setpoint_kg.values()) or 1.0
        budget = DOSE_RATE_KG_S * pdt
        for mat, sp in self.dose_setpoint_kg.items():
            eff_sp = sp
            if self.active_fault and self.active_fault["id"] == "dose_off" and mat == "milk":
                eff_sp = sp * (1.0 - self.active_fault["magnitude"])
            share = budget * (sp / total_sp)
            newval = min(eff_sp, self.dose_actual_kg[mat] + share)
            self.dose_actual_kg[mat] = newval
            if self.dose_actual_kg[mat] < eff_sp - 0.01:
                remaining = True

        # mix level in litres ~ total kg dosed (density ~1 for demo)
        self.mix_level_L = sum(self.dose_actual_kg.values())
        # pull from receiving tank (milk source)
        self.receiving_level_L = max(0.0, self.receiving_level_L - budget * 0.0)

        # gentle pre-warm during dosing
        self.mix_temp_C += (self.receiving_temp_C + 10.0 - self.mix_temp_C) * clamp(pdt * 0.1, 0, 1)

        if not remaining:
            self.state = COOKING
            self.phase = "cooking"
            self.cook_temp_C = self.mix_temp_C
            self._log_event("phase_change", {"to": COOKING})

    def _tick_cooking(self, pdt: float) -> None:
        self.phase = "cooking"
        # effective heat target respects a cook_undertemp cap if present
        target = self.cook_setpoint_C
        if self._cook_temp_cap_C is not None:
            target = min(target, self._cook_temp_cap_C)

        # ramp cook temperature toward target
        if self.cook_temp_C < target:
            self.cook_temp_C = min(target, self.cook_temp_C + HEAT_RATE_C_S * pdt)
        else:
            # settle at target
            self.cook_temp_C += (target - self.cook_temp_C) * clamp(pdt * 0.5, 0, 1)
        self.mix_temp_C = self.cook_temp_C
        self.peak_cook_temp_C = max(self.peak_cook_temp_C, self.cook_temp_C)

        # hold timer starts counting once we are within 1C of the (capped) target
        if self.cook_temp_C >= target - 1.0:
            self.hold_elapsed_sec += pdt

        # live viscosity from gelatinisation physics (the Solve)
        self.viscosity_cP = self._compute_viscosity()

        if self.hold_elapsed_sec >= self.hold_sec:
            self.state = COOLING
            self.phase = "cooling"
            self._log_event("phase_change", {"to": COOLING,
                                             "peak_cook_temp_C": round(self.peak_cook_temp_C, 2),
                                             "viscosity_cP": round(self.viscosity_cP, 1)})

    def _tick_cooling(self, pdt: float) -> None:
        self.phase = "cooling"
        # cook viscosity is now locked in; just cool the mass toward the cooler target
        if self.cool_temp_C == AMBIENT_C and self.cook_temp_C > self.cool_target_C:
            # first cooling entry: start from the cook temperature
            self.cool_temp_C = self.cook_temp_C
        if self.cool_temp_C > self.cool_target_C:
            self.cool_temp_C = max(self.cool_target_C, self.cool_temp_C - COOL_RATE_C_S * pdt)
        else:
            self.cool_temp_C += (self.cool_target_C - self.cool_temp_C) * clamp(pdt * 0.5, 0, 1)
        self.mix_temp_C = self.cool_temp_C
        self.cook_temp_C += (self.cool_temp_C - self.cook_temp_C) * clamp(pdt * 0.3, 0, 1)

        if abs(self.cool_temp_C - self.cool_target_C) < 0.5:
            self.state = FILLING
            self.phase = "filling"
            self._log_event("phase_change", {"to": FILLING})

    def _tick_filling(self, pdt: float) -> None:
        self.phase = "filling"
        packs_to_make = int(self.mix_level_L / self.pack_size_L)
        if self.packs_total < packs_to_make:
            self.packs_total = min(packs_to_make, self.packs_total + int(FILL_RATE_PACKS_S * pdt) + 1)
        if self.packs_total >= packs_to_make:
            self.packs_total = packs_to_make
            self.state = COMPLETE
            self.phase = "complete"
            self._log_event("batch_complete", {
                "batch_id": self.batch_id,
                "packs_total": self.packs_total,
                "peak_cook_temp_C": round(self.peak_cook_temp_C, 2),
                "hold_elapsed_sec": round(self.hold_elapsed_sec, 1),
                "end_viscosity_cP": round(self.viscosity_cP, 1),
            })

    # ------------------------------------------------------------------ physics
    def _compute_viscosity(self) -> float:
        """Gelatinisation viscosity (the Solve), per contract LOCK formula."""
        g_temp = clamp((self.peak_cook_temp_C - GEL_TEMP_MIN) / (GEL_TEMP_REF - GEL_TEMP_MIN), 0.0, 1.0)
        g_hold = clamp(self.hold_elapsed_sec / self.hold_sec, 0.0, 1.0) if self.hold_sec > 0 else 0.0
        g = g_temp * g_hold
        return VISC_BASE_cP + g * VISC_GAIN_cP

    # ------------------------------------------------------------------ snapshot
    def read(self) -> dict:
        """Flat snapshot keyed by the exact contract tag paths (Area.Equipment.tag).

        Returned as a nested dict grouped by (area, equipment) so the OPC-UA server can
        walk it directly to write each read-variable.
        """
        return {
            ("Receiving", "receiving-tank-01"): {
                "level_L": round(self.receiving_level_L, 2),
                "temp_C": round(self.receiving_temp_C, 2),
                "fat_setpoint_pct": round(self.fat_setpoint_pct, 2),
            },
            ("Mixing", "process-tank-01"): {
                "level_L": round(self.mix_level_L, 2),
                "temp_C": round(self.mix_temp_C, 2),
                "agitator_rpm": round(self.agitator_rpm, 1),
                "dose_milk_actual_kg": round(self.dose_actual_kg["milk"], 2),
                "dose_sugar_actual_kg": round(self.dose_actual_kg["sugar"], 2),
                "dose_starch_actual_kg": round(self.dose_actual_kg["starch"], 2),
                "dose_cocoa_actual_kg": round(self.dose_actual_kg["cocoa"], 2),
                "phase": self.phase,
            },
            ("Cook", "cook-unit-01"): {
                "temp_C": round(self.cook_temp_C, 2),
                "setpoint_C": round(self.cook_setpoint_C, 2),
                "hold_sec": round(self.hold_sec, 1),
                "hold_elapsed_sec": round(self.hold_elapsed_sec, 1),
                "viscosity_cP": round(self.viscosity_cP, 1),
            },
            ("Cooling", "cooler-01"): {
                "temp_C": round(self.cool_temp_C, 2),
                "target_C": round(self.cool_target_C, 2),
            },
            ("Filling", "filler-01"): {
                "packs_total": int(self.packs_total),
                "reject_count": int(self.reject_count),
                "pack_size_L": round(self.pack_size_L, 2),
            },
        }

    def batch_status(self) -> dict:
        """Line-level Batch object status tags."""
        return {
            "state": self.state,
            "batch_id": self.batch_id,
            "active_recipe": self.active_recipe,
        }

    # ------------------------------------------------------------------ helpers
    def _make_batch_id(self) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return f"VLA-{stamp}-{self._batch_seq:03d}"

    def _log_event(self, kind: str, payload: dict) -> None:
        self.events.append({"kind": kind, "sim_time_s": round(self.sim_time_s, 1), **payload})
        self.events = self.events[-200:]
