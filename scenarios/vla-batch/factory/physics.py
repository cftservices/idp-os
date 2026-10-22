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
  - Demo time: time-accelerated. Tunable per deployment via the TIME_SCALE /
    *_RATE_* environment variables (see below); the deployed setting gives
    ~30s per phase and ~2 min per batch.
"""

from __future__ import annotations

import os
import time


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _envf(name: str, default: float) -> float:
    """Read a float tuning constant from the environment, falling back to `default`.

    Defaults are the original hard-coded values, so an unconfigured container (and
    every offline test) behaves exactly as before.
    """
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


# --- batch states ---
IDLE = "IDLE"
DOSING = "DOSING"
COOKING = "COOKING"
COOLING = "COOLING"
FILLING = "FILLING"
COMPLETE = "COMPLETE"

STATES = [IDLE, DOSING, COOKING, COOLING, FILLING, COMPLETE]

# --- recipe library (contract: chocolate-vla-1L; lijn 2 maakt de vier smaken) ---
# Alle doseerstromen bestaan op elke lijn; een recept zet wat het niet gebruikt op 0.
DOSE_MATERIALS = ["milk", "sugar", "starch", "cocoa",
                  "vanilla", "banana", "caramel", "strawberry"]


def _doses(d: dict) -> dict:
    return {m: float(d.get(m, 0.0)) for m in DOSE_MATERIALS}


RECIPES = {
    "chocolate-vla-1L": {
        "product_name": "Chocolate Vla 1L",
        "base_L": 5000.0,
        "doses_kg": _doses({"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "cocoa": 100.0}),
        "cook_setpoint_C": 88.0,
        "hold_sec": 300.0,
        "cool_target_C": 22.0,
        "spec_min_cP": 150.0,
        "spec_max_cP": 300.0,
    },
    "vanilla-vla-1L": {
        "product_name": "Vanilla Vla 1L",
        "base_L": 5000.0,
        "doses_kg": _doses({"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "vanilla": 12.0}),
        "cook_setpoint_C": 88.0,
        "hold_sec": 300.0,
        "cool_target_C": 22.0,
        "spec_min_cP": 150.0,
        "spec_max_cP": 300.0,
    },
    "banana-vla-1L": {
        "product_name": "Banana Vla 1L",
        "base_L": 5000.0,
        "doses_kg": _doses({"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "banana": 60.0}),
        "cook_setpoint_C": 88.0,
        "hold_sec": 300.0,
        "cool_target_C": 22.0,
        "spec_min_cP": 150.0,
        "spec_max_cP": 300.0,
    },
    "caramel-vla-1L": {
        "product_name": "Caramel Vla 1L",
        "base_L": 5000.0,
        "doses_kg": _doses({"milk": 5000.0, "sugar": 400.0, "starch": 250.0, "caramel": 120.0}),
        "cook_setpoint_C": 88.0,
        "hold_sec": 300.0,
        "cool_target_C": 22.0,
        "spec_min_cP": 150.0,
        "spec_max_cP": 300.0,
    },
    "strawberry-vla-1L": {
        "product_name": "Strawberry Vla 1L",
        "base_L": 5000.0,
        "doses_kg": _doses({"milk": 5000.0, "sugar": 500.0, "starch": 250.0, "strawberry": 150.0}),
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

# --- demo-time acceleration (all overridable via environment) ---
# Real cook hold_sec is 300s. To keep a batch watchable we run the process clock
# faster than wall time: TIME_SCALE seconds of process time pass per second of
# tick dt. hold_sec stays 300 process-seconds -- it feeds the viscosity formula,
# the sample spec (batches.py) and the EBR contract, so it must not be lowered.
# Slowing the demo down is therefore done by raising TIME_SCALE and deriving the
# rates from a desired phase duration:
#
#     rate = amount / (phase_seconds * TIME_SCALE)
#
# Deployed values (docker-compose.vla.yml) give ~30s per phase, ~120s per batch:
#   TIME_SCALE=15  DOSE=13.0  HEAT=0.47  COOL=0.145  FILL=13.0
# The defaults below are the original fast values (~86s per batch).
TIME_SCALE = _envf("TIME_SCALE", 6.0)

# process rates (in *process* seconds)
DOSE_RATE_KG_S = _envf("DOSE_RATE_KG_S", 400.0)      # kg dosed per process-second (total)
HEAT_RATE_C_S = _envf("HEAT_RATE_C_S", 1.2)          # cook heat-up degrees C per process-second
COOL_RATE_C_S = _envf("COOL_RATE_C_S", 1.5)          # cooling degrees C per process-second
FILL_RATE_PACKS_S = _envf("FILL_RATE_PACKS_S", 60.0)  # 1L packs filled per process-second
AGITATOR_DEFAULT_RPM = 60.0

# --- filler rejects ---------------------------------------------------------
# Until now reject_count was initialised and reset but never incremented, so it
# read 0 forever and every KPI that leans on scrap was uncomputable. Rejects are
# modelled deterministically (no rng: a reproducible simulator is worth more
# than a random one) as a function of end viscosity:
#
#   * a small baseline for seal and fill-weight faults, always present;
#   * a penalty that grows as the product thins out below spec. Under-cooked vla
#     is runny: it overfills, it does not seal cleanly, and the filler rejects it.
#
# So the Solve gets a second, measurable consequence. An under-cooked batch does
# not just go to HOLD, it also throws away packs, and that shows up in the scrap
# ratio and in the loss block in euros.
REJECT_BASE_PCT = _envf("REJECT_BASE_PCT", 0.4)          # % of filled packs, normal running
REJECT_OFFSPEC_PCT = _envf("REJECT_OFFSPEC_PCT", 6.0)    # extra % at full under-cook
VISC_SPEC_MIN_cP = _envf("VISC_SPEC_MIN_cP", 150.0)      # matches factory-model verdict_rule

AMBIENT_C = 20.0
PACK_SIZE_L = 1.0

# --- rauwmelksilo (receiving-tank-01) -------------------------------------
# De silo trok tot nu toe NIETS af bij het doseren: de regel deed
# `receiving_level_L - budget * 0.0`, dus de vermenigvuldiging met nul haalde
# de onttrekking weg. Gevolg: 5000 kg melk per batch kwam uit het niets en het
# niveau stond eeuwig op 8000 L. Dat was ook de reden dat de tag nooit op de
# UNS verscheen: een change-based koppeling publiceert een constante nooit.
RECEIVING_CAPACITY_L = _envf("RECEIVING_CAPACITY_L", 10000.0)
RECEIVING_REFILL_BELOW_L = _envf("RECEIVING_REFILL_BELOW_L", 2500.0)
RECEIVING_REFILL_RATE_L_S = _envf("RECEIVING_REFILL_RATE_L_S", 120.0)
# Rauwe koemelk zit rond 1,03 kg/L. Het is een dichtheid en geen procesinstelling,
# dus hij hoort hier en niet in het recept.
MILK_DENSITY_KG_L = _envf("MILK_DENSITY_KG_L", 1.03)
# Aanvoertemperatuur van een tankwagen, en de silo loopt langzaam op naar de
# omgeving. Zo beweegt temp_C mee en is hij ook echt een meting.
MILK_DELIVERY_C = _envf("MILK_DELIVERY_C", 5.0)
RECEIVING_WARM_RATE_PER_S = _envf("RECEIVING_WARM_RATE_PER_S", 0.0008)


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
        self.receiving_level_L: float = RECEIVING_CAPACITY_L * 0.8
        self.receiving_temp_C: float = MILK_DELIVERY_C
        self._receiving_filling: bool = False
        self.fat_setpoint_pct: float = 3.5

        # --- process-tank-01 (mixing) ---
        self.mix_level_L: float = 0.0
        # specified batch yield in litres (recipe base_L); the filler works off
        # this, not off the dosed mass -- see _tick_dosing
        self.batch_volume_L: float = 0.0
        self.mix_temp_C: float = AMBIENT_C
        self.agitator_rpm: float = 0.0
        self.agitator_setpoint_rpm: float = AGITATOR_DEFAULT_RPM
        self.dose_setpoint_kg: dict[str, float] = {m: 0.0 for m in DOSE_MATERIALS}
        self.dose_actual_kg: dict[str, float] = {m: 0.0 for m in DOSE_MATERIALS}
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
        # fractional pack accumulator: keeps the fill rate exact regardless of
        # TICK_INTERVAL instead of rounding per tick (see _tick_filling)
        self._packs_f: float = 0.0

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
        self.batch_volume_L = float(recipe.get("base_L") or 0.0)
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
        self._packs_f = 0.0
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
        elif t.startswith("dose.") and t[5:] in DOSE_MATERIALS:
            self.dose_setpoint_kg[t[5:]] = value
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

        # De silo leeft ONAFHANKELIJK van de batch: hij wordt aangevuld en warmt
        # langzaam op, ook als de lijn stilstaat. Dat is niet alleen realistisch,
        # het is ook wat een niveaumeting tot een meting maakt in plaats van een
        # constante die nooit gepubliceerd wordt.
        self._tick_receiving(pdt)

        if self.state == DOSING:
            self._tick_dosing(pdt)
        elif self.state == COOKING:
            self._tick_cooking(pdt)
        elif self.state == COOLING:
            self._tick_cooling(pdt)
        elif self.state == FILLING:
            self._tick_filling(pdt)
        # IDLE / COMPLETE: nothing to advance

    def _tick_receiving(self, pdt: float) -> None:
        """Rauwmelksilo: aanvulling en opwarming.

        Zakt het niveau onder de drempel, dan komt er een tankwagen en loopt de
        silo weer vol tot de capaciteit. De aangevoerde melk is koud, dus het
        mengen van oud en nieuw trekt de temperatuur omlaag; daartussenin loopt
        hij langzaam op naar de omgevingstemperatuur.

        Zonder dit stond het niveau eeuwig stil op zijn beginwaarde en werd de
        tag door de change-based OPC-UA-koppeling nooit gepubliceerd. De
        ontvangsttank stond daardoor leeg op elk scherm.
        """
        if self.receiving_level_L < RECEIVING_REFILL_BELOW_L:
            self._receiving_filling = True
        if getattr(self, "_receiving_filling", False):
            toevoer_L = min(
                RECEIVING_REFILL_RATE_L_S * pdt,
                RECEIVING_CAPACITY_L - self.receiving_level_L,
            )
            if toevoer_L > 0:
                # Menging van twee volumes: gewogen gemiddelde, geen sprong.
                totaal = self.receiving_level_L + toevoer_L
                self.receiving_temp_C = (
                    self.receiving_temp_C * self.receiving_level_L
                    + MILK_DELIVERY_C * toevoer_L
                ) / max(totaal, 1e-6)
                self.receiving_level_L = totaal
            if self.receiving_level_L >= RECEIVING_CAPACITY_L - 0.5:
                self.receiving_level_L = RECEIVING_CAPACITY_L
                self._receiving_filling = False
        else:
            # Gekoeld, maar niet perfect: de silo kruipt richting omgeving.
            self.receiving_temp_C += (
                (AMBIENT_C - self.receiving_temp_C) * RECEIVING_WARM_RATE_PER_S * pdt
            )

    def _tick_dosing(self, pdt: float) -> None:
        self.phase = "dosing"
        # dose_off fault reduces the actually-delivered fraction of one material (milk)
        remaining = False
        # distribute dosing budget across materials proportionally to their setpoints
        total_sp = sum(self.dose_setpoint_kg.values()) or 1.0
        budget = DOSE_RATE_KG_S * pdt
        # Stand voor deze tick, om verderop te weten hoeveel melk er ECHT uit de
        # silo is gegaan. Het verschil, niet het budget: bij een dose_off-fout
        # gaat er minder doorheen dan begroot.
        milk_before_kg = self.dose_actual_kg["milk"]
        eff_setpoints: dict[str, float] = {}
        for mat, sp in self.dose_setpoint_kg.items():
            eff_sp = sp
            if self.active_fault and self.active_fault["id"] == "dose_off" and mat == "milk":
                eff_sp = sp * (1.0 - self.active_fault["magnitude"])
            eff_setpoints[mat] = eff_sp
            share = budget * (sp / total_sp)
            newval = min(eff_sp, self.dose_actual_kg[mat] + share)
            self.dose_actual_kg[mat] = newval
            if self.dose_actual_kg[mat] < eff_sp - 0.01:
                remaining = True

        if not remaining:
            # Valves close ON target: snap away the float residue left by the
            # 0.01 kg exit tolerance. Without this the totals land a hair under
            # setpoint (e.g. 5849.9999 kg -> 5849 instead of 5850 packs), and the
            # smaller the per-tick dose the more visible that becomes.
            self.dose_actual_kg = {m: eff_setpoints[m] for m in self.dose_actual_kg}

        # Tank level in LITRES, not dosed kilograms. The recipe's base_L is the
        # specified batch yield: the 5850 kg of milk/sugar/starch/cocoa dissolve
        # into 5000 L of product. Reading the level straight off the mass made a
        # 5000 L order fill 5850 packs, i.e. 117% "yield" out of thin air.
        # Ramped with dosing progress so the tank still fills during DOSING, and
        # a dose_off fault correctly yields proportionally less product.
        total_sp_all = sum(self.dose_setpoint_kg.values()) or 1.0
        dosed_frac = clamp(sum(self.dose_actual_kg.values()) / total_sp_all, 0.0, 1.0)
        self.mix_level_L = self.batch_volume_L * dosed_frac

        # Onttrekking uit de rauwmelksilo. ALLEEN melk komt daaruit; suiker,
        # zetmeel en cacao zijn droge grondstoffen uit een andere stroom. De
        # oude regel vermenigvuldigde de onttrekking met 0.0 en trok dus niets
        # af, waardoor de melk uit het niets kwam en de silo nooit leegliep.
        milk_drawn_kg = max(0.0, self.dose_actual_kg["milk"] - milk_before_kg)
        self.receiving_level_L = max(
            0.0, self.receiving_level_L - milk_drawn_kg / MILK_DENSITY_KG_L
        )

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

    def _reject_rate(self) -> float:
        """Fraction of filled packs the filler rejects, from end viscosity.

        Deterministic by design. `shortfall` is 0.0 at or above the spec minimum
        and 1.0 at raw, ungelatinised viscosity, so a fully under-cooked batch
        gets the whole off-spec penalty and a good one only the baseline."""
        span = max(1e-6, VISC_SPEC_MIN_cP - VISC_BASE_cP)
        shortfall = clamp((VISC_SPEC_MIN_cP - self.viscosity_cP) / span, 0.0, 1.0)
        return (REJECT_BASE_PCT + REJECT_OFFSPEC_PCT * shortfall) / 100.0

    def _tick_filling(self, pdt: float) -> None:
        self.phase = "filling"
        # Every pack the volume yields gets filled; part of them is rejected.
        # packs_total counts GOOD packs only, so good + rejects == filled and the
        # mass balance stays honest for the yield KPI.
        packs_to_fill = int(self.mix_level_L / self.pack_size_L)
        if self._packs_f < packs_to_fill:
            # accumulate fractionally so the rate holds exactly at any TICK_INTERVAL
            self._packs_f += FILL_RATE_PACKS_S * pdt
        filled = min(packs_to_fill, int(self._packs_f))
        self.reject_count = int(filled * self._reject_rate())
        self.packs_total = filled - self.reject_count

        if filled >= packs_to_fill:
            self.state = COMPLETE
            self.phase = "complete"
            self._log_event("batch_complete", {
                "batch_id": self.batch_id,
                "packs_total": self.packs_total,
                "reject_count": self.reject_count,
                "packs_filled": filled,
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
                **{f"dose_{m}_actual_kg": round(self.dose_actual_kg.get(m, 0.0), 2)
                   for m in DOSE_MATERIALS},
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
