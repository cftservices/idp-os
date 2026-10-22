"""Homogenizer — high-pressure piston pump.

Continuous unit: MachSpeed → target pressure (bar). Realistic ranges
150–220 bar for full-cream milk. Pressure ripples around setpoint.

Faults:
    f1   sensor bias (pressure reads high)
    f8   valve seat wear (pressure drops below setpoint)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("homogenizer")
class Homogenizer(PhysicsBase):
    #: Wat deze module ECHT implementeert; gen-park.py leest dit uit
    #: voor park-faults.json, zodat de catalogus geen storing kan
    #: claimen die de fysica niet kent.
    FAULTS = {
        "f12": "oliekoeling valt terug, de olietemperatuur loopt op",
        "f13": "aandrijfslip, doorzet en zuigerbelasting lopen uiteen",
        "f8": "versleten kleppen, de tweede trap haalt zijn druk niet",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.target_bar = float(config.get("target_bar", 180.0))
        self.pressure_bar = 0.0
        self.flow_l_min = 0.0

        # Uitgebreide procesvariabelen voor lijn Vla-B. Achter een
        # vlag: zonder vlag publiceert deze module byte-voor-byte wat
        # de bestaande scenario's al jaren zien, en dat wordt
        # gearchiveerd. selftest_regression.py bewaakt dat.
        self.extended_pvs = bool(config.get("extended_pvs", False))
        self.stage2_bar = 0.0
        self.inlet_temp_C = 60.0
        self.outlet_temp_C = 62.0
        self.piston_load_pct = 0.0
        self.oil_temp_C = 30.0
        self.oil_press_bar = 2.4

    def step(self, dt):
        sm = self.sm
        if sm.is_running():
            target = self.target_bar * (sm.cur_mach_speed / max(sm.mach_design_speed, 1.0))
            if self.faults.is_active("f8"):
                target *= (1.0 - 0.20 * self.faults.magnitude("f8"))
        else:
            target = 0.0
        self.pressure_bar += (target - self.pressure_bar) * 0.2 * dt
        self.pressure_bar += random.gauss(0, max(self.pressure_bar * 0.01, 0.3))
        self.flow_l_min = sm.cur_mach_speed * 8.0

        if self.extended_pvs:
            self._step_extended(dt)

    def _step_extended(self, dt):
        """Tweetraps-homogenisatie, olie-circuit en zuigerbelasting.

        Homogeniseren zet drukverschil om in warmte: over de klep stijgt de
        producttemperatuur ongeveer 0,02 graad per bar. Dat is echte fysica en
        het maakt de uitlaattemperatuur afhankelijk van de druk, zodat de twee
        signalen niet los van elkaar kunnen weglopen.
        """
        import random as _r
        f8 = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
        f12 = self.faults.magnitude("f12") if self.faults.is_active("f12") else 0.0
        f13 = self.faults.magnitude("f13") if self.faults.is_active("f13") else 0.0

        if self.sm.is_running():
            # Tweede trap is klassiek ~10% van de totale druk, en juist die
            # kleppen slijten het eerst.
            want2 = self.pressure_bar * 0.1 * (1.0 - 0.6 * f8)
            self.stage2_bar += (want2 - self.stage2_bar) * min(0.5 * dt, 1.0)
            self.inlet_temp_C += (60.0 - self.inlet_temp_C) * min(0.05 * dt, 1.0)
            self.outlet_temp_C = self.inlet_temp_C + 0.02 * self.pressure_bar
            self.outlet_temp_C += _r.gauss(0, 0.05)
            load = min(1.4, self.pressure_bar / 200.0) * (1.0 + 0.3 * f13)
            self.piston_load_pct += (100.0 * load - self.piston_load_pct) * min(0.4 * dt, 1.0)
            self.oil_temp_C += ((45.0 + 25.0 * f12) - self.oil_temp_C) * min(0.01 * dt, 1.0)
            self.oil_press_bar = 2.4 - 0.6 * f8 + _r.gauss(0, 0.02)
        else:
            self.stage2_bar = max(0.0, self.stage2_bar - 20.0 * dt)
            self.piston_load_pct = max(0.0, self.piston_load_pct - 40.0 * dt)
            self.oil_temp_C += (30.0 - self.oil_temp_C) * min(0.01 * dt, 1.0)
            self.outlet_temp_C += (20.0 - self.outlet_temp_C) * min(0.01 * dt, 1.0)

    def read(self):
        p = self.pressure_bar
        if self.faults.is_active("f1"):
            p += 4.0 * self.faults.magnitude("f1")
        base = {
            "pressure_bar": round(p, 1),
            "flow_L_min": round(self.flow_l_min, 1),
        }
        if not self.extended_pvs:
            return base
        base.update({
            "stage2_bar": round(self.stage2_bar, 2),
            "inlet_temp_C": round(self.inlet_temp_C, 2),
            "outlet_temp_C": round(self.outlet_temp_C, 2),
            "piston_load_pct": round(self.piston_load_pct, 1),
            "oil_temp_C": round(self.oil_temp_C, 1),
            "oil_press_bar": round(self.oil_press_bar, 2),
        })
        return base
