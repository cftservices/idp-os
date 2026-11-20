#!/bin/bash
# Livestream vanaf de Raspberry Pi 5: de carrousel mét 3D-twin naar YouTube (unlisted).
#
#   ./stream.sh start [minuten]   start; met minuten stopt hij daarna vanzelf (bv. 60 voor de uurproef)
#   ./stream.sh stop
#   ./stream.sh status            unit, encoder, temperatuur, throttling, CPU
#   ./stream.sh fps               telt 10 s lang de echt verschillende frames op het scherm
#   ./stream.sh frame [pad]       één PNG van het scherm
#
# Draait als systemd-user-unit "vla-stream": overleeft een verbroken ssh, en herstart na een
# crash. Logboek: journalctl --user -u vla-stream -f (de stream key staat er gemaskeerd in).
#
# De stream key zet Johannes zelf in ~/.broadcast.env (chmod 600), nooit in git of in een chat:
#   install -m 600 /dev/null ~/.broadcast.env && nano ~/.broadcast.env
#   inhoud: YOUTUBE_STREAM_KEY=<sleutel uit YouTube Studio>
#
# Beeld: 1280x720, 30 fps (de twin krijgt via ?fps= dezelfde framecap), libx264 ultrafast met
# constante bitrate 2500k (CBR): YouTube wil een stabiele stroom, ook bij een rustig beeld. De demo komt via een tunnel met de sleutel ~/.ssh/pi-bankproef, die op de
# VPS alleen poort 8091 mag doorsturen (zie README, Bankproef Raspberry Pi 5).
set -euo pipefail

HIER="$(cd "$(dirname "$0")" && pwd)"
UNIT=vla-stream
SLEUTELBESTAND="$HOME/.broadcast.env"
TUNNEL_SLEUTEL="$HOME/.ssh/pi-bankproef"
VPS=root@147.93.56.23
FPS="${FPS:-30}"
URL="${URL:-http://localhost:8091/broadcast?twin=1&fps=${FPS}}"
BITRATE="${BITRATE:-2500k}"
BREEDTE=1280
HOOGTE=720
MAX_RUNTIME="${MAX_RUNTIME:-43200}"   # 12 uur, dan geplande herstart (geheugenlekken)
RTMPS_URL="${YOUTUBE_RTMPS_URL:-rtmps://a.rtmps.youtube.com/live2}"
PROFIEL="/tmp/vla-stream-chrome"
VOORTGANG="/tmp/vla-stream-voortgang.txt"
GEPLAND="/tmp/vla-stream-gepland"

log() { echo "[$(date '+%F %T')] $*"; }

scherm_1280x720() {
  local uit modi
  uit=$(xrandr --query | awk '/ connected/ { print $1; exit }')
  [ -n "$uit" ] || { log "geen (geforceerde) HDMI-uitgang"; return 1; }
  modi=$(xrandr --query | awk -v o="$uit" '$1 == o { f = 1; next } f && /^[^ ]/ { exit } f { print $1 }')
  if grep -qx 1280x720 <<<"$modi"; then
    xrandr --output "$uit" --mode 1280x720
  else
    grep -qx 1280x720_60.00 <<<"$modi" || {
      xrandr --newmode 1280x720_60.00 74.50 1280 1344 1472 1664 720 723 728 748 -hsync +vsync 2>/dev/null || true
      xrandr --addmode "$uit" 1280x720_60.00
    }
    xrandr --output "$uit" --mode 1280x720_60.00
  fi
  xset s off; xset -dpms
}

# De eigenlijke pijplijn; systemd start dit via `stream.sh run`.
run() {
  [ -f "$SLEUTELBESTAND" ] || { log "geen ${SLEUTELBESTAND}"; exit 2; }
  set -a; . "$SLEUTELBESTAND"; set +a
  [ -n "${YOUTUBE_STREAM_KEY:-}" ] || { log "YOUTUBE_STREAM_KEY ontbreekt in ${SLEUTELBESTAND}"; exit 2; }
  local sleutel="$YOUTUBE_STREAM_KEY"
  unset YOUTUBE_STREAM_KEY
  # Grafana in de carrousel als er een Viewer-login is (GRAFANA_USER/GRAFANA_PASS): de proxy op
  # localhost:3002 logt in en de browser krijgt alleen dashboard-uids in de URL.
  local grafana=0
  if [ -n "${GRAFANA_USER:-}" ] && [ -n "${GRAFANA_PASS:-}" ]; then
    grafana=1
    URL="${URL}&gf=stream-factory,pi5-health"
  fi

  # Een losse tunnel van de bankproef houdt poort 8091 bezet; die gaat eerst weg.
  [ -f "$HOME/.bankproef-tunnel.pid" ] && kill "$(cat "$HOME/.bankproef-tunnel.pid")" 2>/dev/null || true
  rm -f "$HOME/.bankproef-tunnel.pid" "$GEPLAND" "$VOORTGANG"

  log "tunnel naar vla-ui"
  ssh -i "$TUNNEL_SLEUTEL" -o IdentitiesOnly=yes -o BatchMode=yes -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -N -L 8091:127.0.0.1:8091 -L 3001:127.0.0.1:3001 "$VPS" &
  local tunnel=$!
  local pids=("$tunnel")
  if [ "$grafana" = 1 ]; then
    python3 "$HIER/grafana-proxy.py" &
    pids+=($!)
  fi
  unset GRAFANA_PASS
  for _ in $(seq 1 30); do curl -fsS -o /dev/null --max-time 5 "$URL" && break; sleep 2; done
  curl -fsS -o /dev/null --max-time 5 "$URL" || { log "${URL} niet bereikbaar"; exit 1; }

  scherm_1280x720
  rm -rf "$PROFIEL"
  log "Chromium -> ${URL}"
  chromium \
    --kiosk --no-first-run --no-default-browser-check --noerrdialogs --disable-infobars \
    --password-store=basic --check-for-update-interval=31536000 --ignore-gpu-blocklist \
    --disable-features=Translate --autoplay-policy=no-user-gesture-required \
    --disable-background-timer-throttling --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    --user-data-dir="$PROFIEL" --window-position=0,0 --window-size="${BREEDTE},${HOOGTE}" \
    "$URL" >/tmp/vla-stream-chromium.log 2>&1 &
  local chrome=$!
  sleep "${WARMUP:-20}"

  log "ffmpeg -> YouTube ${BREEDTE}x${HOOGTE}@${FPS} ${BITRATE}"
  # Het sed-script met de sleutel staat in een bestand (600), niet in de argumenten: die ziet
  # iedereen op de Pi in `ps`.
  local masker; masker=$(mktemp); chmod 600 "$masker"
  printf 's|%s|***|g\n' "$sleutel" > "$masker"

  # Muziek: alle mp3's uit ~/music, zacht onder het beeld. Zonder bestanden een stil spoor. Alleen
  # muziek die je op YouTube mag gebruiken (YouTube Audio Library); andere muziek geeft een
  # Content ID-claim of een afgebroken stream.
  # Alleen mp3: de concat-demuxer wil één codec (mp3 en ogg door elkaar breekt). De lijst staat er
  # 200 keer in in plaats van -stream_loop, want dat is met concat niet betrouwbaar (gemeten op de
  # Pi, 20-11-2026); 200 rondes is ruim meer dan de 12 uur tot de geplande herstart.
  local audio=(-f lavfi -i "anullsrc=channel_layout=stereo:sample_rate=44100") afilter=()
  local lijst ronde; lijst=$(mktemp); ronde=$(mktemp)
  local f
  while IFS= read -r f; do
    printf "file '%s'\n" "${f//\'/\'\\\'\'}" >> "$ronde"
  done < <(find "$HOME/music" -maxdepth 1 -type f -iname '*.mp3' 2>/dev/null | sort)
  if [ -s "$ronde" ]; then
    for _ in $(seq 1 200); do cat "$ronde"; done > "$lijst"
    audio=(-f concat -safe 0 -i "$lijst")
    afilter=(-af "volume=${MUZIEK_VOLUME:-0.35}")
    log "muziek: $(wc -l < "$ronde") nummer(s) uit ~/music"
  fi
  rm -f "$ronde"

  # ffmpeg noemt bij een fout de volledige uitvoer-URL, met de sleutel erin: die gaat eruit.
  ffmpeg -hide_banner -loglevel warning -nostats \
    -progress "$VOORTGANG" -stats_period 30 \
    -thread_queue_size 64 -f x11grab -draw_mouse 0 -framerate "$FPS" -video_size "${BREEDTE}x${HOOGTE}" -i "${DISPLAY}+0,0" \
    -thread_queue_size 64 "${audio[@]}" \
    -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
    -fps_mode cfr -r "$FPS" \
    -b:v "$BITRATE" -minrate "$BITRATE" -maxrate "$BITRATE" -bufsize "$(( ${BITRATE%k} * 2 ))k" \
    -x264-params nal-hrd=cbr \
    -g "$(( FPS * 2 ))" -keyint_min "$(( FPS * 2 ))" -sc_threshold 0 \
    -map 0:v -map 1:a "${afilter[@]}" -c:a aac -b:a 128k -ar 44100 -ac 2 \
    -f flv "${RTMPS_URL}/${sleutel}" \
    2> >(sed -u -f "$masker" >&2) < /dev/null &
  local ff=$!

  local looptijd="$MAX_RUNTIME"
  [ -n "${STOP_NA_MIN:-}" ] && looptijd=$(( STOP_NA_MIN * 60 ))
  ( sleep "$looptijd"; touch "$GEPLAND"; kill "$ff" 2>/dev/null || true ) &
  local timer=$!
  trap 'kill "$ff" "$chrome" "${pids[@]}" "$timer" 2>/dev/null || true; rm -f "$masker" "$lijst"' EXIT TERM INT

  set +e
  wait -n "${pids[@]}" "$chrome" "$ff"
  local rc=$?
  set -e
  if [ -f "$GEPLAND" ] && [ -n "${STOP_NA_MIN:-}" ]; then
    log "gepland einde na ${STOP_NA_MIN} min"; exit 0
  fi
  log "proces gestopt (exit ${rc}); systemd herstart de stream"
  exit 1
}

case "${1:-}" in
  start)
    [ -f "$SLEUTELBESTAND" ] && grep -q '^YOUTUBE_STREAM_KEY=.\+' "$SLEUTELBESTAND" || {
      echo "Geen stream key. Zet hem zelf in ${SLEUTELBESTAND}:" >&2
      echo "  install -m 600 /dev/null ${SLEUTELBESTAND} && nano ${SLEUTELBESTAND}" >&2
      echo "  inhoud: YOUTUBE_STREAM_KEY=<sleutel uit YouTube Studio>" >&2
      exit 1
    }
    [ "$(stat -c %a "$SLEUTELBESTAND")" = 600 ] || { echo "${SLEUTELBESTAND} moet chmod 600 zijn" >&2; exit 1; }
    systemctl --user reset-failed "$UNIT" 2>/dev/null || true
    extra=()
    [ -n "${2:-}" ] && extra+=(--setenv=STOP_NA_MIN="$2")
    systemd-run --user --unit="$UNIT" --collect \
      --property=Restart=on-failure --property=RestartSec=15 \
      --setenv=DISPLAY="${DISPLAY:-:0}" --setenv=XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}" \
      --setenv=URL="$URL" --setenv=FPS="$FPS" --setenv=BITRATE="$BITRATE" \
      "${extra[@]}" "$HIER/stream.sh" run
    echo "gestart${2:+, stopt na $2 min}. Volg: journalctl --user -u ${UNIT} -f"
    echo "Kijk in YouTube Studio of de streamstatus Goed of Uitstekend wordt."
    ;;
  stop)
    systemctl --user stop "$UNIT" 2>/dev/null || true
    echo "gestopt"
    ;;
  status)
    systemctl --user --no-pager status "$UNIT" 2>/dev/null | head -n 5 || echo "unit draait niet"
    echo "--- encoder (speed hoort rond 1x te blijven, elke 30 s bijgewerkt)"
    grep -E '^(frame|fps|bitrate|speed|progress)=' "$VOORTGANG" 2>/dev/null | tail -n 5 || echo "(nog geen voortgang)"
    echo "--- Pi"
    echo "temperatuur $(vcgencmd measure_temp | cut -d= -f2), throttled $(vcgencmd get_throttled | cut -d= -f2), CPU-idle $(vmstat 3 2 | tail -n 1 | awk '{print $15}')%"
    echo "--- laatste logregels"
    journalctl --user -u "$UNIT" -n 6 --no-pager -o cat 2>/dev/null || true
    ;;
  run)
    run
    ;;
  fps)
    export DISPLAY="${DISPLAY:-:0}" XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
    ffmpeg -hide_banner -loglevel error -f x11grab -draw_mouse 0 -framerate "$FPS" \
      -video_size "${BREEDTE}x${HOOGTE}" -i "${DISPLAY}+0,0" -t 10 -f framemd5 - < /dev/null \
      | awk -F, '!/^#/ { gsub(/ /, ""); if ($6 != v) u++; v = $6 } END { printf "%.1f echte fps (10 s)\n", u / 10 }'
    ;;
  frame)
    export DISPLAY="${DISPLAY:-:0}" XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
    doel="${2:-$HOME/stream-frame.png}"
    ffmpeg -hide_banner -loglevel error -y -f x11grab -draw_mouse 0 -video_size "${BREEDTE}x${HOOGTE}" \
      -i "${DISPLAY}+0,0" -frames:v 1 "$doel" < /dev/null
    echo "frame: ${doel}"
    ;;
  *)
    sed -n '2,11p' "$0"; exit 2
    ;;
esac
