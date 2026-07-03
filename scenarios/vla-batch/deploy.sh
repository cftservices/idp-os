#!/usr/bin/env bash
# ============================================================================
# Vla Batch v2 demo — VPS deploy / verify helper (run ON the Ubuntu VPS)
# ============================================================================
# Brings up the slim-base + vla-batch overlay, waits for MonsterMQ, lets the
# one-shot `vla-opcua-init` register the native OPC-UA device, and verifies the
# end-to-end path (factory -> MonsterMQ UNS -> TDengine + batch-engine).
#
# Usage (from the idp-os root, i.e. the dir with docker-compose.slim.yml):
#   ./scenarios/vla-batch/deploy.sh up        # build + start the stack (default)
#   ./scenarios/vla-batch/deploy.sh verify    # health + UNS-flow + device checks
#   ./scenarios/vla-batch/deploy.sh smoke     # run one demo batch via the API
#   ./scenarios/vla-batch/deploy.sh fallback  # switch ingest to the connector
#   ./scenarios/vla-batch/deploy.sh logs [svc]
#   ./scenarios/vla-batch/deploy.sh down      # stop (keeps volumes)
#
# Requires: docker + docker compose v2, a filled ./.env (see .env.example).
# ----------------------------------------------------------------------------
set -euo pipefail

SLIM="docker-compose.slim.yml"
VLA="scenarios/vla-batch/docker-compose.vla.yml"
NET="idp-network"
# Optionele extra overlay (bv. docker-compose.vps-shared.yml op een gedeelde
# VPS met host-nginx). Zet VLA_EXTRA_COMPOSE naar het pad van de overlay.
EXTRA="${VLA_EXTRA_COMPOSE:-}"
DC="docker compose -f $SLIM -f $VLA${EXTRA:+ -f $EXTRA}"

c_g(){ printf "\033[32m%s\033[0m\n" "$*"; }
c_y(){ printf "\033[33m%s\033[0m\n" "$*"; }
c_r(){ printf "\033[31m%s\033[0m\n" "$*"; }
hr(){ printf -- "----------------------------------------------------------------\n"; }

# Ephemeral helpers that join the internal network (image tools, not host tools)
# usage: curl_net <timeout-seconds> <curl-args...>
curl_net(){ local t="$1"; shift; docker run --rm --network "$NET" curlimages/curl:latest -s --max-time "$t" "$@" 2>/dev/null || true; }
gql(){ docker run --rm --network "$NET" curlimages/curl:latest -s --max-time 10 \
        -X POST http://monstermq:4000/graphql -H 'Content-Type: application/json' \
        -d "{\"query\":\"$1\"}" 2>/dev/null || true; }

require(){
  command -v docker >/dev/null || { c_r "docker niet gevonden — installeer Docker Engine."; exit 1; }
  docker compose version >/dev/null 2>&1 || { c_r "docker compose v2 niet gevonden."; exit 1; }
  [ -f "$SLIM" ] || { c_r "Run dit script vanuit de idp-os root (mist $SLIM)."; exit 1; }
  [ -f .env ] || { c_r "Geen .env — kopieer scenarios/vla-batch/.env.example naar ./.env en vul in."; exit 1; }
  if grep -q "replace_with_real_bcrypt_hash" .env; then
    c_r "DASHBOARD_AUTH staat nog op de placeholder — genereer een echte hash:"
    c_y "   htpasswd -nbB demo 'sterk-wachtwoord'   (verdubbel elke \$ naar \$\$ in .env)"; exit 1
  fi
  if grep -qE "TRAEFIK_ACME_EMAIL=your@email.com" .env; then
    c_y "WAARSCHUWING: TRAEFIK_ACME_EMAIL staat nog op de placeholder (Let's Encrypt cert kan falen)."
  fi
}

wait_monstermq(){
  c_y "Wachten tot MonsterMQ (:4000) er is..."
  for i in $(seq 1 40); do
    [ -n "$(curl_net 3 http://monstermq:4000/)" ] && { c_g "MonsterMQ is up."; return 0; }
    sleep 3
  done
  c_r "MonsterMQ kwam niet online binnen ~2 min — check: $DC logs monstermq"; return 1
}

cmd_up(){
  require
  hr; c_y "Stack starten (build)..."; hr
  $DC up -d --build
  wait_monstermq
  c_y "De one-shot 'vla-opcua-init' registreert nu het OPC-UA-device in MonsterMQ..."
  sleep 8
  cmd_verify || c_y "Verify nog niet volledig groen — geef het ~30s en draai opnieuw: $0 verify"
  hr; c_g "Klaar. URLs:"
  local dom; dom=$(grep -E '^DOMAIN=' .env | cut -d= -f2)
  echo "  Dashboard : https://milkdemo.${dom}   (basic-auth)"
  echo "  Grafana   : https://grafana.${dom}"
  hr
}

cmd_verify(){
  require; local ok=1
  hr; c_y "1) Container-status"; hr
  $DC ps
  hr; c_y "2) OPC-UA device geregistreerd in MonsterMQ?"; hr
  local dev; dev=$(gql "{opcUaDevices{name enabled}}")
  echo "$dev"
  echo "$dev" | grep -q '"vla"' && c_g "  device 'vla' aanwezig." || { c_r "  device 'vla' NIET gevonden — init-container gefaald? ($DC logs vla-opcua-init)"; ok=0; }
  hr; c_y "3) batch-engine health"; hr
  local h; h=$(curl_net 8 http://vla-batch-engine:8000/api/v1/health)
  echo "  $h"; echo "$h" | grep -q '"ok"' && c_g "  batch-engine gezond." || { c_r "  batch-engine niet gezond."; ok=0; }
  hr; c_y "4) UNS-flow: publiceert MonsterMQ DairyWorks/Vla/# ? (5 msgs, 12s)"; hr
  local uns; uns=$(docker run --rm --network "$NET" eclipse-mosquitto:latest \
        mosquitto_sub -h monstermq -t 'DairyWorks/Vla/#' -C 5 -W 12 -v 2>/dev/null || true)
  if [ -n "$uns" ]; then c_g "  UNS stroomt:"; echo "$uns" | sed 's/^/    /'; else
    c_r "  GEEN UNS-berichten. Waarschijnlijk de OPC-UA ns-index (asyncua ns=2)."
    c_y "     -> Check: $DC logs vla-factory | grep -i namespace   en   $DC logs monstermq | grep -i opcua"
    c_y "     -> Werkt native niet? Gebruik de fallback-connector:  $0 fallback"
    ok=0
  fi
  hr; c_y "5) TDengine historian gevuld?"; hr
  local tdpass; tdpass=$(grep -E '^TD_PASS=' .env 2>/dev/null | cut -d= -f2-); tdpass="${tdpass:-taosdata}"
  local td; td=$(curl_net 8 -u "root:${tdpass}" -d 'select count(*) from idp.telemetry' http://vla-tdengine:6041/rest/sql)
  echo "  $td"; echo "$td" | grep -q '"code":0' && c_g "  TDengine bereikbaar." || c_y "  TDengine nog geen data (kan even duren, of geen UNS-flow)."
  hr; [ "$ok" = 1 ] && c_g "VERIFY: primaire pad OK." || c_r "VERIFY: aandachtspunten hierboven (zie ns-index / fallback)."
  return 0
}

cmd_smoke(){
  require
  hr; c_y "Smoke-test: één demo-batch via de batch-engine API"; hr
  local start; start=$(curl_net 10 -X POST http://vla-batch-engine:8000/api/v1/batches \
        -H 'Content-Type: application/json' -d '{"recipe_id":"chocolate-vla-1L"}')
  echo "  create: $start"
  local id; id=$(echo "$start" | grep -oE '"batch_id"[: ]*"[^"]+"' | head -1 | grep -oE 'B-[^"]+' || true)
  [ -z "$id" ] && { c_r "  kon batch_id niet lezen — draait de factory + control-pad?"; return 1; }
  c_y "  batch $id gestart; volg de state (~2-3 min tot COMPLETE)..."
  for i in $(seq 1 30); do
    local st; st=$(curl_net 8 "http://vla-batch-engine:8000/api/v1/batches/$id")
    echo "    $(echo "$st" | grep -oE '"state"[: ]*"[^"]+"' | head -1)  $(echo "$st" | grep -oE '"end_viscosity_cP"[: ]*[0-9.]+' | head -1)"
    echo "$st" | grep -q '"COMPLETE"' && { c_g "  batch COMPLETE."; echo "$st" | grep -oE '"verdict"[: ]*"[^"]+"'; break; }
    sleep 6
  done
  c_y "  rapport (PDF) opvraagbaar via: https://milkdemo.<domain>/api/v1/report/$id?format=pdf"
}

cmd_fallback(){
  require
  hr; c_y "Overschakelen naar de connector-fallback (MonsterMQ native OPC-UA uit)"; hr
  c_y "1) MonsterMQ OPC-UA device 'vla' uitschakelen..."
  gql "mutation{opcUaDevice{toggle(name:\\\"vla\\\",enabled:false){success}}}"; echo
  c_y "2) connector starten (profile fallback)..."
  $DC --profile fallback up -d --build vla-connector
  c_g "Fallback actief. Verifieer opnieuw: $0 verify"
}

cmd_logs(){ $DC logs -f --tail=120 "${1:-}"; }
cmd_down(){ $DC down; c_g "Gestopt (volumes behouden)."; }

case "${1:-up}" in
  up) cmd_up ;;
  verify) cmd_verify ;;
  smoke) cmd_smoke ;;
  fallback) cmd_fallback ;;
  logs) shift || true; cmd_logs "${1:-}" ;;
  down) cmd_down ;;
  *) c_r "onbekend commando: $1"; sed -n '2,26p' "$0"; exit 1 ;;
esac
