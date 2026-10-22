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
    #: Wat deze module ECHT implementeert. gen-park.py leest dit uit voor
    #: park-faults.json, zodat de catalogus geen storing kan claimen die niet
    #: bestaat. Een knop die niets doet is erger dan geen knop.
    FAULTS = {
        "f1": "sensorbias, het vetpercentage leest te hoog",
        "f12": "lagerslijtage, de trommel haalt zijn toerental niet en trilt",
        "f8": "verstopte uitlaat, doorzet loopt terug en de drukval loopt op",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.target_fat_pct = float(config.get("target_fat_pct", 3.5))
        self.rpm = 0.0
        self.vibration_mm_s = 0.0

        # Uitgebreide procesvariabelen voor lijn Vla-B. Achter een vlag: zonder
        # vlag publiceert deze module byte-voor-byte wat het bestaande
        # DairyPlant-scenario al jaren ziet, en dat wordt gearchiveerd.
        #
        # vibration_mm_s staat hier met opzet NIET bij de parkvariabelen: die
        # zit al in groep F van het signaalsjabloon. Twee slots met dezelfde
        # canonieke naam laat SignalSet terecht klappen, want dan overschrijft
        # er stil een.
        self.extended_pvs = bool(config.get("extended_pvs", False))
        self.feed_L_min = 0.0
        self.cream_L_min = 0.0
        self.skim_L_min = 0.0
        self.bowl_dp_bar = 0.0
        self.discharge_count = 0
        self.solids_pct = 8.6
        self.throughput_ref_L = float(config.get("throughput_ref_L", 5000.0))
        self._discharge_accum = 0.0

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

        if self.extended_pvs:
            self._step_extended(dt)

    def read(self):
        # Fat% inversely correlates with RPM (higher RPM = better separation)
        rpm_factor = min(self.rpm / 6000.0, 1.0)
        fat = self.target_fat_pct + (1.0 - rpm_factor) * 0.8 + random.gauss(0, 0.05)
        if self.faults.is_active("f1"):
            fat += 0.3 * self.faults.magnitude("f1")
        base = {
            "RPM": round(self.rpm, 1),
            "fat_pct": round(fat, 3),
            "vibration_mm_s": round(self.vibration_mm_s, 2),
        }
        if not self.extended_pvs:
            return base
        base.update({
            "feed_L_min": round(self.feed_L_min, 1),
            "cream_L_min": round(self.cream_L_min, 2),
            "skim_L_min": round(self.skim_L_min, 1),
            "bowl_dp_bar": round(self.bowl_dp_bar, 3),
            "discharge_count": int(self.discharge_count),
            "solids_pct": round(self.solids_pct, 2),
        })
        return base

    def _step_extended(self, dt):
        """Massabalans over de trommel, alleen voor lijn Vla-B.

        Voeding splitst in room en ondermelk. De verhouding volgt het
        vetpercentage, want dat is wat een separator fysiek DOET; hem als een
        vast percentage modelleren geeft getallen die niet op elkaar aansluiten
        en dat is precies wat een procestechnoloog eruit haalt.
        """
        sm = self.sm
        f8 = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0

        if sm.is_running():
            want = min(self.throughput_ref_L / 6.0, 900.0) * (1.0 - 0.6 * f8)
            self.feed_L_min += (want - self.feed_L_min) * min(0.4 * dt, 1.0)
            # Room is de kleine fractie; de rest is ondermelk. Samen weer de
            # voeding, anders klopt de balans niet.
            cream_frac = max(0.02, min(0.14, self.target_fat_pct / 40.0))
            self.cream_L_min = self.feed_L_min * cream_frac
            self.skim_L_min = self.feed_L_min - self.cream_L_min
            # Drukval over de trommel loopt op met doorzet en met verstopping.
            self.bowl_dp_bar = (0.4 + 0.0016 * self.feed_L_min) * (1.0 + 2.2 * f8)
            self.solids_pct += (8.6 - self.solids_pct) * min(0.05 * dt, 1.0)
            # Zelflossing: periodiek slib uitwerpen. Vaker bij verstopping.
            self._discharge_accum += dt * (1.0 + 2.0 * f8)
            if self._discharge_accum >= 900.0:
                self._discharge_accum = 0.0
                self.discharge_count += 1
        else:
            for attr in ("feed_L_min", "cream_L_min", "skim_L_min", "bowl_dp_bar"):
                setattr(self, attr, max(0.0, getattr(self, attr) * (1.0 - 0.5 * dt)))
