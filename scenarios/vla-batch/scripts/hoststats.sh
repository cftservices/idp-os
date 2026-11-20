#!/usr/bin/env bash
# Host-meting van de VPS, elke minuut via cron:
#   * * * * * /root/idp-os/scenarios/vla-batch/scripts/hoststats.sh
# Het werk zit in hoststats.py: stats.json voor de engine (nooit docker.sock) en
# de historie in TDengine voor het Grafana-dashboard vps-health.
# flock: een trage minuut (CPU-steal) mag niet stapelen met de volgende.
set -euo pipefail
OUT_DIR=/opt/hoststats
mkdir -p "$OUT_DIR"
exec flock -n "$OUT_DIR/.lock" timeout 55 python3 "$(dirname "$0")/hoststats.py" \
    2>>/var/log/hoststats.log
