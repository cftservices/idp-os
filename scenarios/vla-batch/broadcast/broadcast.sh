#!/bin/bash
# Bediening van de DairyWorks-livestream op de VPS.
#
#   broadcast.sh test [minuten]   meetproef: encoderen naar /dev/null (standaard 30 min), dan stoppen
#   broadcast.sh start            naar YouTube (unlisted), vraagt de stream key in /root/idp-os/.broadcast.env
#   broadcast.sh stop             renderer weg; de demo blijft draaien
#   broadcast.sh status           container, CPU/geheugen, laatste ffmpeg-regels
#   broadcast.sh frame [pad]      één PNG van het virtuele scherm (rendert WebGL echt?)
#   broadcast.sh build            image (opnieuw) bouwen
#
# Altijd -p vla-broadcast. Nooit een kale `docker compose down`: die raakt het
# project uit de huidige map, en op deze machine is dat te vaak de demo geweest.
set -euo pipefail

HIER="$(cd "$(dirname "$0")" && pwd)"
COMPOSE=(docker compose -p vla-broadcast -f "$HIER/docker-compose.broadcast.yml")
CONTAINER=vla-broadcast-renderer
SLEUTELBESTAND=/root/idp-os/.broadcast.env

# Pre-flight: de demo gaat voor. Drempels uit README.md.
# CPU: gemeten idle in plaats van load1. De batch-engine werkt in stoten, en
# load1 schommelt daardoor tussen 1,3 en 2,5 terwijl de machine 70-77% idle is
# (meting 20-11-2026). De renderer krijgt maximaal één van de twee kernen, dus
# er moet minstens de helft vrij zijn.
MIN_BESCHIKBAAR_MB=2048
MIN_IDLE_PCT=50

preflight() {
  local beschikbaar idle
  beschikbaar=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
  # Gemiddelde idle over 30 s (vmstat-kolom "id"; de eerste regel is sinds boot).
  idle=$(vmstat 10 4 | awk 'NR>3 {s+=$15; n++} END {print int(s/n)}')
  echo "pre-flight: ${beschikbaar} MB beschikbaar, CPU ${idle}% idle (30 s)"
  if [ "$beschikbaar" -lt "$MIN_BESCHIKBAAR_MB" ]; then
    echo "NO-GO: minder dan ${MIN_BESCHIKBAAR_MB} MB beschikbaar" >&2; exit 1
  fi
  if [ "$idle" -lt "$MIN_IDLE_PCT" ]; then
    echo "NO-GO: CPU maar ${idle}% idle (minimaal ${MIN_IDLE_PCT}%); probeer later, of zet FORCE=1" >&2
    [ "${FORCE:-0}" = "1" ] || exit 1
  fi
  docker network inspect idp-network >/dev/null 2>&1 || { echo "NO-GO: idp-network bestaat niet (draait de demo?)" >&2; exit 1; }
  docker inspect -f '{{.State.Health.Status}}' vla-ui 2>/dev/null | grep -q healthy \
    || { echo "NO-GO: vla-ui is niet healthy" >&2; exit 1; }
}

case "${1:-}" in
  build)
    "${COMPOSE[@]}" build renderer
    ;;
  test)
    minuten="${2:-30}"
    preflight
    BROADCAST_MODE=test "${COMPOSE[@]}" up -d --build renderer
    echo "meetproef loopt ${minuten} min; meet ondertussen met: vmstat 5, docker stats, en het Grafana-dashboard vps-health"
    echo "na afloop stopt de renderer vanzelf. Volg: docker logs -f ${CONTAINER}"
    nohup bash -c "sleep $(( minuten * 60 )); ${COMPOSE[*]} rm -sf renderer" >/dev/null 2>&1 &
    ;;
  start)
    if [ ! -f "$SLEUTELBESTAND" ] || ! grep -q '^YOUTUBE_STREAM_KEY=.\+' "$SLEUTELBESTAND"; then
      echo "Geen stream key. Zet hem zelf in ${SLEUTELBESTAND}:" >&2
      echo "  install -m 600 /dev/null ${SLEUTELBESTAND} && nano ${SLEUTELBESTAND}" >&2
      echo "  inhoud: YOUTUBE_STREAM_KEY=<sleutel uit YouTube Studio, stream op Verborgen/Unlisted>" >&2
      exit 1
    fi
    if [ "$(stat -c %a "$SLEUTELBESTAND")" != "600" ]; then
      echo "${SLEUTELBESTAND} moet chmod 600 zijn" >&2; exit 1
    fi
    preflight
    BROADCAST_MODE=youtube "${COMPOSE[@]}" up -d --build renderer
    echo "stream gestart. Controleer in YouTube Studio de streamstatus (Goed/Uitstekend)."
    ;;
  stop)
    "${COMPOSE[@]}" rm -sf renderer
    ;;
  status)
    docker ps -a --filter "name=^${CONTAINER}$" --format 'table {{.Names}}\t{{.Status}}'
    docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' "$CONTAINER" 2>/dev/null || true
    echo "--- encoder (speed moet rond 1x blijven; elke 30 s bijgewerkt)"
    docker exec "$CONTAINER" sh -c 'grep -E "^(frame|fps|speed|progress)=" /tmp/ffmpeg-progress.txt | tail -n 4' < /dev/null 2>/dev/null \
      || echo "(nog geen voortgang)"
    echo "--- laatste logregels"
    docker logs --tail 8 "$CONTAINER" 2>&1 | tail -n 8 || true
    ;;
  frame)
    doel="${2:-/root/broadcast-frame.png}"
    docker exec "$CONTAINER" ffmpeg -hide_banner -loglevel error -y \
      -f x11grab -draw_mouse 0 -video_size "${BROADCAST_WIDTH:-1280}x${BROADCAST_HEIGHT:-720}" -i :99.0 \
      -frames:v 1 /tmp/frame.png < /dev/null
    docker cp "$CONTAINER:/tmp/frame.png" "$doel"
    echo "frame: ${doel}"
    ;;
  *)
    sed -n '2,10p' "$0"; exit 2
    ;;
esac
