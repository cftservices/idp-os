# -*- coding: utf-8 -*-
"""Cross-checks: publiceer de onenigheid in plaats van hem te verbergen.

Twee bronnen die dezelfde fysieke grootheid meten zijn het nooit precies eens.
De verleiding is om er stilletjes een te kiezen, of om te middelen. Allebei fout:

    Een gemiddelde is een getal dat geen enkel instrument ooit gemeten heeft.

Wat deze module doet is de afwijking benoemen. Beide metingen, beide getallen,
het gat, de tolerantie, en welke kant OF RECORD is. Een operator die dat ziet
weet wat hij moet doen; een operator die een gemiddelde ziet weet niets.

Waarom dit met de follow-modus pas echt iets bewijst. Omdat de parkmachines
meelopen met de batch op de monoliet, meten beide kanten hetzelfde ding. Zonder
storing lopen ze binnen de tolerantie gelijk. Met een storing lopen ze uiteen,
en dan is de afwijking ECHT door die storing veroorzaakt. Zouden het twee losse
simulaties zijn, dan zou een afwijking alleen betekenen dat twee modellen
verschillen, en dat is geen bevinding maar ruis.

Eén check publiceert bewust GEEN alarm (`delta_only`). Een totalisator en een
batchdosering zijn verwante maar niet identieke grootheden; daar een
afwijkingsalarm op zetten levert een vals alarm op, en vals alarm is precies de
fout die deze demo elders bekritiseert.
"""

from __future__ import annotations

import datetime as dt


class CrossCheck:
    """Eén vergelijking tussen twee metingen van dezelfde grootheid."""

    def __init__(self, spec):
        self.id = spec["id"]
        self.title = spec.get("title", spec["id"])
        self.a_topic = spec["a"]["topic"]
        self.b_topic = spec["b"]["topic"]
        self.a_label = spec["a"].get("label", self.a_topic)
        self.b_label = spec["b"].get("label", self.b_topic)
        self.of_record = spec.get("of_record", "a")
        self.tolerance = spec.get("tolerance")
        self.unit = spec.get("unit", "")
        self.alarm = bool(spec.get("alarm", True))
        self.why = spec.get("why", "")
        self.out_root = spec["out_root"]

        self._a = None
        self._b = None
        self._a_q = None
        self._b_q = None
        self._alarm_active = False
        # Throttle. Een delta publiceren bij ELKE waarneming leverde op de VPS
        # 210 msg/s op bij vier machines, tegen een budget van ~50; bij twaalf
        # zou dat ~630 worden. De onenigheid tussen twee metingen verandert niet
        # honderd keer per seconde, dus een delta per paar seconden zegt precies
        # evenveel. Een ALARM gaat altijd meteen door: dat is de uitzondering
        # waar throttlen wel schade doet.
        self.min_interval_s = float(spec.get("min_interval_s", 5.0))
        self._last_emit = 0.0

    def observe(self, topic, value, quality=None):
        if topic == self.a_topic:
            self._a, self._a_q = value, quality
        elif topic == self.b_topic:
            self._b, self._b_q = value, quality
        else:
            return False
        return True

    def topics(self):
        return (self.a_topic, self.b_topic)

    def evaluate(self, now=None):
        """[(topic, payload-dict)] of een lege lijst als er nog niets te zeggen is."""
        if self._a is None or self._b is None:
            return []
        if not isinstance(self._a, (int, float)) or not isinstance(self._b, (int, float)):
            return []

        now = now or dt.datetime.now(dt.timezone.utc)
        ts = now.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        delta = float(self._a) - float(self._b)

        # Alarmen slaan de throttle over; delta's niet.
        stamp = now.timestamp()
        breach_now = (self.alarm and self.tolerance is not None
                      and abs(delta) > float(self.tolerance))
        throttled = (stamp - self._last_emit) < self.min_interval_s
        if throttled and breach_now == self._alarm_active:
            return []
        self._last_emit = stamp

        # Is een van beide kanten onbetrouwbaar, dan is het VERSCHIL dat ook.
        # Een afwijking melden op basis van een BAD-meting is een vals alarm.
        q = "GOOD"
        if "BAD" in (self._a_q, self._b_q):
            q = "BAD"
        elif "UNCERTAIN" in (self._a_q, self._b_q):
            q = "UNCERTAIN"

        base = "%s/%s/Status" % (self.out_root, self.id)
        out = [("%s/delta" % base, {
            "value": round(delta, 4), "unit": self.unit, "ts": ts, "quality": q,
            "a": {"label": self.a_label, "value": round(float(self._a), 4)},
            "b": {"label": self.b_label, "value": round(float(self._b), 4)},
            "of_record": self.of_record,
        })]

        if not self.alarm or self.tolerance is None:
            return out

        breach = abs(delta) > float(self.tolerance) and q != "BAD"
        if breach != self._alarm_active:
            self._alarm_active = breach
            record_label = self.a_label if self.of_record == "a" else self.b_label
            record_value = self._a if self.of_record == "a" else self._b
            out.append(("%s/alarm" % base, {
                "value": bool(breach), "unit": "", "ts": ts, "quality": q,
                "check": self.id,
                "message": (
                    "%s: %s meet %.2f %s en %s meet %.2f %s. Verschil %.2f %s, "
                    "tolerantie %.2f %s. Of record is %s (%.2f %s). "
                    "Niet gemiddeld en niet stilletjes gekozen: een gemiddelde is "
                    "een getal dat geen enkel instrument ooit gemeten heeft."
                    % (self.title, self.a_label, float(self._a), self.unit,
                       self.b_label, float(self._b), self.unit,
                       delta, self.unit, float(self.tolerance), self.unit,
                       record_label, float(record_value), self.unit)
                ) if breach else "%s: binnen tolerantie." % self.title,
                "delta": round(delta, 4),
                "tolerance": float(self.tolerance),
                "of_record": self.of_record,
            }))
        return out


def build(specs, out_root):
    return [CrossCheck(dict(s, out_root=out_root)) for s in specs]


#: Fase 1. De b-kant is de MONOLIET: die publiceert al canoniek op
#: DairyWorks/Vla/#, en de parkmachine volgt dezelfde batch. Daardoor is dit
#: een echte vergelijking van twee metingen van hetzelfde ding, waarvan er een
#: door een vies leverancierseiland is gekomen, en niet een vergelijking van
#: twee simulaties.
#:
#: In fase 3 komt preheater-01 erbij en verschuift de b-kant daarheen; deze
#: check blijft dan als derde staan, want de monoliet blijft draaien.
DEFAULT_CHECKS = [
    {
        "id": "cross-check-01",
        "title": "Kooktemperatuur: pasteurisatiedossier tegen procesbeeld",
        "unit": "°C",
        "tolerance": 1.5,
        "of_record": "a",
        "a": {"topic": "DairyWorks/Vla-B/Cook/pasteuriser-01/Status/hold_temp_C",
              "label": "pasteuriser-01 holdbuis (of record)"},
        "b": {"topic": "DairyWorks/Vla/Cook/cook-unit-01/Status/temp_C",
              "label": "cook-unit-01 procesmeting"},
        "why": ("De holdbuis-uittrede is of record voor het pasteurisatiedossier; "
                "de kookmeting is het procesbeeld. Ze horen binnen 1,5 graad "
                "gelijk te lopen. Vervuiling van de warmtewisselaar drijft ze "
                "uit elkaar, en dan is dat een BEVINDING en geen ruis."),
    },
    {
        "id": "cross-check-02",
        "title": "Pakkentelling: vuller tegen omdozer",
        "unit": "st",
        "tolerance": 24,
        "of_record": "a",
        "a": {"topic": "DairyWorks/Vla-B/Filling/filler-01/Status/units_total",
              "label": "filler-01 productieboeking (of record)"},
        "b": {"topic": "DairyWorks/Vla-B/Packaging/case-packer-01/Status/units_total",
              "label": "case-packer-01 verpakkingsdoorzet"},
        "why": ("Wat de vuller telt en wat de omdozer verwerkt hoort binnen twee "
                "dozen gelijk te lopen. Loopt het verder uiteen, dan ligt er "
                "product tussen de twee machines, of telt er een verkeerd. "
                "De vuller is of record voor de productieboeking."),
    },
    {
        "id": "cross-check-03",
        "title": "Melkmassa: goederenontvangst tegen procesverbruik",
        "unit": "kg",
        "tolerance": None,
        "alarm": False,
        "of_record": "a",
        "a": {"topic": "DairyWorks/Vla-B/Receiving/intake-silo-01/Status/mass_total_kg",
              "label": "intake-silo-01 totalisator (of record)"},
        "b": {"topic": "DairyWorks/Vla-B/Mixing/blend-tank-01/Status/dose_milk_kg",
              "label": "blend-tank-01 gedoseerde melk"},
        "why": ("Publiceert BEWUST alleen het verschil en GEEN alarm. Een "
                "totalisator en een batchdosering zijn verwante maar niet "
                "identieke grootheden: de eerste telt alles wat er binnenkwam, "
                "de tweede alleen wat in deze batch ging. Daar een "
                "afwijkingsalarm op zetten levert een vals alarm op, en vals "
                "alarm is precies de fout die deze demo elders bekritiseert. "
                "Het verschil is wel interessant, dus dat publiceren we."),
    },
]
