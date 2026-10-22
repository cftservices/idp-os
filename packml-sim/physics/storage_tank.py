"""Storage / receiving tank — milk reception, raw-material silos.

Continuous fill/draw model. PackML state controls the inflow valve:

    Execute -> inflow at MachSpeed (L/min), draw at config draw_rate.
    Held    -> inflow off, draw continues.
    Stopped -> both off (passive).

Cooling jacket holds temp at config.target_temp_c ± noise.

Faults:
    f1   sensor bias (level reads off)
    f8   inflow valve clogged (rate -70%)
    f12  cooling fail   (temp drifts up)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("storage-tank")
class StorageTank(PhysicsBase):
    #: Wat deze module ECHT implementeert; gen-park.py leest dit uit
    #: voor park-faults.json, zodat de catalogus geen storing kan
    #: claimen die de fysica niet kent.
    FAULTS = {
        "f1": "sensorbias, het niveau leest te hoog",
        "f2": "niveaudrift, de meting loopt langzaam weg",
        "f8": "verstopte uitlaat, de tank loopt niet leeg",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)

        self.capacity_l = float(config.get("capacity_l", 30000.0))
        self.level_l = float(config.get("initial_level_l", 15000.0))
        self.draw_rate_l_min = float(config.get("draw_rate_l_min", 800.0))
        self.target_temp_c = float(config.get("target_temp_c", 4.5))
        self.ambient_temp_c = float(config.get("ambient_temp_c", 18.0))
        self.temp_c = self.target_temp_c

        # Uitgebreide procesvariabelen voor lijn Vla-B. Achter een
        # vlag: zonder vlag publiceert deze module byte-voor-byte wat
        # de bestaande scenario's al jaren zien, en dat wordt
        # gearchiveerd. selftest_regression.py bewaakt dat.
        # _inflow/_draw werden pas in step() gezet, dus read() vlak na de
        # constructor gooide een AttributeError. Een echte unit is direct
        # leesbaar; dit was een latente bug, geen testvolgorde-probleem.
        self._inflow = 0.0
        self._draw = 0.0
        self.extended_pvs = bool(config.get("extended_pvs", False))
        self.ullage_L = 0.0
        self.density_kg_L = 1.031
        self.agitation_on = 0.0

    def step(self, dt):
        sm = self.sm
        inflow = 0.0
        if sm.state == PackMLState.EXECUTE:
            inflow = sm.cur_mach_speed  # L/min
            if self.faults.is_active("f8"):
                inflow *= (1.0 - 0.7 * self.faults.magnitude("f8"))
            inflow += random.gauss(0, max(inflow * 0.01, 0.5))
        draw = self.draw_rate_l_min if sm.is_running() else 0.0
        net_lpm = inflow - draw
        self.level_l = max(0.0, min(self.capacity_l, self.level_l + net_lpm * dt / 60.0))

        # Temperature: chiller fights ambient + inflow heat
        if self.faults.is_active("f12"):
            equilibrium = self.target_temp_c + 6.0 * self.faults.magnitude("f12")
        else:
            equilibrium = self.target_temp_c
        self.temp_c += (equilibrium - self.temp_c) * 0.02 * dt
        self.temp_c += random.gauss(0, 0.04)

        self._inflow = inflow
        self._draw = draw

        if self.extended_pvs:
            self._step_extended(dt)

    def _step_extended(self, dt):
        """Ullage, dichtheid en roerwerk, alleen voor lijn Vla-B.

        Ullage is de vrije ruimte boven het product. Het is geen tweede meting
        maar een AFGELEIDE van capaciteit min niveau; hem los simuleren zou een
        tank opleveren waar de twee getallen niet bij elkaar optellen, en dat
        is het eerste wat een procestechnoloog narekent.
        """
        import random as _r
        cap = float(self.capacity_l)
        self.ullage_L = max(0.0, cap - self.level_l)
        # Dichtheid loopt licht met de temperatuur; melk is ~1,031 bij 4 graden.
        self.density_kg_L = 1.033 - 0.0004 * max(self.temp_c - 4.0, 0.0)
        self.density_kg_L += _r.gauss(0, 0.0002)
        self.agitation_on = 100.0 if self.sm.is_running() else 0.0

    def read(self):
        level = self.level_l
        if self.faults.is_active("f1"):
            level += 500.0 * self.faults.magnitude("f1")
        base = {
            "level_L": round(level, 1),
            "level_pct": round(100.0 * level / self.capacity_l, 2),
            "in_temp_C": round(self.temp_c, 2),
            "flow_L_min": round(self._inflow, 1),
            "draw_L_min": round(self._draw, 1),
        }
        if not self.extended_pvs:
            return base
        base.update({
            "ullage_L": round(self.ullage_L, 1),
            "density_kg_L": round(self.density_kg_L, 4),
            "agitation_on": round(self.agitation_on, 1),
        })
        return base
