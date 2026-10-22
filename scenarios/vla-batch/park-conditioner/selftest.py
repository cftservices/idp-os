# -*- coding: utf-8 -*-
"""Offline selftest van de Condition/Model-laag. Geen broker, geen netwerk.

De hoofdtest is de ROUND-TRIP, en die is met opzet KRUISLINGS: de sim verminkt
met packml-sim/vendor/distort.py, de conditioner repareert met condition.py.
Twee losse implementaties van dezelfde afspraak, allebei gegenereerd uit
hetzelfde model. Zouden ze dezelfde functie delen, dan bewees de test alleen dat
een functie zichzelf kan omkeren.

Waarom dit de waardevolste test in het hele plan is: het teruggedraaide werk van
3 augustus legt vast dat een ontbrekende liter-naar-gallon-conversie ooit een
heel eiland omkeerde, en dat vijf van zijn echte fouten pas bij de eerste live
run zichtbaar werden. Deze test vangt die hele klasse in een seconde, zonder dat
er iets draait.

Draai: python selftest.py   (exit 0 = alles goed)
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # scenarios/vla-batch
IDP = os.path.dirname(os.path.dirname(ROOT))      # sub-os/idp-os
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(IDP, "packml-sim"))

from condition import (condition, suppress, is_stale, map_quality,   # noqa: E402
                       resolve_timestamp, Refused, GOOD, BAD, UNCERTAIN)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def _tolerance(rule):
    """Een halve kwantisatiestap, teruggerekend naar de canonieke eenheid.

    Elke schaalvorm gooit een andere hoeveelheid informatie weg. Alleen
    integer-tienden meerekenen geeft valse alarmen op affine_span (vendor-c,
    ruwe counts over een EU-bereik) en op string-waarden (vendor-d, twee
    decimalen), en dan lijkt een correcte conversie fout.
    """
    kind = rule.get("scaling_kind", "none")
    if kind == "integer_tenths":
        step = abs(float(rule.get("scale", 0.1)))
    elif kind == "affine_span":
        lo = float(rule.get("eu_min_native", rule.get("eu_min", 0.0)))
        hi = float(rule.get("eu_max_native", rule.get("eu_max", 100.0)))
        rlo = float(rule.get("raw_min", 0))
        rhi = float(rule.get("raw_max", 27648))
        step = abs(hi - lo) / max(abs(rhi - rlo), 1.0)
    elif kind == "string_values":
        step = 0.01
    elif kind == "integer_milli_on":
        step = abs(float(rule.get("scale", 0.001)))
    else:
        step = 0.0
    conv = rule.get("unit_conversion")
    if conv:
        step *= abs(float(conv["scale"]))
    # Marge van 0,1% op de halve stap. Een halve kwantisatiestap is het
    # THEORETISCH maximum van de afrondfout, maar een waarde die exact op de
    # grens valt kan er door drijvende-kommaruis een haar overheen gaan. Dat gaf
    # ongeveer een op de twintig runs een vals alarm, en een test die soms faalt
    # is erger dan geen test: dan leer je hem negeren.
    return max(step * 0.5 * 1.001 + 1e-9, 1e-6)


def load_rules():
    p = os.path.join(ROOT, "factory-model", "park-conditioning.json")
    with io.open(p, encoding="utf-8") as fh:
        return json.load(fh)["rules"]


def main():
    rules = load_rules()
    by_id = {r["canonical_id"]: r for r in rules}
    check("1. conditioning-regels geladen", len(rules) > 0, "%d regels" % len(rules))

    # --- 2. de kruislingse round-trip --------------------------------------
    try:
        import random
        import yaml
        from park_runner import ParkMachine

        # ALLE machines, niet alleen de pasteur. Elk vendor-profiel heeft een
        # eigen schaalvorm, en een test die er maar een ziet mist per definitie
        # de fouten in de andere vier.
        import glob
        units = sorted(glob.glob(os.path.join(ROOT, "park-sim", "units", "*.yaml")))
        machines = {}
        bad, checked, refused = [], 0, 0
        now = dt.datetime.now(dt.timezone.utc)

        for unit in units:
            cfg = yaml.safe_load(io.open(unit, encoding="utf-8"))
            machine = ParkMachine(cfg, rng=random.Random(20260809))
            machines[machine.unit_id] = machine

            machine.sm.command("reset")
            for _ in range(600):
                machine.step(0.25)
                if machine.sm.state.name == "IDLE":
                    machine.sm.command("start")

            for name, want in machine.signals.read().items():
                rule = by_id["%s:%s" % (machine.unit_id, name)]
                res = machine.distorter.to_native(name, want, now=now)
                try:
                    out = condition(rule, res.value, quality_raw=res.quality_raw,
                                    raw_ts=res.timestamp, received_at=now)
                except Refused:
                    refused += 1
                    continue
                got = out["value"]
                checked += 1

                if rule.get("datatype") == "String":
                    if str(got) != str(want):
                        bad.append("%s/%s: %r -> %r -> %r"
                                   % (machine.unit_id, name, want, res.value, got))
                    continue

                tol = _tolerance(rule)
                if abs(float(got) - float(want)) > tol:
                    bad.append("%s/%s: %.6f -> %r -> %.6f (afwijking %.6g > tol %.6g)"
                               % (machine.unit_id, name, want, res.value, got,
                                  abs(float(got) - float(want)), tol))

        machine = machines["pasteuriser-01"]
        canonical = machine.signals.read()
        check("2. KRUISLINGSE ROUND-TRIP sim -> conditioner, alle machines",
              not bad and checked > 0,
              "\n       ".join(bad) if bad
              else "%d punten over %d machines kloppen, %d geweigerd (niet-numeriek)"
                   % (checked, len(machines), refused))

        # --- 3. de eenheid komt echt mee -----------------------------------
        rule = by_id["pasteuriser-01:temp_out_C"]
        res = machine.distorter.to_native("temp_out_C", canonical["temp_out_C"], now=now)
        out = condition(rule, res.value, quality_raw=res.quality_raw,
                        raw_ts=res.timestamp, received_at=now)
        check("3. het canonieke bericht draagt eenheid, kwaliteit, tijd en herkomst",
              out["unit"] == "°C" and out["quality"] in (GOOD, BAD, UNCERTAIN)
              and out["ts"].endswith("Z") and out["signal_uuid"]
              and out["ts_source"] == "receive",
              "raw=%r -> %.2f %s, quality=%s, ts_source=%s, uuid=%s"
              % (res.value, out["value"], out["unit"], out["quality"],
                 out["ts_source"], out["signal_uuid"][:8]))
    except ImportError as e:
        check("2. KRUISLINGSE ROUND-TRIP sim -> conditioner", False,
              "packml-sim niet importeerbaar: %s" % e)

    # --- 4. een niet-numerieke waarde wordt GEWEIGERD, niet nul ------------
    r = dict(by_id["pasteuriser-01:temp_out_C"])
    r["scaling_kind"] = "string_values"
    r["null_tokens"] = ["N/A", "", "###"]
    refused_ok = True
    for tok in ("N/A", "", "###", "  "):
        try:
            condition(r, tok)
            refused_ok = False
        except Refused:
            pass
    # en een geldige string blijft gewoon werken
    ok_val = condition(r, "72.40")["value"]
    check("4. niet-numeriek wordt geweigerd, nooit naar 0 geforceerd",
          refused_ok and ok_val > 0,
          "vier tokens geweigerd, '72.40' -> %.2f" % ok_val)

    # --- 5. ontbrekende kwaliteit is UNCERTAIN, nooit GOOD -----------------
    none_rule = {"quality_source": "none", "missing_quality_means": UNCERTAIN}
    da_rule = by_id["pasteuriser-01:temp_out_C"]
    check("5. ontbrekende kwaliteit is UNCERTAIN, nooit een aangenomen GOOD",
          map_quality(none_rule, None) == UNCERTAIN
          and map_quality(da_rule, None) == UNCERTAIN
          and map_quality(da_rule, 192) == GOOD
          and map_quality(da_rule, 64) == UNCERTAIN
          and map_quality(da_rule, 0) == BAD,
          "192->GOOD, 64->UNCERTAIN, 0->BAD, ontbrekend->UNCERTAIN")

    # --- 6. StatusCode is een bitveld, geen boolean ------------------------
    ua_rule = {"quality_source": "opcua-statuscode"}
    check("6. OPC-UA StatusCode wordt op severity-bits gemaskeerd",
          map_quality(ua_rule, 0) == GOOD
          and map_quality(ua_rule, 0x80000000) == BAD
          and map_quality(ua_rule, 0x40000000) == UNCERTAIN
          and map_quality(ua_rule, 0x80AB0000) == BAD
          and map_quality(ua_rule, 0x00AB0000) == GOOD,
          "subcodes in de onderste bits veranderen de severity niet")

    # --- 7. de deadband laat een kwaliteitswissel ALTIJD door --------------
    dbr = {"deadband": 1.0}
    same = {"value": 10.0, "quality": GOOD}
    tiny = {"value": 10.2, "quality": GOOD}
    qflip = {"value": 10.2, "quality": BAD}
    check("7. deadband onderdrukt ruis maar nooit een kwaliteitswissel",
          suppress(dbr, same, tiny) and not suppress(dbr, same, qflip)
          and not suppress(dbr, same, {"value": 12.0, "quality": GOOD}),
          "0.2 onderdrukt, 2.0 doorgelaten, GOOD->BAD altijd doorgelaten")

    # --- 8. tijdstempels ----------------------------------------------------
    now = dt.datetime(2026, 8, 9, 12, 0, 0, tzinfo=dt.timezone.utc)
    t_none, l_none = resolve_timestamp({"timestamp_source": "none"}, None, now)
    t_ms, l_ms = resolve_timestamp({"timestamp_source": "epoch-ms"},
                                   1785240000000, now)
    t_tz, l_tz = resolve_timestamp(
        {"timestamp_source": "local-no-timezone",
         "timestamp_format": "%Y-%m-%d %H:%M:%S",
         "assume_tz": "Europe/Amsterdam"}, "2026-08-09 14:00:00", now)
    t_guess, l_guess = resolve_timestamp(
        {"timestamp_source": "local-no-timezone",
         "timestamp_format": "%Y-%m-%d %H:%M:%S"}, "2026-08-09 14:00:00", now)
    check("8. tijdzone wordt gedeclareerd of eerlijk niet geraden",
          l_none == "receive" and l_ms == "source"
          and l_tz == "source-assumed-tz"
          and l_guess == "receive-no-tz-declared",
          "geen ts->%s | epoch->%s | met assume_tz->%s (%s) | zonder->%s"
          % (l_none, l_ms, l_tz, t_tz, l_guess))

    # --- 9. stilte is stale, geen nul --------------------------------------
    sr = {"stale_after_s": 30.0}
    check("9. stilte is stale en wordt niet als nul gerapporteerd",
          is_stale(sr, None)
          and is_stale(sr, now - dt.timedelta(seconds=31), now)
          and not is_stale(sr, now - dt.timedelta(seconds=5), now),
          "nooit gezien -> stale, 31s -> stale, 5s -> vers")

    # --- 10. elke regel heeft een stabiele identiteit ----------------------
    uu = [r["signal_uuid"] for r in rules]
    topics = [r["canonical_topic"] for r in rules]
    raws = [r["raw_topic"] for r in rules]
    check("10. identiteiten en topics zijn uniek",
          len(set(uu)) == len(uu) and len(set(topics)) == len(topics)
          and len(set(raws)) == len(raws),
          "%d regels, %d uuid, %d canonieke topics, %d raw-topics"
          % (len(rules), len(set(uu)), len(set(topics)), len(set(raws))))

    # --- 11. raw landt NOOIT onder DairyWorks/ -----------------------------
    # Archive group dairyworks_data matcht DairyWorks/# en zou elke
    # ongemodelleerde vendor-tag naar Mongo schrijven: ~1,7 GB per dag aan
    # rommel, een data-swamp binnen de demo die data-swamps veroordeelt.
    leak = [r["raw_topic"] for r in rules if r["raw_topic"].startswith("DairyWorks/")]
    check("11. raw-topics staan buiten DairyWorks/", not leak, leak[:3])

    # --- 12/13. integratie: sim -> conditioner -> canonieke UNS ------------
    # Zonder broker en zonder Docker. Dit is dezelfde weg die de berichten in
    # productie afleggen, alleen zonder transport ertussen.
    try:
        import crosscheck as xc
        from app import Conditioner

        checks = xc.build(xc.DEFAULT_CHECKS, "DairyWorks/Vla-B/DataQuality")
        cond = Conditioner(rules, checks)

        machine.sm.command("reset")
        for _ in range(200):
            machine.step(0.25)
            if machine.sm.state.name == "IDLE":
                machine.sm.command("start")

        published, t = [], 0.0
        for _ in range(400):
            t += 0.25
            machine.step(0.25)
            for topic, payload in machine.emit(t):
                published.extend(cond.handle(topic, payload))

        st = cond.status()
        canon = [p for p in published if p[0].startswith("DairyWorks/Vla-B/Cook/")]
        check("12. sim -> conditioner levert canonieke UNS, niets ongemapt",
              st["unmapped"] == 0 and len(canon) > 0 and st["msgs_out"] > 0,
              "in=%d uit=%d onderdrukt=%d geweigerd=%d ongemapt=%d, %d canonieke berichten"
              % (st["msgs_in"], st["msgs_out"], st["suppressed"], st["refused"],
                 st["unmapped"], len(canon)))

        # De cross-check: park-meting tegen de monoliet-meting. Eerst gelijk,
        # dan met een storing uit elkaar.
        def pump(seconds, monolith_temp):
            out = []
            tt = [t]
            for _ in range(int(seconds / 0.25)):
                tt[0] += 0.25
                machine.step(0.25)
                for tp, pl in machine.emit(tt[0]):
                    out.extend(cond.handle(tp, pl))
                out.extend(cond.handle(
                    "DairyWorks/Vla/Cook/cook-unit-01/Status/temp_C",
                    json.dumps({"value": monolith_temp, "unit": "°C",
                                "quality": "GOOD"})))
            t_new = tt[0]
            return out, t_new

        out_ok, t = pump(60, 88.0)
        alarms_quiet = [json.loads(p) for tp, p in out_ok if tp.endswith("/alarm")]
        deltas = [json.loads(p) for tp, p in out_ok if tp.endswith("/delta")]

        machine.inject_fault("f8", 0.9)
        out_bad, t = pump(400, 88.0)
        alarms = [json.loads(p) for tp, p in out_bad if tp.endswith("/alarm")]
        fired = [a for a in alarms if a["value"] is True]

        check("13. cross-check publiceert de onenigheid met beide getallen",
              len(deltas) > 0 and len(fired) > 0
              and "of record" in (fired[0]["message"] if fired else ""),
              (("rustig: %d delta's, %d alarmen | na f8: alarm '%s'"
                % (len(deltas), len(alarms_quiet), fired[0]["message"][:150]))
               if fired else
               "geen alarm afgegaan (%d delta's, %d alarm-berichten)"
               % (len(deltas), len(alarms))))
    except Exception as e:  # noqa: BLE001
        import traceback
        check("12/13. integratie sim -> conditioner", False,
              "exception: %s\n%s" % (e, traceback.format_exc()[:500]))

    print("\n=== park-conditioner selftest ===")
    ok = True
    for name, passed, detail in results:
        ok = ok and passed
        print("[%s] %s" % ("PASS" if passed else "FAIL", name))
        if detail:
            print("       %s" % detail)
    print("=" * 33)
    print("RESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
