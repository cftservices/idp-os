"""Fill line — packaging filler.

Continuous packs counter. PackML state controls the filler:

    Execute -> packs accumulate at nominal_ppm scaled by MachSpeed;
               a small fraction is rejected (out-of-fill).
    Held/Stopped -> filling pauses.

Faults:
    f13  motor slip     — throughput below setpoint
    f8   nozzle fouling  — reject rate up
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("fill-line")
class FillLine(PhysicsBase):
    #: Wat deze module echt implementeert; gen-park.py leest dit uit.
    FAULTS = {
        "f8": "verstopte vulklep, te weinig volume en meer spreiding tussen de koppen",
        "f12": "sealbalk haalt zijn temperatuur niet, lekkende naden",
        "f13": "aandrijfslip, de vuller haalt zijn snelheid niet",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.pack_size_l = float(config.get("pack_size_l", 1.0))
        self.nominal_ppm = float(config.get("nominal_ppm", 120.0))  # packs/min at design speed
        self.reject_base_pct = float(config.get("reject_base_pct", 0.4))

        self.pack_count = 0
        self.reject_count = 0

        # Uitgebreide procesvariabelen voor lijn Vla-B. Achter een vlag: het
        # bestaande DairyPlant-scenario mag hier niets van merken.
        self.extended_pvs = bool(config.get("extended_pvs", False))
        self.fill_volume_mL = 1000.0
        self.head_dev_mL = 0.0
        self.seal_temp_C = 20.0
        self.cap_torque_Ncm = 0.0
        self.film_remaining_pct = 100.0
        self.target_seal_C = float(config.get("target_seal_C", 185.0))
        self._pack_accum = 0.0
        self.packs_per_min = 0.0

    def step(self, dt):
        sm = self.sm
        if sm.state == PackMLState.EXECUTE:
            speed_factor = max(sm.cur_mach_speed / max(sm.mach_design_speed, 1.0), 0.0)
            if self.faults.is_active("f13"):
                speed_factor *= (1.0 - 0.5 * self.faults.magnitude("f13"))
            self.packs_per_min = self.nominal_ppm * speed_factor
            self._pack_accum += self.packs_per_min * dt / 60.0
            reject_pct = self.reject_base_pct
            if self.faults.is_active("f8"):
                reject_pct += 8.0 * self.faults.magnitude("f8")
            while self._pack_accum >= 1.0:
                self._pack_accum -= 1.0
                if random.random() * 100.0 < reject_pct:
                    self.reject_count += 1
                else:
                    self.pack_count += 1
        else:
            self.packs_per_min = 0.0

        if self.extended_pvs:
            self._step_extended(dt)

    def read(self):
        good = self.pack_count
        total = self.pack_count + self.reject_count
        quality_pct = 100.0 * good / total if total else 100.0
        base = {
            "pack_count": self.pack_count,
            "reject_count": self.reject_count,
            "packs_per_min": round(self.packs_per_min, 1),
            "pack_size_L": self.pack_size_l,
            "quality_pct": round(quality_pct, 2),
        }
        if not self.extended_pvs:
            return base
        base.update({
            "fill_volume_mL": round(self.fill_volume_mL, 1),
            "head_dev_mL": round(self.head_dev_mL, 2),
            "seal_temp_C": round(self.seal_temp_C, 1),
            "cap_torque_Ncm": round(self.cap_torque_Ncm, 1),
            "film_remaining_pct": round(self.film_remaining_pct, 1),
        })
        return base

    def _step_extended(self, dt):
        """Vulvolume, sealtemperatuur en filmvoorraad, alleen voor lijn Vla-B.

        head_dev_mL is de spreiding tussen de vulkoppen. Dat is de grootheid
        waar een vuller in de praktijk op wordt afgerekend: het gemiddelde kan
        keurig op 1000 mL staan terwijl een enkele kop stelselmatig te weinig
        geeft, en dan geef je gratis product weg of lever je ondermaat. Precies
        waarom een gemiddelde alleen niet genoeg is.
        """
        import random as _r
        sm = self.sm
        f8 = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
        f12 = self.faults.magnitude("f12") if self.faults.is_active("f12") else 0.0

        if sm.is_running():
            nominal = self.pack_size_l * 1000.0
            # Een verstopte vulklep geeft stelselmatig te weinig.
            self.fill_volume_mL = nominal * (1.0 - 0.03 * f8) + _r.gauss(0, 1.2)
            self.head_dev_mL = 1.5 + 9.0 * f8 + abs(_r.gauss(0, 0.4))
            want_seal = self.target_seal_C * (1.0 - 0.15 * f12)
            self.seal_temp_C += (want_seal - self.seal_temp_C) * min(0.3 * dt, 1.0)
            self.cap_torque_Ncm = 42.0 - 6.0 * f8 + _r.gauss(0, 0.8)
            # Filmrol loopt leeg met de productie; onder 10% is het bijladen.
            self.film_remaining_pct = max(
                0.0, self.film_remaining_pct - self.packs_per_min * dt / 60.0 * 0.02)
            if self.film_remaining_pct <= 0.5:
                self.film_remaining_pct = 100.0
        else:
            self.seal_temp_C += (20.0 - self.seal_temp_C) * min(0.02 * dt, 1.0)
            self.cap_torque_Ncm = max(0.0, self.cap_torque_Ncm - 20.0 * dt)
            self.head_dev_mL = 0.0

    def on_command(self, cmd, payload):
        if cmd == "ResetCounters":
            self.pack_count = 0
            self.reject_count = 0
            self._pack_accum = 0.0
            return True
        return False
