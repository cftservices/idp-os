#!/usr/bin/env bash
# Host-meting voor het DataOps-tabblad van de datalaag-lens.
# Draait op de host via cron, zodat de engine nooit docker.sock nodig heeft:
#   * * * * * /opt/idp/scenarios/vla-batch/scripts/hoststats.sh
# Schrijft atomair (tmp + mv), want de engine leest het bestand op elk moment.
set -euo pipefail
OUT_DIR=/opt/hoststats
mkdir -p "$OUT_DIR"
TMP="$OUT_DIR/.stats.json.tmp"
docker stats --no-stream --format '{{json .}}' > "$TMP"
mv -f "$TMP" "$OUT_DIR/stats.json"