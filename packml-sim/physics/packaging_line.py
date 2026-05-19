"""Packaging line — slicer + wrapper.

Continuous output counter driven by PackML state + MachSpeed.

    Execute -> output-rate ramps to MachSpeed, accumulator increments.
    Held    -> output-rate to 0, accumulated count frozen.
    Stopped -> idle.

Faults:
    f8   wrapper jam — output rate drops to 30% of setpoint
    f13  slicer slip — increased reject_count
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("packaging-line")
class PackagingLine(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.recipe_id = int(config.get("recipe_id", 101))
        self.units_total = 0
        self.reject_count = 0
        self.output_rate = 0.0

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            rate = sm.cur_mach_speed  # units/min
            if self.faults.is_active("f8"):
                rate *= (1.0 - 0.7 * self.faults.magnitude("f8"))
            rate += random.gauss(0, max(rate * 0.01, 0.1))
            self.output_rate = max(0.0, rate)
            units_this_tick = self.output_rate * dt / 60.0
            self.units_total += units_this_tick
            reject_p = 0.001
            if self.faults.is_active("f13"):
                reject_p *= (1.0 + 30.0 * self.faults.magnitude("f13"))
            if random.random() < reject_p * dt * max(self.output_rate, 1.0) / 60.0:
                self.reject_count += 1
        else:
            self.output_rate = max(0.0, self.output_rate - 5.0 * dt)

    def read(self):
        return {
            "recipe-id": self.recipe_id,
            "output-rate": round(self.output_rate, 2),
            "units-total": int(self.units_total),
            "reject-count": self.reject_count,
        }

    def on_command(self, cmd, payload):
        if cmd == "Recipe":
            try:
                self.recipe_id = int(payload)
                return True
            except ValueError:
                return False
        return False
