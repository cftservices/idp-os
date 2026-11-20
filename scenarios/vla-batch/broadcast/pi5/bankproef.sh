#!/bin/bash
# Bankproef Raspberry Pi 5: kan de Pi de 3D-twin renderen én tegelijk encoderen?
#
#   ./bankproef.sh                        15 minuten op /site?broadcast=1, via de tunnel (zie README)
#   DUUR=5 ./bankproef.sh                 korter, om de opzet te proberen
#   URL='http://localhost:8091/broadcast' ./bankproef.sh     de 2D-carousel in plaats van de twin
#   over ssh: DISPLAY=:0 XAUTHORITY=$HOME/.Xauthority ./bankproef.sh
#
# Wat hij doet, in deze volgorde:
#   1. controleert de opzet: X11-sessie (geen Wayland), scherm aangesloten, demo bereikbaar;
#   2. controleert dat Chromium WebGL op de GPU doet en niet op SwiftShader, want anders meet
#      je precies wat op de VPS al no-go was;
#   3. zet het scherm op 1280x720 en opent de pagina in kiosk;
#   4. draait dezelfde encoder als de VPS-renderer (libx264 ultrafast, 15 fps, 2500k) naar een
#      bestand, en hasht daarnaast elk opgenomen frame. Zo tellen we de frames die écht
#      verschillen: speed=1x zegt alleen dat ffmpeg bijhoudt, niet dat Chromium tekent;
#   5. meet elke 5 s CPU-idle, geheugen, temperatuur en throttling;
#   6. geeft per meetpunt go of no-go, en zet scherm en screensaver terug.
#
# Stuurt niets naar YouTube en wijzigt niets op de VPS. Exitcode 0 = go, 1 = no-go, 2 = opzet fout.
set -euo pipefail

URL="${URL:-http://localhost:8091/site?broadcast=1&embed=1}"
DUUR="${DUUR:-15}"                 # minuten
FPS="${FPS:-15}"
BITRATE="${BITRATE:-2500k}"
WARMUP="${WARMUP:-20}"             # seconden voor de eerste pagina en de 3D-modellen
BREEDTE=1280
HOOGTE=720
UIT="${UIT:-$HOME/bankproef-$(date +%Y%m%d-%H%M)}"

# Drempels. Dezelfde als op de VPS, plus twee die alleen op een Pi spelen.
MIN_SPEED=0.98                     # ffmpeg houdt realtime bij
MIN_UNIEK_PCT=90                   # echte beeldsnelheid ≥ 90% van FPS (alleen voor de twin)
MIN_IDLE=20                        # gemiddelde CPU-idle in %
MAX_TEMP=80                        # °C; de Pi 5 gaat bij 85 terugschakelen
THROTTLE_MASKER=$(( 0x1 | 0x4 | 0x10000 | 0x40000 ))   # onderspanning en throttling, nu of eerder

log()  { echo "[$(date '+%T')] $*"; }
stop() { echo "STOP: $*" >&2; exit 2; }
ge()   { awk -v a="$1" -v b="$2" 'BEGIN { exit !((a + 0) >= (b + 0)) }'; }

# --- 1. opzet -----------------------------------------------------------------------------------
CHROMIUM=$(command -v chromium || command -v chromium-browser || true)
[ -n "$CHROMIUM" ] || stop "Chromium ontbreekt: sudo apt install chromium (op oudere images: chromium-browser)"
for c in ffmpeg ffprobe xrandr xset xwininfo xdpyinfo vmstat curl; do
  command -v "$c" >/dev/null || stop "$c ontbreekt: sudo apt install ffmpeg x11-xserver-utils x11-utils procps curl"
done

[ -n "${DISPLAY:-}" ] || stop "geen DISPLAY. Draai in de desktopsessie, of over ssh met DISPLAY=:0"
xdpyinfo >/dev/null 2>&1 || stop "kan ${DISPLAY} niet openen (over ssh: XAUTHORITY=\$HOME/.Xauthority meegeven)"
if [ -n "${WAYLAND_DISPLAY:-}" ] || pgrep -x labwc >/dev/null || pgrep -x wayfire >/dev/null; then
  stop "de desktop draait op Wayland, en dan neemt x11grab een zwart scherm op. Zet hem op X11:
      sudo raspi-config nonint do_wayland W1 && sudo reboot"
fi

MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo onbekend)
case "$MODEL" in
  *"Raspberry Pi 5"*) ;;
  *) log "LET OP: dit is geen Pi 5 (${MODEL}); de uitkomst zegt dan niets over de Pi" ;;
esac

curl -fsS -o /dev/null --max-time 10 "$URL" \
  || stop "${URL} niet bereikbaar. Staat de tunnel? Zie README, sectie Bankproef Raspberry Pi 5"

XRANDR=$(xrandr --query)
OUTPUT=$(awk '/ connected/ { print $1; exit }' <<<"$XRANDR")
[ -n "$OUTPUT" ] || stop "geen scherm aangesloten. Zonder HDMI-monitor of HDMI-dummy draait er geen GPU-sessie"
MODI=$(awk -v o="$OUTPUT" '$1 == o { f = 1; next } f && /^[^ ]/ { exit } f { print $1 }' <<<"$XRANDR")
MODE_OUD=$(awk -v o="$OUTPUT" '$1 == o { f = 1; next } f && /^[^ ]/ { exit } f && /\*/ { print $1; exit }' <<<"$XRANDR")
# Een geforceerde uitgang zonder scherm (video=HDMI-A-1:...D) heeft geen EDID en biedt dan
# alleen 1024x768. 1280x720 voegen we zelf toe; de modeline komt van `cvt 1280 720 60`.
MODE_NIEUW="${BREEDTE}x${HOOGTE}"
if ! grep -qx "$MODE_NIEUW" <<<"$MODI"; then
  if grep -qx "${MODE_NIEUW}_60.00" <<<"$MODI"; then
    MODE_NIEUW="${MODE_NIEUW}_60.00"
  elif [ "$MODE_NIEUW" = 1280x720 ]; then
    log "${OUTPUT} kent geen 1280x720 (geen EDID); ik voeg die mode toe"
    xrandr --newmode 1280x720_60.00 74.50 1280 1344 1472 1664 720 723 728 748 -hsync +vsync 2>/dev/null || true
    xrandr --addmode "$OUTPUT" 1280x720_60.00 || stop "kan 1280x720 niet toevoegen aan ${OUTPUT}"
    MODE_NIEUW=1280x720_60.00
  else
    stop "${OUTPUT} kent geen ${MODE_NIEUW}"
  fi
fi
XSET=$(xset q)
SS_OUD=$(awk '/timeout:/ { print $2; exit }' <<<"$XSET")
DPMS_OUD=0; grep -q "DPMS is Enabled" <<<"$XSET" && DPMS_OUD=1

mkdir -p "$UIT"
log "uitvoer in ${UIT}"

SAMPLERS=()
opruimen() {
  for p in "${SAMPLERS[@]}"; do kill "$p" 2>/dev/null || true; done
  SAMPLERS=()
  pkill -f -- "--user-data-dir=${UIT}/profiel" 2>/dev/null || true
  if [ -n "${MODE_OUD:-}" ]; then xrandr --output "$OUTPUT" --mode "$MODE_OUD" 2>/dev/null || true; MODE_OUD=""; fi
  if [ -n "${SS_OUD:-}" ]; then xset s "$SS_OUD" 2>/dev/null || true; SS_OUD=""; fi
  if [ "${DPMS_OUD:-0}" = 1 ]; then xset +dpms 2>/dev/null || true; DPMS_OUD=0; fi
}
trap opruimen EXIT

xrandr --output "$OUTPUT" --mode "$MODE_NIEUW"
xset s off
xset -dpms

start_chromium() {   # $1 = url, $2 = naam van het profiel
  "$CHROMIUM" \
    --kiosk --no-first-run --no-default-browser-check --noerrdialogs --disable-infobars \
    --password-store=basic --check-for-update-interval=31536000 \
    --ignore-gpu-blocklist \
    --disable-features=Translate \
    --autoplay-policy=no-user-gesture-required \
    --disable-background-timer-throttling --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    --user-data-dir="${UIT}/profiel-$2" \
    --window-position=0,0 --window-size="${BREEDTE},${HOOGTE}" \
    "$1" >>"${UIT}/chromium.log" 2>&1 &
}

# --- 2. rendert Chromium op de GPU? -------------------------------------------------------------
# Een pagina zet de WebGL-renderer in zijn titel; die lezen we uit de X-vensterboom.
cat > "${UIT}/gl.html" <<'HTML'
<!doctype html><title>GL:?</title>
<script>
  let r = "GEEN-WEBGL";
  const g = document.createElement("canvas").getContext("webgl2")
         || document.createElement("canvas").getContext("webgl");
  if (g) {
    const e = g.getExtension("WEBGL_debug_renderer_info");
    r = e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER);
  }
  document.title = "GL:" + r;
</script>
HTML
start_chromium "file://${UIT}/gl.html" gl
GL=""
for _ in $(seq 1 20); do
  GL=$(xwininfo -root -tree | grep -o '"GL:[^"]*"' | grep -v '"GL:?' | head -n 1 | tr -d '"' || true)
  [ -n "$GL" ] && break
  sleep 1
done
pkill -f -- "--user-data-dir=${UIT}/profiel-gl" 2>/dev/null || true
sleep 2
GL="${GL#GL:}"
GL="${GL% - Chromium}"
GPU_GO=1
case "$GL" in
  "") stop "WebGL-renderer niet uit te lezen (zie ${UIT}/chromium.log)" ;;
  *SwiftShader*|*llvmpipe*|*softpipe*|GEEN-WEBGL)
    GPU_GO=0
    log "Chromium rendert zonder GPU: ${GL}"
    [ "${FORCE:-0}" = 1 ] || stop "geen GPU in Chromium. Kijk in chrome://gpu; FORCE=1 meet toch (en meet dan software)"
    ;;
  *) log "WebGL-renderer: ${GL}" ;;
esac

# --- 3. pagina openen ---------------------------------------------------------------------------
log "Chromium -> ${URL}, ${WARMUP} s opwarmen"
start_chromium "$URL" twin
sleep "$WARMUP"
pgrep -f -- "--user-data-dir=${UIT}/profiel-twin" >/dev/null || stop "Chromium is direct gestopt (zie ${UIT}/chromium.log)"

# --- 5. meten, naast de opname ------------------------------------------------------------------
vmstat -n 5 > "${UIT}/vmstat.log" &
SAMPLERS+=($!)
(
  set +e
  echo "tijd,temp_c,throttled,mem_beschikbaar_mb,chromium_rss_mb"
  while :; do
    t=$(awk '{ printf "%.1f", $1 / 1000 }' /sys/class/thermal/thermal_zone0/temp)
    th=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
    mem=$(awk '/MemAvailable/ { print int($2 / 1024) }' /proc/meminfo)
    rss=$(ps -C chromium,chromium-browse -o rss= 2>/dev/null | awk '{ s += $1 } END { print int(s / 1024) }')
    echo "$(date +%T),${t},${th:-?},${mem},${rss}"
    sleep 5
  done
) > "${UIT}/sensoren.csv" &
SAMPLERS+=($!)

# --- 4. opname: dezelfde encoder als de VPS, plus een hash per frame ----------------------------
# De split kost wat CPU (md5 over elk ruw frame, op een A76 grofweg een tiende kern). Dat telt
# mee in de idle-meting, dus de proef is iets strenger dan de echte stream.
SECONDEN=$(( DUUR * 60 ))
log "opname ${DUUR} min: ${BREEDTE}x${HOOGTE}@${FPS}, libx264 ultrafast ${BITRATE}"
set +e
ffmpeg -hide_banner -loglevel warning -nostats -y \
  -progress "${UIT}/voortgang.txt" -stats_period 10 \
  -thread_queue_size 64 -f x11grab -draw_mouse 0 -framerate "$FPS" -video_size "${BREEDTE}x${HOOGTE}" -i "${DISPLAY}+0,0" \
  -thread_queue_size 64 -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \
  -filter_complex "[0:v]split=2[enc][hash]" \
  -map "[enc]" -map 1:a -t "$SECONDEN" \
    -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
    -fps_mode cfr -r "$FPS" \
    -b:v "$BITRATE" -maxrate "$BITRATE" -bufsize "$(( ${BITRATE%k} * 2 ))k" \
    -g "$(( FPS * 2 ))" -keyint_min "$(( FPS * 2 ))" -sc_threshold 0 \
    -c:a aac -b:a 128k -ar 44100 \
    "${UIT}/opname.mp4" \
  -map "[hash]" -t "$SECONDEN" -fps_mode passthrough -f framemd5 "${UIT}/frames.md5" \
  2> "${UIT}/ffmpeg.log" < /dev/null
FFMPEG_RC=$?
set -e

CHROME_HEEL=1
pgrep -f -- "--user-data-dir=${UIT}/profiel-twin" >/dev/null || CHROME_HEEL=0
opruimen
trap - EXIT

# --- 6. uitwerken -------------------------------------------------------------------------------
log "uitwerken"
ffmpeg -hide_banner -loglevel error -y -ss 60 -i "${UIT}/opname.mp4" -frames:v 1 "${UIT}/frame.png" < /dev/null || true

# Encoder: speed is een lopend gemiddelde sinds de start; de eerste 30 s tellen niet mee.
SPEEDS=$(sed -n 's/^speed= *\([0-9.]*\)x$/\1/p' "${UIT}/voortgang.txt" 2>/dev/null || true)
SPEED_MIN=$(tail -n +4 <<<"$SPEEDS" | sort -g | head -n 1)
SPEED_MIN="${SPEED_MIN:-$(tail -n 1 <<<"$SPEEDS")}"

# Echte beeldsnelheid: hoe vaak verschilt een frame van het vorige, per seconde.
read -r UNIEK_FPS UNIEK_MIN_MINUUT UNIEK_LAATST DUUR_S < <(awk '
  /^#tb 0:/ { split($3, a, "/"); tb = a[1] / a[2]; next }
  /^#/      { next }
  {
    gsub(/ /, ""); split($0, f, ",")
    t = f[3] * tb
    if (n++ == 0) t0 = t
    s = int(t - t0); if (s > laatste) laatste = s
    if (f[6] != vorige) { uniek++; per_s[s]++ }
    vorige = f[6]
  }
  END {
    duur = laatste + 1
    if (duur < 1) { print "0 0 0 0"; exit }
    minimum = -1
    for (m = 0; (m + 1) * 60 <= duur; m++) {
      som = 0; for (s = m * 60; s < (m + 1) * 60; s++) som += per_s[s]
      if (minimum < 0 || som / 60 < minimum) minimum = som / 60
    }
    van = (duur > 120) ? duur - 120 : 0; som = 0
    for (s = van; s < duur; s++) som += per_s[s]
    if (minimum < 0) minimum = uniek / duur
    printf "%.1f %.1f %.1f %d\n", uniek / duur, minimum, som / (duur - van), duur
  }' "${UIT}/frames.md5" 2>/dev/null || echo "0 0 0 0")

read -r IDLE_GEM IDLE_MIN IDLE_LAAG IDLE_N < <(awk '
  NR > 3 { s += $15; n++; if (n == 1 || $15 < m) m = $15; if ($15 < 20) laag++ }
  END { if (n) printf "%d %d %d %d\n", s / n, m, laag, n; else print "0 0 0 0" }' "${UIT}/vmstat.log")

read -r TEMP_MAX MEM_MIN RSS_MAX < <(awk -F, '
  NR > 1 { if ($2 > t) t = $2; if (m == "" || $4 < m) m = $4; if ($5 > r) r = $5 }
  END { printf "%s %s %s\n", t + 0, m + 0, r + 0 }' "${UIT}/sensoren.csv")

TH=0; TH_N=0
while IFS= read -r h; do TH=$(( TH | h )); TH_N=$(( TH_N + 1 )); done \
  < <(awk -F, 'NR > 1 && $3 ~ /^0x/ { print $3 }' "${UIT}/sensoren.csv")
if [ "$TH_N" -gt 0 ]; then
  TH_HEX=$(printf '0x%x' "$TH")
  TH_GO=$(( (TH & THROTTLE_MASKER) == 0 ))
else
  TH_HEX="onbekend (vcgencmd niet te lezen)"   # gebruiker zit dan niet in de groep video
  TH_GO=i
fi

# --- oordeel ------------------------------------------------------------------------------------
NOGO=0
regel() {   # meetpunt, uitkomst, drempel, go (1/0/i = alleen informatief)
  local o
  case "$4" in 1) o="go" ;; i) o="info" ;; *) o="NO-GO"; NOGO=1 ;; esac
  printf '| %-36s | %-30s | %-16s | %-5s |\n' "$1" "$2" "$3" "$o"
}
b() { "$@" && echo 1 || echo 0; }

UNIEK_DREMPEL=$(awk -v f="$FPS" -v p="$MIN_UNIEK_PCT" 'BEGIN { printf "%.1f", f * p / 100 }')
case "$URL" in
  */site*) UNIEK_TEL=1 ;;
  *)       UNIEK_TEL=i ;;   # 2D-schermen staan grotendeels stil; dan zegt dit getal niets
esac
uniek_oordeel() { [ "$UNIEK_TEL" = i ] && echo i || b ge "$1" "$UNIEK_DREMPEL"; }

{
  echo "Bankproef Raspberry Pi 5, $(date '+%F %T')"
  echo
  echo "Machine:   ${MODEL}, $(awk '/MemTotal/ { printf "%.1f GB", $2 / 1048576 }' /proc/meminfo)"
  echo "Systeem:   $(. /etc/os-release && echo "$PRETTY_NAME"), kernel $(uname -r)"
  echo "Chromium:  $("$CHROMIUM" --version 2>/dev/null | head -n 1)"
  echo "ffmpeg:    $(ffmpeg -version | head -n 1 | cut -d' ' -f3)"
  echo "WebGL:     ${GL}"
  echo "Pagina:    ${URL}"
  echo "Opname:    ${DUUR_S} s van ${SECONDEN} s, ${BREEDTE}x${HOOGTE}@${FPS}, ${BITRATE}, ffmpeg exit ${FFMPEG_RC}"
  echo
  printf '| %-36s | %-30s | %-16s | %-5s |\n' "Meetpunt" "Uitkomst" "Drempel" ""
  printf '|%s|%s|%s|%s|\n' "--------------------------------------" "--------------------------------" "------------------" "-------"
  regel "Chromium op de GPU"               "$GL"                                "geen SwiftShader" "$GPU_GO"
  regel "ffmpeg speed (laagste na 30 s)"   "${SPEED_MIN:-?}x"                   "≥ ${MIN_SPEED}x"   "$(b ge "${SPEED_MIN:-0}" "$MIN_SPEED")"
  regel "Echte beeldsnelheid, gemiddeld"   "${UNIEK_FPS} fps"                   "≥ ${UNIEK_DREMPEL} fps" "$(uniek_oordeel "$UNIEK_FPS")"
  regel "Echte beeldsnelheid, slechtste min" "${UNIEK_MIN_MINUUT} fps"          "≥ ${UNIEK_DREMPEL} fps" "$(uniek_oordeel "$UNIEK_MIN_MINUUT")"
  regel "Echte beeldsnelheid, laatste 2 min" "${UNIEK_LAATST} fps"              "≥ ${UNIEK_DREMPEL} fps" "$(uniek_oordeel "$UNIEK_LAATST")"
  regel "CPU-idle"                         "gem ${IDLE_GEM}%, min ${IDLE_MIN}%, ${IDLE_LAAG}/${IDLE_N} < 20%" "gem ≥ ${MIN_IDLE}%" "$(b ge "$IDLE_GEM" "$MIN_IDLE")"
  regel "Temperatuur SoC"                  "max ${TEMP_MAX} °C"                 "< ${MAX_TEMP} °C"  "$(b awk -v t="$TEMP_MAX" -v m="$MAX_TEMP" 'BEGIN { exit !(t > 0 && t < m) }')"
  regel "Throttling of onderspanning"      "$TH_HEX"                            "geen"             "$TH_GO"
  regel "Chromium en ffmpeg heel gebleven" "chromium $([ $CHROME_HEEL = 1 ] && echo ja || echo nee), ffmpeg exit ${FFMPEG_RC}" "ja, exit 0" "$(b test "$CHROME_HEEL" = 1 -a "$FFMPEG_RC" = 0)"
  regel "Geheugen"                         "min ${MEM_MIN} MB vrij, Chromium max ${RSS_MAX} MB" "informatief" i
  echo
  if [ "$NOGO" = 0 ]; then
    echo "UITKOMST: GO. Volgende stap: een uur naar YouTube unlisted (zie README)."
  else
    echo "UITKOMST: NO-GO. Zie de regels hierboven en ${UIT}/ffmpeg.log en chromium.log."
  fi
  echo
  echo "Bekijk ${UIT}/frame.png: staat daar de twin, en geen foutpagina of inlogscherm?"
  echo "De opname (${UIT}/opname.mp4) is ongeveer $(( ${BITRATE%k} * SECONDEN / 8 / 1024 )) MB; weggooien mag."
  echo "De upload is niet gemeten. YouTube vraagt voor dit profiel een stabiele 3 à 4 Mbit/s."
} > "${UIT}/rapport.txt"   # geen pipe naar tee: dan draait dit blok in een subshell en gaat NOGO verloren
cat "${UIT}/rapport.txt"

exit "$NOGO"
