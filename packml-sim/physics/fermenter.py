"""Fermenter — culture fermentation for yoghurt / buttermilk.

PackML state drives a fermentation cycle:

    Idle    -> waiting for Start (pH at start_ph, temp at ambient)
    Execute -> inoculate + hold temp at setpoint_c; pH drops from
               start_ph toward target_ph over ferment_duration_s.
               When pH <= target_ph (and min time reached) -> auto-Complete.
    Held    -> fermentation pauses (temp drifts toward ambient, pH holds).
    Aborted -> reset to idle.

Setpoints (generic): yoghurt ~43 C, buttermilk ~22 C; pH 6.6 -> 3.9.

Faults:
    f12  heating/cooling loss — temp can't hold setpoint, pH drop stalls
    f2   sensor drift — pH reads slowly off (false endpoint)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("fermenter")
class Fermenter(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.setpoint_c = float(config.get("setpoint_c", 43.0))
        self.ambient_temp_c = float(config.get("ambient_temp_c", 20.0))
        self.start_ph = float(config.get("start_ph", 6.6))
        self.target_ph = float(config.get("target_ph", 3.9))
        # Demo-time duration of a full fermentation (real seconds).
        self.ferment_duration_s = float(config.get("ferment_duration_s", 600.0))
        self.culture_id = str(config.get("culture_id", "CULT-STD"))

        self.temp_c = self.ambient_temp_c
        self.ph = self.start_ph
        self.elapsed_s = 0.0
        self.phase = "idle"
        self.batch_counter = 0

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            if self.phase == "idle":
                self._begin()
            self._ferment(dt)
        elif sm.state == PackMLState.HELD:
            self.temp_c += (self.ambient_temp_c - self.temp_c) * 0.01 * dt
        elif sm.state in (PackMLState.ABORTED, PackMLState.STOPPED, PackMLState.IDLE):
            if self.phase != "idle":
                self._reset()

    def _begin(self):
        self.phase = "ferment"
        self.elapsed_s = 0.0
        self.ph = self.start_ph
        self.temp_c = max(self.temp_c, self.ambient_temp_c)

    def _ferment(self, dt):
        self.elapsed_s += dt

        # Temperature approaches setpoint; heating loss (f12) caps it.
        temp_target = self.setpoint_c
        if self.faults.is_active("f12"):
            temp_target -= 8.0 * self.faults.magnitude("f12")
        self.temp_c += (temp_target - self.temp_c) * 0.05 * dt
        self.temp_c += random.gauss(0, 0.03)

        # pH drop rate scales with how close temp is to setpoint (culture
        # activity). If heating fails, the culture stalls and pH barely moves.
        activity = max(0.0, min(1.0, (self.temp_c - self.ambient_temp_c) /
                                max(self.setpoint_c - self.ambient_temp_c, 1.0)))
        total_drop = self.start_ph - self.target_ph
        drop_per_s = total_drop / max(self.ferment_duration_s, 1.0)
        self.ph = max(self.target_ph, self.ph - drop_per_s * activity * dt)

        # Auto-complete when target pH reached and min time elapsed.
        if self.ph <= self.target_ph + 0.02 and self.elapsed_s >= self.ferment_duration_s * 0.9:
            self.batch_counter += 1
            self._reset()
            self.sm.command("stop")

    def _reset(self):
        self.phase = "idle"
        self.elapsed_s = 0.0

    def read(self):
        ph = self.ph
        if self.faults.is_active("f2"):
            ph += 0.3 * self.faults.magnitude("f2")
        return {
            "temp_C": round(self.temp_c, 2),
            "pH": round(ph, 3),
            "ferment_elapsed_min": round(self.elapsed_s / 60.0, 2),
            "culture_id": self.culture_id,
            "batch_counter": self.batch_counter,
            "phase": self.phase,
        }

    def on_command(self, cmd, payload):
        if cmd == "Culture":
            self.culture_id = str(payload)
            return True
        return False
