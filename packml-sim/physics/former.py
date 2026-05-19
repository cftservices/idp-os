"""Dough former / scaler — PLC-FORM-A / PLC-FORM-B.

Continuous unit: divides bulk dough into individual scaled pieces.
MachSpeed = pieces/min. Each piece has a target weight, with noise
that scales up under faults.

Faults:
    f1   load cell drift (scaler reads heavy)
    f8   guillotine sticky (cut weight inconsistent, wider stddev)
    f13  belt slip (output rate below setpoint)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("former")
class Former(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.target_weight_g = float(config.get("target_weight_g", 800.0))
        self.tolerance_g = float(config.get("tolerance_g", 10.0))
        self.pieces_per_min = 0.0
        self.pieces_total = 0
        self.reject_count = 0
        self.last_weight_g = self.target_weight_g

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            rate = sm.cur_mach_speed
            if self.faults.is_active("f13"):
                rate *= (1.0 - 0.3 * self.faults.magnitude("f13"))
            rate += random.gauss(0, max(rate * 0.01, 0.2))
            self.pieces_per_min = max(0.0, rate)
            new_pieces = self.pieces_per_min * dt / 60.0
            self.pieces_total += new_pieces
            stddev = 3.0
            if self.faults.is_active("f8"):
                stddev = 3.0 + 15.0 * self.faults.magnitude("f8")
            weight = self.target_weight_g + random.gauss(0, stddev)
            self.last_weight_g = weight
            if abs(weight - self.target_weight_g) > self.tolerance_g:
                if random.random() < new_pieces:
                    self.reject_count += 1
        else:
            self.pieces_per_min = max(0.0, self.pieces_per_min - 6.0 * dt)

    def read(self):
        w = self.last_weight_g
        if self.faults.is_active("f1"):
            w += 12.0 * self.faults.magnitude("f1")
        return {
            "pieces-per-min": round(self.pieces_per_min, 2),
            "pieces-total": int(self.pieces_total),
            "scaler-weight": round(w, 2),
            "reject-count": self.reject_count,
        }
