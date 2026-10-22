"""Preheater — flow heater that warms milk before mixing/pasteurizing.

Simple thermal unit (no divert-safety, unlike the pasteurizer):

    Execute -> temp approaches setpoint_c; flow scales with MachSpeed.
    Held/Stopped -> heater off, temp drifts toward ambient.

Faults:
    f12  heating loss — setpoint not reached
    f1   sensor bias  — temp reads high
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("preheater")
class Preheater(PhysicsBase):
    #: Wat deze module ECHT implementeert; gen-park.py leest dit uit
    #: voor park-faults.json, zodat de catalogus geen storing kan
    #: claimen die de fysica niet kent.
    FAULTS = {
        "f1": "sensorbias op de uittredetemperatuur",
        "f12": "stoomklep haalt het setpoint niet",
        "f8": "vervuiling, de warmteoverdracht loopt terug",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.setpoint_c = float(config.get("setpoint_c", 45.0))
        self.ambient_temp_c = float(config.get("ambient_temp_c", 8.0))
        self.temp_c = self.ambient_temp_c
        self.flow_l_min = 0.0

        # Uitgebreide procesvariabelen voor lijn Vla-B. Achter een
        # vlag: zonder vlag publiceert deze module byte-voor-byte wat
        # de bestaande scenario's al jaren zien, en dat wordt
        # gearchiveerd. selftest_regression.py bewaakt dat.
        self.extended_pvs = bool(config.get("extended_pvs", False))
        self.temp_in_C = 8.0
        self.steam_press_bar = 0.0
        self.approach_dT_C = 0.0
        self.condensate_L_min = 0.0
        self.duty_kW = 0.0
        self.fouling_index = 0.0

    def step(self, dt):
        sm = self.sm
        if sm.is_running():
            target = self.setpoint_c
            if self.faults.is_active("f12"):
                target -= 12.0 * self.faults.magnitude("f12")
            self.temp_c += (target - self.temp_c) * 0.06 * dt
            self.temp_c += random.gauss(0, 0.05)
            self.flow_l_min = sm.cur_mach_speed * 6.0
        else:
            self.temp_c += (self.ambient_temp_c - self.temp_c) * 0.01 * dt
            self.flow_l_min = max(0.0, self.flow_l_min - 20.0 * dt)

        if self.extended_pvs:
            self._step_extended(dt)

    def _step_extended(self, dt):
        """Warmtebalans over de voorverwarmer.

        approach_dT_C is het verschil tussen stoomtemperatuur en de bereikte
        producttemperatuur. Dat getal is de vroegste indicator van vervuiling
        die er is: het loopt op lang voordat de uittredetemperatuur zakt, want
        de regeling compenseert eerst met meer stoom. Precies dezelfde les als
        bij de pasteur, maar hier is hij een eigen meting.
        """
        import random as _r
        f8 = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
        if self.sm.is_running():
            self.temp_in_C += (8.0 - self.temp_in_C) * min(0.05 * dt, 1.0)
            self.temp_in_C += _r.gauss(0, 0.03)
            self.fouling_index += (f8 * 100.0 - self.fouling_index) * min(0.05 * dt, 1.0)
            self.steam_press_bar = (1.6 + 1.4 * f8) + _r.gauss(0, 0.02)
            steam_temp = 100.0 + 20.0 * self.steam_press_bar
            self.approach_dT_C = max(0.0, steam_temp - self.temp_c)
            dT = max(0.0, self.temp_c - self.temp_in_C)
            # Q = m * c * dT, met melk c ~ 3,9 kJ/kg/K en dichtheid ~1,03.
            self.duty_kW = self.flow_l_min / 60.0 * 1.03 * 3.9 * dT
            self.condensate_L_min = self.duty_kW / 2200.0 * 60.0
        else:
            self.steam_press_bar = max(0.0, self.steam_press_bar - 0.5 * dt)
            self.duty_kW = max(0.0, self.duty_kW - 50.0 * dt)
            self.condensate_L_min = max(0.0, self.condensate_L_min - 1.0 * dt)
            self.approach_dT_C = 0.0

    def read(self):
        reading = self.temp_c
        if self.faults.is_active("f1"):
            reading += 2.0 * self.faults.magnitude("f1")
        base = {
            "temp_C": round(reading, 2),
            "flow_L_min": round(self.flow_l_min, 1),
            "setpoint_C": self.setpoint_c,
        }
        if not self.extended_pvs:
            return base
        base.update({
            "temp_in_C": round(self.temp_in_C, 2),
            "steam_press_bar": round(self.steam_press_bar, 3),
            "approach_dT_C": round(self.approach_dT_C, 2),
            "condensate_L_min": round(self.condensate_L_min, 2),
            "duty_kW": round(self.duty_kW, 1),
            "fouling_index": round(self.fouling_index, 1),
        })
        return base
