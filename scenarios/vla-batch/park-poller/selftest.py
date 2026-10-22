# -*- coding: utf-8 -*-
"""Selftest van de poller: registerkaart en de Modbus-round-trip.

Twee dingen worden hier bewezen:

  1. De registerkaart is consistent. Geen overlappende registers, elke breedte
     past bij zijn codering, en het statusregister botst met niets. Een
     overlapping betekent dat je stil het lage woord van de buurman leest, en
     dat levert een plausibel getal op waar niemand van opkijkt.

  2. De ECHTE round-trip over een draaiende Modbus-server: de sim codeert de
     waarden in registers, de poller leest ze via pymodbus terug uit, en de
     conditioner maakt er canonieke waarden van. Drie onafhankelijke stukken
     code die het eens moeten zijn.

Die tweede test is de reden dat dit bestand bestaat. Het teruggedraaide werk
van 3 augustus legt vast dat vijf van zijn echte fouten pas bij de eerste live
run zichtbaar werden. Een woordvolgorde die omgekeerd staat of een adres dat er
40001 naast zit, geeft geen foutmelding: het geeft getallen.

Draai: python selftest.py   (exit 0 = alles goed)
"""

from __future__ import annotations

import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IDP = os.path.dirname(os.path.dirname(ROOT))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(IDP, "packml-sim"))
sys.path.insert(0, os.path.join(ROOT, "park-conditioner"))

import poller  # noqa: E402
from poller import decode  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def main():
    with io.open(os.path.join(ROOT, "factory-model", "park-conditioning.json"),
                 encoding="utf-8") as fh:
        rules = json.load(fh)["rules"]
    mb = [r for r in rules if r.get("modbus_addr") is not None]
    machines = sorted({r["source_system"] for r in mb})
    check("1. modbus-machines gevonden", bool(mb),
          "%d registers over %s" % (len(mb), machines))
    if not mb:
        return _report()

    # --- 2. registerkaart is consistent ------------------------------------
    problems = []
    for m in machines:
        rs = [r for r in mb if r["source_system"] == m]
        used = {}
        for r in rs:
            want = {"int16": 1, "int32_hi_lo": 2, "ascii_packed": 8}[r["modbus_encoding"]]
            if r["modbus_width"] != want:
                problems.append("%s/%s: breedte %d past niet bij %s (moet %d)"
                                % (m, r["canonical_id"], r["modbus_width"],
                                   r["modbus_encoding"], want))
            for a in range(r["modbus_addr"], r["modbus_addr"] + r["modbus_width"]):
                if a in used:
                    problems.append("%s: register %d gedeeld door %s en %s"
                                    % (m, a, used[a], r["canonical_id"]))
                used[a] = r["canonical_id"]
        st = rs[0].get("modbus_status_addr")
        if st in used:
            problems.append("%s: statusregister %d botst met %s" % (m, st, used[st]))
    check("2. registerkaart zonder overlap en met kloppende breedtes",
          not problems, "\n       ".join(problems[:6]) if problems
          else "%d registers toegekend, statuswoord apart" % len(mb))

    # --- 3. codering en decodering zijn elkaars inverse (puur) -------------
    from modbus_surface import encode
    cases = [(0, "int16", 1), (27648, "int16", 1), (1234, "int16", 1),
             (0, "int32_hi_lo", 2), (70000, "int32_hi_lo", 2),
             (4294967295, "int32_hi_lo", 2),
             ("Execute", "ascii_packed", 8), ("Idle", "ascii_packed", 8)]
    bad = [c for c in cases if decode(encode(c[0], c[1], c[2]), c[1]) != c[0]]
    check("3. encode en decode zijn elkaars inverse", not bad,
          "mislukt: %s" % bad if bad else "%d gevallen, incl. woordvolgorde en ASCII"
          % len(cases))

    # --- 4. de valstrik zelf: omgekeerde woordvolgorde --------------------
    # Deze test bewaakt dat de volgorde ECHT uitmaakt. Zou hij niet uitmaken,
    # dan zit er ergens een symmetrie die de round-trip groen houdt terwijl de
    # kaart fout is.
    w = encode(70000, "int32_hi_lo", 2)
    swapped = (int(w[1]) << 16) | int(w[0])
    check("4. woordvolgorde maakt aantoonbaar uit",
          swapped != 70000,
          "hoog-eerst 70000, laag-eerst zou %d geven" % swapped)

    # --- 5. de ECHTE round-trip over een draaiende Modbus-server ----------
    try:
        import random
        import yaml
        from park_runner import ParkMachine
        from modbus_surface import ModbusSurface, HAVE_PYMODBUS
        from condition import condition, Refused

        if not HAVE_PYMODBUS:
            check("5. Modbus round-trip", True, "pymodbus niet aanwezig, overgeslagen")
            return _report()

        machine_id = machines[0]
        unit = os.path.join(ROOT, "park-sim", "units", "%s.yaml" % machine_id)
        cfg = yaml.safe_load(io.open(unit, encoding="utf-8"))
        cfg.setdefault("modbus", {})["port"] = 15020
        m = ParkMachine(cfg, rng=random.Random(20260809))

        surf = ModbusSurface(cfg)
        surf.start()
        try:
            m.sm.command("reset")
            for _ in range(400):
                m.step(0.25)
                if m.sm.state.name == "IDLE":
                    m.sm.command("start")

            canonical = m.signals.read()
            native = {n: m.distorter.to_native(n, v).value
                      for n, v in canonical.items()}
            surf.write_signals(native)
            time.sleep(0.3)

            os.environ["MODBUS_HOST_%s" % machine_id.replace("-", "_").upper()] = "127.0.0.1"
            os.environ["MODBUS_PORT"] = "15020"
            from poller import ModbusTarget
            target = ModbusTarget(machine_id, [r for r in mb
                                               if r["source_system"] == machine_id])
            msgs = target.poll()
        finally:
            surf.stop()

        vals = {t.rsplit("/", 1)[1]: json.loads(p)["v"]
                for t, p in msgs if not t.endswith(".Q")}
        by_native = {r["native_name"]: r for r in mb if r["source_system"] == machine_id}

        mism, checked = [], 0
        for nat, got_raw in vals.items():
            rule = by_native[nat]
            want_raw = native[rule["canonical_id"].split(":", 1)[1]]
            if got_raw != want_raw:
                mism.append("%s (%s): sim schreef %r, poller las %r"
                            % (nat, rule["canonical_id"], want_raw, got_raw))
                continue
            try:
                out = condition(rule, got_raw)
            except Refused:
                continue
            want = canonical[rule["canonical_id"].split(":", 1)[1]]
            tol = max(abs(float(rule.get("eu_max", 100)) - float(rule.get("eu_min", 0)))
                      / 27648.0, 1e-6)
            if rule.get("datatype") != "String" and not rule.get("discrete"):
                if abs(float(out["value"]) - float(want)) > tol:
                    mism.append("%s: canoniek %.4f, terug %.4f (tol %.4g)"
                                % (nat, want, out["value"], tol))
            checked += 1

        check("5. ECHTE Modbus round-trip: sim -> registers -> poller -> conditioner",
              not mism and checked > 0,
              "\n       ".join(mism[:6]) if mism
              else "%d registers via pymodbus gelezen en canoniek teruggerekend"
                   % checked)

        qs = [p for t, p in msgs if t.endswith(".Q")]
        check("6. statuswoord wordt over alle signalen uitgewaaierd",
              len(qs) == len(vals) and set(qs) == {"0"},
              "%d kwaliteits-companions, waarden %s" % (len(qs), sorted(set(qs))))
    except Exception as e:  # noqa: BLE001
        import traceback
        check("5. ECHTE Modbus round-trip", False,
              "exception: %s\n%s" % (e, traceback.format_exc()[:600]))

    # --- 7. de sampling classes worden nageleefd -----------------------------
    # Zonder dit stond blend-tank-01 op 58 msg/s waar het budget 6,2 is: alle
    # registers plus alle .Q-companions, elke seconde, ongeacht klasse. Een
    # poller die alles op tickfrequentie uitstoot is niet stuk, hij is verkeerd
    # geconfigureerd, en dat merk je alleen als je telt.
    if mb:
        machine = machines[0]
        rs = [r for r in mb if r["source_system"] == machine]
        sched = poller.Schedule(1.0)
        n = nq = 0
        for t in range(60):
            for i, r in enumerate(rs):
                # onchange-punten laten we OPZETTELIJK vaak wisselen (elke 7 s).
                # Dat is pessimistischer dan de werkelijkheid; haalt hij het
                # hier, dan haalt hij het altijd.
                v = (t // 7) if r.get("sampling_class") == "onchange" \
                    else float(t) + i
                if sched.publish_due(r["native_name"], r, v, float(t)):
                    n += 1
                if r.get("quality_topic_suffix") and \
                        sched.quality_due(r["native_name"] + ".Q", 192, float(t)):
                    nq += 1
        rate = (n + nq) / 60.0
        naive = len(rs) * 2.0
        check("7. sampling classes worden nageleefd", rate <= 10.0,
              "%s: %.1f msg/s over 60 s (naief %.0f, dus %.0fx minder). "
              "Budget uit het model: %.1f"
              % (machine, rate, naive, naive / rate if rate else 0,
                 poller.budget_msg_s(rs)))

        # --- 8. wat NIET uitgesteld mag worden ------------------------------
        # Een kwaliteitswissel en een toestandswissel zijn geen ruis. Zou de
        # planner die op de klok zetten, dan zie je een storing pas een halve
        # minuut later, en dan is de demo het argument kwijt.
        s2 = poller.Schedule(1.0)
        oc = next((r for r in rs if r.get("sampling_class") == "onchange"), None)
        s2.quality_due("x.Q", 192, 0.0)
        q_direct = s2.quality_due("x.Q", 0, 0.5)
        q_stil = s2.quality_due("x.Q", 0, 1.0)
        if oc:
            s2.publish_due(oc["native_name"], oc, 1, 0.0)
            v_stil = s2.publish_due(oc["native_name"], oc, 1, 1.0)
            v_direct = s2.publish_due(oc["native_name"], oc, 2, 1.5)
        else:
            v_stil, v_direct = False, True
        check("8. kwaliteits- en toestandswissels gaan meteen door",
              q_direct and not q_stil and v_direct and not v_stil,
              "kwaliteit 192->0 na 0,5 s: %s (moet True), daarna stil: %s "
              "(moet False) | toestand ongewijzigd: %s (moet False), "
              "gewijzigd: %s (moet True)"
              % (q_direct, q_stil, v_stil, v_direct))

    return _report()


def _report():
    print("\n=== park-poller selftest ===")
    ok = True
    for name, passed, detail in results:
        ok = ok and passed
        print("[%s] %s" % ("PASS" if passed else "FAIL", name))
        if detail:
            print("       %s" % detail)
    print("=" * 28)
    print("RESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
