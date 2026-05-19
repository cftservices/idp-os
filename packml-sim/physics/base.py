"""PhysicsBase — common interface for all process simulators.

Each physics module models one piece of equipment (mixer, oven,
pasteurizer, ...). The orchestrator (server.py) instantiates one
physics object per container based on the unit YAML's `type:` field,
ticks it every SIM_STEP seconds, and publishes its `read()` dict
every PUBLISH_INTERVAL seconds.

Module registration is decorator-based — subclasses register a
type-name that unit YAMLs reference. See e.g. batch_mixer.py.
"""

from __future__ import annotations

from typing import Callable, Dict, Type

from packml import PackMLStateMachine, FaultInjector


class PhysicsBase:
    """Base class for an equipment-specific physics model.

    Lifecycle:
        __init__(config, state_machine, fault_injector)
        step(dt) — called every SIM_STEP seconds
        read() -> dict — called every PUBLISH_INTERVAL seconds, returns
                          {tag: value} that the publisher emits as
                          {base}/Status/{tag}.
        on_command(cmd, payload) — optional, handles equipment-specific
                                    MQTT commands beyond PackML standard.
    """

    #: Subclasses set this to a unique kebab-case identifier used in YAMLs.
    type_name: str = ""

    def __init__(
        self,
        config: dict,
        state_machine: PackMLStateMachine,
        fault_injector: FaultInjector,
    ) -> None:
        self.config = config
        self.sm = state_machine
        self.faults = fault_injector

    def step(self, dt: float) -> None:
        raise NotImplementedError

    def read(self) -> Dict[str, object]:
        raise NotImplementedError

    def on_command(self, cmd: str, payload: str) -> bool:
        """Return True if the equipment consumed the command."""
        return False


class _Registry:
    _by_type: Dict[str, Type[PhysicsBase]] = {}

    def register(self, type_name: str) -> Callable[[Type[PhysicsBase]], Type[PhysicsBase]]:
        def _decorator(cls: Type[PhysicsBase]) -> Type[PhysicsBase]:
            cls.type_name = type_name
            self._by_type[type_name] = cls
            return cls
        return _decorator

    def get(self, type_name: str) -> Type[PhysicsBase]:
        if type_name not in self._by_type:
            raise KeyError(
                f"Unknown physics type {type_name!r}. "
                f"Available: {sorted(self._by_type)}"
            )
        return self._by_type[type_name]

    def types(self) -> list[str]:
        return sorted(self._by_type)


PhysicsRegistry = _Registry()
