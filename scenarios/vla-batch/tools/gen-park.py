#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen-park.py — genereert alles wat uit het ISA-95-model afleidbaar is.

Met de hand geschreven zijn precies drie bestanden:

    factory-model/isa95-vla.json        de fabriek (areas, work centers, pvs)
    factory-model/signal-template.json  de 30 slots die elke machine heeft
    factory-model/vendor-profiles.json  hoe elke leverancier liegt

Alles hieronder is daarvan afgeleid en wordt door dit script geschreven:

    factory-model/park-aliases.json     legacy tag -> canonieke identiteit
    factory-model/park-conditioning.json  de reparatieregels per punt
    factory-model/park-faults.json      de storingscatalogus per machine
    park-sim/units/<machine>.yaml       de unit-config die de container mount
    monstermq-init/init-park.sh         devices + adressen voor het park
    monstermq-init/init-vla-opcua.sh    het TAGS-blok van de MONOLIET

Die laatste is met opzet de eerste klant van deze generator. Het model en
factory/server.py kennen allebei vier dose_*_setpoint_kg-tags; het init-script
kende ze niet, dus de helft van elk target/actual-paar heeft de UNS nooit
gehaald. Vier tags met echt effect, en het bewijst de generator tegen een
bekend-goed doel voordat er 360 van afhangen.

De sim leest dit model NIET at runtime. Een echte PLC leest bij het opstarten
geen enterprise-model; dat wel doen vervaagt precies de OT/IT-grens waar deze
demo over gaat. Generatietijd, niet runtime.

Gebruik:
    python tools/gen-park.py            schrijf alle artefacten
    python tools/gen-park.py --check    faal (exit 1) bij drift, schrijf niets

--check is pure bestands-I/O: geen broker, geen Mongo, geen netwerk. Daarom kan
hij in batch-engine/selftest.py, in deploy.sh verify en in een pre-commit hook.
"""

from __future__ import annotations

import argparse
import difflib
import io
import json
import os
import re
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # scenarios/vla-batch
MODEL_DIR = os.path.join(ROOT, "factory-model")
INIT_DIR = os.path.join(ROOT, "monstermq-init")
UNITS_DIR = os.path.join(ROOT, "park-sim", "units")

BANNER = "GEGENEREERD DOOR tools/gen-park.py — NIET MET DE HAND BEWERKEN"
BEGIN = "# BEGIN GENERATED"
END = "# END GENERATED"

# Vaste UUID-namespace. NOOIT wijzigen: elke historian-rij en elke alias-rij
# leidt zijn identiteit hiervan af. Verander je hem, dan begint elke reeks
# stilzwijgend opnieuw en dat merk je pas als een trend een gat heeft.
UUID_NS = uuid.UUID("6f9d2a1e-0c3b-4d7a-9e14-8b2c4f6a1d30")

# Elke container krijgt een expliciete log-cap, direct achter restart:.
# De daemon-default uit /etc/docker/daemon.json is op de VPS nooit ingelezen
# (dockerd draait sinds voor dat bestand geschreven werd), dus containers
# loggen daar ongelimiteerd. De sims logden op INFO elke asyncua publish-ack:
# 25 MB per dag per machine, en json-logs van meerdere GB per container.
# Dit blok maakt de cap onafhankelijk van de daemon-instelling.
LOG_CAP = ("\n    logging:"
           "\n      driver: json-file"
           "\n      options:"
           "\n        max-size: \"10m\""
           "\n        max-file: \"3\"")


# --------------------------------------------------------------------------- io

def load(name):
    with io.open(os.path.join(MODEL_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def read_text(path):
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def emit(path, content, check, drift):
    """Schrijf, of registreer drift in --check-modus."""
    current = read_text(path)
    if current == content:
        return
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    if check:
        if current is None:
            drift.append((rel, "ONTBREEKT"))
        else:
            d = list(difflib.unified_diff(
                current.splitlines(), content.splitlines(),
                fromfile=rel + " (op schijf)", tofile=rel + " (gegenereerd)",
                lineterm="", n=1))
            drift.append((rel, "\n".join(d[:40])))
        return
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    print("  geschreven: %s" % rel)


def jdump(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ naamgeving

def pascal(name):
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[_\-/]", name) if p)


def camel(name):
    p = pascal(name)
    return p[:1].lower() + p[1:]


def upper_snake(name):
    s = re.sub(r"[\-/]", "_", name)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    return s.upper()


def instrument_code(profiles, slot_name, unit):
    codes = profiles["instrument_codes"]
    if slot_name in codes["by_name_override"]:
        return codes["by_name_override"][slot_name]
    return codes["by_unit"].get(unit, "XT")


def native_name(profiles, profile_id, machine, ordinal, slot_no, slot_name,
                unit, group, protocol):
    prof = profiles["profiles"][profile_id]
    nm = prof["naming"]

    if profile_id == "vendor-a":
        code = instrument_code(profiles, slot_name, unit)
        return "Ch1.Dev%d.%s_%d_PV" % (ordinal, code, 2000 + slot_no * 7)

    if profile_id == "vendor-b":
        return "Machine.%s.%s" % (nm["group_map"][group], pascal(slot_name))

    if profile_id == "vendor-c":
        if protocol == "modbus-tcp":
            return str(40001 + ordinal * 100 + slot_no)
        return "DB%d.DBD%d" % (10 + ordinal, slot_no * 4)

    if profile_id == "vendor-d":
        return "sensors/%s" % camel(slot_name)

    if profile_id == "vendor-e":
        return "VLA.%s.%s" % (profiles["machine_abbrev"][machine],
                              upper_snake(slot_name))

    raise KeyError("onbekend profiel %r" % profile_id)


# ------------------------------------------------------------ slot-expansie

def expand_slots(wc, template, profiles):
    """Eén work center -> exact 30 canonieke tag-definities."""
    park = wc["park"]
    profile_id = park["vendor_profile"]
    prof = profiles["profiles"][profile_id]
    protocol = park["protocol"]
    machine = wc["equipment_id"]
    ordinal = park["machine_ordinal"]
    pvs = park["pvs"]

    if len(pvs) != 8:
        raise ValueError("%s heeft %d pvs, moeten er 8 zijn" % (machine, len(pvs)))

    out = []
    for slot in template["slots"]:
        no = slot["slot"]
        if slot["group"] == "B":
            pv = pvs[slot["pv_index"]]
            name = pv["name"]
            unit = pv["unit"]
            datatype = pv["datatype"]
            display = pv["display_name"]
            source = pv["source"]
            eu_min, eu_max = pv["eu_min"], pv["eu_max"]
            deadband = round(abs(eu_max - eu_min) * 0.002, 4)
        else:
            name = slot["name"]
            datatype = slot["datatype"]
            display = slot["display_name"]
            source = slot["source"]
            deadband = slot.get("deadband")
            unit = slot.get("unit", "")
            if "unit_from" in slot:
                unit = pvs[int(slot["unit_from"].split(":")[1])]["unit"]
            rng = slot.get("normal_range") or {}
            eu_min = rng.get("min", 0)
            eu_max = rng.get("max", 100)

            # Een setpoint hoort bij zijn eigen grootheid, niet bij een
            # willekeurig PV-slot. Het sjabloon geeft een default (unit_from),
            # het model mag hem overrulen. Zonder deze override kreeg het
            # debiet-setpoint van de pasteur graden Celsius.
            sp = (park.get("setpoints") or {}).get(name)
            if sp:
                if "unit_from_pv" in sp:
                    pv = pvs[sp["unit_from_pv"]]
                    unit = pv["unit"]
                    eu_min, eu_max = pv["eu_min"], pv["eu_max"]
                elif "unit" in sp:
                    unit = sp["unit"]
                if "display_name" in sp:
                    display = sp["display_name"]
                if "source" in sp:
                    source = sp["source"]
                if eu_max != eu_min:
                    deadband = round(abs(eu_max - eu_min) * 0.002, 4)

        canonical_id = "%s:%s" % (machine, name)
        # (modbus-adressen worden hieronder in een tweede pas toegekend, omdat
        #  de breedte per signaal verschilt en dus de offset van het volgende)
        out.append({
            "slot": no,
            "group": slot["group"],
            "machine": machine,
            "name": name,
            "display_name": display,
            "canonical_id": canonical_id,
            "signal_uuid": str(uuid.uuid5(UUID_NS, canonical_id)),
            "canonical_unit": unit,
            "datatype": datatype,
            "writable": slot.get("writable", False),
            "sampling_class": slot["sampling_class"],
            "deadband": deadband,
            "eu_min": eu_min,
            "eu_max": eu_max,
            "source": source,
            "native_name": native_name(profiles, profile_id, machine, ordinal,
                                       no, name, unit, slot["group"], protocol),
            "native_unit": prof["units"].get(unit, unit),
            "profile": profile_id,
            "protocol": protocol,
            "bool_as_pct": (slot["group"] == "B"
                            and pvs[slot["pv_index"]].get("bool_as_pct", False)),
        })

    if protocol == "modbus-tcp":
        _allocate_modbus(out, park)
    return out


# Breedte per soort waarde, in 16-bits holding registers.
#
#   int16         1  een analoge waarde als ruwe count 0..27648
#   int32_hi_lo   2  een teller, hoog woord EERST. De woordvolgorde is de
#                    klassiekste Modbus-valstrik die er is: verkeerd om gelezen
#                    levert het een gigantisch maar volstrekt plausibel getal op.
#   ascii_packed  8  tekst als twee tekens per register, big-endian, 16 tekens.
#                    Modbus kent geen strings; dit is hoe apparaten het echt
#                    doen. Niets in het protocol vertelt je dat, en dat is
#                    precies waarom alle betekenis van buiten moet komen.
_MODBUS_WIDTH = {"int16": 1, "int32_hi_lo": 2, "ascii_packed": 8}


def _modbus_encoding(tag):
    if tag["datatype"] == "String":
        return "ascii_packed"
    if tag["datatype"] in ("Int32", "Int64"):
        return "int32_hi_lo"
    return "int16"


def _allocate_modbus(tags, park):
    """Adressen sequentieel toekennen; de breedte bepaalt de volgende offset.

    Een vaste stap per slot zou botsen zodra een teller twee registers pakt, en
    dan lees je stil het lage woord van de buurman. Daarom een echte allocatie.
    """
    base = int((park.get("modbus") or {}).get("register_base", 40001))
    addr = base
    for t in tags:
        enc = _modbus_encoding(t)
        t["modbus_encoding"] = enc
        t["modbus_width"] = _MODBUS_WIDTH[enc]
        t["modbus_addr"] = addr
        t["native_name"] = str(addr)
        addr += t["modbus_width"]

    # EEN statusregister voor het hele apparaat, aan het eind van het blok.
    #
    # Geen kwaliteit per meting: zo werkt een PLC-registerblok ook niet. Het
    # skid heeft een status en die geldt voor alles wat eruit komt. Dat is
    # meteen de beperking die je hardop moet noemen: gaat er een sensor stuk,
    # dan zegt dit woord alleen DAT er iets mis is, niet WAT. De poller waaiert
    # hem uit over alle dertig signalen, zodat de conditioner er niets van hoeft
    # te weten en protocol-agnostisch blijft.
    for t in tags:
        t["modbus_status_addr"] = addr


def park_work_centers(model):
    """Alle work centers van lijn Vla-B, in modelvolgorde."""
    for site in model["enterprise"]["sites"]:
        for line in site["lines"]:
            if line["id"] != "LINE-VLA-B":
                continue
            for area in line["areas"]:
                for wc in area["work_centers"]:
                    yield line, area, wc


# ---------------------------------------------------------------- conditioning

def conditioning_rule(tag, template, profiles):
    """De reparatie die de conditioner moet uitvoeren, van native naar canoniek."""
    prof = profiles["profiles"][tag["profile"]]
    scaling = prof["scaling"]
    q = prof["quality"]
    ts = prof["timestamp"]
    classes = template["sampling_classes"]

    interval = classes[tag["sampling_class"]]["interval_ms"]
    expected_s = (interval / 1000.0) if interval else template["keepalive_s"]

    rule = {
        "canonical_id": tag["canonical_id"],
        "signal_uuid": tag["signal_uuid"],
        "source_system": tag["machine"],
        # Het PROTOCOL staat hier alleen zodat een connector weet of hij dit
        # punt moet ophalen. De CONDITIONER kijkt er nooit naar: die is
        # protocol-agnostisch, en check-generated.sh bewaakt dat.
        "protocol": tag["protocol"],
        "native_name": tag["native_name"],
        "native_unit": tag["native_unit"],
        "canonical_unit": tag["canonical_unit"],
        "datatype": tag["datatype"],
        "quality_source": q["source"],
        "missing_quality_means": q["missing_means"],
        "timestamp_source": ts["source"],
        "ts_source_label": ts["ts_source_label"],
        "deadband": tag["deadband"],
        # De klasse ZELF, niet alleen het interval eruit. Een poller moet
        # "onchange met 60 s hartslag" kunnen onderscheiden van "elke 60 s
        # cyclisch", en uit expected_interval_s=60 volgt dat niet. De
        # conditioner kijkt hier niet naar; dit is er voor de connectoren.
        "sampling_class": tag["sampling_class"],
        "expected_interval_s": expected_s,
        "stale_after_s": max(3.0 * expected_s, 30.0),
    }

    if q.get("carrier") == "companion_item":
        rule["quality_topic_suffix"] = q["companion_suffix"]
    if q.get("carrier") == "column":
        rule["quality_column"] = q["column"]
    if q.get("encoding"):
        rule["quality_encoding"] = q["encoding"]

    if ts.get("assume_tz"):
        rule["assume_tz"] = ts["assume_tz"]
    if ts.get("format"):
        rule["timestamp_format"] = ts["format"]
    if ts.get("fixed_offset_hours") is not None:
        rule["fixed_offset_hours"] = ts["fixed_offset_hours"]

    # Alleen analoge waarden worden geschaald en geconverteerd.
    #
    # Een leverancier levert een toestandsnummer, een teller of een alarmwoord
    # als integer, niet als tienden: 0.1 x een bitfield vernietigt de bits, en
    # een tekst laat zich uberhaupt niet schalen. Dit is geen defensieve
    # programmering maar de werkelijkheid van een DA-itemlijst, waar analoge
    # en discrete items naast elkaar staan met verschillende conventies.
    analog = tag["datatype"] == "Double"

    if analog and tag["native_unit"] != tag["canonical_unit"]:
        key = "%s->%s" % (tag["native_unit"], tag["canonical_unit"])
        conv = profiles["unit_conversions"].get(key)
        if conv is None:
            raise KeyError("geen conversie %r voor %s" % (key, tag["canonical_id"]))
        rule["unit_conversion"] = dict(conv, key=key)
    elif not analog:
        rule["native_unit"] = tag["canonical_unit"]

    kind = scaling["kind"] if analog else "none"
    rule["scaling_kind"] = kind
    if not analog:
        rule["discrete"] = True
        rule["discrete_note"] = ("Discreet: toestand, teller, enum of bitfield. "
                                 "Niet schalen en niet converteren, wel dezelfde "
                                 "kwaliteits- en tijdregels als de analoge punten.")
    if kind == "integer_tenths":
        rule["scale"] = scaling["scale"]
    elif kind == "affine_span":
        rule["raw_min"] = scaling["raw_min"]
        rule["raw_max"] = scaling["raw_max"]
        rule["eu_min"] = tag["eu_min"]
        rule["eu_max"] = tag["eu_max"]
        # De span loopt van ruwe counts naar de LEVERANCIERSEENHEID, en pas
        # daarna converteert de eenheid naar canoniek. De grenzen moeten dus
        # ook in die eenheid staan. Zou je hier de canonieke grenzen laten
        # staan, dan is elke vendor-c-meting met een eenheidsconversie stil
        # verkeerd geschaald, en dat merk je alleen als je de getallen kent.
        conv = rule.get("unit_conversion")
        if conv:
            sc, off = float(conv["scale"]), float(conv.get("offset", 0.0))
            inv = (lambda v: (v - off) / sc) if conv["formula"] == "affine" \
                else (lambda v: v / sc)
            rule["eu_min_native"] = round(inv(float(tag["eu_min"])), 6)
            rule["eu_max_native"] = round(inv(float(tag["eu_max"])), 6)
        else:
            rule["eu_min_native"] = tag["eu_min"]
            rule["eu_max_native"] = tag["eu_max"]
    elif kind == "string_values":
        rule["null_tokens"] = scaling["null_tokens"]
        rule["null_behaviour"] = scaling["null_behaviour"]
    elif kind == "integer_milli_on":
        if tag["canonical_unit"] in scaling["applies_to_units"]:
            rule["scale"] = scaling["scale"]
        else:
            rule["scaling_kind"] = "none"
    elif kind != "none":
        raise KeyError("onbekende scaling %r" % kind)

    if tag["bool_as_pct"]:
        rule["bool_as_pct"] = True

    # Het datatype dat ECHT op de draad staat. Niet hetzelfde als het canonieke
    # type: temp_out_C is canoniek een Double, maar vendor-a levert hem als
    # Int32 in tienden Fahrenheit. Een OPC-UA-node met het canonieke type
    # weigert die waarde, en dan blijft de node op nul staan zonder dat er iets
    # crasht. Stil op nul blijven staan is de vervelendste storing die er is.
    if rule["scaling_kind"] == "integer_tenths":
        rule["native_datatype"] = "Int32"
    elif rule["scaling_kind"] == "affine_span":
        rule["native_datatype"] = "Int16"
    elif rule["scaling_kind"] == "string_values":
        rule["native_datatype"] = "String"
    elif rule["scaling_kind"] == "integer_milli_on":
        rule["native_datatype"] = "Int32"
    else:
        rule["native_datatype"] = tag["datatype"]

    if tag.get("modbus_addr") is not None:
        rule["modbus_addr"] = tag["modbus_addr"]
        rule["modbus_width"] = tag["modbus_width"]
        rule["modbus_encoding"] = tag["modbus_encoding"]
        rule["modbus_status_addr"] = tag["modbus_status_addr"]
        # Een holding register draagt GEEN kwaliteit. De companion is dus geen
        # apart topic maar een apart REGISTER, en de poller moet weten waar.
        # Niets in Modbus vertelt hem dat; het staat hier of nergens.
        rule["native_datatype"] = {"int16": "Int16", "int32_hi_lo": "Int32",
                                   "ascii_packed": "String"}[tag["modbus_encoding"]]

    if rule["quality_source"] == "da-quality-word":
        rule["quality_datatype"] = "Int32"
    elif rule["quality_source"] == "opcua-statuscode":
        rule["quality_datatype"] = "UInt32"
    elif rule["quality_source"] == "status-word":
        rule["quality_datatype"] = "Int16"
    elif rule["quality_source"] == "text-enum":
        rule["quality_datatype"] = "String"

    return rule


# -------------------------------------------------------------------- yaml-uit

def yaml_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if s == "" or re.search(r"[:#{}\[\],&*?|<>=!%@`\"']", s) or s != s.strip():
        return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')
    return s


def unit_yaml(line, area, wc, tags, rules, template, profiles):
    park = wc["park"]
    prof = profiles["profiles"][park["vendor_profile"]]
    by_id = {r["canonical_id"]: r for r in rules}
    L = []
    a = L.append
    a("# %s" % BANNER)
    a("# bron: factory-model/isa95-vla.json + signal-template.json + vendor-profiles.json")
    a("#")
    a("# Unit-config voor %s. De container mount dit read-only en leest verder" % wc["equipment_id"])
    a("# NIETS uit het enterprise-model: een PLC doet dat ook niet.")
    a("#")
    a("# Het distort-blok per signaal is LETTERLIJK dezelfde regel als in")
    a("# factory-model/park-conditioning.json. De sim past hem VOORWAARTS toe")
    a("# (canoniek -> native), de conditioner draait hem TERUG. Uit een bron")
    a("# gegenereerd, dus de twee kanten kunnen niet uiteenlopen; dat de wiskunde")
    a("# ook echt omkeerbaar is bewijst de round-trip-selftest.")
    a("")
    a("unit_id: %s" % wc["equipment_id"])
    a("equipment: %s" % wc["equipment_id"])
    a("name: %s" % yaml_scalar(wc["name"]))
    a("type: %s" % wc["physics_type"])
    a("area: %s" % area["name"])
    a("line: %s" % line["name"])
    a("site: DairyWorks")
    a("")
    a("packml:")
    a("  mach_design_speed: %s" % yaml_scalar(park["physics"].get("design_speed", 120.0)))
    a("  initial_mach_speed: %s" % yaml_scalar(park["physics"].get("design_speed", 120.0)))
    a("  auto_start: true")
    a("")
    a("surface: %s" % {"opc-ua": "opcua", "opc-da": "opcua",
                       "modbus-tcp": "modbus", "mqtt": "mqtt",
                       "rest": "rest", "sql": "sql"}[park["protocol"]])
    a("protocol: %s" % park["protocol"])
    a("vendor_profile: %s" % park["vendor_profile"])
    a("machine_ordinal: %d" % park["machine_ordinal"])
    a("raw_root: %s/%s" % (profiles["raw_root"], wc["equipment_id"]))
    a("extended_pvs: %s" % yaml_scalar(park.get("extended_pvs", False)))
    a("")
    if park["protocol"] in ("opc-ua", "opc-da"):
        a("opcua:")
        a("  endpoint: %s" % park["opcua_endpoint"])
        a("  namespace_uri: %s" % park["opcua_namespace_uri"])
        a("  namespace_index: %d" % park["opcua_namespace_index"])
        a("  # Weiger te starten als register_namespace() een andere index geeft:")
        a("  # de gegenereerde adreslijst hardcodeert hem en zou er stil naast grijpen.")
        a("  strict_namespace_index: true")
        a("")
    a("sim:")
    a("  step_s: 0.2")
    a("  sampling_classes:")
    for cls, spec in template["sampling_classes"].items():
        a("    %s: %s" % (cls, yaml_scalar(spec["interval_ms"])))
    a("  keepalive_s: %d" % template["keepalive_s"])
    a("")

    by = park.get("tdengine_bypass")
    if by:
        # Het anti-patroon, met opzet. Deze machine schrijft ZELF line protocol
        # de historian in, langs de bus en langs de modellaag heen. Dat is de
        # scherpste vijf seconden van de demo: de reeks staat er, en verder
        # niets. Geen eenheid, geen kwaliteit, geen asset, geen signal_uuid,
        # niet te koppelen aan een batch.
        #
        # Dit blok stond wel in het model maar werd nooit uitgeschreven, dus
        # het pad was in de praktijk uitgeschakeld en idp_bypass bleef leeg.
        # Er is GEEN wachtwoord in dit bestand: dat komt uit de omgeving, want
        # een gegenereerd artefact gaat de repo in.
        a("# %s" % by["why"])
        a("tdengine_bypass:")
        a("  enabled: %s" % yaml_scalar(bool(by.get("enabled"))))
        a("  database: %s" % by.get("database", "idp_bypass"))
        a("  url: %s" % by["url"])
        a("")

    fol = park.get("follow")
    if fol:
        a("# Deze machine LOOPT MEE met de batch op de monoliet. Hij is geen")
        a("# tweede fabriek: de monoliet-waarden zijn stuurwaarden, en de eigen")
        a("# fysica bepaalt met welke traagheid, ruis en storing hij ze haalt.")
        a("follow:")
        a("  mode: %s" % fol["mode"])
        a("  batch_state_topic: %s" % yaml_scalar(fol["batch_state_topic"]))
        a("  batch_id_topic: %s" % yaml_scalar(fol["batch_id_topic"]))
        a("  active_phases:")
        for p in fol["active_phases"]:
            a("    - %s" % p)
        a("  drivers:")
        for d in fol["drivers"]:
            a("    - topic: %s" % yaml_scalar(d["topic"]))
            a("      target: %s" % yaml_scalar(d["target"]))
            a("      unit: %s" % yaml_scalar(d.get("unit", "")))
        fb = fol["fallback"]
        a("  fallback:")
        a("    mode: %s" % fb["mode"])
        a("    after_silence_s: %s" % yaml_scalar(fb["after_silence_s"]))
        a("    announce_topic: %s" % yaml_scalar(fb["announce_topic"]))
        a("")
    a("physics:")
    for k, v in sorted(park["physics"].items()):
        a("  %s: %s" % (k, yaml_scalar(v)))
    a("")
    a("faults:")
    for f in park["faults"]:
        a("  - %s" % f)
    a("")
    if prof["quality"].get("carrier") == "companion_item":
        a("quality_companion:")
        a("  suffix: %s" % yaml_scalar(prof["quality"]["companion_suffix"]))
        a("  sampling_class: %s" % template["quality_companion"]["sampling_class"])
        a("  encoding:")
        for k, v in prof["quality"]["encoding"].items():
            a("    %s: %s" % (yaml_scalar(k), v))
        a("")
    a("# 30 slots. Elke regel: canonieke naam, wat de leverancier ervan maakt,")
    a("# en hoe hij hem verminkt. De sim past dit VOORWAARTS toe; de conditioner")
    a("# draait het terug. Die round-trip is de belangrijkste offline test.")
    a("signals:")
    for t in tags:
        a("  - slot: %d" % t["slot"])
        a("    group: %s" % t["group"])
        a("    name: %s" % t["name"])
        a("    display_name: %s" % yaml_scalar(t["display_name"]))
        a("    native_name: %s" % yaml_scalar(t["native_name"]))
        a("    canonical_unit: %s" % yaml_scalar(t["canonical_unit"]))
        a("    native_unit: %s" % yaml_scalar(t["native_unit"]))
        a("    datatype: %s" % t["datatype"])
        a("    writable: %s" % yaml_scalar(t["writable"]))
        a("    sampling_class: %s" % t["sampling_class"])
        a("    source: %s" % yaml_scalar(t["source"]))
        a("    eu_min: %s" % yaml_scalar(t["eu_min"]))
        a("    eu_max: %s" % yaml_scalar(t["eu_max"]))
        if t["bool_as_pct"]:
            a("    bool_as_pct: true")
        r = by_id[t["canonical_id"]]
        a("    native_datatype: %s" % r["native_datatype"])
        if "quality_datatype" in r:
            a("    quality_datatype: %s" % r["quality_datatype"])
        a("    distort:")
        a("      scaling_kind: %s" % r["scaling_kind"])
        if r.get("discrete"):
            a("      discrete: true")
        for k in ("scale", "raw_min", "raw_max", "eu_min", "eu_max",
                  "eu_min_native", "eu_max_native",
                  "modbus_addr", "modbus_width", "modbus_encoding",
                  "modbus_status_addr"):
            if k in r:
                a("      %s: %s" % (k, yaml_scalar(r[k])))
        if "unit_conversion" in r:
            uc = r["unit_conversion"]
            a("      unit_conversion:")
            a("        key: %s" % yaml_scalar(uc["key"]))
            a("        formula: %s" % uc["formula"])
            a("        scale: %s" % yaml_scalar(uc["scale"]))
            if "offset" in uc:
                a("        offset: %s" % yaml_scalar(uc["offset"]))
        if "null_tokens" in r:
            a("      null_tokens:")
            for tok in r["null_tokens"]:
                a("        - %s" % yaml_scalar(tok))
            a("      null_rate: %s" % yaml_scalar(
                profiles["profiles"][t["profile"]]["scaling"].get("null_rate", 0.0)))
        a("      quality_source: %s" % r["quality_source"])
        if "quality_encoding" in r:
            a("      quality_encoding:")
            for k, v in r["quality_encoding"].items():
                a("        %s: %s" % (yaml_scalar(k), v))
        a("      timestamp_source: %s" % r["timestamp_source"])
        if "assume_tz" in r:
            a("      assume_tz: %s" % yaml_scalar(r["assume_tz"]))
        if "timestamp_format" in r:
            a("      timestamp_format: %s" % yaml_scalar(r["timestamp_format"]))
        if "fixed_offset_hours" in r:
            a("      fixed_offset_hours: %s" % yaml_scalar(r["fixed_offset_hours"]))
    a("")
    return "\n".join(L)


# ------------------------------------------------------- de monoliet, TAGS-blok

def monolith_tags(model):
    """Het TAGS-blok voor init-vla-opcua.sh, uit lijn Vla (A).

    Dit is de reden dat de generator eerst op de monoliet wordt gericht: de vier
    dose_*_setpoint_kg-tags staan al jaren in het model en in factory/server.py,
    maar niet in dit script, dus ze hebben de UNS nooit gehaald.
    """
    rows = []
    for site in model["enterprise"]["sites"]:
        for line in site["lines"]:
            if line["id"] != "LINE-VLA":
                continue
            for area in line["areas"]:
                for wc in area["work_centers"]:
                    eq = wc["equipment_id"]
                    for tag in wc["tags"]:
                        name = tag["id"].split(":", 1)[1]
                        rows.append("%s.%s.%s|%s/%s/Status/%s"
                                    % (area["name"], eq, name,
                                       area["name"], eq, name))
            bo = line.get("batch_object")
            if bo:
                for tag in bo["tags"]:
                    name = tag["id"].split(":", 1)[1]
                    rows.append("Batch.%s|Batch/Status/%s" % (name, name))
    return rows


def splice_monolith(model, check, drift):
    path = os.path.join(INIT_DIR, "init-vla-opcua.sh")
    current = read_text(path)
    if current is None:
        raise SystemExit("init-vla-opcua.sh ontbreekt")

    rows = monolith_tags(model)
    block = "\n".join([
        BEGIN + " (tools/gen-park.py) — uit isa95-vla.json, lijn Vla",
        'TAGS="',
    ] + rows + ['"', END])

    pat = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if pat.search(current):
        new = pat.sub(lambda _m: block, current)
    else:
        # Eerste keer: vervang het handgeschreven TAGS="..."-blok.
        old = re.compile(r'^TAGS="\n.*?^"\n', re.S | re.M)
        if not old.search(current):
            raise SystemExit("kon het TAGS-blok in init-vla-opcua.sh niet vinden")
        new = old.sub(lambda _m: block + "\n", current)

    emit(path, new, check, drift)
    return len(rows)


# ------------------------------------------------------------ init-park.sh

def init_park(model, template, profiles, all_tags):
    L = []
    a = L.append
    a("#!/bin/sh")
    a("# %s" % BANNER)
    a("#")
    a("# Registreert de OPC-UA- en OPC-DA-machines van lijn Vla-B in MonsterMQ.")
    a("# Alleen die: Modbus, MQTT, REST en SQL lopen NIET via MonsterMQ's")
    a("# OPC-UA-client. Modbus en REST gaan via vla-park-poller, SQL via")
    a("# vla-park-gateway, en MQTT publiceert de sim rechtstreeks. Daardoor")
    a("# houdt MonsterMQ maar een handvol sessies open in plaats van twaalf.")
    a("#")
    a("# Idempotent PER DEVICE, niet in het geheel: een halve mislukking mag")
    a("# geen half-geregistreerde machine achterlaten die niemand opmerkt.")
    a("#")
    a("# Let op: `down -v` wist de in Mongo bewaarde device-config, dus dan moet")
    a("# dit script opnieuw. `down` zonder -v niet, en dan mag het niet dupliceren.")
    a("")
    a('GQL="http://monstermq:4000/graphql"')
    a("")
    a('echo "[park-init] Wachten op MonsterMQ..."')
    a('until curl -sf "http://monstermq:4000/" > /dev/null 2>&1; do sleep 3; done')
    a("")
    a("gql() {")
    a('  curl -sf -X POST "$GQL" -H "Content-Type: application/json" \\')
    a('    -d "{\\"query\\":\\"$1\\"}"')
    a("}")
    a("")
    a("TOTAL_OK=0; TOTAL_FAIL=0")
    a("")

    opc = [(wc, tags) for (wc, tags) in all_tags
           if wc["park"]["protocol"] in ("opc-ua", "opc-da")]

    for wc, tags in opc:
        park = wc["park"]
        dev = wc["equipment_id"]
        ns = "ns=%d" % park["opcua_namespace_index"]
        a("# ---------------------------------------------------------------- %s" % dev)
        a("# profiel %s, protocol %s" % (park["vendor_profile"], park["protocol"]))
        a('EXISTS=$(gql "{opcUaDevices{name}}")')
        a('case "$EXISTS" in')
        # 'Bestaat al' is niet hetzelfde als 'staat aan'. Een device kan
        # geregistreerd zijn en toch op enabled:false staan; skippen laat het
        # dan stil uit staan, zonder ingest en zonder foutmelding.
        # Zo gebeurde het in augustus 2026: cip-skid-01, homogeniser-01 en
        # intake-silo-01 zijn uitgezet omdat hun sims (profiel "park", niet
        # "park-slim") niet draaien en de connector anders eindeloos
        # UnknownHostException logt, 2 GB per dag. Wie daarna het volledige
        # park opzet, kreeg met de oude skip-tak drie stille machines.
        a("  *'\"%s\"'*)" % dev)
        a('    echo "[park-init] %s bestaat al; enabled afdwingen."' % dev)
        a('    ON=$(gql "mutation{opcUaDevice{toggle(name:\\\\\\"%s\\\\\\",'
          'enabled:true){success}}}")' % dev)
        a('    echo "[park-init] enable %s: $ON"' % dev)
        a("    ;;")
        a("  *)")
        a('    ADD=$(gql "mutation{opcUaDevice{add(input:{name:\\\\\\"%s\\\\\\",'
          'namespace:\\\\\\"raw/vla-park\\\\\\",nodeId:\\\\\\"local\\\\\\",enabled:true,'
          'config:{endpointUrl:\\\\\\"%s\\\\\\",securityPolicy:None,'
          'subscriptionSamplingInterval:1000.0}}){success errors}}}")'
          % (dev, park["opcua_endpoint"]))
        a('    echo "[park-init] add %s: $ADD"' % dev)
        a('    case "$ADD" in')
        a("      *'\"success\":true'*) : ;;")
        a('      *) echo "[park-init] FOUT: add %s mislukt"; TOTAL_FAIL=$((TOTAL_FAIL+1)) ;;' % dev)
        a("    esac")

        rows = []
        for t in tags:
            rows.append("%s|%s" % (t["native_name"], t["native_name"]))
            # De .Q-companion krijgt een eigen adres; zonder hartslag zou een
            # constant qualityword een keer publiceren en daarna nooit meer.
        q = profiles["profiles"][park["vendor_profile"]]["quality"]
        if q.get("carrier") == "companion_item":
            for t in tags:
                suf = q["companion_suffix"]
                rows.append("%s%s|%s%s" % (t["native_name"], suf,
                                           t["native_name"], suf))
        a('    ADDR_%s="' % re.sub(r"[^A-Za-z0-9]", "_", dev).upper())
        for r in rows:
            a(r)
        a('"')
        a("    for LINE in $ADDR_%s; do" % re.sub(r"[^A-Za-z0-9]", "_", dev).upper())
        a("      NODE=${LINE%%|*}; TOPIC=${LINE##*|}")
        a('      RES=$(gql "mutation{opcUaDevice{addAddress(deviceName:\\\\\\"%s\\\\\\",'
          'input:{address:\\\\\\"NodeId://%s;s=$NODE\\\\\\",'
          'topic:\\\\\\"%s/$TOPIC\\\\\\",publishMode:SEPARATE}){success errors}}}")'
          % (dev, ns, dev))
        a('      case "$RES" in')
        a("        *'\"success\":true'*) TOTAL_OK=$((TOTAL_OK+1)) ;;")
        a('        *) TOTAL_FAIL=$((TOTAL_FAIL+1)); echo "[park-init] WARN $NODE -> $RES" ;;')
        a("      esac")
        a("    done")
        a("    ;;")
        a("esac")
        a("")

    # ---------------------------------------------------------- archive group
    #
    # VIA GRAPHQL, niet via monstermq/config.yaml. Op de VPS is die YAML NIET
    # leidend: MonsterMQ bewaart zijn archive-config in Mongo (collectie
    # `archiveconfigs`) en leest de YAML alleen bij een lege installatie.
    #
    # Gemeten op 2026-08-09: van de vijf groepen in config.yaml waren er op de
    # VPS maar TWEE actief, `Default` (uit) en `dw_uns_archive`. De andere vier
    # horen bij scenario's die daar niet draaien en zijn nooit gemigreerd. Een
    # park-groep alleen in de YAML zetten had dus precies niets gedaan, en dat
    # ontdek je pas als je je afvraagt waarom er geen batchrapport-data is.
    crit = []
    for wc, _t in all_tags:
        park = wc["park"]
        c = park.get("critical_tag") or park["pvs"][0]["name"]
        crit.append('DairyWorks/Vla-B/%s/%s/Status/%s'
                    % (wc["_area_name"], wc["equipment_id"], c))
    filters = ",".join('\\\\\\"%s\\\\\\"' % t for t in crit)

    a("")
    a("# ---------------------------------------------------------- archive group")
    a("# Alleen de %d kritieke topics naar Mongo, 14 dagen. De overige %d" % (len(crit), len(all_tags) * 30 - len(crit)))
    a("# parksignalen gaan naar TDengine: dat is een historian en Mongo niet.")
    a("# Zonder deze beperking is het park ~4,3 miljoen documenten per dag.")
    a('AG=$(gql "{archiveGroups{name}}")')
    a('case "$AG" in')
    a("  *'\"dw_park_critical\"'*)")
    a("    # Bestaat al. Toch enable aanroepen: idempotent hoort CONVERGEREND te")
    a("    # zijn, niet 'bestaat al, klaar'. Een groep die ooit uitgeschakeld is")
    a("    # aangemaakt blijft anders voor altijd stil, en het init-script meldt")
    a("    # doodleuk succes. enable is veilig herhaalbaar.")
    a('    EN=$(gql "mutation{archiveGroup{enable(name:\\\"dw_park_critical\\\")'
      '{success message}}}")')
    a('    echo "[park-init] archive group bestond al, enable: $EN"')
    a("    ;;")
    a("  *)")
    a('    RES=$(gql "mutation{archiveGroup{create(input:{name:\\\\\\"dw_park_critical\\\\\\",'
      'topicFilter:[%s],lastValType:NONE,archiveType:MONGODB,'
      'archiveRetention:\\\\\\"14d\\\\\\"}){success message}}}")' % filters)
    a('    echo "[park-init] archive group: $RES"')
    a('    case "$RES" in')
    a("      *'\"success\":true'*)")
    a("        # Een archive group komt UITGESCHAKELD ter wereld. Zonder deze")
    a("        # enable-stap archiveert hij niets, en dat merk je pas als er een")
    a("        # batchrapport ontbreekt. De create-melding zegt het er zelf bij:")
    a("        # 'created successfully (disabled by default)'.")
    a('        EN=$(gql "mutation{archiveGroup{enable(name:\\\"dw_park_critical\\\")'
      '{success message}}}")')
    a('        echo "[park-init] archive group enable: $EN"')
    a("        ;;")
    a('      *) echo "[park-init] WAARSCHUWING: archive group niet aangemaakt. '
      'Het park draait dan wel, maar er gaat niets naar Mongo." ;;')
    a("    esac")
    a("    ;;")
    a("esac")
    a("")
    a('echo "[park-init] klaar: $TOTAL_OK adressen OK, $TOTAL_FAIL mislukt."')
    a('[ "$TOTAL_FAIL" -gt 0 ] && exit 1')
    a('echo "[park-init] MonsterMQ ingest %d OPC-machines -> raw/vla-park/#"' % len(opc))
    a("exit 0")
    a("")
    return "\n".join(L)


# ------------------------------------------------------------------------ main

def splice_archive_group(all_tags, check, drift):
    """De topic-lijst van archive group dw_park_critical.

    Per machine EEN topic: het punt dat een batchrapport of een auditvraag echt
    nodig heeft, aangewezen in het model via `critical_tag`. Zonder die
    beperking zou het park ~4,3 miljoen documenten per dag naar Mongo schrijven.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(ROOT)),
                        "monstermq", "config.yaml")
    current = read_text(path)
    if current is None:
        raise SystemExit("monstermq/config.yaml ontbreekt")

    rows = []
    for wc, tags in all_tags:
        park = wc["park"]
        crit = park.get("critical_tag")
        if not crit:
            # Standaard: de tag die of record is in een cross-check, anders de
            # eerste procesvariabele. Nooit alles.
            role = park.get("cross_check_role") or {}
            crit = role.get("tag") or park["pvs"][0]["name"]
        rows.append('        - "DairyWorks/Vla-B/%s/%s/Status/%s"'
                    % (wc["_area_name"], wc["equipment_id"], crit))

    block = "\n".join([
        "    # BEGIN GENERATED park-critical (tools/gen-park.py)",
        "    - Name: dw_park_critical",
        "      Topics:",
    ] + rows + [
        "      Enabled: true",
        "      RetentionDays: 14",
        "    # END GENERATED park-critical",
    ])

    pat = re.compile(r"    # BEGIN GENERATED park-critical.*?"
                     r"    # END GENERATED park-critical", re.S)
    if not pat.search(current):
        raise SystemExit("markers park-critical niet gevonden in monstermq/config.yaml")
    emit(path, pat.sub(lambda _m: block, current), check, drift)


def compose(all_tags):
    """docker-compose.park.yml. Alles achter profiles, dus zonder vlag is de
    stack byte-identiek aan vandaag."""
    L = []
    a = L.append
    a("# %s" % BANNER)
    a("#")
    a("# Lijn Vla-B: het machinepark. Overlay op docker-compose.slim.yml, NAAST")
    a("# docker-compose.vla.yml. De monoliet blijft ongemoeid draaien.")
    a("#")
    a("# Alles zit achter compose-profielen:")
    a("#   park-slim   een machine per leveranciersprofiel, de staande configuratie")
    a("#   park        het volledige park, omhoog voor een live demo")
    a("# Zonder --profile start er niets van dit bestand. Dat is de belangrijkste")
    a("# regressiebeveiliging: geen vlag, geen verandering.")
    a("#")
    a("# Geheugen- en CPU-limieten staan op ELKE machine. De VPS is 2 vCPU en")
    a("# 8 GB en draait de monoliet al; een doorgeslagen sim mag de broker niet")
    a("# kunnen uithongeren. Zie het VPS-hoofdstuk van het plan voor het budget.")
    a("")
    a("services:")
    a("")

    slim = {"pasteuriser-01", "separator-01", "blend-tank-01", "filler-01"}

    for wc, _tags in all_tags:
        park = wc["park"]
        name = park["container_name"]
        prof = '["park", "park-slim"]' if wc["equipment_id"] in slim else '["park"]'
        a("  # %s | %s | %s" % (wc["equipment_id"], park["vendor_profile"],
                                park["protocol"]))
        a("  %s:" % name)
        a("    build:")
        a("      context: ./packml-sim")
        a("      dockerfile: Dockerfile")
        a("    image: techflow/packml-sim:latest")
        a("    container_name: %s" % name)
        a("    restart: unless-stopped" + LOG_CAP)
        a("    entrypoint: [\"python\", \"park_runner.py\"]")
        a("    environment:")
        a("      UNIT_CONFIG: /units/%s.yaml" % wc["equipment_id"])
        a("      MQTT_HOST: monstermq")
        a("      MQTT_PORT: \"1883\"")
        a("      LOG_LEVEL: ${PARK_LOG_LEVEL:-INFO}")
        if park.get("tdengine_bypass"):
            # Het bypass-pad schrijft rechtstreeks in TDengine en heeft dus een
            # wachtwoord nodig. Dat komt uit .env op de VPS en staat bewust NIET
            # in de unit-config, want die is gegenereerd en gaat de repo in.
            # TD_USER en TD_PASS, niet TD_PASSWORD: dat is de naam die de rest
            # van de stack al gebruikt en die op de VPS in .env staat. Een
            # eigen variabelenaam verzinnen valt stil terug op de default en
            # dan werkt het pad niet, zonder dat iets faalt.
            a("      TD_BYPASS_USER: ${TD_USER:-root}")
            a("      TD_BYPASS_PASSWORD: ${TD_PASS:-taosdata}")
        a("    volumes:")
        a("      - ./scenarios/vla-batch/park-sim/units:/units:ro")
        # GEMETEN op de VPS, niet begroot: een asyncua-server kost ~92 MiB en
        # zat daarmee op 96% van de oorspronkelijke 96m. Dat is OOM-gebied, en
        # wat de kernel dan kiest is niet per se deze container. MQTT- en
        # Modbus-machines blijven ruim onder de 30 MiB.
        opcua = park["protocol"] in ("opc-ua", "opc-da")
        a("    mem_limit: %s" % ("192m" if opcua else "96m"))
        a("    cpus: 0.15")
        a("    networks:")
        a("      - idp-network")
        a("    depends_on:")
        a("      monstermq:")
        a("        condition: service_healthy")
        a("    profiles: %s" % prof)
        a("")

    pollable = [wc for wc, _ in all_tags
                if wc["park"]["protocol"] in ("modbus-tcp", "rest")]
    if pollable:
        a("  # Modbus TCP en REST kennen geen push, dus iemand moet ze POLLEN.")
        a("  # Bij die protocollen is de frequentie een keuze van de datalaag en")
        a("  # niet van de machine, en het tijdstempel is per definitie")
        a("  # aankomsttijd. Dat staat in de conditioning-regel en wordt niet")
        a("  # verzwegen. Machines: %s" % ", ".join(w["equipment_id"] for w in pollable))
        a("  vla-park-poller:")
        a("    build:")
        a("      context: ./scenarios/vla-batch/park-poller")
        a("      dockerfile: Dockerfile")
        a("    image: techflow/vla-park-poller:latest")
        a("    container_name: vla-park-poller")
        a("    restart: unless-stopped" + LOG_CAP)
        a("    environment:")
        a("      MQTT_HOST: monstermq")
        a("      MQTT_PORT: \"1883\"")
        a("      MODEL_DIR: /model")
        a("      RAW_ROOT: raw/vla-park")
        a("      MODBUS_PORT: \"5020\"")
        a("      POLL_INTERVAL_S: \"1.0\"")
        # De poller verbindt met CONTAINERS, niet met machines. Docker resolvet
        # de containernaam uit het model, en die is niet gelijk aan het
        # equipment_id. Dit moet dus expliciet mee: de fallback in poller.py is
        # het kale equipment_id, dat nergens naar resolvet. Gevolg was dat
        # blend-tank-01 nooit een byte heeft geleverd zonder dat iets faalde,
        # want een stille poller lijkt op een machine die niets doet.
        for w in pollable:
            env = w["equipment_id"].replace("-", "_").upper()
            host = w["park"]["container_name"]
            if w["park"]["protocol"] == "modbus-tcp":
                a("      MODBUS_HOST_%s: %s" % (env, host))
            else:
                a("      REST_URL_%s: http://%s:8000" % (env, host))
        a("    volumes:")
        a("      - ./scenarios/vla-batch/factory-model:/model:ro")
        a("    mem_limit: 128m")
        a("    cpus: 0.20")
        a("    networks:")
        a("      - idp-network")
        a("    depends_on:")
        a("      monstermq:")
        a("        condition: service_healthy")
        a("    profiles: %s" % ('["park", "park-slim"]'
                                if any(w["equipment_id"] in slim for w in pollable)
                                else '["park"]'))
        a("")

    sqlm = [wc for wc, _ in all_tags if wc["park"]["protocol"] == "sql"]
    if sqlm:
        a("  # De silo zonder technisch excuus. Twee machines loggen naar een")
        a("  # Postgres-tabel; niemand heeft hem ooit aangesloten. Geen protocolgat,")
        a("  # geen verouderd apparaat: gewoon een database die niemand leest.")
        a("  #")
        a("  # Postgres en GEEN SQL Server: die eist ~2 GB als ondergrens en die")
        a("  # hebben we niet naast de rest op een 8 GB VPS. Het punt van dit")
        a("  # eiland is niet het merk van de database.")
        a("  vla-park-db:")
        a("    image: postgres:16-alpine")
        a("    container_name: vla-park-db")
        a("    restart: unless-stopped" + LOG_CAP)
        a("    environment:")
        a("      POSTGRES_DB: vendor_e")
        a("      POSTGRES_USER: vendor_e")
        a("      POSTGRES_PASSWORD: ${PARK_DB_PASSWORD:-vendor_e}")
        a("      PGDATA: /var/lib/postgresql/data/pgdata")
        a("    volumes:")
        a("      - park-db-data:/var/lib/postgresql/data")
        a("    mem_limit: 192m")
        a("    cpus: 0.20")
        a("    healthcheck:")
        a("      test: [\"CMD-SHELL\", \"pg_isready -U vendor_e -d vendor_e\"]")
        a("      interval: 10s")
        a("      timeout: 5s")
        a("      retries: 5")
        a("    networks:")
        a("      - idp-network")
        a("    profiles: [\"park\"]")
        a("")
        a("  vla-park-gateway:")
        a("    build:")
        a("      context: ./scenarios/vla-batch/park-gateway")
        a("      dockerfile: Dockerfile")
        a("    image: techflow/vla-park-gateway:latest")
        a("    container_name: vla-park-gateway")
        a("    restart: unless-stopped" + LOG_CAP)
        a("    environment:")
        a("      MQTT_HOST: monstermq")
        a("      MQTT_PORT: \"1883\"")
        a("      MODEL_DIR: /model")
        a("      RAW_ROOT: raw/vla-park")
        a("      PG_HOST: vla-park-db")
        a("      PG_DB: vendor_e")
        a("      PG_USER: vendor_e")
        a("      PG_PASSWORD: ${PARK_DB_PASSWORD:-vendor_e}")
        a("      POLL_INTERVAL_S: \"2.0\"")
        a("    volumes:")
        a("      - ./scenarios/vla-batch/factory-model:/model:ro")
        a("    mem_limit: 128m")
        a("    cpus: 0.20")
        a("    networks:")
        a("      - idp-network")
        a("    depends_on:")
        a("      vla-park-db:")
        a("        condition: service_healthy")
        a("      monstermq:")
        a("        condition: service_healthy")
        a("    profiles: [\"park\"]")
        a("")

    a("  # Registreert alleen de OPC-UA- en OPC-DA-machines bij MonsterMQ.")
    a("  # Modbus, MQTT, REST en SQL lopen buiten de broker om.")
    a("  vla-park-init:")
    a("    image: curlimages/curl:latest")
    a("    container_name: vla-park-init")
    a("    restart: \"no\"")
    a("    entrypoint: [\"sh\", \"/init/init-park.sh\"]")
    a("    volumes:")
    a("      - ./scenarios/vla-batch/monstermq-init:/init:ro")
    a("    networks:")
    a("      - idp-network")
    a("    depends_on:")
    a("      monstermq:")
    a("        condition: service_healthy")
    a("    profiles: [\"park\", \"park-slim\"]")
    a("")

    a("  # Connect -> Condition -> Model. Protocol-agnostisch: hij weet niet via")
    a("  # welk protocol een waarde binnenkwam, en dat hoort ook zo.")
    a("  vla-park-conditioner:")
    a("    build:")
    a("      context: ./scenarios/vla-batch/park-conditioner")
    a("      dockerfile: Dockerfile")
    a("    image: techflow/vla-park-conditioner:latest")
    a("    container_name: vla-park-conditioner")
    a("    restart: unless-stopped" + LOG_CAP)
    a("    environment:")
    a("      MQTT_HOST: monstermq")
    a("      MQTT_PORT: \"1883\"")
    a("      MODEL_DIR: /model")
    a("      CANONICAL_ROOT: DairyWorks/Vla-B")
    a("      RAW_ROOT: raw/vla-park")
    a("      HTTP_PORT: \"8080\"")
    a("      TZ: Europe/Amsterdam")
    a("    volumes:")
    a("      - ./scenarios/vla-batch/factory-model:/model:ro")
    a("    mem_limit: 256m")
    a("    cpus: 0.30")
    a("    networks:")
    a("      - idp-network")
    a("    depends_on:")
    a("      monstermq:")
    a("        condition: service_healthy")
    a("    profiles: [\"park\", \"park-slim\"]")
    a("")

    a("  # Storingsscenario's die vanzelf lopen, op BATCHOVERGANGEN en niet op een")
    a("  # wandklok: een scenario dat op minuten tikt loopt uit de pas met een")
    a("  # fabriek die je hebt versneld of stilgezet. De cursor staat in Mongo,")
    a("  # zodat een herstart hervat in plaats van de curve opnieuw op te bouwen.")
    a("  vla-park-scenario:")
    a("    build:")
    a("      context: ./scenarios/vla-batch/park-scenario")
    a("      dockerfile: Dockerfile")
    a("    image: techflow/vla-park-scenario:latest")
    a("    container_name: vla-park-scenario")
    a("    restart: unless-stopped" + LOG_CAP)
    a("    environment:")
    a("      MQTT_HOST: monstermq")
    a("      MQTT_PORT: \"1883\"")
    a("      SCENARIO_DIR: /scenarios")
    a("      BATCH_ENGINE_URL: http://vla-batch-engine:8000")
    a("      MONGO_URL: mongodb://${MONGO_INITDB_ROOT_USERNAME}:${MONGO_INITDB_ROOT_PASSWORD}@mongo:27017/?authSource=admin")
    a("      MONGO_DB: ${MONGO_DB:-idp}")
    a("    volumes:")
    a("      - ./scenarios/vla-batch/park-scenario/scenarios:/scenarios:ro")
    a("    mem_limit: 128m")
    a("    cpus: 0.10")
    a("    networks:")
    a("      - idp-network")
    a("    depends_on:")
    a("      monstermq:")
    a("        condition: service_healthy")
    a("    profiles: [\"park\", \"park-slim\"]")
    a("")

    a("  # Eigen database idp_park met KEEP 90d. Zonder retentie loopt 100 GB in")
    a("  # ongeveer achttien maanden vol, en dat legt de demo op een zondag om.")
    a("  vla-park-tdengine-bridge:")
    a("    build:")
    a("      context: ./tdengine-poc")
    a("      dockerfile: Dockerfile")
    a("    image: techflow/vla-tdengine-bridge:latest")
    a("    container_name: vla-park-tdengine-bridge")
    a("    restart: unless-stopped" + LOG_CAP)
    a("    environment:")
    a("      MQTT_HOST: monstermq")
    a("      MQTT_PORT: \"1883\"")
    a("      # ALLEEN de canonieke UNS. raw/vla-park/# staat hier met opzet NIET:")
    a("      # ongemodelleerde data wordt niet opgeslagen.")
    a("      MQTT_TOPICS: \"DairyWorks/Vla-B/#\"")
    a("      TD_URL: \"http://vla-tdengine:6041\"")
    a("      TD_DB: ${TD_PARK_DB:-idp_park}")
    a("      # Losse tags site/line/area/machine/tagname, zodat je PER MACHINE")
    a("      # kunt queryen zonder LIKE op de topic-string:")
    a("      #   select last(value) from idp_park.telemetry")
    a("      #    where machine = 'pasteuriser-01' group by tagname;")
    a("      # De bridge van de MONOLIET blijft op legacy: de tag-set bepaalt de")
    a("      # sub-tabel-structuur van een bestaande super-tabel, en de Grafana-")
    a("      # dashboards hangen daaraan. Vandaar een eigen database idp_park.")
    a("      TAG_SCHEME: uns")
    a("      TD_USER: ${TD_USER:-root}")
    a("      TD_PASS: ${TD_PASS:-taosdata}")
    a("      FLUSH_SECONDS: \"1.0\"")
    a("      FLUSH_LINES: \"200\"")
    a("    mem_limit: 128m")
    a("    cpus: 0.20")
    a("    networks:")
    a("      - idp-network")
    a("    depends_on:")
    a("      vla-tdengine:")
    a("        condition: service_healthy")
    a("      monstermq:")
    a("        condition: service_healthy")
    a("    profiles: [\"park\", \"park-slim\"]")
    a("")
    if sqlm:
        a("volumes:")
        a("  park-db-data:")
        a("    name: vla-park-db-data")
        a("")
    a("networks:")
    a("  idp-network:")
    a("    name: idp-network")
    a("    external: true")
    a("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="faal bij drift, schrijf niets")
    args = ap.parse_args()

    model = load("isa95-vla.json")
    template = load("signal-template.json")
    profiles = load("vendor-profiles.json")

    # Het sjabloon moet met zichzelf kloppen voordat we er 360 tags op bouwen.
    exp = template["expected_counts"]
    assert len(template["slots"]) == exp["slots_total"], "sjabloon telt geen 30 slots"
    by_group = {}
    by_class = {}
    for s in template["slots"]:
        by_group[s["group"]] = by_group.get(s["group"], 0) + 1
        by_class[s["sampling_class"]] = by_class.get(s["sampling_class"], 0) + 1
    assert by_group == exp["by_group"], (by_group, exp["by_group"])
    assert by_class == exp["by_sampling_class"], (by_class, exp["by_sampling_class"])

    drift = []
    all_tags = []
    aliases = []
    conditioning = []
    faults = {}

    for line, area, wc in park_work_centers(model):
        wc["_area_name"] = area["name"]
        tags = expand_slots(wc, template, profiles)
        all_tags.append((wc, tags))
        park = wc["park"]
        base = "DairyWorks/Vla-B/%s/%s/Status" % (area["name"], wc["equipment_id"])
        raw_base = "%s/%s" % (profiles["raw_root"], wc["equipment_id"])

        wc_rules = []
        for t in tags:
            aliases.append({
                "legacy_tag": "%s/%s" % (raw_base, t["native_name"]),
                "canonical_signal_uuid": t["signal_uuid"],
                "canonical_topic": "%s/%s" % (base, t["name"]),
                "canonical_id": t["canonical_id"],
                "canonical_unit": t["canonical_unit"],
                "vendor": t["profile"],
                "protocol": t["protocol"],
                "native_name": t["native_name"],
                "retired_at": None,
            })
            rule = conditioning_rule(t, template, profiles)
            rule["raw_topic"] = "%s/%s" % (raw_base, t["native_name"])
            rule["canonical_topic"] = "%s/%s" % (base, t["name"])
            conditioning.append(rule)
            wc_rules.append(rule)

        faults[wc["equipment_id"]] = {
            "physics_type": wc["physics_type"],
            "vendor": park["vendor_profile"],
            "faults": park["faults"],
        }

        emit(os.path.join(UNITS_DIR, "%s.yaml" % wc["equipment_id"]),
             unit_yaml(line, area, wc, tags, wc_rules, template, profiles),
             args.check, drift)

    n_machines = len(all_tags)
    n_tags = sum(len(t) for _, t in all_tags)

    emit(os.path.join(MODEL_DIR, "park-aliases.json"), jdump({
        "_generated_by": BANNER,
        "_uuid_namespace": str(UUID_NS),
        "_uuid_note": "canonical_signal_uuid = uuid5(namespace, canonical_id). "
                      "Afgeleid van de CANONIEKE id, nooit van de leveranciersnaam. "
                      "Hernoemt een leverancier zijn item, dan verandert legacy_tag "
                      "en blijft de identiteit staan, dus de historian houdt een "
                      "doorlopende reeks in plaats van stil een tweede te beginnen. "
                      "Een rij met retired_at resolvet nog bij lezen maar publiceert niet meer.",
        "machines": n_machines,
        "signals": n_tags,
        "aliases": aliases,
    }), args.check, drift)

    emit(os.path.join(MODEL_DIR, "park-conditioning.json"), jdump({
        "_generated_by": BANNER,
        "_refusals": profiles["refusal_rules"],
        "rules": conditioning,
    }), args.check, drift)

    emit(os.path.join(MODEL_DIR, "park-faults.json"), jdump({
        "_generated_by": BANNER,
        "_note": "Per machine de storingen die de fysica ECHT implementeert. "
                 "packml-sim/selftest_park.py vergelijkt dit met het FAULTS-attribuut "
                 "op de physics-klasse, zodat de catalogus geen storing kan claimen "
                 "die niet bestaat.",
        "machines": faults,
    }), args.check, drift)

    emit(os.path.join(INIT_DIR, "init-park.sh"),
         init_park(model, template, profiles, all_tags), args.check, drift)

    emit(os.path.join(ROOT, "docker-compose.park.yml"),
         compose(all_tags), args.check, drift)

    splice_archive_group(all_tags, args.check, drift)

    n_mono = splice_monolith(model, args.check, drift)

    if args.check:
        if drift:
            print("DRIFT: %d gegenereerd artefact(en) lopen achter op het model.\n"
                  % len(drift), file=sys.stderr)
            for rel, d in drift:
                print("--- %s" % rel, file=sys.stderr)
                print(d, file=sys.stderr)
                print("", file=sys.stderr)
            print("Draai: python tools/gen-park.py", file=sys.stderr)
            return 1
        print("gen-park --check: geen drift (%d machines, %d parksignalen, "
              "%d monoliet-adressen)" % (n_machines, n_tags, n_mono))
        return 0

    print("\nklaar: %d machines, %d parksignalen, %d monoliet-adressen"
          % (n_machines, n_tags, n_mono))
    return 0


if __name__ == "__main__":
    sys.exit(main())
