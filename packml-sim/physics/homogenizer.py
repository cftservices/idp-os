"""Homogenizer — high-pressure piston pump.

Continuous unit: MachSpeed → target pressure (bar). Realistic ranges
150–220 bar for full-cream milk. Pressure ripples around setpoint.

Faults:
    f1   sensor bias (pressure reads high)
    f8   valve seat wear (pressure drops below setpoint)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("homogenizer")
class Homogenizer(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.target_bar = float(config.get("target_bar", 180.0))
        self.pressure_bar = 0.0
        self.flow_l_min = 0.0

    def step(self, dt):
        sm = self.sm
        if sm.is_running():
            target = self.target_bar * (sm.cur_mach_speed / max(sm.mach_design_speed, 1.0))
            if self.faults.is_active("f8"):
                target *= (1.0 - 0.20 * self.faults.magnitude("f8"))
        else:
            target = 0.0
        self.pressure_bar += (target - self.pressure_bar) * 0.2 * dt
        self.pressure_bar += random.gauss(0, max(self.pressure_bar * 0.01, 0.3))
        self.flow_l_min = sm.cur_mach_speed * 8.0

    def read(self):
        p = self.pressure_bar
        if self.faults.is_active("f1"):
            p += 4.0 * self.faults.magnitude("f1")
        return {
            "pressure_bar": round(p, 1),
            "flow_L_min": round(self.flow_l_min, 1),
        }
