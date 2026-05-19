"""Tunnel proofer — PLC-PROOF-A / PLC-PROOF-B.

Continuous unit (~60 min dwell tunnel) with climate control:
temperature 35°C, humidity 85%. Belt speed determines actual
dwell time:

    dwell_actual_min = tunnel_length_m / (belt_speed_m_min)

Solve-C source: when belt slows (cooling demand from downstream
oven backup), dwell exceeds spec and yeast over-activates → planner
needs to replan.

Faults:
    f1   sensor bias (temp/humidity reads off)
    f12  heater fail (chamber temp droops)
    f13  belt slip (dwell exceeds spec — Solve-C trigger)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


_AMBIENT_C = 22.0


@PhysicsRegistry.register("proofer")
class Proofer(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.temp_setpoint = float(config.get("temp_setpoint_c", 35.0))
        self.humidity_setpoint = float(config.get("humidity_setpoint_pct", 85.0))
        self.tunnel_length_m = float(config.get("tunnel_length_m", 30.0))
        self.target_belt_speed = float(config.get("belt_speed_m_min", 0.5))
        self.dwell_spec_min = float(config.get("dwell_spec_min", 60.0))

        self.belt_speed = 0.0
        self.temp_c = _AMBIENT_C
        self.humidity_pct = 50.0

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            target = self.target_belt_speed * (sm.cur_mach_speed / max(sm.mach_design_speed, 1.0))
            if self.faults.is_active("f13"):
                target *= (1.0 - 0.5 * self.faults.magnitude("f13"))
        else:
            target = 0.0
        ramp = self.target_belt_speed / 5.0
        if self.belt_speed < target:
            self.belt_speed = min(target, self.belt_speed + ramp * dt)
        else:
            self.belt_speed = max(target, self.belt_speed - ramp * dt)

        heaters_on = sm.is_running()
        t_target = self.temp_setpoint if heaters_on else _AMBIENT_C
        h_target = self.humidity_setpoint if heaters_on else 50.0
        if self.faults.is_active("f12"):
            t_target -= 4.0 * self.faults.magnitude("f12")
        self.temp_c += (t_target - self.temp_c) * 0.025 * dt + random.gauss(0, 0.08)
        self.humidity_pct = max(20.0, min(
            100.0,
            self.humidity_pct + (h_target - self.humidity_pct) * 0.03 * dt + random.gauss(0, 0.2),
        ))

    def read(self):
        temp = self.temp_c
        hum = self.humidity_pct
        if self.faults.is_active("f1"):
            temp += 1.0 * self.faults.magnitude("f1")
            hum -= 4.0 * self.faults.magnitude("f1")
        dwell_actual_min = (self.tunnel_length_m / self.belt_speed / 60.0
                            if self.belt_speed > 0.001 else 0.0)
        # Clamp visually-massive dwell when belt is near zero
        dwell_actual_min = min(dwell_actual_min, 999.0)
        dwell_overshoot = max(0.0, dwell_actual_min - self.dwell_spec_min)
        return {
            "temperature": round(temp, 2),
            "humidity": round(hum, 1),
            "belt-speed": round(self.belt_speed, 3),
            "dwell-time-actual-min": round(dwell_actual_min, 1),
            "dwell-spec-min": self.dwell_spec_min,
            "dwell-overshoot-min": round(dwell_overshoot, 1),
        }
