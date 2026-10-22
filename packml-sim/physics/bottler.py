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
    #: Wat deze module ECHT implementeert; gen-park.py leest dit uit
    #: voor park-faults.json, zodat de catalogus geen storing kan
    #: claimen die de fysica niet kent.
    FAULTS = {
        "f12": "spoelwaterdruk valt weg",
        "f13": "carrousel-slip, de machine haalt zijn snelheid niet",
        "f8": "verstopte vulklep, te weinig volume en lagere doorzet",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.target_mL = float(config.get("fill_volume_mL", 1000.0))
        self.tolerance_mL = float(config.get("tolerance_mL", 3.0))
        self.bot_per_min = 0.0
        self.reject_count = 0
        self.bottles_total = 0
        self.last_fill_mL = self.target_mL

        # Uitgebreide procesvariabelen voor lijn Vla-B. Achter een
        # vlag: zonder vlag publiceert deze module byte-voor-byte wat
        # de bestaande scenario's al jaren zien, en dat wordt
        # gearchiveerd. selftest_regression.py bewaakt dat.
        self.extended_pvs = bool(config.get("extended_pvs", False))
        self.fill_press_bar = 0.0
        self.rinse_flow_L_min = 0.0
        self.cap_torque_Ncm = 0.0
        self.carousel_rpm = 0.0
        self.quality_pct = 100.0
        self.preform_level_pct = 100.0

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

        if self.extended_pvs:
            self._step_extended(dt)

    def _step_extended(self, dt):
        """Vuldruk, spoelcircuit en dopkoppel.

        quality_pct wordt hier BEREKEND uit de tellers en niet los gesimuleerd.
        Twee getallen die hetzelfde zouden moeten zeggen maar apart worden
        verzonnen, gaan gegarandeerd uit elkaar lopen, en dan is de demo zijn
        eigen tegenvoorbeeld.
        """
        import random as _r
        f8 = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
        f12 = self.faults.magnitude("f12") if self.faults.is_active("f12") else 0.0
        f13 = self.faults.magnitude("f13") if self.faults.is_active("f13") else 0.0

        if self.sm.is_running():
            self.fill_press_bar = (2.8 - 1.2 * f8) + _r.gauss(0, 0.03)
            self.rinse_flow_L_min = (42.0 - 30.0 * f12) + _r.gauss(0, 0.4)
            self.cap_torque_Ncm = 38.0 - 5.0 * f8 + _r.gauss(0, 0.7)
            self.carousel_rpm = self.bot_per_min / 12.0 * (1.0 - 0.3 * f13)
            self.preform_level_pct = max(
                0.0, self.preform_level_pct - self.bot_per_min * dt / 60.0 * 0.03)
            if self.preform_level_pct <= 0.5:
                self.preform_level_pct = 100.0
        else:
            self.fill_press_bar = max(0.0, self.fill_press_bar - 1.0 * dt)
            self.rinse_flow_L_min = max(0.0, self.rinse_flow_L_min - 10.0 * dt)
            self.cap_torque_Ncm = max(0.0, self.cap_torque_Ncm - 15.0 * dt)
            self.carousel_rpm = max(0.0, self.carousel_rpm - 3.0 * dt)

        total = self.bottles_total + self.reject_count
        self.quality_pct = (100.0 * self.bottles_total / total) if total else 100.0

    def read(self):
        fill_reading = self.last_fill_mL
        if self.faults.is_active("f1"):
            fill_reading += 3.0 * self.faults.magnitude("f1")
        base = {
            "bottles_per_min": round(self.bot_per_min, 2),
            "fill_volume_mL": round(fill_reading, 2),
            "reject_count": self.reject_count,
            "bottles_total": int(self.bottles_total),
        }
        if not self.extended_pvs:
            return base
        base.update({
            "fill_press_bar": round(self.fill_press_bar, 2),
            "rinse_flow_L_min": round(self.rinse_flow_L_min, 1),
            "cap_torque_Ncm": round(self.cap_torque_Ncm, 1),
            "carousel_rpm": round(self.carousel_rpm, 1),
            "quality_pct": round(self.quality_pct, 2),
            "preform_level_pct": round(self.preform_level_pct, 1),
        })
        return base
