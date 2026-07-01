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
    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.packs_per_pallet = int(config.get("packs_per_pallet", 42))
        self.nominal_ppm = float(config.get("nominal_ppm", 120.0))

        self.packs_on_pallet = 0
        self.pallet_seq = 0
        self.pallet_count = 0
        self._accum = 0.0
        self.last_hu_complete = False

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

    def read(self):
        return {
            "pallet_count": self.pallet_count,
            "pallet_seq": self.pallet_seq,
            "packs_on_pallet": self.packs_on_pallet,
            "packs_per_pallet": self.packs_per_pallet,
            "hu_complete_pulse": self.last_hu_complete,
        }

    def on_command(self, cmd, payload):
        if cmd == "ResetCounters":
            self.packs_on_pallet = 0
            self.pallet_seq = 0
            self.pallet_count = 0
            self._accum = 0.0
            return True
        return False
