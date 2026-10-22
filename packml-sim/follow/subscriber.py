"""Laat een parkmachine meelopen met de batch die op de monoliet draait.

Zonder dit zouden lijn Vla en lijn Vla-B twee losse fysica-engines zijn die
toevallig naast elkaar draaien. Dan bewijst een cross-check niets: twee
simulaties verschillen, en dat is geen meetverschil maar een modelverschil.

Met dit erin is er één batch. De monoliet is de bron van waarheid voor de
batchtoestand en voor de procesgrootheden; elke parkmachine gebruikt die als
STUURWAARDE en haalt hem met zijn eigen traagheid, ruis en storingsgedrag.
Zonder storing lopen park en monoliet binnen de tolerantie gelijk. Met een
storing lopen ze uiteen, en dan is de afwijking echt door die storing
veroorzaakt. Dat is wat XC-COOK-TEMP meet.

Twee dingen die dit bestand met opzet NIET doet:

  1. Het schrijft niets terug naar de monoliet. De monoliet weet niet dat het
     park bestaat, en dat is de enige reden dat het park de bestaande milkdemo
     niet kan breken.
  2. Het doet niet alsof. Valt de monoliet stil, dan schakelt de machine na
     `after_silence_s` naar vrijloop EN zegt dat op een eigen DataQuality-topic.
     Stil doorgaan met verzonnen waarden terwijl je zegt dat je meeloopt, is
     precies de leugen waar deze demo tegen is.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from packml import PackMLState

log = logging.getLogger("packml-sim.follow")

MODE_FOLLOW = "follow"
MODE_FREE = "free"


def _coerce(payload):
    """UNS-payload -> scalar. Contract is {value,unit,ts,quality}, maar een kale
    scalar moet ook werken: de monoliet publiceert niet alles even net."""
    if payload is None:
        return None
    s = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
    s = s.strip()
    if not s:
        return None
    if s[:1] in "{[":
        try:
            d = json.loads(s)
        except ValueError:
            return None
        if isinstance(d, dict):
            v = d.get("value", d.get("v"))
            return v
        return d
    try:
        return float(s)
    except ValueError:
        return s


class BatchFollower:
    """Volgt de batchtoestand en de stuurwaarden van de monoliet."""

    def __init__(self, follow_cfg, physics, state_machine, health=None):
        cfg = follow_cfg or {}
        self.enabled = bool(cfg) and cfg.get("mode", MODE_FOLLOW) == MODE_FOLLOW
        self.physics = physics
        self.sm = state_machine
        self.health = health

        self.batch_state_topic = cfg.get("batch_state_topic")
        self.batch_id_topic = cfg.get("batch_id_topic")
        self.active_phases = set(cfg.get("active_phases") or [])
        self.drivers = {d["topic"]: d for d in (cfg.get("drivers") or [])}

        fb = cfg.get("fallback") or {}
        self.silence_limit_s = float(fb.get("after_silence_s", 90))
        self.announce_topic = fb.get("announce_topic")

        self._lock = threading.Lock()
        self._last_rx = 0.0
        self._phase = None
        self._batch_id = None
        self._mode = MODE_FOLLOW if self.enabled else MODE_FREE
        self._announced = None
        self._prev_phase = None

    # ----------------------------------------------------------------- topics

    def topics(self):
        t = list(self.drivers.keys())
        if self.batch_state_topic:
            t.append(self.batch_state_topic)
        if self.batch_id_topic:
            t.append(self.batch_id_topic)
        return t

    # ---------------------------------------------------------------- inkomend

    def on_message(self, topic, payload):
        if not self.enabled:
            return
        value = _coerce(payload)
        if value is None:
            return
        with self._lock:
            self._last_rx = time.monotonic()
            if topic == self.batch_state_topic:
                self._phase = str(value)
                return
            if topic == self.batch_id_topic:
                self._batch_id = str(value)
                return
            d = self.drivers.get(topic)
            if not d:
                return
            target = d.get("target", "")
            if not target.startswith("physics."):
                return
            key = target.split(".", 1)[1]
            try:
                setattr(self.physics, key, float(value))
            except (TypeError, ValueError):
                log.debug("driver %s: niet-numerieke waarde %r genegeerd", topic, value)

    # -------------------------------------------------------------------- step

    def step(self, dt):
        """Stuurt de toestandsmachine op basis van de batchfase."""
        if not self.enabled:
            return
        with self._lock:
            silent_for = time.monotonic() - self._last_rx if self._last_rx else 1e9
            phase = self._phase

        mode = MODE_FREE if silent_for > self.silence_limit_s else MODE_FOLLOW
        if mode != self._mode:
            self._mode = mode
            if mode == MODE_FREE:
                log.warning("monoliet zwijgt %.0fs: vrijloop. Dit wordt gemeld op %s",
                            silent_for, self.announce_topic)
            else:
                log.info("monoliet weer hoorbaar: volgen hervat")
        if mode == MODE_FREE or phase is None:
            return

        # De machine draait alleen als de batch in een fase zit waar hij aan
        # meedoet. Een pasteur die staat te koken terwijl de batch nog doseert,
        # is precies het soort onzin dat een procestechnoloog eruit haalt.
        #
        # Vergelijken tegen de ENUM, niet tegen state_name(). Die laatste geeft
        # weergavenamen terug ("Idle", niet "IDLE"), dus een vergelijking op
        # tekst matcht nooit en de machine zou stilstaan zonder één foutmelding.
        want_run = phase in self.active_phases
        state = self.sm.state
        if want_run:
            # ABORTED vraagt eerst CLEAR: vanuit ABORTED is RESET geen geldige
            # PackML-overgang. Zonder deze stap blijft een machine die een keer
            # is getript (bijvoorbeeld een pasteur die onder de holdtemperatuur
            # divert) voorgoed staan, en dan lijkt de rest van de demo gewoon
            # stil te vallen zonder dat iets een fout meldt.
            if state == PackMLState.ABORTED:
                self.sm.command("clear")
            elif state in (PackMLState.STOPPED, PackMLState.COMPLETE):
                self.sm.command("reset")
            elif state == PackMLState.IDLE:
                self.sm.command("start")
        elif state == PackMLState.EXECUTE:
            self.sm.command("stop")

        # Eén batch die de actieve fase verlaat is één cyclus voor de CIP-teller.
        if self.health is not None and self._prev_phase != phase:
            if self._prev_phase in self.active_phases and phase not in self.active_phases:
                self.health.note_cycle()
        self._prev_phase = phase

    # ------------------------------------------------------------------ status

    @property
    def mode(self):
        return self._mode

    @property
    def phase(self):
        return self._phase

    @property
    def batch_id(self):
        return self._batch_id

    def announcement(self):
        """(topic, payload) als de modus is veranderd, anders None."""
        if not self.announce_topic:
            return None
        if self._announced == self._mode:
            return None
        self._announced = self._mode
        return self.announce_topic, json.dumps({
            "value": self._mode,
            "unit": "",
            "quality": "GOOD" if self._mode == MODE_FOLLOW else "UNCERTAIN",
            "note": ("volgt de batch op lijn Vla"
                     if self._mode == MODE_FOLLOW
                     else "monoliet stil: vrijloop, waarden zijn niet aan een batch gekoppeld"),
        })
