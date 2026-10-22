"""Plate cooler — product/glycol plate heat exchanger.

The only new physics module in the park. Deliberately NOT spiral_cooler: that
one models a bakery belt cooler (belt speed, ambient temperature, product-out
temperature). A dairy product cooler is a plate heat exchanger with two fluid
circuits and an approach temperature. Reusing the belt cooler would produce
numbers that look credible and that a process engineer spots immediately.

Two circuits, one exchanger:

    product   in  ~88 C  ->  out ~22 C     (the vla, coming off the cook)
    glycol    in  ~2 C   ->  out ~8 C      (the coolant, counter-current)

The approach temperature (product out minus glycol in) is the honest measure of
exchanger health. It rises with fouling long before the outlet temperature
misses its setpoint, because the control loop compensates first by pulling more
glycol. Same lesson as the pasteurizer and the preheater: the earliest signal is
rarely the one the alarm sits on.

Faults:
    f8   plate fouling      (heat transfer drops, approach and dp rise)
    f12  glycol supply loss (coolant inlet warms, outlet cannot reach target)
    f1   sensor bias        (product outlet reads cold, so it looks fine)
"""

from __future__ import annotations

import random

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("plate-cooler")
class PlateCooler(PhysicsBase):
    #: Wat deze module ECHT implementeert; gen-park.py leest dit uit voor
    #: park-faults.json, zodat de catalogus geen storing kan claimen die de
    #: fysica niet kent.
    FAULTS = {
        "f8": "plaatvervuiling, de warmteoverdracht loopt terug",
        "f12": "glycoltoevoer valt weg, de koeling haalt zijn target niet",
        "f1": "sensorbias, de uittredetemperatuur leest te koud",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.target_out_c = float(config.get("target_out_c", 22.0))
        self.prod_in_nominal_c = float(config.get("prod_in_c", 88.0))
        self.glycol_in_c_nominal = float(config.get("glycol_in_c", 2.0))
        self.ua_nominal = float(config.get("ua_kw_per_k", 34.0))
        self.throughput_ref_L = float(config.get("throughput_ref_L", 5000.0))

        self.prod_in_C = 20.0
        self.prod_out_C = 20.0
        self.glycol_in_C = self.glycol_in_c_nominal
        self.glycol_out_C = self.glycol_in_c_nominal
        self.flow_L_min = 0.0
        self.glycol_flow_L_min = 0.0
        self.approach_dT_C = 0.0
        self.dp_bar = 0.0

        # Het park gebruikt deze module altijd volledig; de vlag staat er voor
        # symmetrie met de andere modules en voor de regressietest.
        self.extended_pvs = bool(config.get("extended_pvs", True))

    def step(self, dt):
        sm = self.sm
        f8 = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
        f12 = self.faults.magnitude("f12") if self.faults.is_active("f12") else 0.0

        if not sm.is_running():
            for attr, rest in (("prod_in_C", 20.0), ("prod_out_C", 20.0),
                               ("glycol_out_C", self.glycol_in_c_nominal)):
                setattr(self, attr,
                        getattr(self, attr) + (rest - getattr(self, attr))
                        * min(0.01 * dt, 1.0))
            self.flow_L_min = max(0.0, self.flow_L_min - 30.0 * dt)
            self.glycol_flow_L_min = max(0.0, self.glycol_flow_L_min - 20.0 * dt)
            self.dp_bar = max(0.0, self.dp_bar - 0.2 * dt)
            self.approach_dT_C = max(0.0, self.prod_out_C - self.glycol_in_C)
            return

        # Productzijde: debiet volgt de machinesnelheid, met de doorzet uit de
        # monoliet als bovengrens.
        want_flow = min(sm.cur_mach_speed * 7.0, self.throughput_ref_L / 6.0)
        self.flow_L_min += (want_flow - self.flow_L_min) * min(0.3 * dt, 1.0)

        self.prod_in_C += (self.prod_in_nominal_c - self.prod_in_C) * min(0.08 * dt, 1.0)
        self.prod_in_C += random.gauss(0, 0.05)

        # Glycolzijde. Bij verlies van toevoer komt het koudemiddel warmer aan
        # EN stroomt er minder, en die twee samen halen de koeling onderuit.
        self.glycol_in_C += ((self.glycol_in_c_nominal + 14.0 * f12) - self.glycol_in_C) \
            * min(0.05 * dt, 1.0)

        # Vervuiling verlaagt UA. De regeling compenseert eerst met meer glycol,
        # en pas als die op zijn maximum zit zakt de koeling echt weg. Daarom
        # zie je vervuiling eerder in het glycoldebiet en de approach dan in de
        # uittredetemperatuur.
        ua = max(self.ua_nominal * (1.0 - 0.7 * f8), 1.0)
        need = max(0.0, self.prod_in_C - self.target_out_c)
        want_glycol = min(900.0, need * self.flow_L_min / max(ua, 1.0) * 1.8)
        want_glycol *= (1.0 - 0.7 * f12)
        self.glycol_flow_L_min += (want_glycol - self.glycol_flow_L_min) \
            * min(0.25 * dt, 1.0)

        # Bereikbare uittredetemperatuur uit een NTU-achtige benadering: hoe
        # minder overdracht en hoe minder koudemiddel, hoe dichter het product
        # bij zijn intredetemperatuur blijft.
        capacity = ua * max(self.glycol_flow_L_min, 1.0)
        demand = max(self.flow_L_min, 1.0) * max(need, 0.1)
        eff = max(0.05, min(0.98, capacity / (capacity + demand * 6.0)))
        reachable = self.prod_in_C - (self.prod_in_C - self.glycol_in_C) * eff
        target_out = max(self.target_out_c, reachable)
        self.prod_out_C += (target_out - self.prod_out_C) * min(0.12 * dt, 1.0)
        self.prod_out_C += random.gauss(0, 0.04)

        # Warmte die het product verliest komt in de glycol terecht.
        duty = max(0.0, self.prod_in_C - self.prod_out_C) * self.flow_L_min
        rise = duty / max(self.glycol_flow_L_min, 1.0) * 0.9
        self.glycol_out_C = self.glycol_in_C + min(rise, 18.0)

        self.approach_dT_C = max(0.0, self.prod_out_C - self.glycol_in_C)
        # Drukval loopt op met debiet en met aanslag op de platen.
        self.dp_bar = (0.25 + 0.0012 * self.flow_L_min) * (1.0 + 2.6 * f8)

    def read(self):
        out_reading = self.prod_out_C
        if self.faults.is_active("f1"):
            # Te koud lezen is de gevaarlijke kant op: de batch lijkt goed
            # gekoeld terwijl hij te warm de vuller in gaat.
            out_reading -= 2.5 * self.faults.magnitude("f1")
        return {
            "prod_in_C": round(self.prod_in_C, 2),
            "prod_out_C": round(out_reading, 2),
            "glycol_in_C": round(self.glycol_in_C, 2),
            "glycol_out_C": round(self.glycol_out_C, 2),
            "flow_L_min": round(self.flow_L_min, 1),
            "glycol_flow_L_min": round(self.glycol_flow_L_min, 1),
            "approach_dT_C": round(self.approach_dT_C, 2),
            "dp_bar": round(self.dp_bar, 3),
        }
