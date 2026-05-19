"""Bulk fermentation chamber — PLC-FERM-A / PLC-FERM-B.

Batch unit (45-90 min): receives mixed dough, holds at temperature
and humidity for first proof. PackML drives chamber climate:

    Idle    -> chamber idle, temp drifts toward ambient
    Execute -> chamber regulates to setpoint (temp + humidity)
    Held    -> chamber maintains current PV (dough keeps fermenting)
    Stopped -> chamber off (heaters off, blowers off)

Solve hook: dough_temp + ambient combined determine yeast activity,
which downstream proofing (proofer.py) uses to adjust dwell time.

Faults:
    f1   sensor bias (temp / humidity reads off)
    f12  heater fail (chamber temp droops)
    f8   damper stuck (humidity off-target)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


_AMBIENT_C = 22.0


@PhysicsRegistry.register("bulk-fermenter")
class BulkFermenter(PhysicsBase):
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.temp_setpoint = float(config.get("temp_setpoint_c", 28.0))
        self.humidity_setpoint = float(config.get("humidity_setpoint_pct", 75.0))
        self.batch_duration_s = float(config.get("batch_duration_s", 4500.0))  # 75 min
        self.temp_c = _AMBIENT_C
        self.humidity_pct = 50.0
        self.batch_elapsed_s = 0.0
        self.batch_id = 0
        self.batch_counter = 0

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            if self.batch_elapsed_s == 0.0:
                self.batch_id += 1
            self.batch_elapsed_s += dt
            t_target = self.temp_setpoint
            h_target = self.humidity_setpoint
            if self.faults.is_active("f12"):
                t_target *= (1.0 - 0.15 * self.faults.magnitude("f12"))
            if self.faults.is_active("f8"):
                h_target -= 15.0 * self.faults.magnitude("f8")
            self.temp_c += (t_target - self.temp_c) * 0.03 * dt
            self.humidity_pct += (h_target - self.humidity_pct) * 0.05 * dt
            if self.batch_elapsed_s >= self.batch_duration_s:
                self.batch_counter += 1
                self.batch_elapsed_s = 0.0
                sm.command("stop")
        elif sm.state == PackMLState.HELD:
            pass  # maintain PV
        else:
            self.temp_c += (_AMBIENT_C - self.temp_c) * 0.005 * dt
            self.humidity_pct += (50.0 - self.humidity_pct) * 0.005 * dt
        self.temp_c += random.gauss(0, 0.1)
        self.humidity_pct = max(20.0, min(100.0, self.humidity_pct + random.gauss(0, 0.2)))

    def read(self):
        temp = self.temp_c
        hum = self.humidity_pct
        if self.faults.is_active("f1"):
            temp += 1.5 * self.faults.magnitude("f1")
            hum -= 5.0 * self.faults.magnitude("f1")
        return {
            "temperature": round(temp, 2),
            "humidity": round(hum, 1),
            "batch-id": self.batch_id,
            "batch-counter": self.batch_counter,
            "batch-elapsed-s": int(self.batch_elapsed_s),
            "batch-progress-pct": round(100.0 * self.batch_elapsed_s / self.batch_duration_s, 1),
        }
