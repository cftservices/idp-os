"""Clean-In-Place station — line-B allergen-swap guardrail.

Batch unit running CIP cycles between allergen recipe changes.
Each cycle has phases (rinse → caustic → rinse → acid → rinse → final-rinse).

Solve-B source: `cycle-stale-min` is the time since last completed
CIP. If a recipe switch to glutenfree is attempted while
cycle-stale-min > 60, the N8N guardrail blocks production.

States:
    Execute  -> CIP cycle running (allergen-mode reflects current/next recipe)
    Idle     -> ready, last-completed timestamp valid
    Held     -> mid-cycle pause (operator hold)

Faults:
    f8   chemical pump weak (cycle takes longer)
    f12  heater fail (caustic phase under-temp, cycle considered failed)
"""

from __future__ import annotations

import random
import time

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


_PHASES = [
    ("pre-rinse", 60.0),
    ("caustic", 300.0),
    ("intermediate-rinse", 60.0),
    ("acid", 180.0),
    ("final-rinse", 120.0),
]


@PhysicsRegistry.register("cip-station")
class CIPStation(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.caustic_setpoint_c = float(config.get("caustic_setpoint_c", 75.0))
        self.cycle_id = 0
        self.cycle_elapsed_s = 0.0
        self.phase = "idle"
        self.last_completed_at_s = time.monotonic() - 9999.0  # very stale at boot
        self.allergen_mode = config.get("allergen_mode", "standard")
        self.caustic_temp_c = 25.0
        self.flow_l_min = 0.0

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            if self.phase == "idle":
                self.cycle_id += 1
                self.phase = _PHASES[0][0]
                self.cycle_elapsed_s = 0.0
            self.cycle_elapsed_s += dt
            self._advance_phase()
            self.flow_l_min = 200.0 + random.gauss(0, 5)
            if self.faults.is_active("f8"):
                self.flow_l_min *= (1.0 - 0.4 * self.faults.magnitude("f8"))
            # Caustic temp
            if self.phase == "caustic":
                target = self.caustic_setpoint_c
                if self.faults.is_active("f12"):
                    target -= 12.0 * self.faults.magnitude("f12")
                self.caustic_temp_c += (target - self.caustic_temp_c) * 0.1 * dt
            else:
                self.caustic_temp_c += (25.0 - self.caustic_temp_c) * 0.02 * dt
        elif sm.state == PackMLState.IDLE:
            self.phase = "idle"
            self.flow_l_min = max(0.0, self.flow_l_min - 30.0 * dt)
            self.caustic_temp_c += (25.0 - self.caustic_temp_c) * 0.02 * dt

    def _advance_phase(self):
        cumulative = 0.0
        for name, duration in _PHASES:
            cumulative += duration
            if self.cycle_elapsed_s < cumulative:
                self.phase = name
                return
        # Past last phase — complete
        self.phase = "idle"
        self.last_completed_at_s = time.monotonic()
        self.cycle_elapsed_s = 0.0
        self.sm.command("stop")

    def read(self):
        stale_s = time.monotonic() - self.last_completed_at_s
        stale_min = stale_s / 60.0
        return {
            "phase": self.phase,
            "cycle-id": self.cycle_id,
            "cycle-elapsed-s": int(self.cycle_elapsed_s),
            "allergen-mode": self.allergen_mode,
            "caustic-temp": round(self.caustic_temp_c, 2),
            "flow_L_min": round(self.flow_l_min, 1),
            "cycle-stale-min": round(stale_min, 1),
            "last-completed-ago-min": round(stale_min, 1),
        }

    def on_command(self, cmd, payload):
        if cmd == "AllergenMode":
            self.allergen_mode = payload.strip() or "standard"
            return True
        return False
