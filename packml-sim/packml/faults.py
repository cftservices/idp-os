"""Fault injection registry.

Each physics module declares the faults it understands as a dict
of {fault_id: human_label}. The orchestrator (server.py) accepts
`Command/Fault/Inject` MQTT payloads and forwards them to the
active physics module's `inject_fault(fault_id, magnitude)`.

Standard fault IDs (from PackML-Sim3Tanks original spec, extended
for bakery domain):

    f1   sensor bias            (process value offset)
    f2   sensor drift           (slow PV drift over time)
    f8   actuator clogged       (pump/valve flow reduction)
    f12  heating element loss   (zone temp can't reach setpoint) — bakery
    f13  belt motor slip        (belt-speed below setpoint)     — bakery
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class ActiveFault:
    fault_id: str
    magnitude: float = 1.0
    elapsed_s: float = 0.0


@dataclass
class FaultInjector:
    """Holds the set of currently-injected faults for one physics unit."""

    active: Dict[str, ActiveFault] = field(default_factory=dict)

    def inject(self, fault_id: str, magnitude: float = 1.0) -> None:
        magnitude = max(0.0, min(magnitude, 1.0))
        self.active[fault_id] = ActiveFault(fault_id=fault_id, magnitude=magnitude)

    def clear(self, fault_id: str | None = None) -> None:
        if fault_id is None:
            self.active.clear()
        else:
            self.active.pop(fault_id, None)

    def step(self, dt: float) -> None:
        for f in self.active.values():
            f.elapsed_s += dt

    def is_active(self, fault_id: str) -> bool:
        return fault_id in self.active

    def magnitude(self, fault_id: str) -> float:
        f = self.active.get(fault_id)
        return f.magnitude if f else 0.0

    def elapsed(self, fault_id: str) -> float:
        f = self.active.get(fault_id)
        return f.elapsed_s if f else 0.0

    def snapshot(self) -> Dict[str, float]:
        return {fid: f.magnitude for fid, f in self.active.items()}
