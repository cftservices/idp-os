"""Preheater — flow heater that warms milk before mixing/pasteurizing.

Simple thermal unit (no divert-safety, unlike the pasteurizer):

    Execute -> temp approaches setpoint_c; flow scales with MachSpeed.
    Held/Stopped -> heater off, temp drifts toward ambient.

Faults:
    f12  heating loss — setpoint not reached
    f1   sensor bias  — temp reads high
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("preheater")
class Preheater(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.setpoint_c = float(config.get("setpoint_c", 45.0))
        self.ambient_temp_c = float(config.get("ambient_temp_c", 8.0))
        self.temp_c = self.ambient_temp_c
        self.flow_l_min = 0.0

    def step(self, dt):
        sm = self.sm
        if sm.is_running():
            target = self.setpoint_c
            if self.faults.is_active("f12"):
                target -= 12.0 * self.faults.magnitude("f12")
            self.temp_c += (target - self.temp_c) * 0.06 * dt
            self.temp_c += random.gauss(0, 0.05)
            self.flow_l_min = sm.cur_mach_speed * 6.0
        else:
            self.temp_c += (self.ambient_temp_c - self.temp_c) * 0.01 * dt
            self.flow_l_min = max(0.0, self.flow_l_min - 20.0 * dt)

    def read(self):
        reading = self.temp_c
        if self.faults.is_active("f1"):
            reading += 2.0 * self.faults.magnitude("f1")
        return {
            "temp_C": round(reading, 2),
            "flow_L_min": round(self.flow_l_min, 1),
            "setpoint_C": self.setpoint_c,
        }
