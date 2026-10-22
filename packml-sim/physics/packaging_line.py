"""Packaging line — slicer + wrapper.

Continuous output counter driven by PackML state + MachSpeed.

    Execute -> output-rate ramps to MachSpeed, accumulator increments.
    Held    -> output-rate to 0, accumulated count frozen.
    Stopped -> idle.

Faults:
    f8   wrapper jam — output rate drops to 30% of setpoint
    f13  slicer slip — increased reject_count
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("packaging-line")
class PackagingLine(PhysicsBase):
    #: Wat deze module ECHT implementeert; gen-park.py leest dit uit
    #: voor park-faults.json, zodat de catalogus geen storing kan
    #: claimen die de fysica niet kent.
    FAULTS = {
        "f12": "lijmtemperatuur zakt weg, dozen sluiten niet",
        "f13": "bandslip, de baanverdeling loopt scheef",
        "f8": "vastloper in de infeed, de wachtrij loopt op",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.recipe_id = int(config.get("recipe_id", 101))
        self.units_total = 0
        self.reject_count = 0
        self.output_rate = 0.0

        # Uitgebreide procesvariabelen voor lijn Vla-B. Achter een
        # vlag: zonder vlag publiceert deze module byte-voor-byte wat
        # de bestaande scenario's al jaren zien, en dat wordt
        # gearchiveerd. selftest_regression.py bewaakt dat.
        self.extended_pvs = bool(config.get("extended_pvs", False))
        self.packs_per_case = 12
        self.case_count_rate = 0.0
        self.infeed_backlog = 0.0
        self.glue_temp_C = 20.0
        self.glue_level_pct = 100.0
        self.jam_count_rate = 0.0
        self.lane_balance_pct = 100.0

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            rate = sm.cur_mach_speed  # units/min
            if self.faults.is_active("f8"):
                rate *= (1.0 - 0.7 * self.faults.magnitude("f8"))
            rate += random.gauss(0, max(rate * 0.01, 0.1))
            self.output_rate = max(0.0, rate)
            units_this_tick = self.output_rate * dt / 60.0
            self.units_total += units_this_tick
            reject_p = 0.001
            if self.faults.is_active("f13"):
                reject_p *= (1.0 + 30.0 * self.faults.magnitude("f13"))
            if random.random() < reject_p * dt * max(self.output_rate, 1.0) / 60.0:
                self.reject_count += 1
        else:
            self.output_rate = max(0.0, self.output_rate - 5.0 * dt)

        if self.extended_pvs:
            self._step_extended(dt)

    def _step_extended(self, dt):
        """Infeed-wachtrij, lijmcircuit en baanverdeling.

        infeed_backlog is het signaal waar een verpakkingslijn in de praktijk
        op stuurt: hij loopt op vóórdat de doorzet zakt, want de machine
        vertraagt pas als de buffer vol is. Weer dezelfde les: het vroegste
        signaal is zelden de grootheid waar het alarm op staat.
        """
        import random as _r
        f8 = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
        f12 = self.faults.magnitude("f12") if self.faults.is_active("f12") else 0.0
        f13 = self.faults.magnitude("f13") if self.faults.is_active("f13") else 0.0

        if self.sm.is_running():
            rate = float(getattr(self, "output_rate", 0.0) or 0.0)
            self.case_count_rate = rate / max(self.packs_per_case, 1)
            want_backlog = 5.0 + 85.0 * f8
            self.infeed_backlog += (want_backlog - self.infeed_backlog) * min(0.15 * dt, 1.0)
            self.glue_temp_C += ((165.0 - 60.0 * f12) - self.glue_temp_C) * min(0.05 * dt, 1.0)
            self.glue_level_pct = max(0.0, self.glue_level_pct - rate * dt / 3600.0 * 0.5)
            if self.glue_level_pct <= 1.0:
                self.glue_level_pct = 100.0
            self.jam_count_rate = 0.2 + 9.0 * f8 + _r.gauss(0, 0.05)
            self.lane_balance_pct = 100.0 - 45.0 * f13 + _r.gauss(0, 0.3)
        else:
            self.case_count_rate = 0.0
            self.infeed_backlog = max(0.0, self.infeed_backlog - 5.0 * dt)
            self.glue_temp_C += (20.0 - self.glue_temp_C) * min(0.01 * dt, 1.0)
            self.jam_count_rate = 0.0

    def read(self):
        base = {
            "recipe-id": self.recipe_id,
            "output-rate": round(self.output_rate, 2),
            "units-total": int(self.units_total),
            "reject-count": self.reject_count,
        }
        if not self.extended_pvs:
            return base
        base.update({
            "output_rate_ph": round(float(getattr(self, "output_rate", 0.0) or 0.0), 1),
            "packs_per_case": int(self.packs_per_case),
            "case_count_rate": round(self.case_count_rate, 2),
            "infeed_backlog": round(self.infeed_backlog, 1),
            "glue_temp_C": round(self.glue_temp_C, 1),
            "glue_level_pct": round(self.glue_level_pct, 1),
            "jam_count_rate": round(self.jam_count_rate, 2),
            "lane_balance_pct": round(self.lane_balance_pct, 1),
        })
        return base

    def on_command(self, cmd, payload):
        if cmd == "Recipe":
            try:
                self.recipe_id = int(payload)
                return True
            except ValueError:
                return False
        return False
