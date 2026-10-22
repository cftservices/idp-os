"""De simlus voor een machine van lijn Vla-B.

Verschilt op drie punten van de klassieke packml-sim-lus (server.py):

  1. Hij publiceert niet de sleutels van physics.read(), maar exact de 30 slots
     van het sjabloon, samengesteld uit toestandsmachine, fysica en het
     gezondheidsmodel.
  2. Hij publiceert ze onder de NATIVE naam op de raw-root, verminkt volgens het
     leveranciersprofiel. Niets hiervan is canoniek; dat maakt de conditioner.
  3. Hij loopt MEE met de batch op de monoliet in plaats van autonoom te draaien.

Gelaagde sampling is geen optimalisatie maar een voorwaarde. Naief alle 360
parktags op 1 Hz is ~360 msg/s ruw, en met vier processen die elk bericht
aanraken loopt dat op 2 vCPU vast. Met fast/normal/slow/onchange komt het uit op
~6 msg/s per machine.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import signal as _signal
import sys
import time
from pathlib import Path

import yaml

from packml import PackMLStateMachine, FaultInjector, UnitMode
from physics import PhysicsRegistry  # noqa: F401  (eager import vult de registry)
from physics import PhysicsRegistry as _Reg
from mqtt import MQTTPublisher, TopicBuilder
from signals import HealthModel, SignalSet
from vendor import VendorDistorter
from follow import BatchFollower

log = logging.getLogger("packml-sim.park")


def load_config(path=None):
    path = path or os.environ.get("UNIT_CONFIG")
    if not path:
        raise SystemExit("UNIT_CONFIG niet gezet")
    p = Path(path)
    if not p.exists():
        raise SystemExit("UNIT_CONFIG bestaat niet: %s" % p)
    with p.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class ParkMachine:
    """Alles wat een parkmachine is, zonder transport. Zo blijft hij testbaar."""

    def __init__(self, cfg, rng=None):
        self.cfg = cfg
        self.unit_id = cfg.get("unit_id") or cfg["equipment"]
        self.raw_root = cfg.get("raw_root", "raw/vla-park/%s" % self.unit_id)

        pack = cfg.get("packml") or {}
        self.sm = PackMLStateMachine(
            unit_mode=UnitMode.PRODUCTION,
            transient_duration_s=float(pack.get("transient_duration_s", 5.0)))
        self.sm.mach_design_speed = float(pack.get("mach_design_speed", 120.0))
        self.sm.set_mach_speed(float(pack.get("initial_mach_speed", 100.0)))

        self.faults = FaultInjector()

        phys_cfg = dict(cfg.get("physics") or {})
        phys_cfg["extended_pvs"] = bool(cfg.get("extended_pvs", False))
        self.physics = _Reg.get(cfg["type"])(phys_cfg, self.sm, self.faults)

        self.health = HealthModel(phys_cfg, self.sm, self.faults)
        self.signals = SignalSet(cfg, self.sm, self.physics, self.health, self.faults)
        self.distorter = VendorDistorter(cfg, self.faults, rng=rng)
        self.follower = BatchFollower(cfg.get("follow"), self.physics,
                                      self.sm, self.health)

        self.slot_by_name = {s["name"]: s for s in cfg["signals"]}
        classes = (cfg.get("sim") or {}).get("sampling_classes") or {}
        self.intervals = {}
        for s in cfg["signals"]:
            ms = classes.get(s["sampling_class"])
            self.intervals[s["name"]] = (float(ms) / 1000.0) if ms else None
        self.keepalive_s = float((cfg.get("sim") or {}).get("keepalive_s", 60))
        self.q_suffix = (cfg.get("quality_companion") or {}).get("suffix")
        self.q_interval = 30.0

        self._last_pub = {}
        self._last_val = {}
        self._last_q = 0.0

    # ------------------------------------------------------------------ step

    def step(self, dt):
        self.sm.step(dt)
        self.faults.step(dt)
        self.physics.step(dt)
        self.health.step(dt)
        self.follower.step(dt)

    # --------------------------------------------------------------- publish

    def due(self, now):
        """Welke signalen mogen nu de draad op? Gelaagd, niet allemaal tegelijk."""
        out = []
        canonical = self.signals.read()
        for name, value in canonical.items():
            iv = self.intervals.get(name)
            last_t = self._last_pub.get(name, -1e9)
            if iv is None:
                # onchange: alleen bij verandering, plus een keepalive zodat
                # stilte niet met stale wordt verward.
                if value != self._last_val.get(name, object()) or \
                        (now - last_t) >= self.keepalive_s:
                    out.append((name, value))
            elif (now - last_t) >= iv:
                out.append((name, value))
        return out, canonical

    def emit(self, now):
        """[(topic, payload)] voor deze tick, inclusief de .Q-companions."""
        due, _ = self.due(now)
        msgs = []
        for name, value in due:
            res = self.distorter.to_native(name, value)
            slot = self.slot_by_name[name]
            msgs.append(("%s/%s" % (self.raw_root, slot["native_name"]),
                         self._payload(res, slot)))
            self._last_pub[name] = now
            self._last_val[name] = value

        # De .Q-companions krijgen een HARTSLAG en geen onchange. Een constant
        # qualityword zou bij onchange precies een keer publiceren; een
        # conditioner die daarna herstart heeft dan nooit kwaliteit gezien en
        # zet alles op UNCERTAIN. Dat is in de vorige poging echt gebeurd.
        if self.q_suffix and (now - self._last_q) >= self.q_interval:
            self._last_q = now
            for slot in self.cfg["signals"]:
                res = self.distorter.to_native(slot["name"],
                                               self._last_val.get(slot["name"], 0))
                if res.quality_raw is not None:
                    msgs.append(("%s/%s%s" % (self.raw_root, slot["native_name"],
                                              self.q_suffix),
                                 str(res.quality_raw)))
        return msgs

    @staticmethod
    def _payload(res, slot):
        """De raw-payload. BEWUST geen contract: MonsterMQ's OPC-UA-client geeft
        {value,timestamp,status}, deze fallback iets anders, en vendor-d stuurt
        strings. Dat de vormen niet op elkaar aansluiten is precies waarom de
        Condition-stap structureel verplicht is en geen luxe."""
        d = {"v": res.value}
        if res.timestamp is not None:
            d["ts"] = res.timestamp
        if res.quality_raw is not None and \
                (slot.get("distort") or {}).get("quality_source") == "opcua-statuscode":
            d["status"] = res.quality_raw
        return json.dumps(d)

    # ---------------------------------------------------------------- control

    def inject_fault(self, fault_id, magnitude=1.0):
        self.faults.inject(str(fault_id), float(magnitude))

    def clear_fault(self, fault_id=None):
        self.faults.clear(fault_id or None)


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s", datefmt="%H:%M:%S")

    cfg = load_config()
    machine = ParkMachine(cfg)
    log.info("parkmachine %s: type=%s profiel=%s protocol=%s, %d signalen",
             machine.unit_id, cfg["type"], cfg.get("vendor_profile"),
             cfg.get("protocol"), len(cfg["signals"]))

    surf_kind = cfg.get("surface", "mqtt")
    surface = None
    modbus = None
    rest = None
    sqlw = None
    if surf_kind == "opcua":
        from opcua_surface import OpcUaSurface
        surface = OpcUaSurface(
            cfg,
            on_inject_fault=machine.inject_fault,
            on_clear_fault=machine.clear_fault)
        surface.start()
    elif surf_kind == "modbus":
        from modbus_surface import ModbusSurface
        modbus = ModbusSurface(cfg)
        modbus.start()
    elif surf_kind == "rest":
        from rest_surface import RestSurface
        rest = RestSurface(cfg)
        rest.start()
    elif surf_kind == "sql":
        from sql_surface import SqlSurface
        sqlw = SqlSurface(cfg)
        sqlw.start()

    # Het anti-patroon: rechtstreeks de historian in, langs bus en modellaag
    # heen. Alleen waar het model dat expliciet aanzet, en nooit naar idp_park.
    from tdengine_direct import TDengineDirect
    bypass = TDengineDirect(cfg)
    if bypass.enabled:
        log.warning("BYPASS actief: schrijft ook rechtstreeks naar %s. "
                    "Dat is met opzet het tegenvoorbeeld.", bypass.database)

    # Wie publiceert de RUWE data?
    #
    #   mqtt    de machine zelf, want er is geen connector
    #   opcua   MonsterMQ's eigen OPC-UA-client
    #   modbus  vla-park-poller
    #   rest    vla-park-poller
    #   sql     vla-park-gateway
    #
    # Publiceert de machine ook als er een connector is, dan krijgt de
    # conditioner elk punt twee keer met verschillende tijdstempels, en dat kan
    # zijn deadband niet van echte verandering onderscheiden. Daarom is dit
    # afgeleid van het oppervlak en geen losse knop; overrulen kan wel, maar
    # dan bewust.
    raw_publish = os.environ.get("RAW_PUBLISH")
    raw_publish = (surf_kind == "mqtt") if raw_publish is None \
        else raw_publish.strip().lower() in ("1", "true", "yes", "on")
    log.info("oppervlak=%s, machine publiceert raw zelf: %s",
             surf_kind, "ja" if raw_publish else "nee (een connector doet dat)")

    # Ook met een OPC-UA-oppervlak blijft de MQTT-verbinding open: de machine
    # moet de batch van de monoliet kunnen volgen en commando's kunnen ontvangen.
    topics = TopicBuilder(site=cfg["site"], line=cfg["line"],
                          area=cfg["area"], equipment=cfg["equipment"])

    def handle_command(cmd, payload):
        if cmd == "Fault/Inject":
            try:
                d = json.loads(payload)
                machine.inject_fault(d.get("fault", ""), float(d.get("magnitude", 1.0)))
            except (ValueError, TypeError) as e:
                log.warning("slechte Fault/Inject-payload %r (%s)", payload, e)
        elif cmd == "Fault/Clear":
            s = (payload or "").strip()
            machine.clear_fault(None if s in ("", "1", "all", "*") else s)

    pub = MQTTPublisher(
        host=os.environ.get("MQTT_HOST", "monstermq"),
        port=int(os.environ.get("MQTT_PORT", 1883)),
        client_id="park-%s" % machine.unit_id,
        topic_builder=topics,
        username=os.environ.get("MQTT_USERNAME") or None,
        password=os.environ.get("MQTT_PASSWORD") or None,
        on_command=handle_command,
    )
    pub.start()

    for t in machine.follower.topics():
        try:
            pub.client.subscribe(t, qos=0)
            log.info("volgt %s", t)
        except Exception as e:  # noqa: BLE001
            log.warning("kon niet abonneren op %s: %s", t, e)

    _orig_on_message = pub.client.on_message

    def _on_message(client, userdata, msg):
        machine.follower.on_message(msg.topic, msg.payload)
        if _orig_on_message:
            try:
                _orig_on_message(client, userdata, msg)
            except Exception:  # noqa: BLE001
                pass

    pub.client.on_message = _on_message

    stop = {"v": False}
    _signal.signal(_signal.SIGTERM, lambda *_: stop.update(v=True))
    _signal.signal(_signal.SIGINT, lambda *_: stop.update(v=True))

    step_s = float((cfg.get("sim") or {}).get("step_s", 0.2))
    last = time.monotonic()

    while not stop["v"]:
        now = time.monotonic()
        machine.step(now - last)
        last = now

        msgs = machine.emit(now)
        if raw_publish:
            for topic, payload in msgs:
                pub.client.publish(topic, payload, qos=0)
        if msgs and (surface is not None or modbus is not None
                     or rest is not None or sqlw is not None
                     or bypass.enabled):
            # Van het topic terug naar de NATIVE naam. Niet met rsplit("/", 1):
            # vendor-d noemt zijn punten `sensors/stateCurrent`, dus die geeft
            # `stateCurrent` en dat bestaat nergens. Het REST-oppervlak
            # antwoordde daardoor "no value yet" op elk punt en het bypass-pad
            # schreef onder namen die niemand kent. Dat viel niet op omdat de
            # andere vendor-d-machine (filler-01) via MQTT gaat en het volledige
            # topic publiceert, dus deze tak nooit raakt.
            _pfx = len(machine.raw_root) + 1
            native = [(t[_pfx:],
                       json.loads(p).get("v") if p.startswith("{") else p)
                      for t, p in msgs]
            if surface is not None:
                surface.write_many(native)
            if sqlw is not None:
                # Lokale wandkloktijd ZONDER zone, en kwaliteit als TEKST.
                # Precies zoals een legacy-export het doet, en precies wat de
                # conditioner straks moet repareren.
                _ts = (_dt.datetime.now() + _dt.timedelta(hours=1)).strftime(
                    "%Y-%m-%d %H:%M:%S")
                sqlw.write([(n, v, "Good", _ts) for n, v in native])
            if rest is not None:
                rest.publish_many(native, timestamp=int(time.time() * 1000))
            if bypass.enabled:
                bypass.write(dict(native), int(time.time() * 1000))
            if modbus is not None:
                # De Modbus-kaart is per SIGNAALNAAM, niet per native naam:
                # de native naam is daar het registeradres.
                by_native = {s["native_name"]: s["name"] for s in cfg["signals"]}
                modbus.write_signals({by_native[n]: v for n, v in native
                                      if n in by_native})

        ann = machine.follower.announcement()
        if ann:
            pub.client.publish(ann[0], ann[1], qos=0, retain=True)

        time.sleep(max(0.01, step_s - (time.monotonic() - now)))

    if surface is not None:
        surface.stop()
    if modbus is not None:
        modbus.stop()
    if rest is not None:
        rest.stop()
    if sqlw is not None:
        sqlw.stop()
    pub.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
