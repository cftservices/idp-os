"""Palletizer — groups packs into pallets (Handling Units).

Continuous accumulation. PackML state controls the robot:

    Execute -> packs accepted at rate scaled by MachSpeed; when
               packs_on_pallet reaches packs_per_pallet, a pallet is
               completed (pallet_seq ++, pallet_count ++) and a fresh
               pallet starts. Each completed pallet is a Handling Unit
               that the MES layer stamps with an SSCC.
    Held/Stopped -> palletizing pauses.

Faults:
    f8   robot jam — accept rate reduced
"""

from __future__ import annotations

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("palletizer")
class Palletizer(PhysicsBase):
    #: Wat deze module ECHT implementeert; gen-park.py leest dit uit
    #: voor park-faults.json, zodat de catalogus geen storing kan
    #: claimen die de fysica niet kent.
    FAULTS = {
        "f13": "aandrijfslip op de laagvormer",
        "f8": "grijper verliest druk, pakken glijden",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.packs_per_pallet = int(config.get("packs_per_pallet", 42))
        self.nominal_ppm = float(config.get("nominal_ppm", 120.0))

        self.packs_on_pallet = 0
        self.pallet_seq = 0
        self.pallet_count = 0
        self._accum = 0.0
        self.last_hu_complete = False

        # Uitgebreide procesvariabelen voor lijn Vla-B. Achter een
        # vlag: zonder vlag publiceert deze module byte-voor-byte wat
        # de bestaande scenario's al jaren zien, en dat wordt
        # gearchiveerd. selftest_regression.py bewaakt dat.
        self.extended_pvs = bool(config.get("extended_pvs", False))
        self.layer_index = 0
        self.gripper_press_bar = 0.0
        self.wrap_cycles = 0
        self.infeed_rate_ph = 0.0
        self._wrap_accum = 0.0

    def step(self, dt):
        sm = self.sm
        self.last_hu_complete = False
        if sm.state == PackMLState.EXECUTE:
            speed_factor = max(sm.cur_mach_speed / max(sm.mach_design_speed, 1.0), 0.0)
            if self.faults.is_active("f8"):
                speed_factor *= (1.0 - 0.6 * self.faults.magnitude("f8"))
            self._accum += self.nominal_ppm * speed_factor * dt / 60.0
            while self._accum >= 1.0:
                self._accum -= 1.0
                self.packs_on_pallet += 1
                if self.packs_on_pallet >= self.packs_per_pallet:
                    self.packs_on_pallet = 0
                    self.pallet_seq += 1
                    self.pallet_count += 1
                    self.last_hu_complete = True

        if self.extended_pvs:
            self._step_extended(dt)

    def _step_extended(self, dt):
        """Laagvorming, grijperdruk en wikkelcycli.

        layer_index wordt AFGELEID uit packs_on_pallet en de laaggrootte. Los
        bijhouden zou betekenen dat de laag en het aantal pakken uit de pas
        kunnen lopen, en dan klopt de pallet niet meer met zichzelf.
        """
        import random as _r
        f8 = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
        f13 = self.faults.magnitude("f13") if self.faults.is_active("f13") else 0.0
        per_layer = max(int(self.config.get("packs_per_layer", 8)), 1)
        self.layer_index = int(self.packs_on_pallet) // per_layer

        if self.sm.is_running():
            self.gripper_press_bar = (5.5 - 3.0 * f8) + _r.gauss(0, 0.05)
            self.infeed_rate_ph = self.sm.cur_mach_speed * 60.0 * (1.0 - 0.3 * f13)
            self._wrap_accum += dt
            if self._wrap_accum >= 45.0:
                self._wrap_accum = 0.0
                self.wrap_cycles += 1
        else:
            self.gripper_press_bar = max(0.0, self.gripper_press_bar - 2.0 * dt)
            self.infeed_rate_ph = 0.0

    def read(self):
        base = {
            "pallet_count": self.pallet_count,
            "pallet_seq": self.pallet_seq,
            "packs_on_pallet": self.packs_on_pallet,
            "packs_per_pallet": self.packs_per_pallet,
            "hu_complete_pulse": self.last_hu_complete,
        }
        if not self.extended_pvs:
            return base
        base.update({
            "layer_index": int(self.layer_index),
            "gripper_press_bar": round(self.gripper_press_bar, 2),
            "wrap_cycles": int(self.wrap_cycles),
            "infeed_rate_ph": round(self.infeed_rate_ph, 1),
        })
        return base

    def on_command(self, cmd, payload):
        if cmd == "ResetCounters":
            self.packs_on_pallet = 0
            self.pallet_seq = 0
            self.pallet_count = 0
            self._accum = 0.0
            return True
        return False
