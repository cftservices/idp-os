# -*- coding: utf-8 -*-
"""Condition en Model: van leveranciersrommel naar een canoniek signaal.

Pure functies. Geen broker, geen database, geen netwerk, geen klok die je niet
kunt meegeven. Dat is geen netheid maar een voorwaarde: de conversielogica is
het enige in deze stack waar een fout stilzwijgend een plausibel getal oplevert,
en dat moet je offline in een diff kunnen reviewen en in een test kunnen vangen.

Waarom dit een eigen service is en geen brokerconfiguratie. MonsterMQ's
flow-engine bewaart regels in Mongo, dus de transformatie zou niet in git staan.
Dat breekt versiebeheer, de eerste DataOps-discipline, en het is niet offline
testbaar. NiFi wil ~1,5 GB heap. Node-RED is verboden.

    De broker doet wat configuratie is, deze service doet wat logica is,
    want logica moet je in een diff kunnen reviewen. Een conversie die je
    niet in een git-diff ziet kun je niet auditen, en in voedselproductie
    is audit het punt.

Wat hier NOOIT gebeurt:

    - een niet-numerieke waarde naar 0 forceren        -> weigeren en tellen
    - ontbrekende kwaliteit als GOOD aannemen          -> UNCERTAIN
    - stilte als nul rapporteren                       -> stale
    - een tijdzone raden                               -> gedeclareerd of niets
    - een kwaliteitswissel wegdeadbanden               -> altijd doorlaten
"""

from __future__ import annotations

import datetime as dt
import math

GOOD, BAD, UNCERTAIN = "GOOD", "BAD", "UNCERTAIN"


class Refused(ValueError):
    """De waarde is geweigerd. Er wordt NIETS gepubliceerd.

    Een geweigerde waarde is een gat, geen nul. Dat verschil is in
    voedselproductie het verschil tussen een batchrapport en een auditbevinding.
    """

    def __init__(self, reason, raw=None):
        super().__init__(reason)
        self.reason = reason
        self.raw = raw


# --------------------------------------------------------------------- waarde

def _to_number(raw, rule):
    """Ruwe waarde naar een getal, of weigeren."""
    if raw is None:
        raise Refused("leeg", raw)
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw)):
            raise Refused("niet-eindig getal", raw)
        return float(raw)
    s = str(raw).strip()
    if s in (rule.get("null_tokens") or []) or s == "":
        raise Refused("niet-numeriek token %r" % s, raw)
    try:
        return float(s)
    except ValueError:
        raise Refused("niet-numeriek %r" % s, raw)


def descale(raw, rule):
    """Ruwe waarde -> waarde in de LEVERANCIERSEENHEID."""
    kind = rule.get("scaling_kind", "none")

    if rule.get("discrete"):
        if rule.get("datatype") == "String":
            return str(raw)
        return int(_to_number(raw, rule))

    v = _to_number(raw, rule)

    if kind == "integer_tenths":
        return v * float(rule["scale"])
    if kind == "integer_milli_on":
        return v * float(rule.get("scale", 0.001))
    if kind == "affine_span":
        # Span loopt naar de LEVERANCIERSEENHEID; de eenheidsconversie komt
        # daarna. Staan hier canonieke grenzen, dan is elke tag met een
        # eenheidsconversie stil verkeerd geschaald.
        lo = float(rule.get("eu_min_native", rule.get("eu_min", 0.0)))
        hi = float(rule.get("eu_max_native", rule.get("eu_max", 100.0)))
        rlo, rhi = float(rule["raw_min"]), float(rule["raw_max"])
        if rhi == rlo:
            return lo
        return lo + (v - rlo) / (rhi - rlo) * (hi - lo)
    return v


def to_canonical_unit(value, rule):
    """Leverancierseenheid -> canonieke eenheid."""
    conv = rule.get("unit_conversion")
    if not conv or not isinstance(value, (int, float)):
        return value
    scale = float(conv["scale"])
    if conv["formula"] == "affine":
        return float(value) * scale + float(conv.get("offset", 0.0))
    return float(value) * scale


# ------------------------------------------------------------------ kwaliteit

def map_quality(rule, quality_raw):
    """Ruwe kwaliteit -> GOOD / BAD / UNCERTAIN.

    Ontbreekt hij, dan is het UNCERTAIN. Nooit een aangenomen GOOD: een bron
    zonder kwaliteitsindicatie IS onzeker, en dat weten is de halve waarde.
    """
    src = rule.get("quality_source", "none")
    if src == "none" or quality_raw is None:
        return rule.get("missing_quality_means", UNCERTAIN)

    if src == "opcua-statuscode":
        # Een StatusCode is een severity-bitveld, geen boolean. De onderste bits
        # zijn subcodes en die moet je maskeren, anders leest elke sub-status
        # als BAD.
        try:
            code = int(quality_raw)
        except (TypeError, ValueError):
            return UNCERTAIN
        sev = code & 0xC0000000
        if sev == 0x80000000:
            return BAD
        if sev == 0x40000000:
            return UNCERTAIN
        return GOOD

    enc = rule.get("quality_encoding") or {}
    key = str(quality_raw).strip()
    if key in enc:
        return enc[key]
    try:
        ikey = str(int(float(key)))
        if ikey in enc:
            return enc[ikey]
    except (TypeError, ValueError):
        pass
    return UNCERTAIN


# --------------------------------------------------------------------- tijd

def resolve_timestamp(rule, raw_ts, received_at):
    """(iso8601 UTC, ts_source-label).

    Een tijdzone wordt GEDECLAREERD (assume_tz) of er is er geen. Raden is twee
    keer per jaar een uur fout en de rest van het jaar onzichtbaar, en dat is
    de vervelendste soort fout die er is.
    """
    src = rule.get("timestamp_source", "none")

    if src == "none" or raw_ts is None:
        return _iso(received_at), "receive"

    if src == "opcua-source":
        try:
            s = str(raw_ts).replace("Z", "+00:00")
            d = dt.datetime.fromisoformat(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return _iso(d.astimezone(dt.timezone.utc)), "source"
        except ValueError:
            return _iso(received_at), "receive-fallback"

    if src == "epoch-ms":
        try:
            d = dt.datetime.fromtimestamp(float(raw_ts) / 1000.0, dt.timezone.utc)
            return _iso(d), "source"
        except (TypeError, ValueError, OSError):
            return _iso(received_at), "receive-fallback"

    if src in ("local-no-timezone", "local-wallclock-dst-broken"):
        fmt = rule.get("timestamp_format", "%Y-%m-%d %H:%M:%S")
        try:
            naive = dt.datetime.strptime(str(raw_ts), fmt)
        except (TypeError, ValueError):
            return _iso(received_at), "receive-fallback"
        tz = rule.get("assume_tz")
        if not tz:
            # Geen gedeclareerde zone: dan gebruiken we de aankomsttijd en
            # zeggen dat ook. Liever een eerlijk grover tijdstip dan een
            # verzonnen precies tijdstip.
            return _iso(received_at), "receive-no-tz-declared"
        off = _tz_offset_hours(tz, naive)
        if src == "local-wallclock-dst-broken":
            off = float(rule.get("fixed_offset_hours", off))
        d = naive.replace(tzinfo=dt.timezone(dt.timedelta(hours=off)))
        return _iso(d.astimezone(dt.timezone.utc)), "source-assumed-tz"

    return _iso(received_at), "receive"


def _tz_offset_hours(tz_name, naive):
    try:
        from zoneinfo import ZoneInfo
        return naive.replace(tzinfo=ZoneInfo(tz_name)).utcoffset().total_seconds() / 3600.0
    except Exception:  # noqa: BLE001
        # Zonder tzdata (slim image zonder tzdata-pakket) vallen we terug op de
        # standaardtijd van Europe/Amsterdam en zeggen dat in het label.
        return 1.0


def _iso(d):
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ de regel

def condition(rule, raw_value, quality_raw=None, raw_ts=None, received_at=None):
    """Eén ruw punt -> het canonieke UNS-bericht.

    Gooit Refused als de waarde geen getal is. Dat is geen fout in de pijplijn
    maar informatie over de bron, en hij wordt geteld en gerapporteerd.
    """
    received_at = received_at or dt.datetime.now(dt.timezone.utc)

    native = descale(raw_value, rule)
    value = to_canonical_unit(native, rule)
    if isinstance(value, float):
        value = round(value, 6)

    quality = map_quality(rule, quality_raw)
    ts, ts_source = resolve_timestamp(rule, raw_ts, received_at)

    return {
        # Het contract. Vier sleutels, identiek aan de bestaande UNS van lijn
        # Vla, zodat batch-engine/vla/bus.py en de UI ongewijzigd blijven werken.
        "value": value,
        "unit": rule.get("canonical_unit", ""),
        "ts": ts,
        "quality": quality,
        # Herkomst. Additief: wie het niet kent negeert het. Dit is wat een
        # meting van een getal onderscheidt.
        "source_system": rule.get("source_system"),
        "native_name": rule.get("native_name"),
        "signal_uuid": rule.get("signal_uuid"),
        "ts_source": ts_source,
    }


# ---------------------------------------------------------------- deadband

def suppress(rule, prev, new):
    """True als dit bericht onderdrukt mag worden.

    Een kwaliteitswissel gaat ALTIJD door. Zou je die wegdeadbanden, dan leert
    een consument nooit dat het instrument slecht is geworden, en blijft hij
    rekenen met een waarde die niemand meer garandeert. Eén tag leverde ooit
    5,34 van de 5,35 miljoen historian-rijen; deadbands zijn nodig, maar niet
    op kwaliteit.
    """
    if prev is None:
        return False
    if prev.get("quality") != new.get("quality"):
        return False
    db = rule.get("deadband")
    if db is None:
        return prev.get("value") == new.get("value")
    a, b = prev.get("value"), new.get("value")
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return a == b
    return abs(float(b) - float(a)) < float(db)


def is_stale(rule, last_seen, now=None):
    """Stilte is stale, geen nul."""
    if last_seen is None:
        return True
    now = now or dt.datetime.now(dt.timezone.utc)
    limit = float(rule.get("stale_after_s", 30.0))
    return (now - last_seen).total_seconds() > limit
