"""Fill line — packaging filler.

Continuous packs counter. PackML state controls the filler:

    Execute -> packs accumulate at nominal_ppm scaled by MachSpeed;
               a small fraction is rejected (out-of-fill).
    Held/Stopped -> filling pauses.

Faults:
    f13  motor slip     — throughput below setpoint
    f8   nozzle fouling  — reject rate up
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("fill-line")
class FillLine(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.pack_size_l = float(config.get("pack_size_l", 1.0))
        self.nominal_ppm = float(config.get("nominal_ppm", 120.0))  # packs/min at design speed
        self.reject_base_pct = float(config.get("reject_base_pct", 0.4))

        self.pack_count = 0
        self.reject_count = 0
        self._pack_accum = 0.0
        self.packs_per_min = 0.0

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            speed_factor = max(sm.cur_mach_speed / max(sm.mach_design_speed, 1.0), 0.0)
            if self.faults.is_active("f13"):
                speed_factor *= (1.0 - 0.5 * self.faults.magnitude("f13"))
            self.packs_per_min = self.nominal_ppm * speed_factor
            self._pack_accum += self.packs_per_min * dt / 60.0
            reject_pct = self.reject_base_pct
            if self.faults.is_active("f8"):
                reject_pct += 8.0 * self.faults.magnitude("f8")
            while self._pack_accum >= 1.0:
                self._pack_accum -= 1.0
                if random.random() * 100.0 < reject_pct:
                    self.reject_count += 1
                else:
                    self.pack_count += 1
        else:
            self.packs_per_min = 0.0

    def read(self):
        good = self.pack_count
        total = self.pack_count + self.reject_count
        quality_pct = 100.0 * good / total if total else 100.0
        return {
            "pack_count": self.pack_count,
            "reject_count": self.reject_count,
            "packs_per_min": round(self.packs_per_min, 1),
            "pack_size_L": self.pack_size_l,
            "quality_pct": round(quality_pct, 2),
        }

    def on_command(self, cmd, payload):
        if cmd == "ResetCounters":
            self.pack_count = 0
            self.reject_count = 0
            self._pack_accum = 0.0
            return True
        return False
