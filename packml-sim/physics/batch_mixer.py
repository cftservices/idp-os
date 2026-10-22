"""Batch dough mixer (200 kg) — PLC-MIX-A / PLC-MIX-B.

PackML state drives a discrete batch cycle:

    Idle    -> waiting for Start command (load = 0)
    Execute -> recipe runs through phases:
                 LOAD (15 s, ramp load to 200 kg)
                 MIX  (8 min, motor at MachSpeed, dough_temp climbs)
                 REST (2 min, motor off, dough_temp stabilises)
                 DISCHARGE (10 s, load -> 0, batch_counter ++)
               then auto-Complete -> back to Idle awaiting next batch.
    Held    -> mix pauses, dough_temp drifts toward ambient.
    Aborted -> dump batch (load -> 0), do not increment counter.

Faults:
    f8   dough sticking — load drops slower during DISCHARGE
    f13  motor slip       — dough_temp climbs slower (under-mixed)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


_PHASE_LOAD = "load"
_PHASE_MIX = "mix"
_PHASE_REST = "rest"
_PHASE_DISCHARGE = "discharge"
_PHASE_IDLE = "idle"

#: Fase als getal, voor het park. Een Modbus-register kan geen tekst dragen,
#: dus vendor-c krijgt een code. De volgorde is de procesvolgorde; hem
#: hernummeren breekt elk dashboard dat op het getal filtert.
_PHASE_CODE = {
    _PHASE_IDLE: 0,
    _PHASE_LOAD: 1,
    _PHASE_MIX: 2,
    _PHASE_REST: 3,
    _PHASE_DISCHARGE: 4,
}

_PHASE_DURATION = {
    _PHASE_LOAD: 15.0,
    _PHASE_MIX: 480.0,  # 8 min
    _PHASE_REST: 120.0,  # 2 min
    _PHASE_DISCHARGE: 10.0,
}


@PhysicsRegistry.register("batch-mixer")
class BatchMixer(PhysicsBase):
    #: Wat deze module echt implementeert; gen-park.py leest dit uit.
    FAULTS = {
        "f8": "aankoeken en verstopte doseerklep, uitdoseren en lossen lopen terug",
        "f13": "motorslip, het roerwerk haalt zijn toerental niet",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)

        self.capacity_kg = float(config.get("capacity_kg", 200.0))
        self.ambient_temp_c = float(config.get("ambient_temp_c", 22.0))
        self.target_dough_temp_c = float(config.get("target_dough_temp_c", 26.0))
        self.recipe_id = int(config.get("recipe_id", 101))

        self.phase = _PHASE_IDLE
        self.phase_elapsed_s = 0.0
        self.load_kg = 0.0
        self.dough_temp_c = self.ambient_temp_c
        self.power_kw = 0.0
        self.batch_id = 0
        self.batch_counter = 0

        # Zuivel-doseerkant voor lijn Vla-B. Deze module is van huis uit een
        # bakkerij-deegmenger; voor het park is hij de mengtank waarin melk,
        # suiker, zetmeel en cacao worden gedoseerd. Achter een vlag, want het
        # bakkerij-scenario mag hier niets van merken.
        self.extended_pvs = bool(config.get("extended_pvs", False))
        self.recipe_kg = dict(config.get("recipe_kg") or {
            "milk": 4854.0, "sugar": 700.0, "starch": 250.0, "cocoa": 46.0})
        self.dose_milk_kg = 0.0
        self.dose_sugar_kg = 0.0
        self.dose_starch_kg = 0.0
        self.dose_cocoa_kg = 0.0
        self.level_L = 0.0
        self.agitator_rpm = 0.0
        self.product_density_kg_L = float(config.get("product_density_kg_L", 1.04))
        self.throughput_ref_L = float(config.get("throughput_ref_L", 5000.0))

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.IDLE and self.phase != _PHASE_IDLE:
            self._reset_to_idle()
        elif sm.state == PackMLState.EXECUTE:
            if self.phase == _PHASE_IDLE:
                self._begin_batch()
            self._advance_phase(dt)
        elif sm.state == PackMLState.HELD:
            # Drift dough temp toward ambient while paused
            self.dough_temp_c += (self.ambient_temp_c - self.dough_temp_c) * 0.005 * dt
            self.power_kw = 0.0
        elif sm.state in (PackMLState.ABORTED, PackMLState.STOPPED):
            self._reset_to_idle()
            self.load_kg = max(0.0, self.load_kg - 30.0 * dt)  # dump

        if self.extended_pvs:
            self._step_extended(dt)

    # ----------------------------------------------------------------- phasing

    def _begin_batch(self):
        self.batch_id += 1
        self.phase = _PHASE_LOAD
        self.phase_elapsed_s = 0.0
        self.load_kg = 0.0
        self.dough_temp_c = self.ambient_temp_c

    def _advance_phase(self, dt):
        self.phase_elapsed_s += dt
        if self.phase == _PHASE_LOAD:
            self.load_kg = min(self.capacity_kg, self.load_kg + self.capacity_kg * dt / _PHASE_DURATION[_PHASE_LOAD])
            self.power_kw = 2.5  # auger
            if self.phase_elapsed_s >= _PHASE_DURATION[_PHASE_LOAD]:
                self._next_phase(_PHASE_MIX)
        elif self.phase == _PHASE_MIX:
            speed_factor = max(self.sm.cur_mach_speed / max(self.sm.mach_design_speed, 1.0), 0.05)
            self.power_kw = 18.0 * speed_factor + random.gauss(0, 0.4)
            warm_per_s = (self.target_dough_temp_c - self.ambient_temp_c) / _PHASE_DURATION[_PHASE_MIX]
            if self.faults.is_active("f13"):
                warm_per_s *= (1.0 - 0.5 * self.faults.magnitude("f13"))
            self.dough_temp_c = min(self.target_dough_temp_c, self.dough_temp_c + warm_per_s * dt * speed_factor)
            if self.phase_elapsed_s >= _PHASE_DURATION[_PHASE_MIX]:
                self._next_phase(_PHASE_REST)
        elif self.phase == _PHASE_REST:
            self.power_kw = 0.5
            self.dough_temp_c += (self.ambient_temp_c - self.dough_temp_c) * 0.001 * dt
            if self.phase_elapsed_s >= _PHASE_DURATION[_PHASE_REST]:
                self._next_phase(_PHASE_DISCHARGE)
        elif self.phase == _PHASE_DISCHARGE:
            drain_rate = self.capacity_kg / _PHASE_DURATION[_PHASE_DISCHARGE]
            if self.faults.is_active("f8"):
                drain_rate *= (1.0 - 0.6 * self.faults.magnitude("f8"))
            self.load_kg = max(0.0, self.load_kg - drain_rate * dt)
            self.power_kw = 3.0
            if self.phase_elapsed_s >= _PHASE_DURATION[_PHASE_DISCHARGE] and self.load_kg <= 1.0:
                self.batch_counter += 1
                self._next_phase(_PHASE_IDLE)
                # Signal cycle-complete back to the SM
                self.sm.command("stop")

    def _next_phase(self, phase):
        self.phase = phase
        self.phase_elapsed_s = 0.0

    def _reset_to_idle(self):
        self.phase = _PHASE_IDLE
        self.phase_elapsed_s = 0.0
        self.power_kw = 0.0

    # ------------------------------------------------------------------- read

    def read(self):
        base = {
            "recipe-id": self.recipe_id,
            "batch-id": self.batch_id,
            "batch-counter": self.batch_counter,
            "load": round(self.load_kg, 2),
            "dough-temp": round(self.dough_temp_c, 2),
            "power": round(self.power_kw, 2),
            "phase": self.phase,
        }
        if not self.extended_pvs:
            return base
        base.update({
            "level_L": round(self.level_L, 1),
            "temp_C": round(self.dough_temp_c, 2),
            "agitator_rpm": round(self.agitator_rpm, 1),
            "dose_milk_kg": round(self.dose_milk_kg, 1),
            "dose_sugar_kg": round(self.dose_sugar_kg, 1),
            "dose_starch_kg": round(self.dose_starch_kg, 2),
            "dose_cocoa_kg": round(self.dose_cocoa_kg, 2),
            "phase_code": _PHASE_CODE.get(self.phase, 0),
        })
        return base

    def _step_extended(self, dt):
        """Doseren en mengen, alleen voor lijn Vla-B.

        De doseringen lopen mee met de LOAD-fase van de bestaande fasemachine,
        zodat er niet twee onafhankelijke tijdlijnen ontstaan. Het niveau volgt
        uit de gedoseerde massa gedeeld door de dichtheid; als je dat los
        modelleert, klopt de massabalans niet en is dat het eerste wat opvalt.
        """
        sm = self.sm
        total_kg = sum(self.recipe_kg.values())

        if sm.is_running():
            # De LOAD-fase doseert; daarna blijft de inhoud staan. Fasen zijn
            # STRINGS in deze module, geen nummers; vergelijken tegen een getal
            # levert een doseerkant op die nooit aangaat en niets meldt.
            if self.phase == _PHASE_LOAD:
                frac = min(1.0, self.phase_elapsed_s
                           / max(_PHASE_DURATION[_PHASE_LOAD], 0.1))
                f_dose = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
                # Verstopte doseerklep levert te weinig. Dat is precies de
                # dose_off-storing van de monoliet, hier mechanisch gemodelleerd.
                self.dose_milk_kg = self.recipe_kg["milk"] * frac * (1.0 - 0.25 * f_dose)
                self.dose_sugar_kg = self.recipe_kg["sugar"] * frac
                self.dose_starch_kg = self.recipe_kg["starch"] * frac * (1.0 - 0.4 * f_dose)
                self.dose_cocoa_kg = self.recipe_kg["cocoa"] * frac
            dosed = (self.dose_milk_kg + self.dose_sugar_kg
                     + self.dose_starch_kg + self.dose_cocoa_kg)
            self.level_L = dosed / max(self.product_density_kg_L, 0.1)
            f13 = self.faults.magnitude("f13") if self.faults.is_active("f13") else 0.0
            want_rpm = (sm.cur_mach_speed * 0.9) * (1.0 - 0.5 * f13)
            self.agitator_rpm += (want_rpm - self.agitator_rpm) * min(1.0 * dt, 1.0)
        else:
            self.agitator_rpm = max(0.0, self.agitator_rpm - 40.0 * dt)
            self.level_L = max(0.0, self.level_L - (total_kg / 30.0) * dt)

    def on_command(self, cmd, payload):
        if cmd == "Recipe":
            try:
                self.recipe_id = int(payload)
                return True
            except ValueError:
                return False
        return False
