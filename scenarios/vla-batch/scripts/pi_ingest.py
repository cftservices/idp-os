#!/usr/bin/env python3
"""Ontvangt één meting van de Raspberry Pi 5 en schrijft die naar TDengine (idp.pi_host).

Draait als *forced command* achter een eigen ssh-sleutel van de Pi, in
/root/.ssh/authorized_keys op de VPS:

    restrict,command="python3 /root/idp-os/scenarios/vla-batch/scripts/pi_ingest.py pi5" ssh-ed25519 AAAA... pi-health

De Pi stuurt elke minuut één JSON-regel op stdin (broadcast/pi5/pi-health.sh). Die sleutel kan
dus niets anders dan deze ene regel afleveren: geen shell, geen tunnel, en het TDengine-wachtwoord
blijft op de VPS. Daarom vertrouwt dit script de invoer niet: alleen bekende velden, alleen
getallen, de tijd van de VPS en een vaste hostnaam uit de sleutelregel.

Dashboard: grafana/pi_board.py (uid pi5-health).
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hoststats as h  # noqa: E402  (read_env, td_post, td_url, lit, TD_DB, ENV_FILE)

MAX_INVOER = 8192
MARKER = h.OUT_DIR / ".pi-schema-v1"

# kolom -> type. Volgorde = kolomvolgorde in de tabel.
KOLOMMEN: dict[str, type] = {
    "temp_c": float, "fan_rpm": int, "arm_mhz": float,
    "throttled_raw": int, "undervolt_now": int, "freq_capped_now": int, "throttled_now": int,
    "soft_temp_now": int, "undervolt_ever": int, "freq_capped_ever": int, "throttled_ever": int,
    "soft_temp_ever": int,
    "load1": float, "load5": float, "load15": float,
    "cpu_user": float, "cpu_system": float, "cpu_iowait": float, "cpu_idle": float,
    "mem_total_mb": float, "mem_avail_mb": float, "swap_used_mb": float,
    "disk_used_pct": float, "uptime_h": float, "containers_running": int,
    "stream_active": int, "stream_restarts": int, "stream_speed": float, "stream_fps": float,
    "stream_kbps": float,
}
SQL_TYPE = {float: "DOUBLE", int: "INT"}
SCHEMA = (f"CREATE STABLE IF NOT EXISTS {h.TD_DB}.pi_host (ts TIMESTAMP, "
          + ", ".join(f"{k} {SQL_TYPE[t]}" for k, t in KOLOMMEN.items())
          + ") TAGS (host_name VARCHAR(64))")


def schoon(meting: dict) -> dict:
    """Alleen bekende velden met een eindig getal; de rest wordt NULL."""
    uit = {}
    for k, t in KOLOMMEN.items():
        v = meting.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            uit[k] = None
        else:
            uit[k] = int(v) if t is int else float(v)
    return uit


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "pi5"
    if not re.fullmatch(r"[a-z0-9_]{1,32}", host):
        print("pi_ingest: ongeldige hostnaam", file=sys.stderr)
        return 2
    ruw = sys.stdin.read(MAX_INVOER + 1)
    if len(ruw) > MAX_INVOER:
        print("pi_ingest: invoer te groot", file=sys.stderr)
        return 2
    try:
        meting = json.loads(ruw)
    except json.JSONDecodeError as e:
        print(f"pi_ingest: geen geldige JSON ({e.msg})", file=sys.stderr)
        return 2
    if not isinstance(meting, dict):
        print("pi_ingest: verwacht een JSON-object", file=sys.stderr)
        return 2
    rij = schoon(meting)

    url = h.td_url()
    if not url:
        print("pi_ingest: TDengine niet gevonden", file=sys.stderr)
        return 1
    env = h.read_env(h.ENV_FILE)
    user, password = env.get("TD_USER", "root"), env.get("TD_PASS", "")
    ts_ms = int(time.time() * 1000)
    sql = (f"INSERT INTO {h.TD_DB}.pi_h_{host} USING {h.TD_DB}.pi_host TAGS ({h.lit(host)}) "
           f"VALUES ({ts_ms}, {', '.join(h.lit(rij[k]) for k in KOLOMMEN)})")
    try:
        if not MARKER.exists():
            h.td_post(url, user, password, SCHEMA)
            MARKER.write_text("ok")
        h.td_post(url, user, password, sql)
    except Exception as e:  # noqa: BLE001  volgende minuut opnieuw
        MARKER.unlink(missing_ok=True)
        print(f"pi_ingest: schrijven mislukt: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return 1
    print(f"ok {sum(v is not None for v in rij.values())}/{len(KOLOMMEN)} velden")
    return 0


if __name__ == "__main__":
    sys.exit(main())
