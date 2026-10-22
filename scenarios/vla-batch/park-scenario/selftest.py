# -*- coding: utf-8 -*-
"""Selftest van de scenario-runner. Geen broker, geen batch-engine, geen Mongo.

Wat hier bewezen wordt is het gedrag waar je tijdens een demo op vertrouwt:

  - een scenario noemt geen storing die de machine niet kent (dat zou pas
    halverwege je demo blijken, en dan sta je te improviseren)
  - het reageert op de OVERGANG en niet op elke herhaling van dezelfde toestand
  - een herstart hervat bij de cursor en begint niet opnieuw, want opnieuw
    afspelen levert een andere vervuilingscurve op dan die op je slide staat

Draai: python selftest.py   (exit 0 = alles goed)
"""

from __future__ import annotations

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from runner import Cursor, Runner, load_scenarios  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def real_catalogue():
    """De ECHTE catalogus uit park-faults.json, niet een verzonnen lijstje."""
    with io.open(os.path.join(ROOT, "factory-model", "park-faults.json"),
                 encoding="utf-8") as fh:
        machines = json.load(fh)["machines"]
    return {"machines": [{"equipment_id": k, "faults": v["faults"]}
                         for k, v in machines.items()]}


def main():
    scens = load_scenarios(os.path.join(HERE, "scenarios"))
    check("1. scenario's geladen", len(scens) >= 1,
          ", ".join("%s (%d stappen)" % (s.id, len(s.steps)) for s in scens))
    if not scens:
        return _report()

    # --- 2. elke stap noemt een storing die de machine ECHT kent -----------
    cat = real_catalogue()
    problems = [p for s in scens for p in s.validate(cat)]
    check("2. geen spookstoringen in de scenario's", not problems,
          "\n       ".join(problems[:5]) if problems
          else "%d stappen gevalideerd tegen park-faults.json"
               % sum(len(s.steps) for s in scens))

    # --- 3. een verzonnen storing wordt WEL gevangen ------------------------
    # Anders bewijst test 2 alleen dat de validator niets doet.
    from runner import Scenario
    bogus = Scenario({"id": "SCN-BOGUS", "steps": [
        {"at_trigger": 1, "machine": "pasteuriser-01", "fault": "f99"},
        {"at_trigger": 2, "machine": "bestaat-niet", "fault": "f8"},
        {"at_trigger": 3, "machine": "pasteuriser-01", "fault": "f8",
         "magnitude": 7.0}]})
    found = bogus.validate(cat)
    check("3. de validator vangt onzin ook echt", len(found) == 3,
          "%d van 3 problemen gevonden: %s"
          % (len(found), [p.split(": ", 1)[1][:38] for p in found]))

    # --- 4. reageert op overgangen, met de juiste volgorde ------------------
    sc = [s for s in scens if s.id == "SCN-FOULING-HX"][0]
    applied = []
    r = Runner([sc], Cursor(None), post=lambda step: (applied.append(step) or
                                                      {"ok": True}))
    for _ in range(6):
        r.on_trigger(sc.trigger_topic, "COMPLETE")
    mags = [(s["machine"], s["fault"], s["magnitude"]) for s in applied]
    check("4. zes batchovergangen leveren de zes oplopende stappen",
          len(applied) == len(sc.steps)
          and mags[0] == ("pasteuriser-01", "f8", 0.10)
          and mags[-1] == ("preheater-01", "f8", 0.55),
          "%d stappen toegepast, magnitudes %s"
          % (len(applied),
             [m[2] for m in mags if m[0] == "pasteuriser-01"]))

    # --- 5. een ander topic of een andere waarde doet NIETS ----------------
    before = len(applied)
    r.on_trigger("DairyWorks/Vla/Batch/Status/state", "COOKING")
    r.on_trigger("DairyWorks/Vla/Cook/cook-unit-01/Status/temp_C", "COMPLETE")
    check("5. alleen de juiste overgang telt", len(applied) == before,
          "geen extra stappen na COOKING of een ander topic")

    # --- 6. herstart hervat bij de cursor, niet vanaf stap 1 ---------------
    cur = Cursor(None)
    a1 = []
    r1 = Runner([sc], cur, post=lambda s: (a1.append(s) or {"ok": True}))
    for _ in range(3):
        r1.on_trigger(sc.trigger_topic, "COMPLETE")
    # "herstart": nieuwe Runner, DEZELFDE cursor
    a2 = []
    r2 = Runner([sc], cur, post=lambda s: (a2.append(s) or {"ok": True}))
    r2.on_trigger(sc.trigger_topic, "COMPLETE")
    check("6. na een herstart gaat hij verder waar hij was",
          cur.get(sc.id) == 4 and a2 and a2[0]["magnitude"] == 0.48,
          "cursor=%d, eerste stap na herstart magnitude=%s (moet 0.48 zijn, "
          "niet 0.10)" % (cur.get(sc.id), a2[0]["magnitude"] if a2 else None))

    # --- 7. loop begint opnieuw en ruimt eerst op --------------------------
    cleared = {"n": 0}
    a3 = []
    r3 = Runner([sc], Cursor(None), post=lambda s: (a3.append(s) or {"ok": True}),
                clear=lambda: cleared.__setitem__("n", cleared["n"] + 1))
    for _ in range(sc.last_trigger + 1):
        r3.on_trigger(sc.trigger_topic, "COMPLETE")
    check("7. na de laatste stap: eerst schoon, dan opnieuw",
          cleared["n"] == 1 and a3[-1]["magnitude"] == 0.10,
          "clear-all %dx aangeroepen, laatste stap magnitude=%s"
          % (cleared["n"], a3[-1]["magnitude"]))

    return _report()


def _report():
    print("\n=== park-scenario selftest ===")
    ok = True
    for name, passed, detail in results:
        ok = ok and passed
        print("[%s] %s" % ("PASS" if passed else "FAIL", name))
        if detail:
            print("       %s" % detail)
    print("=" * 30)
    print("RESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
