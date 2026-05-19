"""Spiral cooler — PLC-COOL-A / PLC-COOL-B.

Continuous belt cooler (~45 min spiral). Belt-speed × ambient drives
cool-down profile. Output product-temp = config target_out_temp_c
when at spec, climbs when belt slows or ambient warms.

Faults:
    f1   sensor bias (out_temp reads cool)
    f12  fan fail (ambient warms, cooling insufficient)
    f13  belt slip
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("spiral-cooler")
class SpiralCooler(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.target_belt_speed = float(config.get("belt_speed_m_min", 1.0))
        self.ambient_target_c = float(config.get("ambient_temp_c", 18.0))
        self.in_temp_c = float(config.get("in_temp_c", 95.0))
        self.target_out_temp_c = float(config.get("target_out_temp_c", 28.0))

        self.belt_speed = 0.0
        self.ambient_c = self.ambient_target_c
        self.product_out_c = self.target_out_temp_c

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            target = self.target_belt_speed * (sm.cur_mach_speed / max(sm.mach_design_speed, 1.0))
            if self.faults.is_active("f13"):
                target *= (1.0 - 0.4 * self.faults.magnitude("f13"))
        else:
            target = 0.0
        self.belt_speed += (target - self.belt_speed) * 0.1 * dt
        # Ambient drifts: fans active vs off
        a_target = self.ambient_target_c
        if self.faults.is_active("f12"):
            a_target += 8.0 * self.faults.magnitude("f12")
        self.ambient_c += (a_target - self.ambient_c) * 0.01 * dt + random.gauss(0, 0.08)
        # Cooling effectiveness: more belt-time = better cool-down
        cooling_factor = max(0.1, min(1.0, self.belt_speed / max(self.target_belt_speed, 0.01)))
        baseline = self.ambient_c + (self.in_temp_c - self.ambient_c) * (1.0 - cooling_factor)
        self.product_out_c += (baseline - self.product_out_c) * 0.05 * dt + random.gauss(0, 0.15)

    def read(self):
        out = self.product_out_c
        if self.faults.is_active("f1"):
            out -= 3.0 * self.faults.magnitude("f1")
        return {
            "belt-speed": round(self.belt_speed, 3),
            "ambient-temp": round(self.ambient_c, 2),
            "product-out-temp": round(out, 2),
        }
