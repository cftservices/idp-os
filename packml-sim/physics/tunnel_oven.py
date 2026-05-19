"""4-zone tunnel oven — DCS-OVEN-A / DCS-OVEN-B.

Continuous process. PackML state controls belt + heaters:

    Execute -> belt at MachSpeed, all 4 zone heaters regulate to setpoint.
    Held    -> belt stops, heaters maintain (product stays in oven, scrap risk).
    Stopped -> belt stops, heaters off (zones cool to ambient slowly).

Each zone has its own setpoint (240/220/200/180 °C by default).
A thermal-mass model gives realistic warm-up + drift behaviour.

This is the Solve-A trigger source: zone-3 has a configurable
drift-event (heating element degradation) that can be scheduled via
config or injected at runtime via fault f12.

Faults:
    f1   sensor bias  — zone-N reads N degrees too high
    f2   sensor drift — zone-N drifts slowly over time
    f12  heater loss  — zone setpoint not reached, PV droops
    f13  belt slip    — belt-speed below MachSpeed
"""

from __future__ import annotations

import random
import time

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


_AMBIENT_C = 25.0


@PhysicsRegistry.register("tunnel-oven")
class TunnelOven(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)

        zones_cfg = config.get("zones", [
            {"setpoint": 240.0},
            {"setpoint": 220.0},
            {"setpoint": 200.0},
            {"setpoint": 180.0},
        ])
        self.zones = [
            {
                "setpoint": float(z["setpoint"]),
                "pv": _AMBIENT_C,
                "power_kw": 0.0,
                "label": z.get("label", f"zone-{i+1}"),
            }
            for i, z in enumerate(zones_cfg)
        ]

        self.belt_speed = 0.0  # m/min — actual
        self.target_belt_speed = float(config.get("belt_speed_m_min", 1.5))
        self.thermal_tau_s = float(config.get("thermal_tau_s", 90.0))

        # Optional scheduled drift event (Solve-A canvas)
        ev = config.get("drift_event")
        self._drift = None
        if ev:
            self._drift = {
                "zone_index": int(ev.get("zone_index", 2)),  # zone-3 (0-based)
                "trigger_at_s": float(ev.get("trigger_at_s", 600.0)),
                "magnitude": float(ev.get("magnitude", 0.4)),
                "ramp_s": float(ev.get("ramp_s", 120.0)),
            }
        self._uptime_s = 0.0

    def step(self, dt):
        self._uptime_s += dt
        self._maybe_trigger_scheduled_drift()
        self._update_belt(dt)
        self._update_zones(dt)

    def _update_belt(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            target = self.target_belt_speed * (sm.cur_mach_speed / max(sm.mach_design_speed, 1.0))
            if self.faults.is_active("f13"):
                target *= (1.0 - 0.4 * self.faults.magnitude("f13"))
        else:
            target = 0.0
        ramp = self.target_belt_speed / 3.0  # 3 s to spin up
        if self.belt_speed < target:
            self.belt_speed = min(target, self.belt_speed + ramp * dt)
        elif self.belt_speed > target:
            self.belt_speed = max(target, self.belt_speed - ramp * dt)

    def _update_zones(self, dt):
        sm = self.sm
        heaters_on = sm.state in (PackMLState.EXECUTE, PackMLState.HELD, PackMLState.SUSPENDED)
        for i, z in enumerate(self.zones):
            target = z["setpoint"] if heaters_on else _AMBIENT_C
            if self.faults.is_active("f12"):
                target *= (1.0 - 0.25 * self.faults.magnitude("f12"))
            # Exponential approach with thermal time constant
            alpha = 1.0 - pow(2.71828, -dt / self.thermal_tau_s)
            z["pv"] += (target - z["pv"]) * alpha
            # Stochastic jitter
            z["pv"] += random.gauss(0, 0.3)
            # Estimated electrical power draw — proportional to demand
            demand = max(0.0, target - z["pv"])
            z["power_kw"] = round(demand * 0.6 + (4.0 if heaters_on else 0.0) + random.gauss(0, 0.1), 2)

    def _maybe_trigger_scheduled_drift(self):
        if self._drift is None:
            return
        if self._uptime_s < self._drift["trigger_at_s"]:
            return
        if not self.faults.is_active("f12"):
            self.faults.inject("f12", self._drift["magnitude"])
            self._drift = None  # one-shot

    def read(self):
        out = {
            "belt-speed": round(self.belt_speed, 3),
            "power": round(sum(z["power_kw"] for z in self.zones), 2),
        }
        for i, z in enumerate(self.zones):
            zone_id = z["label"]
            pv = z["pv"]
            if self.faults.is_active("f1"):
                pv += 2.0 * self.faults.magnitude("f1")
            if self.faults.is_active("f2"):
                pv += 0.001 * self.faults.elapsed("f2") * self.faults.magnitude("f2")
            out[f"{zone_id}/temperature"] = round(pv, 2)
            out[f"{zone_id}/setpoint"] = z["setpoint"]
            out[f"{zone_id}/power"] = z["power_kw"]
            out[f"{zone_id}/product-present"] = self.belt_speed > 0.1
        return out

    def on_command(self, cmd, payload):
        # Allow per-zone setpoint changes: Command/Zone/1/Setpoint = "238.0"
        parts = cmd.split("/")
        if len(parts) == 3 and parts[0] == "Zone" and parts[2] == "Setpoint":
            try:
                idx = int(parts[1]) - 1
                if 0 <= idx < len(self.zones):
                    self.zones[idx]["setpoint"] = float(payload)
                    return True
            except (ValueError, IndexError):
                return False
        return False
