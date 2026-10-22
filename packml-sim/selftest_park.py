"""Offline selftest voor een parkmachine. Geen broker, geen netwerk.

De belangrijkste test hier is de ROUND-TRIP: elke canonieke waarde die de sim
verminkt tot een leveranciersgetal moet door de omgekeerde bewerking weer op
zijn oorspronkelijke waarde uitkomen, binnen de kwantisatiefout van de schaling.

Waarom dat de belangrijkste is. Het teruggedraaide werk van 3 augustus legt vast
dat een ontbrekende L-naar-gallon-conversie ooit een heel eiland omkeerde, en dat
vijf van zijn echte fouten pas bij de eerste live run zichtbaar werden. Een
round-trip vangt de hele conversieklasse offline, in een seconde, zonder dat er
ook maar iets draait.

Draai: python selftest_park.py [pad/naar/unit.yaml]   (exit 0 = alles goed)
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import yaml  # noqa: E402

from park_runner import ParkMachine  # noqa: E402
from vendor.distort import native_unit_to_canonical  # noqa: E402
from signals import EXPECTED_SLOTS  # noqa: E402

DEFAULT_UNIT = os.path.join(
    HERE, "..", "scenarios", "vla-batch", "park-sim", "units", "pasteuriser-01.yaml")

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def tolerance(d):
    """Een halve kwantisatiestap, teruggerekend naar de canonieke eenheid.

    Elke schaalvorm gooit informatie weg, en per vorm een andere hoeveelheid:

        integer_tenths   0,1 in leverancierseenheid
        affine_span      het EU-bereik gedeeld over 0..27648 counts
        string_values    twee decimalen, dus 0,01
        integer_milli_on de opgegeven schaal

    Alleen rekening houden met tienden geeft valse alarmen op elke andere vorm,
    en dat is precies waar deze test op afging voordat hij dit deed. Strakker
    dan een halve stap kan niet: dan test je de kwantisatie zelf.
    """
    kind = d.get("scaling_kind", "none")
    if kind == "integer_tenths":
        step = abs(float(d.get("scale", 0.1)))
    elif kind == "affine_span":
        lo = float(d.get("eu_min_native", d.get("eu_min", 0.0)))
        hi = float(d.get("eu_max_native", d.get("eu_max", 100.0)))
        rlo, rhi = float(d.get("raw_min", 0)), float(d.get("raw_max", 27648))
        step = abs(hi - lo) / max(abs(rhi - rlo), 1.0)
    elif kind == "string_values":
        step = 0.01
    elif kind == "integer_milli_on":
        step = abs(float(d.get("scale", 0.001)))
    else:
        step = 0.0
    conv = d.get("unit_conversion")
    if conv:
        step *= abs(float(conv["scale"]))
    # Marge van 0,1% op de halve stap. Een halve kwantisatiestap is het
    # THEORETISCH maximum van de afrondfout, maar een waarde die exact op de
    # grens valt kan er door drijvende-kommaruis een haar overheen gaan. Dat gaf
    # ongeveer een op de twintig runs een vals alarm, en een test die soms faalt
    # is erger dan geen test: dan leer je hem negeren.
    return max(step * 0.5 * 1.001 + 1e-9, 1e-6)


def undistort(raw_value, slot):
    """Native -> canoniek. Dit is wat de conditioner straks doet.

    Staat hier met opzet apart geimplementeerd en niet als import uit de
    conditioner: als beide kanten dezelfde functie zouden delen, bewijst de
    round-trip alleen dat de functie zichzelf kan omkeren, niet dat twee
    onafhankelijke implementaties het eens zijn.
    """
    d = slot.get("distort") or {}
    kind = d.get("scaling_kind", "none")

    if d.get("discrete") or slot["datatype"] == "String":
        return raw_value

    if kind == "integer_tenths":
        native = float(raw_value) * float(d["scale"])
    elif kind == "affine_span":
        lo = float(d.get("eu_min_native", d.get("eu_min", 0.0)))
        hi = float(d.get("eu_max_native", d.get("eu_max", 100.0)))
        rlo, rhi = float(d["raw_min"]), float(d["raw_max"])
        frac = (float(raw_value) - rlo) / (rhi - rlo) if rhi != rlo else 0.0
        native = lo + frac * (hi - lo)
    elif kind == "string_values":
        toks = d.get("null_tokens") or []
        if isinstance(raw_value, str) and raw_value in toks:
            raise ValueError("geweigerd: %r is geen getal" % raw_value)
        native = float(raw_value)
    elif kind == "integer_milli_on":
        native = float(raw_value) * float(d.get("scale", 0.001))
    else:
        native = float(raw_value)

    return native_unit_to_canonical(native, d.get("unit_conversion"))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_UNIT
    if not os.path.exists(path):
        print("unit-config niet gevonden: %s" % path, file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    import random
    machine = ParkMachine(cfg, rng=random.Random(20260809))

    # --- 1. exact 30 slots, geen dubbele namen -----------------------------
    try:
        canonical = machine.signals.read()
        check("1. exact %d canonieke signalen" % EXPECTED_SLOTS,
              len(canonical) == EXPECTED_SLOTS, "kreeg %d" % len(canonical))
    except Exception as e:
        check("1. exact %d canonieke signalen" % EXPECTED_SLOTS, False, "exception: %s" % e)
        canonical = {}

    natives = [s["native_name"] for s in cfg["signals"]]
    check("2. geen dubbele native namen", len(set(natives)) == len(natives),
          "%d namen, %d uniek" % (len(natives), len(set(natives))))

    # --- 3. elke bron bestaat echt -----------------------------------------
    # Een slot dat naar een niet-bestaande physics-key wijst mag NIET stil nul
    # worden: een nul is een meting en een ontbrekende bron is een gat.
    missing = []
    pr = machine.physics.read()
    for s in cfg["signals"]:
        # Schrijfbare slots zijn setpoints: die worden in SignalSet bijgehouden
        # en hoeven geen fysica-bron te hebben. Alleen METINGEN moeten ergens
        # vandaan komen, want een meting zonder bron is een verzonnen getal.
        if s.get("writable"):
            continue
        src = s.get("source", "")
        if src.startswith("physics."):
            key = src.split(".", 1)[1]
            if key not in pr and not hasattr(machine.physics, key):
                missing.append("%s <- %s" % (s["name"], src))
    check("3. elke gemeten bron bestaat echt", not missing, ", ".join(missing))

    # --- 4. gedeclareerde storingen bestaan in de fysica --------------------
    declared = set(cfg.get("faults") or [])
    implemented = set(getattr(type(machine.physics), "FAULTS", {}) or {})
    ghost = sorted(declared - implemented)
    check("4. geen spookstoringen in de catalogus", not ghost,
          "gedeclareerd maar niet geimplementeerd: %s" % ghost if ghost
          else "%d storingen: %s" % (len(declared), sorted(declared)))

    # --- 5. laat hem draaien ------------------------------------------------
    from packml import PackMLState
    machine.sm.command("reset")
    for _ in range(40):
        machine.step(0.25)
        if machine.sm.state == PackMLState.IDLE:
            machine.sm.command("start")
    check("5. machine draait na de opstartsequentie",
          machine.sm.state == PackMLState.EXECUTE,
          "toestand=%s" % machine.sm.state_name())

    # --- 6. DE ROUND-TRIP ---------------------------------------------------
    canonical = machine.signals.read()
    bad, refused, checked = [], 0, 0
    for name, value in canonical.items():
        slot = machine.slot_by_name[name]
        res = machine.distorter.to_native(name, value)
        try:
            back = undistort(res.value, slot)
        except ValueError:
            refused += 1
            continue
        if slot["datatype"] == "String":
            if str(back) != str(value):
                bad.append("%s: %r -> %r -> %r" % (name, value, res.value, back))
            checked += 1
            continue
        tol = tolerance(slot.get("distort") or {})
        if abs(float(back) - float(value)) > tol:
            bad.append("%s: %s -> %s -> %s (afwijking %.6g > tol %.6g)"
                       % (name, value, res.value, back,
                          abs(float(back) - float(value)), tol))
        checked += 1
    check("6. ROUND-TRIP canoniek -> native -> canoniek",
          not bad, "\n       ".join(bad) if bad
          else "%d van %d punten kloppen, %d geweigerd (niet-numeriek)"
               % (checked, len(canonical), refused))

    # --- 7. de eenheidsconversie doet echt iets -----------------------------
    # Een conversie die per ongeluk 1:1 is geeft een groene round-trip en een
    # fout getal op het scherm. Dus: controleer dat native ECHT afwijkt.
    conv_slots = [s for s in cfg["signals"]
                  if (s.get("distort") or {}).get("unit_conversion")]
    unchanged = []
    for s in conv_slots:
        v = canonical[s["name"]]
        if abs(float(v)) < 1e-6:
            continue
        res = machine.distorter.to_native(s["name"], v)
        native_in_eu = float(res.value) * float((s["distort"]).get("scale", 1.0))
        if abs(native_in_eu - float(v)) < 1e-6:
            unchanged.append("%s (%s)" % (s["name"], s["distort"]["unit_conversion"]["key"]))
    check("7. eenheidsconversies veranderen de waarde echt",
          not unchanged, "verdacht 1:1: %s" % unchanged if unchanged
          else "%d tags geconverteerd" % len(conv_slots))

    # --- 8. elke gedeclareerde storing doet ECHT iets -----------------------
    # Machine-onafhankelijk, want deze test moet straks op twaalf machines
    # draaien. Eerst uitregelen: meet je de nulmeting terwijl de machine nog
    # opstart, dan maskeert de aanloop het storingseffect en meet je niets.
    def run(steps):
        """Stappen EN de machine draaiend houden.

        Een batchmachine loopt zijn cyclus af en roept dan zelf stop() aan. In
        productie zet de follower hem bij de volgende batch weer aan; in deze
        test is er geen monoliet, dus zonder dit staat de mengtank na een
        cyclus stil en meet je bij elke storing keurig nul verschil. Dat leek
        op een storing die niets doet, terwijl de machine gewoon uit stond.
        """
        for _ in range(steps):
            machine.step(0.25)
            # ABORTED vraagt eerst CLEAR. Vanuit ABORTED is RESET geen geldige
            # PackML-overgang, dus een machine die eenmaal is getript blijft
            # anders voorgoed staan en elke meting daarna is nul.
            if machine.sm.state == PackMLState.ABORTED:
                machine.sm.command("clear")
            elif machine.sm.state in (PackMLState.STOPPED, PackMLState.COMPLETE):
                machine.sm.command("reset")
            elif machine.sm.state == PackMLState.IDLE:
                machine.sm.command("start")

    run(1600)
    before = machine.signals.read()

    # De grootheden waar een storing zich in HOORT te melden. Kan een storing
    # geen van deze bewegen, dan is het een knop die niets doet, en dat is
    # erger dan geen knop: in een demo prikt het publiek daar als eerste doorheen.
    watch = [cfg["signals"][5]["name"], cfg["signals"][6]["name"],
             "motor_current_A", "valve_pos_pct", "vibration_mm_s", "drive_out_pct"]
    watch = [w for w in watch if w in before]

    dead = []
    detail = []
    for fid in sorted(cfg.get("faults") or []):
        machine.clear_fault()
        run(400)
        base = machine.signals.read()
        machine.inject_fault(fid, 0.8)
        peak = {w: 0.0 for w in watch}
        for _ in range(1600):
            machine.step(0.25)
            # ABORTED vraagt eerst CLEAR. Vanuit ABORTED is RESET geen geldige
            # PackML-overgang, dus een machine die eenmaal is getript blijft
            # anders voorgoed staan en elke meting daarna is nul.
            if machine.sm.state == PackMLState.ABORTED:
                machine.sm.command("clear")
            elif machine.sm.state in (PackMLState.STOPPED, PackMLState.COMPLETE):
                machine.sm.command("reset")
            elif machine.sm.state == PackMLState.IDLE:
                machine.sm.command("start")
            s = machine.signals.read()
            for w in watch:
                try:
                    peak[w] = max(peak[w], abs(float(s[w]) - float(base[w])))
                except (TypeError, ValueError):
                    pass
        # Drempel: absolute vloer van 0,5 plus een halve procent van de
        # basiswaarde. Twee procent was te grof: een sensorbias van 1 graad op
        # 88 graden is op een veiligheidsband van 3 graden wel degelijk
        # significant, en die viel er stil doorheen.
        moved = {w: d for w, d in peak.items()
                 if d > max(abs(float(base[w])) * 0.005, 0.5)}
        if not moved:
            dead.append(fid)
        else:
            top = sorted(moved.items(), key=lambda kv: -kv[1])[:2]
            detail.append("%s -> %s" % (fid, ", ".join(
                "%s %+.1f" % (k, v) for k, v in top)))
        machine.clear_fault(fid)

    check("8. elke gedeclareerde storing is meetbaar zichtbaar",
          not dead,
          ("storingen zonder enig effect: %s" % dead) if dead
          else " | ".join(detail))
    machine.clear_fault()

    # --- 8b. de Solve, alleen voor een machine met een veiligheidsdivert -----
    # Tijdens de run meten en niet erna: na de trip stopt de machine en koelt
    # hij af, dus de eindtoestand laat juist NIET zien wat er gebeurd is.
    if "divert_position" in before and hasattr(machine.physics, "hold_min_c"):
        # Met run(), niet met een kale lus: check 8 laat de machine mogelijk
        # getript achter, en dan meet je een stilstaande machine en concludeer
        # je dat de divert niet werkt terwijl hij nooit is aangegaan.
        run(1600)
        base = machine.signals.read()
        machine.inject_fault("f8", 0.6)
        min_temp, max_divert = 1e9, 0.0
        for _ in range(2400):
            machine.step(0.25)
            s = machine.signals.read()
            min_temp = min(min_temp, s["temp_out_C"])
            max_divert = max(max_divert, s["divert_position"])
        hold_min = machine.physics.hold_min_c
        check("8b. vervuiling zakt door de holdgrens en divert (de Solve)",
              min_temp < hold_min and max_divert > 50.0,
              "temp %.2f -> laagste %.2f (holdgrens %.1f), divert max %.0f%%"
              % (base["temp_out_C"], min_temp, hold_min, max_divert))
        machine.clear_fault()

    # --- 9. gelaagde sampling houdt het berichtenvolume laag ----------------
    machine._last_pub.clear()
    machine._last_val.clear()
    machine._last_q = 0.0
    t, sent = 0.0, 0
    for _ in range(240):  # 60 s bij 0,25 s per tick
        t += 0.25
        machine.step(0.25)
        sent += len(machine.emit(t))
    rate = sent / 60.0
    check("9. berichtvolume blijft binnen het budget",
          rate <= 12.0, "%.1f msg/s over 60 s (budget ~6, plafond 12)" % rate)

    # --- 10. de payload draagt geen betekenis -------------------------------
    # Als de raw-payload al eenheid en kwaliteit zou bevatten, zou de
    # Condition-stap een luxe zijn in plaats van een noodzaak. Hij hoort kaal
    # te zijn, en dat is hier een test en geen aanname.
    msgs = machine.emit(t + 100.0)
    leaky = []
    for topic, payload in msgs:
        if not payload.startswith("{"):
            continue
        keys = set(json.loads(payload).keys())
        if keys & {"unit", "quality", "engineering_unit", "signal_uuid"}:
            leaky.append(topic)
    check("10. raw-payload bevat geen eenheid of kwaliteit",
          not leaky, "lekt betekenis: %s" % leaky[:3] if leaky
          else "%d berichten, alleen waarde en soms een tijdstempel" % len(msgs))

    # --- 11. het OPC-UA-oppervlak draagt de NATIVE waarde ------------------
    # Deze test bestaat omdat hij twee echte fouten ving die geen van beide
    # crashten: nodes met het canonieke datatype (een Int32 in tienden past niet
    # in een Double-node) en een kale Python-int die asyncua als Int64 typeert.
    # In beide gevallen wordt de write geweigerd en blijft de node op nul staan.
    # Een adresruimte vol nullen ziet eruit als een stilstaande machine.
    if cfg.get("surface") == "opcua":
        try:
            from opcua_surface import OpcUaSurface, HAVE_ASYNCUA
            if not HAVE_ASYNCUA:
                check("11. OPC-UA-oppervlak", True, "asyncua niet aanwezig, overgeslagen")
            else:
                import copy
                c2 = copy.deepcopy(cfg)
                c2["opcua"]["endpoint"] = "opc.tcp://0.0.0.0:48419/selftest"
                surf = OpcUaSurface(c2)
                surf.start()
                canon = machine.signals.read()
                pairs = []
                for name, v in canon.items():
                    slot = machine.slot_by_name[name]
                    res = machine.distorter.to_native(name, v)
                    pairs.append((slot["native_name"], res.value))
                    if res.quality_raw is not None:
                        pairs.append((slot["native_name"] + ".Q", res.quality_raw))
                surf.write_many(pairs)

                # Een SENTINEL en niet de live waarde. De live waarde mag
                # legitiem nul zijn (een lege silo is echt leeg), en dan kun je
                # "geweigerde write" niet onderscheiden van "klopt gewoon". Met
                # een herkenbaar getal test je het transport en de typering, en
                # niet toevallig de processtand.
                probe_name = cfg["signals"][5]["name"]   # eerste procesvariabele
                probe = machine.slot_by_name[probe_name]["native_name"]
                node = surf._nodes[probe]  # noqa: SLF001
                import asyncio as _aio

                dt_native = machine.slot_by_name[probe_name].get(
                    "native_datatype", "Double")
                sentinel = "SENTINEL" if dt_native == "String" else 4242
                surf.write_many([(probe, sentinel)])
                got_s = _aio.run_coroutine_threadsafe(
                    node.read_value(), surf._loop).result(timeout=5)  # noqa: SLF001

                # En daarna de ECHTE waarde, zodat ook de gewone weg gedekt is.
                expect = machine.distorter.to_native(
                    probe_name, canon[probe_name]).value
                surf.write_many([(probe, expect)])
                got_live = _aio.run_coroutine_threadsafe(
                    node.read_value(), surf._loop).result(timeout=5)  # noqa: SLF001
                surf.stop()
                check("11. OPC-UA-node draagt de native waarde (sentinel + live)",
                      got_s == sentinel and got_live == expect,
                      "%s: sentinel %r -> %r | live %s=%s -> native %r -> %r"
                      % (probe, sentinel, got_s, probe_name,
                         canon[probe_name], expect, got_live))
        except Exception as e:  # noqa: BLE001
            import traceback
            check("11. OPC-UA-oppervlak", False,
                  "exception: %s\n%s" % (e, traceback.format_exc()[:400]))

    print("\n=== packml-sim park-selftest (%s) ===" % os.path.basename(path))
    ok = True
    for name, passed, detail in results:
        ok = ok and passed
        print("[%s] %s" % ("PASS" if passed else "FAIL", name))
        if detail:
            print("       %s" % detail)
    print("=" * 46)
    print("RESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
