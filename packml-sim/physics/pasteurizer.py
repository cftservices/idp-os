"""HTST pasteurizer — safety-critical thermal unit.

Continuous heat-treatment. PackML state controls steam valve + flow.
Holding-tube hold time is fixed by physical geometry.

Safety logic (PMO 21 CFR 1240 — High Temperature Short Time):
    If HTST temperature < hold_min_c, divert valve trips immediately
    and the SM auto-Aborts (regulatory requirement).

Faults:
    f1   sensor bias  (temp reads high → false safety)
    f12  steam valve  (temp can't reach setpoint)
    f8   regen plate fouling (heat-exchange efficiency drops)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("pasteurizer")
class Pasteurizer(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.setpoint_c = float(config.get("setpoint_c", 72.0))
        self.hold_min_c = float(config.get("hold_min_c", 71.5))
        self.hold_sec = int(config.get("hold_sec", 15))
        self.regen_eff = float(config.get("regen_eff", 0.85))
        self.htst_temp_c = 25.0
        self.divert = False
        self.flow_l_min = 0.0
        self._auto_aborted = False

    def step(self, dt):
        sm = self.sm
        if sm.is_running():
            steam_target = self.setpoint_c
            if self.faults.is_active("f12"):
                steam_target *= (1.0 - 0.04 * self.faults.magnitude("f12"))
            if self.faults.is_active("f8"):
                # Regen plate fouling — slower approach
                tau_factor = 1.0 / max(self.regen_eff - 0.4 * self.faults.magnitude("f8"), 0.1)
            else:
                tau_factor = 1.0 / max(self.regen_eff, 0.1)
            alpha = min(0.15 / tau_factor * dt, 1.0)
            self.htst_temp_c += (steam_target - self.htst_temp_c) * alpha
            self.htst_temp_c += random.gauss(0, 0.08)
            self.flow_l_min = sm.cur_mach_speed * 8.0  # 120 → 960 L/min nominal
        else:
            self.htst_temp_c += (25.0 - self.htst_temp_c) * 0.005 * dt
            self.flow_l_min = max(0.0, self.flow_l_min - 30.0 * dt)

        # Safety: divert if temp below hold_min while producing
        reading = self.htst_temp_c
        if self.faults.is_active("f1"):
            reading += 1.5 * self.faults.magnitude("f1")
        if sm.state == PackMLState.EXECUTE and reading < self.hold_min_c:
            self.divert = True
            if not self._auto_aborted:
                sm.command("abort")
                self._auto_aborted = True
        else:
            self.divert = False
            if sm.state == PackMLState.IDLE:
                self._auto_aborted = False

    def read(self):
        reading = self.htst_temp_c
        if self.faults.is_active("f1"):
            reading += 1.5 * self.faults.magnitude("f1")
        return {
            "HTST_temp_C": round(reading, 2),
            "hold_sec": self.hold_sec,
            "divert_valve_status": self.divert,
            "flow_L_min": round(self.flow_l_min, 1),
            "regen_efficiency_pct": round(self.regen_eff * 100.0, 1),
        }
