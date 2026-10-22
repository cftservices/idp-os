#!/usr/bin/env bash
# --------------------------------------------------------------------------
# Koppelt de Vla-UI (met de 3D site-twin op /site) aan de ingress van `aios`.
# Idempotent: opnieuw draaien vervangt alleen ons eigen blok.
#
# Sinds 10-2026 zijn 80/443 van de container aios-caddy; host-nginx en
# Traefik draaien niet meer. Dit script volgt het patroon van
# /opt/emcp/deploy/scripts/attach-ingress.sh:
#   1. In-place schrijven, nooit mv: de Caddyfile is een bind-mount van een
#      bestand en hangt aan een inode.
#   2. Eerst het netwerk koppelen, daarna herladen. Caddy lost vla-ui op via
#      Docker-DNS.
#   3. caddy validate, bij een fout terugdraaien, en daarna in de container
#      controleren dat het blok er echt staat.
#
# Basic-auth: het wachtwoord staat alleen op de VPS, in $AUTH_FILE
# (regel "gebruiker:wachtwoord"). Bestaat het bestand niet, dan wordt er een
# wachtwoord gegenereerd. Het komt nooit in de repo.
# --------------------------------------------------------------------------
set -euo pipefail

CADDYFILE=${CADDYFILE:-/root/enterprise-ai/deploy/hostinger/Caddyfile}
CADDY_CONTAINER=${CADDY_CONTAINER:-aios-caddy}
NETWERK=${NETWERK:-idp-network}
DOMEIN=${DOMEIN:-milkdemo.techflow24.com}
UPSTREAM=${UPSTREAM:-vla-ui:3000}
GRAFANA_UPSTREAM=${GRAFANA_UPSTREAM:-grafana:3000}
AUTH_FILE=${AUTH_FILE:-/root/idp-os/.milkdemo-auth}

BEGIN="# >>> vla begin (beheerd door attach-ingress-caddy.sh, niet met de hand bewerken)"
EIND="# <<< vla eind"

[ -f "$CADDYFILE" ] || { echo "Caddyfile niet gevonden: $CADDYFILE" >&2; exit 1; }

# --- 0. credentials --------------------------------------------------------
if [ ! -f "$AUTH_FILE" ]; then
    (umask 077; echo "demo:$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)" > "$AUTH_FILE")
    echo "• nieuw wachtwoord gegenereerd in ${AUTH_FILE}"
fi
AUTH_USER=$(cut -d: -f1 "$AUTH_FILE")
AUTH_PASS=$(cut -d: -f2- "$AUTH_FILE")
# Niet escapen: string.Template vult een waarde letterlijk in en parseert hem
# niet opnieuw. Een $$-escape in de waarde komt dus als $$ in de Caddyfile,
# en dan weigert basic_auth elk wachtwoord met een kale 401.
AUTH_HASH=$(docker exec "$CADDY_CONTAINER" caddy hash-password --plaintext "$AUTH_PASS" < /dev/null)

# --- 1. netwerk ------------------------------------------------------------
docker network inspect "$NETWERK" >/dev/null 2>&1 \
    || { echo "Netwerk ${NETWERK} bestaat niet; start eerst de vla-stack." >&2; exit 1; }
if docker network inspect "$NETWERK" --format '{{range .Containers}}{{.Name}} {{end}}' \
    | grep -qw "$CADDY_CONTAINER"; then
    echo "• ${CADDY_CONTAINER} hangt al aan ${NETWERK}"
else
    docker network connect "$NETWERK" "$CADDY_CONTAINER"
    echo "• ${CADDY_CONTAINER} gekoppeld aan ${NETWERK}"
fi

# --- 2. het blok -----------------------------------------------------------
cp -- "$CADDYFILE" "${CADDYFILE}.voor-vla"

python3 - "$CADDYFILE" "$BEGIN" "$EIND" <<'PY'
import sys
pad, begin, eind = sys.argv[1:4]
regels = open(pad, encoding="utf-8").read().splitlines(keepends=True)
uit, binnen = [], False
for regel in regels:
    if regel.strip() == begin.strip():
        binnen = True
        continue
    if binnen and regel.strip() == eind.strip():
        binnen = False
        continue
    if not binnen:
        uit.append(regel)
with open(pad, "w", encoding="utf-8") as bestand:  # zelfde inode
    bestand.writelines(uit)
PY

if grep -q "^[[:space:]]*${DOMEIN}[[:space:]]*{" "$CADDYFILE"; then
    echo "FOUT: ${DOMEIN} staat al in de Caddyfile buiten ons blok." >&2
    cp -- "${CADDYFILE}.voor-vla" "$CADDYFILE"
    exit 1
fi

vul_in() {
    python3 -c 'import os, string, sys; sys.stdout.write(string.Template(sys.stdin.read()).safe_substitute(os.environ))'
}
export BEGIN EIND DOMEIN UPSTREAM GRAFANA_UPSTREAM AUTH_USER AUTH_HASH

vul_in >> "$CADDYFILE" <<'SJABLOON'

${BEGIN}
${DOMEIN} {
	encode zstd gzip

	# De healthcheck van een monitor mag zonder wachtwoord; de rest niet.
	@health path /api/ui/model
	handle @health {
		reverse_proxy ${UPSTREAM}
	}

	# Grafana onder een subpad: dezelfde voordeur als de site, en daarachter
	# nog Grafana's eigen login. Geen prefix-strip (serve_from_sub_path doet
	# dat al). De Authorization-header gaat er bewust af: anders probeert
	# Grafana het demowachtwoord als eigen basic-auth en weigert elk verzoek,
	# en zou het demowachtwoord ook een Grafana-gebruiker kunnen raden.
	handle /grafana* {
		basic_auth {
			${AUTH_USER} ${AUTH_HASH}
		}
		reverse_proxy ${GRAFANA_UPSTREAM} {
			header_up -Authorization
			header_up X-Real-IP {remote_host}
		}
	}

	handle {
		basic_auth {
			${AUTH_USER} ${AUTH_HASH}
		}
		reverse_proxy ${UPSTREAM} {
			header_up X-Real-IP {remote_host}
		}
	}

	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		?X-Frame-Options "SAMEORIGIN"
		?Referrer-Policy "strict-origin-when-cross-origin"
		-Server
	}
}
${EIND}
SJABLOON
echo "• vla-blok toegevoegd voor ${DOMEIN} → ${UPSTREAM} (+ /grafana → ${GRAFANA_UPSTREAM})"

# --- 3. controleren en herladen --------------------------------------------
if ! docker exec "$CADDY_CONTAINER" caddy validate --config /etc/caddy/Caddyfile \
    --adapter caddyfile < /dev/null >/dev/null 2>&1; then
    echo "FOUT: Caddy keurt de configuratie af. Teruggedraaid." >&2
    cp -- "${CADDYFILE}.voor-vla" "$CADDYFILE"
    exit 1
fi
docker exec "$CADDY_CONTAINER" caddy reload --config /etc/caddy/Caddyfile \
    --adapter caddyfile < /dev/null
echo "• Caddy herladen"

if docker exec "$CADDY_CONTAINER" grep -q "$DOMEIN" /etc/caddy/Caddyfile < /dev/null; then
    echo "• geverifieerd: ${DOMEIN} staat in de Caddyfile ín de container"
else
    echo "WAARSCHUWING: de container ziet ${DOMEIN} niet (inode-valkuil). Herstart ${CADDY_CONTAINER}." >&2
    exit 1
fi
echo "Klaar. Controleer: curl -sI https://${DOMEIN}/api/ui/model"
