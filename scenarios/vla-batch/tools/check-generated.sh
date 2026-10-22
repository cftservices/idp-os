#!/bin/sh
# check-generated.sh — één commando dat alles bewaakt wat gegenereerd is.
#
# Bedoeld voor een pre-commit hook, voor CI en voor `deploy.sh verify` stap 0.
# Draait volledig offline: geen broker, geen Mongo, geen netwerk.
#
# Twee poorten:
#   1. DRIFT       lopen de gegenereerde artefacten achter op isa95-vla.json?
#   2. ANONIMISATIE staat er een echte leveranciers-, klant- of werkgeversnaam in?
#
# Die tweede is geen formaliteit. Eén echte naam in een gegenereerd bestand
# belandt in 360 alias-rijen en daarna in een publieke demo. De regel is hard:
# uitsluitend vendor-a t/m vendor-e, en een generieke fabriek "DairyWorks".

set -e

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
cd "$ROOT"

FAIL=0

# ---------------------------------------------------------------- 1. drift
echo "[check] drift tussen model en gegenereerde artefacten..."
if python tools/gen-park.py --check; then
  echo "[check] OK: geen drift"
else
  echo "[check] FOUT: gegenereerde artefacten lopen achter. Draai: python tools/gen-park.py"
  FAIL=1
fi

# --------------------------------------------------------- 2. anonimisatie
# De denylist. Bewust breed: liever een vals alarm dan een echte naam in een
# publieke demo. Staat een term hier terecht in (bijvoorbeeld in een citaat),
# dan hoort de uitzondering hier gedocumenteerd te worden, niet weggehaald.
DENY='FrieslandCampina\|Friesland Campina\|Campina\|Danone\|ICT Group\|ICT_Group\|RijkZwaan\|Rijk Zwaan\|AVEVA\|Wonderware\|Siemens\|Rockwell\|Allen-Bradley\|AllenBradley\|Schneider\|Emerson\|Endress\|Krohne\|Tetra Pak\|TetraPak\|GEA \|SPX Flow\|Alfa Laval\|AlfaLaval\|WWMESDB\|B2MML\|InfoPlus\|IP\.21\|Aspen'

# Gegenereerde artefacten plus de drie handgeschreven bronbestanden: een naam
# lekt net zo hard via het model als via wat eruit rolt.
TARGETS="factory-model/isa95-vla.json
factory-model/signal-template.json
factory-model/vendor-profiles.json
factory-model/park-aliases.json
factory-model/park-conditioning.json
factory-model/park-faults.json
monstermq-init/init-park.sh
monstermq-init/init-vla-opcua.sh"

echo "[check] anonimisatie..."
for f in $TARGETS; do
  [ -f "$f" ] || continue
  if grep -n -i "$DENY" "$f"; then
    echo "[check] FOUT: verboden naam in $f (zie hierboven)"
    FAIL=1
  fi
done
if [ -d park-sim/units ]; then
  for f in park-sim/units/*.yaml; do
    [ -f "$f" ] || continue
    if grep -n -i "$DENY" "$f"; then
      echo "[check] FOUT: verboden naam in $f"
      FAIL=1
    fi
  done
fi

# Een echt IPv4-adres in een gegenereerd bestand is per definitie fout: alles
# binnen de stack praat via containernamen op idp-network.
echo "[check] harde IP-adressen..."
for f in $TARGETS; do
  [ -f "$f" ] || continue
  if grep -nE '([0-9]{1,3}\.){3}[0-9]{1,3}' "$f" | grep -v '127\.0\.0\.1' | grep -v '0\.0\.0\.0'; then
    echo "[check] FOUT: hard IP-adres in $f"
    FAIL=1
  fi
done

# -------------------------------------------- 3. de twee harde datalaag-regels
#
# (a) Ongemodelleerde data wordt NIET opgeslagen. Geen enkele actieve archive
#     group en geen enkele bridge mag een raw/-topic matchen. Zou dat wel
#     gebeuren, dan legt de demo een data-swamp aan binnen de demo die
#     data-swamps veroordeelt, en op ~80 msg/s is dat ~1,7 GB per dag.
# (b) Het park publiceert buiten DairyWorks/Vla/, want die subtree is dragend
#     voor de bestaande milkdemo.
echo "[check] datalaag-regels..."
python - "$ROOT" <<'PYEOF' || FAIL=1
import io, os, re, sys, yaml

root = sys.argv[1]
idp = os.path.dirname(os.path.dirname(root))
problems = []

# (a1) archive groups
cfg = yaml.safe_load(io.open(os.path.join(idp, "monstermq", "config.yaml"),
                             encoding="utf-8"))
groups = cfg["Archive"]["Groups"]
for g in groups:
    if not g.get("Enabled"):
        continue
    for t in g["Topics"]:
        if t.startswith("raw/"):
            problems.append("archive group %s is ACTIEF en matcht %s"
                            % (g["Name"], t))

# (a2) bridges
for name in os.listdir(root):
    if not name.startswith("docker-compose") or not name.endswith(".yml"):
        continue
    txt = io.open(os.path.join(root, name), encoding="utf-8").read()
    for m in re.finditer(r'MQTT_TOPICS:\s*"([^"]+)"', txt):
        if m.group(1).startswith("raw/"):
            problems.append("%s bridget %s naar de historian" % (name, m.group(1)))

# (b) canonieke root
rules = __import__("json").load(io.open(
    os.path.join(root, "factory-model", "park-conditioning.json"), encoding="utf-8"))["rules"]
for r in rules:
    if r["raw_topic"].startswith("DairyWorks/"):
        problems.append("raw-topic binnen DairyWorks/: %s" % r["raw_topic"])
    if not r["canonical_topic"].startswith("DairyWorks/Vla-B/"):
        problems.append("canoniek topic buiten Vla-B: %s" % r["canonical_topic"])
    if r["canonical_topic"].startswith("DairyWorks/Vla/"):
        problems.append("park publiceert in de subtree van de MONOLIET: %s"
                        % r["canonical_topic"])

if problems:
    for p in problems:
        print("[check] FOUT: %s" % p)
    sys.exit(1)
print("[check] OK: raw wordt niet opgeslagen, park zit buiten DairyWorks/Vla/")
PYEOF

# ------------------------------ 4. raw landt buiten de gemodelleerde boom
#
# MonsterMQ plakt de device-NAMESPACE voor elk adres-topic. Staat daar
# DairyWorks/Vla-B, dan landt alle RUWE data onder DairyWorks/ en is de
# scheiding tussen rommella en gemodelleerde fabriek weg. Dat is precies wat er
# op 2026-08-09 op de VPS gebeurde, en het viel alleen op omdat het
# berichtvolume niet klopte. Vandaar een gate en geen goede voornemens.
echo "[check] raw-namespace van de OPC-UA-devices..."
if [ -f monstermq-init/init-park.sh ]; then
  if grep -q 'namespace:.*DairyWorks' monstermq-init/init-park.sh; then
    echo "[check] FOUT: init-park.sh registreert een device onder DairyWorks/."
    echo "        Ruwe data hoort op raw/vla-park/, niet in de gemodelleerde boom."
    FAIL=1
  elif grep -q 'namespace:.*raw/vla-park' monstermq-init/init-park.sh; then
    echo "[check] OK: devices registreren onder raw/vla-park"
  else
    echo "[check] FOUT: geen device-namespace gevonden in init-park.sh"
    FAIL=1
  fi
fi

# ------------------- 5. de poller weet elke gepollde machine te BEREIKEN
# Modbus en REST pushen niet, dus als de poller de host niet kan resolven
# gebeurt er precies niets: geen crash, geen restart-loop, geen ongemapt topic.
# Stilte lijkt op een machine die stilstaat. Op 2026-08-11 had blend-tank-01
# daardoor nog nooit een byte geleverd terwijl de container gezond draaide en
# de Modbus-server keurig op 5020 luisterde. De fallback in poller.py is het
# kale equipment_id, en dat is nooit de containernaam. Dus: voor elke gepollde
# machine moet er een expliciete host in de compose staan die exact gelijk is
# aan de container_name uit het model.
echo "[check] pollers kunnen hun machines bereiken..."
PARK_ROOT="$ROOT" python - <<'PY' || FAIL=1
import io, json, os, re, sys

root = os.environ.get("PARK_ROOT") or os.getcwd()
with io.open(os.path.join(root, "factory-model", "isa95-vla.json"),
             encoding="utf-8") as fh:
    model = json.load(fh)
try:
    with io.open(os.path.join(root, "docker-compose.park.yml"),
                 encoding="utf-8") as fh:
        compose = fh.read()
except IOError:
    print("[check] OVERGESLAGEN: docker-compose.park.yml bestaat nog niet")
    sys.exit(0)

bad = []
lines = [ln for site in model["enterprise"]["sites"]
         for ln in site.get("lines", [])]
for line in lines:
    for area in line.get("areas", []):
        for wc in area.get("work_centers", []):
            park = wc.get("park")
            if not park:
                continue
            proto = park.get("protocol")
            if proto not in ("modbus-tcp", "rest"):
                continue
            env = wc["equipment_id"].replace("-", "_").upper()
            host = park["container_name"]
            key = ("MODBUS_HOST_%s" % env) if proto == "modbus-tcp" \
                  else ("REST_URL_%s" % env)
            m = re.search(r"^\s*%s:\s*(\S+)\s*$" % re.escape(key), compose,
                          re.M)
            if not m:
                bad.append("%s: %s ontbreekt in docker-compose.park.yml"
                           % (wc["equipment_id"], key))
            elif host not in m.group(1):
                bad.append("%s: %s wijst naar %s, moet %s zijn"
                           % (wc["equipment_id"], key, m.group(1), host))

if bad:
    print("[check] FOUT: de poller kan deze machines niet bereiken:")
    for b in bad:
        print("        %s" % b)
    sys.exit(1)
print("[check] OK: elke gepollde machine heeft een expliciete host")
PY

# -------------------- 6. wat het model verklaart, staat ook in de unit-config
# Het model had voor bottle-filler-01 een tdengine_bypass-blok, maar de
# generator schreef het nooit uit. Gevolg: het anti-patroon stond stil
# uitgeschakeld en idp_bypass bleef leeg, terwijl model, code en documentatie
# alle drie beweerden dat het pad bestond. Niets faalde. Dit is dezelfde
# klasse als de stille poller: iets dat verklaard is maar niet aangesloten.
echo "[check] verklaarde bypass-paden zijn ook aangesloten..."
PARK_ROOT="$ROOT" python - <<'PY' || FAIL=1
import io, json, os, re, sys

root = os.environ.get("PARK_ROOT") or os.getcwd()
with io.open(os.path.join(root, "factory-model", "isa95-vla.json"),
             encoding="utf-8") as fh:
    model = json.load(fh)

bad = []
found = 0
for site in model["enterprise"]["sites"]:
    for line in site.get("lines", []):
        for area in line.get("areas", []):
            for wc in area.get("work_centers", []):
                by = (wc.get("park") or {}).get("tdengine_bypass")
                if not by or not by.get("enabled"):
                    continue
                found += 1
                path = os.path.join(root, "park-sim", "units",
                                    "%s.yaml" % wc["equipment_id"])
                try:
                    with io.open(path, encoding="utf-8") as fh:
                        y = fh.read()
                except IOError:
                    bad.append("%s: unit-yaml ontbreekt" % wc["equipment_id"])
                    continue
                if not re.search(r"^tdengine_bypass:", y, re.M):
                    bad.append("%s: model verklaart tdengine_bypass, "
                               "de unit-yaml niet" % wc["equipment_id"])
                elif not re.search(r"^\s+enabled:\s*true", y, re.M):
                    bad.append("%s: bypass staat uit in de unit-yaml"
                               % wc["equipment_id"])
                if re.search(r"^\s+database:\s*idp_park", y, re.M):
                    bad.append("%s: bypass schrijft naar idp_park; dan is het "
                               "tegenvoorbeeld zijn eigen argument kwijt"
                               % wc["equipment_id"])
                if re.search(r"(?i)^\s+password:", y, re.M):
                    bad.append("%s: wachtwoord in een GEGENEREERD bestand; dat "
                               "gaat de repo in. Gebruik TD_BYPASS_PASSWORD."
                               % wc["equipment_id"])

if bad:
    print("[check] FOUT:")
    for b in bad:
        print("        %s" % b)
    sys.exit(1)
print("[check] OK: %d bypass-pad(en) verklaard en aangesloten" % found)
PY

if [ "$FAIL" -eq 0 ]; then
  echo "[check] ALLES OK"
  exit 0
fi
echo "[check] MISLUKT"
exit 1
