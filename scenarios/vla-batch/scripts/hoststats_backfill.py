#!/usr/bin/env python3
"""Vul idp.vps_host aan met de geschiedenis die sysstat al had (sar, elke 2 minuten).

    python3 hoststats_backfill.py            # proef: telt wat er zou komen, schrijft niets
    python3 hoststats_backfill.py --schrijf  # schrijft naar TDengine

Alleen de host-kolommen die sysstat kent: CPU (ook steal), load, beschikbaar geheugen en
swap. Containers, schijf, OOM, back-up en certificaten blijven NULL: daar is geen
geschiedenis van, en een verzonnen waarde is erger dan een gat. Schrijft alleen
tijdstippen van vóór de eerste live meting, zodat er niets dubbel komt.
"""
from __future__ import annotations

import glob
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hoststats as H  # noqa: E402

SA_FILES = "/var/log/sysstat/sa[0-9][0-9]"
BATCH = 400


def parse_sadf(text: str) -> dict[int, dict[str, float]]:
    """`sadf -d -U` -> {epoch_s: {kolom: waarde}}. Herstartregels en kopregels vallen weg."""
    out: dict[int, dict[str, float]] = {}
    header = None
    for line in text.splitlines():
        if line.startswith("#"):
            header = [h.strip() for h in line.lstrip("# ").split(";")]
            continue
        parts = line.split(";")
        if not header or len(parts) != len(header):
            continue
        try:
            ts = int(parts[2])
            vals = {h: float(v) for h, v in zip(header[3:], parts[3:])}
        except ValueError:          # bv. "LINUX-RESTART (2 CPU)"
            continue
        out.setdefault(ts, {}).update(vals)
    return out


def to_host_row(v: dict[str, float], mem_total_mb: float) -> dict:
    def g(k):
        return v.get(k)

    def add(*ks):
        xs = [v[k] for k in ks if k in v]
        return round(sum(xs), 2) if len(xs) == len(ks) else None

    return {"load1": g("ldavg-1"), "load5": g("ldavg-5"), "load15": g("ldavg-15"),
            "cpu_user": add("%usr", "%nice"), "cpu_system": add("%sys", "%irq", "%soft"),
            "cpu_idle": g("%idle"), "cpu_iowait": g("%iowait"), "cpu_steal": g("%steal"),
            "mem_total_mb": mem_total_mb,
            "mem_avail_mb": round(v["kbavail"] / 1024.0, 1) if "kbavail" in v else None,
            "swap_used_mb": round(v["kbswpused"] / 1024.0, 1) if "kbswpused" in v else None}


def insert_batches(rows: list[tuple[int, dict]], hostname: str) -> list[str]:
    table = f"{H.TD_DB}.vps_h_{H.slug(hostname)} USING {H.TD_DB}.vps_host TAGS ({H.lit(hostname)})"
    out = []
    for i in range(0, len(rows), BATCH):
        vals = " ".join(f"({ts * 1000}, {', '.join(H.lit(r.get(c)) for c in H.HOST_COLS)})"
                        for ts, r in rows[i:i + BATCH])
        out.append(f"INSERT INTO {table} VALUES {vals}")
    return out


def main() -> int:
    write = "--schrijf" in sys.argv
    mem_total = H.meminfo(Path("/proc/meminfo").read_text())["mem_total_mb"]
    merged: dict[int, dict[str, float]] = {}
    for f in sorted(glob.glob(SA_FILES)):
        for act in (["-u", "ALL"], ["-q"], ["-r"], ["-S"]):
            for ts, v in parse_sadf(H.run(["sadf", "-d", "-U", f, "--", *act], timeout=60)).items():
                merged.setdefault(ts, {}).update(v)

    url = H.td_url()
    env = H.read_env(H.ENV_FILE)
    user, password = env.get("TD_USER", "root"), env.get("TD_PASS", "")
    first_live = None
    if url:
        import base64, json, urllib.request  # noqa: E401
        req = urllib.request.Request(
            f"{url}/rest/sql", data=f"select cast(first(ts) as bigint) from {H.TD_DB}.vps_host".encode(),
            method="POST", headers={"Authorization": "Basic " + base64.b64encode(
                f"{user}:{password}".encode()).decode()})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r).get("data") or []
        first_live = data[0][0] // 1000 if data and data[0][0] else None

    rows = sorted((ts, to_host_row(v, mem_total)) for ts, v in merged.items()
                  if first_live is None or ts < first_live)
    print(f"sysstat: {len(merged)} tijdstippen, waarvan {len(rows)} vóór de eerste live meting"
          + (f" ({rows[0][0]} t/m {rows[-1][0]})" if rows else ""))
    if not write or not rows:
        print("proef: niets geschreven" if not write else "niets te schrijven")
        return 0
    if not url:
        print("TDengine niet gevonden", file=sys.stderr)
        return 1
    for stmt in insert_batches(rows, socket.gethostname()):
        H.td_post(url, user, password, stmt)
    print(f"geschreven: {len(rows)} rijen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
