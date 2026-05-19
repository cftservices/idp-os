"""Bottler — filling + capping station.

PackML state drives output rate (bottles/min) toward MachSpeed.
Fill volume oscillates around target_mL with realistic noise.
Reject rate climbs when fill is out of tolerance.

Faults:
    f1   load cell drift (fill_mL reads high)
    f8   filler valve sticky (fill rate slow, more rejects)
    f13  capper jam (reject rate spike)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("bottler")
class Bottler(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.target_mL = float(config.get("fill_volume_mL", 1000.0))
        self.tolerance_mL = float(config.get("tolerance_mL", 3.0))
        self.bot_per_min = 0.0
        self.reject_count = 0
        self.bottles_total = 0
        self.last_fill_mL = self.target_mL

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            rate = sm.cur_mach_speed
            if self.faults.is_active("f8"):
                rate *= (1.0 - 0.5 * self.faults.magnitude("f8"))
            rate += random.gauss(0, max(rate * 0.015, 0.2))
            self.bot_per_min = max(0.0, rate)
            bottles_this_tick = self.bot_per_min * dt / 60.0
            self.bottles_total += bottles_this_tick

            fill = self.target_mL + random.gauss(0, 1.5)
            if self.faults.is_active("f8"):
                fill -= 8.0 * self.faults.magnitude("f8")
            self.last_fill_mL = fill

            reject_p = 0.005
            out_of_tol = abs(fill - self.target_mL) > self.tolerance_mL
            if out_of_tol:
                reject_p += 0.5
            if self.faults.is_active("f13"):
                reject_p += 0.05 * self.faults.magnitude("f13")
            if random.random() < reject_p * bottles_this_tick:
                self.reject_count += 1
        else:
            self.bot_per_min = max(0.0, self.bot_per_min - 8.0 * dt)

    def read(self):
        fill_reading = self.last_fill_mL
        if self.faults.is_active("f1"):
            fill_reading += 3.0 * self.faults.magnitude("f1")
        return {
            "bottles_per_min": round(self.bot_per_min, 2),
            "fill_volume_mL": round(fill_reading, 2),
            "reject_count": self.reject_count,
            "bottles_total": int(self.bottles_total),
        }
