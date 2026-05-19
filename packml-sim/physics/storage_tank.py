"""Storage / receiving tank — milk reception, raw-material silos.

Continuous fill/draw model. PackML state controls the inflow valve:

    Execute -> inflow at MachSpeed (L/min), draw at config draw_rate.
    Held    -> inflow off, draw continues.
    Stopped -> both off (passive).

Cooling jacket holds temp at config.target_temp_c ± noise.

Faults:
    f1   sensor bias (level reads off)
    f8   inflow valve clogged (rate -70%)
    f12  cooling fail   (temp drifts up)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("storage-tank")
class StorageTank(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)

        self.capacity_l = float(config.get("capacity_l", 30000.0))
        self.level_l = float(config.get("initial_level_l", 15000.0))
        self.draw_rate_l_min = float(config.get("draw_rate_l_min", 800.0))
        self.target_temp_c = float(config.get("target_temp_c", 4.5))
        self.ambient_temp_c = float(config.get("ambient_temp_c", 18.0))
        self.temp_c = self.target_temp_c

    def step(self, dt):
        sm = self.sm
        inflow = 0.0
        if sm.state == PackMLState.EXECUTE:
            inflow = sm.cur_mach_speed  # L/min
            if self.faults.is_active("f8"):
                inflow *= (1.0 - 0.7 * self.faults.magnitude("f8"))
            inflow += random.gauss(0, max(inflow * 0.01, 0.5))
        draw = self.draw_rate_l_min if sm.is_running() else 0.0
        net_lpm = inflow - draw
        self.level_l = max(0.0, min(self.capacity_l, self.level_l + net_lpm * dt / 60.0))

        # Temperature: chiller fights ambient + inflow heat
        if self.faults.is_active("f12"):
            equilibrium = self.target_temp_c + 6.0 * self.faults.magnitude("f12")
        else:
            equilibrium = self.target_temp_c
        self.temp_c += (equilibrium - self.temp_c) * 0.02 * dt
        self.temp_c += random.gauss(0, 0.04)

        self._inflow = inflow
        self._draw = draw

    def read(self):
        level = self.level_l
        if self.faults.is_active("f1"):
            level += 500.0 * self.faults.magnitude("f1")
        return {
            "level_L": round(level, 1),
            "level_pct": round(100.0 * level / self.capacity_l, 2),
            "in_temp_C": round(self.temp_c, 2),
            "flow_L_min": round(self._inflow, 1),
            "draw_L_min": round(self._draw, 1),
        }
