"""Stelt de exact 30 canonieke signalen van één parkmachine samen.

De unit-YAML noemt per slot een `source`, en dit bestand lost die op tegen de
toestandsmachine, de physics-module of het gezondheidsmodel. De uitkomst is
altijd in de CANONIEKE eenheid: graden Celsius, liter per minuut, bar. Wat de
leverancier daarvan maakt gebeurt daarna, in vendor/distort.py.

Die volgorde is niet vrijblijvend. Elk getal dat een distortion-blok in gaat
staat in SI; converteren naar de leverancierseenheid en schalen naar een
register-integer komt erna, in die volgorde. Draai je dat om, dan krijg je een
plausibel getal dat een factor 3,8 fout is, en dat is precies de bugklasse waar
deze demo over gaat.

Exact 30 is een assertie, geen streven: loopt een machine uit de pas met het
sjabloon, dan klopt de Grafana-rij niet meer, klopt de alias-tabel niet meer en
komt dat pas bij de demo aan het licht.
"""

from __future__ import annotations

import logging

log = logging.getLogger("packml-sim.signals")

EXPECTED_SLOTS = 30


class MissingSource(KeyError):
    """Een slot verwijst naar een bron die de machine niet levert."""


class SignalSet:
    """De 30 canonieke waarden van één machine."""

    def __init__(self, cfg, state_machine, physics, health, faults):
        self.signals = list(cfg.get("signals") or [])
        self.sm = state_machine
        self.physics = physics
        self.health = health
        self.faults = faults
        self.unit_id = cfg.get("unit_id") or cfg.get("equipment") or "?"

        if len(self.signals) != EXPECTED_SLOTS:
            raise ValueError(
                "%s heeft %d signalen, het sjabloon eist er %d"
                % (self.unit_id, len(self.signals), EXPECTED_SLOTS))

        names = [s["name"] for s in self.signals]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError("%s heeft dubbele signaalnamen: %s" % (self.unit_id, dupes))

        natives = [s["native_name"] for s in self.signals]
        ndupes = sorted({n for n in natives if natives.count(n) > 1})
        if ndupes:
            raise ValueError(
                "%s heeft dubbele native namen: %s. Twee canonieke tags die op "
                "hetzelfde leveranciers-item landen betekent dat er een stil "
                "overschrijft, en dat merk je nooit." % (self.unit_id, ndupes))

        # Setpoints worden geschreven (OPC-UA writable, of een MQTT-command) en
        # leven dus hier, niet in de physics-module.
        self._sp = {}
        for s in self.signals:
            if s.get("writable"):
                self._sp[s["name"]] = self._default_for(s)

    # ---------------------------------------------------------------- helpers

    def _default_for(self, slot):
        src = slot.get("source", "")
        if src.startswith("physics."):
            key = src.split(".", 1)[1]
            v = getattr(self.physics, key, None)
            if v is not None and not callable(v):
                return v
        if slot["datatype"] == "String":
            return "default"
        return 0.0

    def set_setpoint(self, name, value):
        if name not in self._sp:
            return False
        self._sp[name] = value
        # Een setpoint dat de UI schrijft moet ook echt de fysica raken, anders
        # is het een knop die niets doet en dat is erger dan geen knop.
        slot = next(s for s in self.signals if s["name"] == name)
        src = slot.get("source", "")
        if src.startswith("physics."):
            key = src.split(".", 1)[1]
            if hasattr(self.physics, key):
                try:
                    setattr(self.physics, key, float(value))
                except (TypeError, ValueError):
                    setattr(self.physics, key, value)
        return True

    def _resolve(self, slot, physics_read):
        src = slot.get("source") or ""
        name = slot["name"]

        if name in self._sp:
            return self._sp[name]

        if src.startswith("state_machine."):
            key = src.split(".", 1)[1]
            sm = self.sm
            if key == "current.value":
                return int(sm.state)
            if key == "current.name":
                return sm.state_name()
            if key == "unit_mode":
                return int(sm.unit_mode)
            if key == "mach_speed":
                return float(sm.mach_speed)
            if key == "cur_mach_speed":
                return float(sm.cur_mach_speed)
            if key == "speed_setpoint_pct":
                design = float(getattr(sm, "mach_design_speed", 0.0)) or 1.0
                return round(100.0 * float(sm.mach_speed) / design, 1)
            raise MissingSource("%s: onbekende state_machine-bron %r" % (self.unit_id, key))

        if src.startswith("health."):
            key = src.split(".", 1)[1]
            hr = self.health.read()
            for cand in (key, key.replace("_a", "_A").replace("_c", "_C"),
                         {"motor_current_a": "motor_current_A",
                          "bearing_temp_c": "bearing_temp_C",
                          "energy_kwh": "energy_kWh"}.get(key, key)):
                if cand in hr:
                    return hr[cand]
            raise MissingSource("%s: onbekende health-bron %r" % (self.unit_id, key))

        if src.startswith("physics."):
            key = src.split(".", 1)[1]
            if key in physics_read:
                return physics_read[key]
            v = getattr(self.physics, key, None)
            if v is None or callable(v):
                raise MissingSource(
                    "%s: physics-module %s levert geen %r. Zet extended_pvs aan "
                    "of vul de ontbrekende procesvariabele aan; NOOIT stilzwijgend "
                    "nul teruggeven, want een nul is een meting."
                    % (self.unit_id, type(self.physics).__name__, key))
            return v

        raise MissingSource("%s: onbekend bronvoorvoegsel %r" % (self.unit_id, src))

    # ------------------------------------------------------------------- read

    def read(self) -> dict:
        """{canonieke naam: waarde in de canonieke eenheid}, exact 30 stuks."""
        pr = self.physics.read()
        out = {}
        for slot in self.signals:
            v = self._resolve(slot, pr)
            if slot.get("bool_as_pct"):
                v = 100.0 if bool(v) else 0.0
            if slot["datatype"] == "String":
                v = str(v)
            elif slot["datatype"] in ("Int32", "Int64"):
                v = int(round(float(v)))
            else:
                v = float(v)
            out[slot["name"]] = v
        if len(out) != EXPECTED_SLOTS:
            raise ValueError("%s leverde %d waarden in plaats van %d"
                             % (self.unit_id, len(out), EXPECTED_SLOTS))
        return out
