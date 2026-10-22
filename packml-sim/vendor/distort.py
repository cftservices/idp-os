"""Canoniek -> leveranciersdialect. De conditioner draait dit terug.

De sim krijgt nette SI-waarden van signals/template.py en verminkt ze hier tot
wat een echt leveranciersysteem zou uitspugen: Fahrenheit in tienden, kwaliteit
in een apart item, geen tijdstempel, een register-integer, of gewoon een string
met af en toe "N/A" erin.

Volgorde, en die is dwingend:

    canoniek --(eenheid)--> leverancierseenheid --(schaal)--> ruwe waarde

Omdraaien geeft een plausibel getal dat een factor 3,8 fout is. Dat is geen
theoretisch risico: het is de bugklasse waar deze hele demo over gaat, en het is
in de vorige poging een keer echt gebeurd.

Het distort-blok per signaal komt uit de unit-YAML en is LETTERLIJK dezelfde
regel die de conditioner gebruikt, uit een bron gegenereerd. Dat sluit uit dat
de twee kanten iets anders denken; dat de wiskunde ook echt omkeerbaar is,
bewijst de round-trip-selftest en niet dit bestand.
"""

from __future__ import annotations

import datetime as _dt
import random

# DA-qualityword. 192 = GOOD, 64 = UNCERTAIN, 0 = BAD.
DA_GOOD, DA_UNCERTAIN, DA_BAD = 192, 64, 0

# OPC-UA StatusCode severity-bits.
UA_GOOD, UA_UNCERTAIN, UA_BAD = 0, 0x40000000, 0x80000000


class DistortResult:
    """Wat er de draad op gaat voor één signaal."""

    __slots__ = ("value", "quality_raw", "quality_label", "timestamp", "refused")

    def __init__(self, value, quality_raw=None, quality_label="GOOD",
                 timestamp=None, refused=False):
        self.value = value
        self.quality_raw = quality_raw
        self.quality_label = quality_label
        self.timestamp = timestamp
        self.refused = refused

    def __repr__(self):
        return ("DistortResult(value=%r, q=%r/%s, ts=%r, refused=%s)"
                % (self.value, self.quality_raw, self.quality_label,
                   self.timestamp, self.refused))


def canonical_to_native_unit(value, conv):
    """Inverse van de conditioner-conversie (die gaat native -> canoniek)."""
    if not conv:
        return value
    scale = float(conv["scale"])
    if conv["formula"] == "affine":
        return (float(value) - float(conv.get("offset", 0.0))) / scale
    return float(value) / scale


def native_unit_to_canonical(value, conv):
    """Voorwaarts, zoals de conditioner hem toepast. Hier om te kunnen testen."""
    if not conv:
        return value
    scale = float(conv["scale"])
    if conv["formula"] == "affine":
        return float(value) * scale + float(conv.get("offset", 0.0))
    return float(value) * scale


class VendorDistorter:
    """Past het profiel van één machine toe op zijn 30 signalen."""

    def __init__(self, cfg, fault_injector=None, rng=None):
        self.profile = cfg.get("vendor_profile", "vendor-a")
        self.tz_offset_h = 0
        self.slots = {s["name"]: s for s in (cfg.get("signals") or [])}
        self.faults = fault_injector
        self.rng = rng or random.Random()
        # Kwaliteit is niet altijd GOOD. Een demo waarin nooit iets mis is met
        # de meting laat de kwaliteitsweg van de conditioner nooit zien.
        self.uncertain_rate = float(cfg.get("quality_uncertain_rate", 0.004))
        self.bad_rate = float(cfg.get("quality_bad_rate", 0.001))

    # ------------------------------------------------------------- kwaliteit

    def _quality(self, slot):
        label = "GOOD"
        # Een actieve sensorstoring hoort zich in de KWALITEIT te melden, niet
        # alleen in de waarde. Dat is het halve punt van een qualityword.
        if self.faults is not None and (self.faults.is_active("f1")
                                        or self.faults.is_active("f2")):
            if self.rng.random() < 0.25:
                label = "UNCERTAIN"
        r = self.rng.random()
        if r < self.bad_rate:
            label = "BAD"
        elif r < self.bad_rate + self.uncertain_rate and label == "GOOD":
            label = "UNCERTAIN"

        src = (slot.get("distort") or {}).get("quality_source", "none")
        if src == "da-quality-word":
            return {"GOOD": DA_GOOD, "UNCERTAIN": DA_UNCERTAIN, "BAD": DA_BAD}[label], label
        if src == "opcua-statuscode":
            return {"GOOD": UA_GOOD, "UNCERTAIN": UA_UNCERTAIN, "BAD": UA_BAD}[label], label
        if src == "status-word":
            return {"GOOD": 0, "BAD": 1, "UNCERTAIN": 2}[label], label
        if src == "text-enum":
            return {"GOOD": "Good", "UNCERTAIN": "Suspect", "BAD": "Bad"}[label], label
        # vendor-d levert helemaal geen kwaliteit. Dan hoort de conditioner
        # UNCERTAIN te concluderen, niet GOOD aan te nemen.
        return None, label

    # -------------------------------------------------------------- tijdstip

    def _timestamp(self, slot, now=None):
        src = (slot.get("distort") or {}).get("timestamp_source", "none")
        now = now or _dt.datetime.now(_dt.timezone.utc)
        if src == "none":
            # Een DA-item heeft geen meettijd. Er gaat dus NIETS mee, en de
            # conditioner moet aankomsttijd gebruiken en dat ook zo labelen.
            return None
        if src == "opcua-source":
            return now.isoformat().replace("+00:00", "Z")
        if src == "epoch-ms":
            return int(now.timestamp() * 1000)
        d = (slot.get("distort") or {})
        fmt = d.get("timestamp_format", "%Y-%m-%d %H:%M:%S")
        if src == "local-no-timezone":
            # Lokale tijd zonder zone. De conditioner mag de zone niet raden;
            # die staat GEDECLAREERD in assume_tz.
            return (now + _dt.timedelta(hours=2)).strftime(fmt)
        if src == "local-wallclock-dst-broken":
            off = float(d.get("fixed_offset_hours", 1))
            return (now + _dt.timedelta(hours=off)).strftime(fmt)
        return None

    # ----------------------------------------------------------------- waarde

    def _value(self, slot, canonical):
        d = slot.get("distort") or {}
        kind = d.get("scaling_kind", "none")

        if d.get("discrete") or slot["datatype"] == "String":
            # Toestand, teller, enum of bitfield: niet schalen, niet converteren.
            if slot["datatype"] == "String":
                return str(canonical)
            return int(canonical)

        native = canonical_to_native_unit(float(canonical), d.get("unit_conversion"))

        if kind == "integer_tenths":
            return int(round(native / float(d["scale"])))

        if kind == "affine_span":
            lo = float(d.get("eu_min_native", d.get("eu_min", 0.0)))
            hi = float(d.get("eu_max_native", d.get("eu_max", 100.0)))
            rlo, rhi = float(d["raw_min"]), float(d["raw_max"])
            if hi == lo:
                return int(rlo)
            frac = (native - lo) / (hi - lo)
            return int(round(rlo + frac * (rhi - rlo)))

        if kind == "string_values":
            # Waarden komen als string binnen, en af en toe is het geen getal.
            # Die worden door de conditioner GEWEIGERD, nooit naar nul gedwongen.
            rate = float(d.get("null_rate", 0.0))
            toks = d.get("null_tokens") or ["N/A"]
            if rate and self.rng.random() < rate:
                return self.rng.choice([t for t in toks if t]) or "N/A"
            return "%.2f" % native

        if kind == "integer_milli_on":
            return int(round(native / float(d.get("scale", 0.001))))

        return round(native, 4)

    # -------------------------------------------------------------------- api

    def to_native(self, name, canonical_value, now=None) -> DistortResult:
        slot = self.slots[name]
        q_raw, q_label = self._quality(slot)
        value = self._value(slot, canonical_value)
        refused = (isinstance(value, str)
                   and value in ((slot.get("distort") or {}).get("null_tokens") or []))
        return DistortResult(value=value, quality_raw=q_raw, quality_label=q_label,
                             timestamp=self._timestamp(slot, now), refused=refused)

    def all_native(self, canonical: dict, now=None) -> dict:
        return {name: self.to_native(name, v, now) for name, v in canonical.items()}
