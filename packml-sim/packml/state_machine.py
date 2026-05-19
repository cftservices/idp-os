"""PackML ISA-88 state machine.

Wire-compatible with libremfg/PackML-MQTT-Simulator. The numeric codes
in PackMLState match the standard so downstream consumers (Grafana, N8N,
training material) can reuse PackML state tables without translation.

The state machine is purely declarative: callers send commands via
`command()` and tick the machine via `step(dt)`. Physics modules read
`.state` / `.unit_mode` / `.mach_speed` to decide what to produce.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Callable, Optional


class PackMLState(IntEnum):
    """ISA-88 / PackML state numeric codes (libremfg-compatible)."""

    UNDEFINED = 0
    CLEARING = 1
    STOPPED = 2
    STARTING = 3
    IDLE = 4
    SUSPENDED = 5
    EXECUTE = 6
    STOPPING = 7
    ABORTING = 8
    ABORTED = 9
    HOLDING = 10
    HELD = 11
    UNHOLDING = 12
    SUSPENDING = 13
    UNSUSPENDING = 14
    RESETTING = 15
    COMPLETING = 16
    COMPLETE = 17


_STATE_NAME = {
    PackMLState.UNDEFINED: "Undefined",
    PackMLState.CLEARING: "Clearing",
    PackMLState.STOPPED: "Stopped",
    PackMLState.STARTING: "Starting",
    PackMLState.IDLE: "Idle",
    PackMLState.SUSPENDED: "Suspended",
    PackMLState.EXECUTE: "Execute",
    PackMLState.STOPPING: "Stopping",
    PackMLState.ABORTING: "Aborting",
    PackMLState.ABORTED: "Aborted",
    PackMLState.HOLDING: "Holding",
    PackMLState.HELD: "Held",
    PackMLState.UNHOLDING: "Unholding",
    PackMLState.SUSPENDING: "Suspending",
    PackMLState.UNSUSPENDING: "Unsuspending",
    PackMLState.RESETTING: "Resetting",
    PackMLState.COMPLETING: "Completing",
    PackMLState.COMPLETE: "Complete",
}


class UnitMode(IntEnum):
    PRODUCTION = 1
    MAINTENANCE = 2
    MANUAL = 3


_MODE_NAME = {
    UnitMode.PRODUCTION: "Production",
    UnitMode.MAINTENANCE: "Maintenance",
    UnitMode.MANUAL: "Manual",
}


# Transient → terminal target state. After `transient_duration_s`
# seconds in the transient state, the SM auto-advances to the terminal.
_TRANSIENT_TO_TERMINAL = {
    PackMLState.RESETTING: PackMLState.IDLE,
    PackMLState.STARTING: PackMLState.EXECUTE,
    PackMLState.HOLDING: PackMLState.HELD,
    PackMLState.UNHOLDING: PackMLState.EXECUTE,
    PackMLState.SUSPENDING: PackMLState.SUSPENDED,
    PackMLState.UNSUSPENDING: PackMLState.EXECUTE,
    PackMLState.STOPPING: PackMLState.STOPPED,
    PackMLState.ABORTING: PackMLState.ABORTED,
    PackMLState.CLEARING: PackMLState.STOPPED,
    PackMLState.COMPLETING: PackMLState.COMPLETE,
}


class PackMLStateMachine:
    """ISA-88 PackML state machine with command queue and transient timers.

    Commands accepted at any state — invalid ones are silently ignored
    (matches PackML-spec behaviour). Use `is_running()` to check if the
    underlying physics should be active.

    The optional `on_state_change` hook fires after every transition,
    useful for logging or external state-mirror.
    """

    def __init__(
        self,
        unit_mode: UnitMode = UnitMode.PRODUCTION,
        transient_duration_s: float = 5.0,
        on_state_change: Optional[Callable[[PackMLState, PackMLState], None]] = None,
    ) -> None:
        self.state: PackMLState = PackMLState.STOPPED
        self.unit_mode: UnitMode = unit_mode
        self.transient_duration_s = transient_duration_s
        self._timer_s: float = 0.0
        self._on_state_change = on_state_change

        # Setpoints — physics reads these
        self.mach_speed: float = 0.0  # current target setpoint
        self.mach_design_speed: float = 120.0
        self.cur_mach_speed: float = 0.0  # actual achieved speed (physics writes)

    # ------------------------------------------------------------------ commands

    def command(self, cmd: str) -> bool:
        """Process a PackML command. Returns True if accepted."""
        cmd = cmd.lower()
        s = self.state

        if cmd == "reset":
            if s in (PackMLState.STOPPED, PackMLState.COMPLETE):
                return self._transition(PackMLState.RESETTING)
        elif cmd == "start":
            if s == PackMLState.IDLE:
                return self._transition(PackMLState.STARTING)
        elif cmd == "hold":
            if s == PackMLState.EXECUTE:
                return self._transition(PackMLState.HOLDING)
        elif cmd == "unhold":
            if s == PackMLState.HELD:
                return self._transition(PackMLState.UNHOLDING)
        elif cmd == "suspend":
            if s == PackMLState.EXECUTE:
                return self._transition(PackMLState.SUSPENDING)
        elif cmd == "unsuspend":
            if s == PackMLState.SUSPENDED:
                return self._transition(PackMLState.UNSUSPENDING)
        elif cmd == "stop":
            if s not in (
                PackMLState.STOPPED,
                PackMLState.STOPPING,
                PackMLState.ABORTING,
                PackMLState.ABORTED,
            ):
                return self._transition(PackMLState.STOPPING)
        elif cmd == "abort":
            if s != PackMLState.ABORTED:
                return self._transition(PackMLState.ABORTING)
        elif cmd == "clear":
            if s == PackMLState.ABORTED:
                return self._transition(PackMLState.CLEARING)
        return False

    def set_unit_mode(self, mode: UnitMode) -> None:
        self.unit_mode = mode

    def set_mach_speed(self, speed: float) -> None:
        self.mach_speed = max(0.0, min(speed, self.mach_design_speed))

    # ----------------------------------------------------------------- step/tick

    def step(self, dt: float) -> None:
        """Advance the state machine by `dt` seconds.

        Handles transient timers (Starting → Execute, etc.) and ramps
        cur_mach_speed toward mach_speed during ramp-up/down phases.
        """
        # Transient state expiry
        if self.state in _TRANSIENT_TO_TERMINAL:
            self._timer_s += dt
            if self._timer_s >= self.transient_duration_s:
                self._transition(_TRANSIENT_TO_TERMINAL[self.state])

        # Pump-speed ramping (matches PackML+Sim3Tanks original behaviour)
        target = self._target_cur_speed()
        if self.cur_mach_speed < target:
            ramp = self.mach_design_speed / max(self.transient_duration_s, 0.1)
            self.cur_mach_speed = min(target, self.cur_mach_speed + ramp * dt)
        elif self.cur_mach_speed > target:
            ramp = self.mach_design_speed / max(self.transient_duration_s, 0.1)
            self.cur_mach_speed = max(target, self.cur_mach_speed - ramp * dt)

    def _target_cur_speed(self) -> float:
        if self.state == PackMLState.EXECUTE:
            return self.mach_speed
        if self.state in (PackMLState.STARTING, PackMLState.UNHOLDING, PackMLState.UNSUSPENDING):
            return self.mach_speed  # ramping up to setpoint
        return 0.0  # any non-producing state

    # -------------------------------------------------------------------- helpers

    def is_running(self) -> bool:
        """True when underlying physics should be active (producing output)."""
        return self.state in (
            PackMLState.STARTING,
            PackMLState.EXECUTE,
            PackMLState.UNHOLDING,
            PackMLState.UNSUSPENDING,
            PackMLState.HOLDING,
            PackMLState.SUSPENDING,
            PackMLState.COMPLETING,
        )

    def state_name(self) -> str:
        return _STATE_NAME.get(self.state, "Undefined")

    def unit_mode_name(self) -> str:
        return _MODE_NAME.get(self.unit_mode, "Production")

    # -------------------------------------------------------------------- internal

    def _transition(self, target: PackMLState) -> bool:
        if target == self.state:
            return False
        previous = self.state
        self.state = target
        self._timer_s = 0.0
        if self._on_state_change is not None:
            self._on_state_change(previous, target)
        return True
