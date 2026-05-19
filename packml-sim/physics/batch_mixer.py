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

_PHASE_DURATION = {
    _PHASE_LOAD: 15.0,
    _PHASE_MIX: 480.0,  # 8 min
    _PHASE_REST: 120.0,  # 2 min
    _PHASE_DISCHARGE: 10.0,
}


@PhysicsRegistry.register("batch-mixer")
class BatchMixer(PhysicsBase):
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
        return {
            "recipe-id": self.recipe_id,
            "batch-id": self.batch_id,
            "batch-counter": self.batch_counter,
            "load": round(self.load_kg, 2),
            "dough-temp": round(self.dough_temp_c, 2),
            "power": round(self.power_kw, 2),
            "phase": self.phase,
        }

    def on_command(self, cmd, payload):
        if cmd == "Recipe":
            try:
                self.recipe_id = int(payload)
                return True
            except ValueError:
                return False
        return False
