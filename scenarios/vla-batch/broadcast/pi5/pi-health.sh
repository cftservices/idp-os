#!/bin/bash
# Gezondheid van de Raspberry Pi 5, elke minuut naar het Grafana-dashboard pi5-health.
#
#   ./pi-health.sh          print de meting als JSON (om te kijken)
#   ./pi-health.sh send     stuurt hem naar de VPS
#
# Cron op de Pi (crontab -e):
#   * * * * * $HOME/pi-health.sh send >>$HOME/.pi-health.log 2>&1
#
# De sleutel ~/.ssh/pi-health mag op de VPS alleen scripts/pi_ingest.py voeden (forced command):
# geen shell, geen tunnel. Het TDengine-wachtwoord blijft op de VPS.
#
# Gemeten: temperatuur, throttling en onderspanning (nu en sinds de boot), ventilator,
# CPU-klok, load, CPU over de afgelopen minuut, geheugen, schijf, containers, en de stream
# (systemd-unit vla-stream plus de voortgang van ffmpeg).
set -uo pipefail

VPS=root@147.93.56.23
SLEUTEL="$HOME/.ssh/pi-health"
STAAT="$HOME/.cache/pi-health-cpu"
VOORTGANG=/tmp/vla-stream-voortgang.txt
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

num() { [[ "$1" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] && echo "$1" || echo null; }

meet() {
  local temp fan mhz th load1 load5 load15 cpu mem swap disk up cont
  temp=$(awk '{ printf "%.1f", $1 / 1000 }' /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
  fan=$(cat /sys/class/hwmon/*/fan1_input 2>/dev/null | head -n 1)
  mhz=$(vcgencmd measure_clock arm 2>/dev/null | awk -F= '{ printf "%.0f", $2 / 1000000 }')
  th=$(( $(vcgencmd get_throttled 2>/dev/null | cut -d= -f2 || echo 0) ))
  read -r load1 load5 load15 _ < /proc/loadavg

  # CPU over de afgelopen minuut: verschil met de vorige stand van /proc/stat.
  local nu vorig
  nu=$(awk '/^cpu / { print $2 + $3, $4 + $7 + $8, $6, $5, $2 + $3 + $4 + $5 + $6 + $7 + $8 + $9 }' /proc/stat)
  mkdir -p "$(dirname "$STAAT")"
  vorig=$(cat "$STAAT" 2>/dev/null || echo "")
  echo "$nu" > "$STAAT"
  cpu=$(awk -v a="$vorig" -v b="$nu" 'BEGIN {
    n = split(a, p, " "); split(b, q, " "); t = q[5] - p[5]
    if (n < 5 || t <= 0) { print "null null null null"; exit }
    printf "%.1f %.1f %.1f %.1f", (q[1]-p[1])*100/t, (q[2]-p[2])*100/t, (q[3]-p[3])*100/t, (q[4]-p[4])*100/t }')

  mem=$(awk '/MemTotal/ { t = $2 } /MemAvailable/ { a = $2 } END { printf "%.0f %.0f", t / 1024, a / 1024 }' /proc/meminfo)
  swap=$(awk '/SwapTotal/ { t = $2 } /SwapFree/ { f = $2 } END { printf "%.0f", (t - f) / 1024 }' /proc/meminfo)
  disk=$(df --output=pcent / | tail -n 1 | tr -dc '0-9')
  up=$(awk '{ printf "%.2f", $1 / 3600 }' /proc/uptime)
  cont=$(docker ps -q 2>/dev/null | wc -l)

  # Stream: unit actief, herstarts, en de laatste voortgang van ffmpeg (alleen als die vers is).
  local actief herstarts speed fps kbps
  actief=0; systemctl --user is-active -q vla-stream 2>/dev/null && actief=1
  herstarts=$(systemctl --user show vla-stream -p NRestarts --value 2>/dev/null)
  speed=null; fps=null; kbps=null
  if [ "$actief" = 1 ] && [ -f "$VOORTGANG" ] && [ $(( $(date +%s) - $(stat -c %Y "$VOORTGANG") )) -lt 90 ]; then
    speed=$(num "$(grep '^speed=' "$VOORTGANG" | tail -n 1 | sed 's/speed= *//; s/x$//')")
    fps=$(num "$(grep '^fps=' "$VOORTGANG" | tail -n 1 | cut -d= -f2)")
    kbps=$(num "$(grep '^bitrate=' "$VOORTGANG" | tail -n 1 | sed 's/bitrate= *//; s/kbits\/s$//')")
  fi

  local cu cs cw ci mt ma
  read -r cu cs cw ci <<<"$cpu"
  read -r mt ma <<<"$mem"
  printf '{"temp_c":%s,"fan_rpm":%s,"arm_mhz":%s,"throttled_raw":%s,' \
    "$(num "$temp")" "$(num "${fan:-}")" "$(num "$mhz")" "$th"
  printf '"undervolt_now":%d,"freq_capped_now":%d,"throttled_now":%d,"soft_temp_now":%d,' \
    $(( th & 1 )) $(( (th >> 1) & 1 )) $(( (th >> 2) & 1 )) $(( (th >> 3) & 1 ))
  printf '"undervolt_ever":%d,"freq_capped_ever":%d,"throttled_ever":%d,"soft_temp_ever":%d,' \
    $(( (th >> 16) & 1 )) $(( (th >> 17) & 1 )) $(( (th >> 18) & 1 )) $(( (th >> 19) & 1 ))
  printf '"load1":%s,"load5":%s,"load15":%s,' "$(num "$load1")" "$(num "$load5")" "$(num "$load15")"
  printf '"cpu_user":%s,"cpu_system":%s,"cpu_iowait":%s,"cpu_idle":%s,' "$(num "$cu")" "$(num "$cs")" "$(num "$cw")" "$(num "$ci")"
  printf '"mem_total_mb":%s,"mem_avail_mb":%s,"swap_used_mb":%s,"disk_used_pct":%s,' \
    "$(num "$mt")" "$(num "$ma")" "$(num "$swap")" "$(num "$disk")"
  printf '"uptime_h":%s,"containers_running":%s,' "$(num "$up")" "$(num "$cont")"
  printf '"stream_active":%d,"stream_restarts":%s,"stream_speed":%s,"stream_fps":%s,"stream_kbps":%s}\n' \
    "$actief" "$(num "${herstarts:-}")" "$speed" "$fps" "$kbps"
}

case "${1:-}" in
  send)
    meet | ssh -i "$SLEUTEL" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 "$VPS" \
      | grep -v '^ok ' || true
    ;;
  *)
    meet
    ;;
esac
