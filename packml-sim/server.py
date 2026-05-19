"""packml-sim entrypoint.

One container = one piece of equipment. Behaviour is driven by:
  - UNIT_CONFIG env var: path to YAML inside the container
    (mounted from packml-sim/scenarios/<scenario>/<unit>.yaml)
  - MQTT_HOST / MQTT_PORT / MQTT_USERNAME / MQTT_PASSWORD: broker connection

The YAML defines the equipment type + UNS coordinates + physics params.
See packml-sim/scenarios/*/ for examples.

Lifecycle:
  1. Read YAML + env
  2. Build PackMLStateMachine + FaultInjector + Physics + MQTT publisher
  3. Loop: every SIM_STEP advance state machine + physics
           every PUBLISH_INTERVAL publish PackML status + physics tags
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import yaml

from packml import PackMLStateMachine, FaultInjector, UnitMode
from physics import PhysicsRegistry  # eager-import populates registry
from mqtt import MQTTPublisher, TopicBuilder


log = logging.getLogger("packml-sim")


def _setup_logging():
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_config() -> dict:
    path = os.environ.get("UNIT_CONFIG")
    if not path:
        log.error("UNIT_CONFIG env var not set")
        sys.exit(2)
    p = Path(path)
    if not p.exists():
        log.error("UNIT_CONFIG file not found: %s", p)
        sys.exit(2)
    with p.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    log.info("Loaded unit config %s", p)
    return cfg


def main() -> int:
    _setup_logging()
    cfg = _load_config()

    # Required top-level keys
    try:
        unit_type = cfg["type"]
        site = cfg["site"]
        line = cfg["line"]
        area = cfg["area"]
        equipment = cfg["equipment"]
    except KeyError as e:
        log.error("Missing required key in UNIT_CONFIG: %s", e)
        return 2

    physics_cfg = cfg.get("physics", {})
    pack_cfg = cfg.get("packml", {})
    publish_interval = float(cfg.get("publish_interval_s", os.environ.get("PUBLISH_INTERVAL", 1.0)))
    sim_step = float(cfg.get("sim_step_s", os.environ.get("SIM_STEP", 0.2)))
    initial_mach_speed = float(pack_cfg.get("initial_mach_speed", 100.0))
    design_speed = float(pack_cfg.get("mach_design_speed", 120.0))
    transient_s = float(pack_cfg.get("transient_duration_s", 5.0))
    auto_start = bool(pack_cfg.get("auto_start", True))

    client_id = f"packml-{site}-{line}-{area}-{equipment}".replace("/", "-")
    host = os.environ.get("MQTT_HOST", "monstermq")
    port = int(os.environ.get("MQTT_PORT", 1883))
    username = os.environ.get("MQTT_USERNAME") or None
    password = os.environ.get("MQTT_PASSWORD") or None

    topics = TopicBuilder(site=site, line=line, area=area, equipment=equipment)

    state_machine = PackMLStateMachine(
        unit_mode=UnitMode.PRODUCTION,
        transient_duration_s=transient_s,
        on_state_change=lambda old, new: log.info("PackML %s → %s", old.name, new.name),
    )
    state_machine.mach_design_speed = design_speed
    state_machine.set_mach_speed(initial_mach_speed)

    faults = FaultInjector()

    physics_cls = PhysicsRegistry.get(unit_type)
    physics = physics_cls(physics_cfg, state_machine, faults)

    def handle_command(cmd: str, payload: str) -> None:
        log.debug("CMD %s = %r", cmd, payload)
        # PackML standard
        if cmd in ("Start", "Stop", "Reset", "Hold", "Unhold", "Suspend",
                   "Unsuspend", "Abort", "Clear"):
            state_machine.command(cmd.lower())
            return
        if cmd == "MachSpeed":
            try:
                state_machine.set_mach_speed(float(payload))
            except ValueError:
                log.warning("Bad MachSpeed payload %r", payload)
            return
        if cmd == "UnitMode":
            try:
                mode = UnitMode(int(payload))
                state_machine.set_unit_mode(mode)
            except (ValueError, KeyError):
                log.warning("Bad UnitMode payload %r", payload)
            return
        if cmd == "Fault/Inject":
            try:
                data = json.loads(payload)
                faults.inject(str(data.get("fault", "")), float(data.get("magnitude", 1.0)))
            except (ValueError, TypeError) as e:
                log.warning("Bad Fault/Inject payload %r (%s)", payload, e)
            return
        if cmd == "Fault/Clear":
            payload_s = payload.strip()
            if payload_s and payload_s not in ("1", "all", "*"):
                faults.clear(payload_s)
            else:
                faults.clear()
            return
        # Fall through to physics module
        if not physics.on_command(cmd, payload):
            log.debug("Unhandled command %s", cmd)

    pub = MQTTPublisher(
        host=host,
        port=port,
        client_id=client_id,
        topic_builder=topics,
        username=username,
        password=password,
        on_command=handle_command,
    )
    pub.start()

    stop_flag = {"stop": False}

    def _on_signal(signum, frame):
        log.info("Signal %s — shutting down", signum)
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    if auto_start:
        # Boot sequence: Stopped -> Reset -> Idle -> Start -> Execute
        state_machine.command("reset")

    log.info(
        "packml-sim ready: type=%s topic=%s mach_speed=%.1f",
        unit_type, topics.base, initial_mach_speed,
    )

    last_publish = 0.0
    last_tick = time.monotonic()

    while not stop_flag["stop"]:
        now = time.monotonic()
        dt = now - last_tick
        last_tick = now

        state_machine.step(dt)
        faults.step(dt)
        physics.step(dt)

        # Auto-start sequence: once Idle is reached, transition to Execute
        if auto_start:
            if state_machine.state.name == "IDLE":
                state_machine.command("start")

        if now - last_publish >= publish_interval:
            _publish_snapshot(pub, state_machine, physics, faults)
            last_publish = now

        time.sleep(max(0.01, sim_step - (time.monotonic() - now)))

    # Final clean snapshot before exit
    state_machine.command("stop")
    _publish_snapshot(pub, state_machine, physics, faults)
    pub.stop()
    return 0


def _publish_snapshot(pub, sm, physics, faults):
    pub.publish("StateCurrent", int(sm.state), retain=True)
    pub.publish("StateCurrentStr", sm.state_name(), retain=True)
    pub.publish("UnitMode", int(sm.unit_mode), retain=True)
    pub.publish("UnitModeStr", sm.unit_mode_name(), retain=True)
    pub.publish("MachSpeed", float(sm.mach_speed))
    pub.publish("CurMachSpeed", float(sm.cur_mach_speed))
    pub.publish("MachDesignSpeed", float(sm.mach_design_speed), retain=True)

    active = faults.snapshot()
    pub.publish("ActiveFaults", active)
    pub.publish("FaultCount", len(active))

    for tag, value in physics.read().items():
        pub.publish(tag, value)


if __name__ == "__main__":
    sys.exit(main())
