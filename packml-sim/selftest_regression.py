"""Bewaakt dat bestaande sims niets merken van de park-uitbreidingen.

Eén image bedient alles: twintig bakkerij-units, vijf zuivel-units en straks
twaalf parkmachines. Een procesvariabele toevoegen aan bijvoorbeeld separator.py
verandert wat het DairyPlant-scenario publiceert, en dat wordt gearchiveerd door
archive group `dairy_data`. Een sim uitbreiden mag NOOIT een bestaande UNS
wijzigen; daarom zit elke toevoeging achter `extended_pvs` en daarom bestaat
deze test.

Wat hij doet: elke geregistreerde physics-module instantieren met lege config
(dus extended_pvs uit), een paar stappen laten draaien, en de VERZAMELING
sleutels van read() vergelijken met de vastgelegde momentopname hieronder.

Verandert er iets bewust, dan hoort de momentopname in dezelfde commit mee te
veranderen. Verandert er iets onbewust, dan valt deze test erover voordat een
dashboard in stilte een tag kwijtraakt.

Draai: python selftest_regression.py   (exit 0 = alles ongewijzigd)
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from packml import PackMLStateMachine, FaultInjector, UnitMode  # noqa: E402
from physics import PhysicsRegistry  # noqa: E402  (eager import vult de registry)

SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "selftest_regression_snapshot.json")


def current_keys():
    out = {}
    for type_name in sorted(PhysicsRegistry._by_type):  # noqa: SLF001
        cls = PhysicsRegistry.get(type_name)
        sm = PackMLStateMachine(unit_mode=UnitMode.PRODUCTION)
        sm.mach_design_speed = 120.0
        sm.set_mach_speed(100.0)
        faults = FaultInjector()
        try:
            unit = cls({}, sm, faults)
        except Exception as e:  # noqa: BLE001
            out[type_name] = ["<init-fout: %s>" % e]
            continue
        sm.command("reset")
        for _ in range(5):
            sm.step(0.2)
            if sm.state_name() == "IDLE":
                sm.command("start")
            faults.step(0.2)
            try:
                unit.step(0.2)
            except Exception as e:  # noqa: BLE001
                out[type_name] = ["<step-fout: %s>" % e]
                break
        else:
            try:
                out[type_name] = sorted(unit.read().keys())
            except Exception as e:  # noqa: BLE001
                out[type_name] = ["<read-fout: %s>" % e]
    return out


def main():
    cur = current_keys()

    if "--update" in sys.argv:
        with open(SNAPSHOT, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cur, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        print("momentopname bijgewerkt: %d modules" % len(cur))
        return 0

    if not os.path.exists(SNAPSHOT):
        print("Geen momentopname. Maak hem eerst aan:\n"
              "    python selftest_regression.py --update\n"
              "en commit hem samen met de wijziging.", file=sys.stderr)
        return 2

    with open(SNAPSHOT, encoding="utf-8") as fh:
        old = json.load(fh)

    problems = []
    for name in sorted(set(old) | set(cur)):
        a, b = old.get(name), cur.get(name)
        if a is None:
            # Een nieuwe module is geen regressie; wel iets om te melden.
            print("[NIEUW] %s: %d sleutels" % (name, len(b)))
            continue
        if b is None:
            problems.append("%s: module is VERDWENEN uit de registry" % name)
            continue
        if a != b:
            problems.append("%s: read()-sleutels gewijzigd\n    was: %s\n    nu : %s\n"
                            "    (extra sleutels horen achter extended_pvs te staan)"
                            % (name, a, b))

    print("\n=== packml-sim regressietest ===")
    if problems:
        for p in problems:
            print("[FAIL] %s" % p)
        print("=" * 32)
        print("RESULT: REGRESSIE. Bestaande scenario's zouden andere tags publiceren.")
        return 1
    print("[PASS] %d modules publiceren onveranderd" % len(old))
    print("=" * 32)
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
