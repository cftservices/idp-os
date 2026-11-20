#!/bin/bash
# Xvfb + kiosk-Chromium + ffmpeg, als één geheel.
#
# Geen eigen herstartlus. Valt één van de drie om, dan stopt de container en
# herstart Docker hem (restart: unless-stopped, met oplopende wachttijd). Een
# lus in bash die Chromium "opnieuw probeert" verbergt precies de fout die je
# wilt zien, en laat een half kapotte combinatie eindeloos doorlopen.
#
# MODE=test     encodeert met dezelfde instellingen naar /dev/null: de meetproef.
# MODE=youtube  stuurt naar YouTube; weigert te starten zonder stream key.
set -euo pipefail

MODE="${MODE:-test}"
URL="${BROADCAST_URL:-http://vla-ui:3000/broadcast}"
WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FPS="${FPS:-15}"
VIDEO_BITRATE="${VIDEO_BITRATE:-2500k}"
MAX_RUNTIME="${MAX_RUNTIME:-43200}"   # 12 uur, daarna gepland opnieuw (geheugenlekken)
RTMPS_URL="${YOUTUBE_RTMPS_URL:-rtmps://a.rtmps.youtube.com/live2}"

log() { echo "[$(date '+%F %T')] $*"; }

case "$MODE" in
  test) OUTPUT=(-f null -) ;;
  youtube)
    if [ -z "${YOUTUBE_STREAM_KEY:-}" ]; then
      log "MODE=youtube zonder YOUTUBE_STREAM_KEY; ik stop."
      exit 2
    fi
    OUTPUT=(-f flv "${RTMPS_URL}/${YOUTUBE_STREAM_KEY}")
    ;;
  *) log "onbekende MODE '$MODE' (test of youtube)"; exit 2 ;;
esac

log "Wachten op ${URL}"
for _ in $(seq 1 60); do
  curl -fsS -o /dev/null --max-time 5 "$URL" && break
  sleep 5
done
curl -fsS -o /dev/null --max-time 5 "$URL" || { log "${URL} niet bereikbaar"; exit 1; }

# Een vorige run kan een lockbestand achterlaten (zelfde patroon als de
# Playwright-profielen elders in TechFlow-OS).
rm -f /tmp/.X99-lock /tmp/ffmpeg-progress.txt
rm -rf /tmp/chrome

log "Xvfb ${WIDTH}x${HEIGHT}"
Xvfb :99 -screen 0 "${WIDTH}x${HEIGHT}x24" -nolisten tcp -ac +extension RANDR &
XVFB_PID=$!
for _ in $(seq 1 20); do xdpyinfo -display :99 >/dev/null 2>&1 && break; sleep 0.5; done

log "Chromium -> ${URL}"
# --enable-unsafe-swiftshader: zonder GPU valt WebGL terug op SwiftShader, en
# nieuwere Chromium-versies doen dat alleen nog met deze vlag. Zonder vlag
# toont /site "Deze browser ondersteunt geen WebGL".
chromium \
  --no-sandbox \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --disable-gpu-sandbox \
  --use-angle=swiftshader \
  --enable-unsafe-swiftshader \
  --ignore-gpu-blocklist \
  --autoplay-policy=no-user-gesture-required \
  --disable-background-timer-throttling \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --disable-features=Translate,MediaRouter \
  --disable-infobars \
  --noerrdialogs \
  --user-data-dir=/tmp/chrome \
  --window-position=0,0 \
  --window-size="${WIDTH},${HEIGHT}" \
  --kiosk \
  "$URL" >/tmp/chromium.log 2>&1 &
CHROME_PID=$!

# Tijd voor de eerste pagina en de 3D-modellen.
sleep "${WARMUP:-20}"

log "ffmpeg MODE=${MODE} ${WIDTH}x${HEIGHT}@${FPS} ${VIDEO_BITRATE}"
# Voortgang via -progress naar een bestand, niet via -stats: die schrijft met
# een carriage return, en Docker logt pas een regel bij een newline, dus
# speed= bleef onzichtbaar. broadcast.sh status leest dit bestand.
ffmpeg \
  -hide_banner -loglevel warning -nostats \
  -progress /tmp/ffmpeg-progress.txt -stats_period 30 \
  -thread_queue_size 64 -f x11grab -draw_mouse 0 -framerate "$FPS" -video_size "${WIDTH}x${HEIGHT}" -i :99.0 \
  -thread_queue_size 64 -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \
  -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
  -fps_mode cfr -r "$FPS" \
  -b:v "$VIDEO_BITRATE" -maxrate "$VIDEO_BITRATE" -bufsize "$(( ${VIDEO_BITRATE%k} * 2 ))k" \
  -g "$(( FPS * 2 ))" -keyint_min "$(( FPS * 2 ))" -sc_threshold 0 \
  -c:a aac -b:a 128k -ar 44100 \
  "${OUTPUT[@]}" &
FFMPEG_PID=$!

( sleep "$MAX_RUNTIME"; log "MAX_RUNTIME bereikt, geplande herstart"; kill "$FFMPEG_PID" 2>/dev/null || true ) &
TIMER_PID=$!

trap 'kill "$FFMPEG_PID" "$CHROME_PID" "$XVFB_PID" "$TIMER_PID" 2>/dev/null || true' EXIT TERM INT

# Wacht tot één van de drie stopt; dan stopt het geheel.
set +e
wait -n "$XVFB_PID" "$CHROME_PID" "$FFMPEG_PID"
rc=$?
log "proces gestopt (exit ${rc}); container stopt, Docker herstart hem"
tail -n 20 /tmp/chromium.log || true
exit 1
