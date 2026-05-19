"""Cream separator — centrifuge.

Continuous unit: spins at MachSpeed (RPM target), separates cream
from skim. Higher RPM = lower outgoing fat%.

Faults:
    f1   sensor bias (fat% reads high)
    f12  bearing wear (RPM struggles to reach setpoint, vibration up)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("separator")
class Separator(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.target_fat_pct = float(config.get("target_fat_pct", 3.5))
        self.rpm = 0.0
        self.vibration_mm_s = 0.0

    def step(self, dt):
        sm = self.sm
        if sm.is_running():
            target = sm.cur_mach_speed * 50.0  # MachSpeed 0..120 → 0..6000 RPM
            if self.faults.is_active("f12"):
                target *= (1.0 - 0.15 * self.faults.magnitude("f12"))
        else:
            target = 0.0
        # First-order approach
        self.rpm += (target - self.rpm) * 0.1 * dt
        self.rpm += random.gauss(0, max(target * 0.005, 0.5))
        # Vibration baseline + spike when faulty
        base_vib = 1.2
        if self.faults.is_active("f12"):
            base_vib += 4.0 * self.faults.magnitude("f12")
        self.vibration_mm_s = base_vib + random.gauss(0, 0.15)

    def read(self):
        # Fat% inversely correlates with RPM (higher RPM = better separation)
        rpm_factor = min(self.rpm / 6000.0, 1.0)
        fat = self.target_fat_pct + (1.0 - rpm_factor) * 0.8 + random.gauss(0, 0.05)
        if self.faults.is_active("f1"):
            fat += 0.3 * self.faults.magnitude("f1")
        return {
            "RPM": round(self.rpm, 1),
            "fat_pct": round(fat, 3),
            "vibration_mm_s": round(self.vibration_mm_s, 2),
        }
