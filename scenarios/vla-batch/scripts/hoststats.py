#!/usr/bin/env python3
"""Host-meting van de VPS, elke minuut via cron (hoststats.sh), alleen standaardbibliotheek.

Doet twee dingen:
  1. schrijft /opt/hoststats/stats.json (atomair): een regel per container, de
     `docker stats`-velden plus herstarts en health, en een regel {"kind": "host"}.
     De batch-engine leest dit bestand voor /system/resources; nooit docker.sock.
  2. schrijft dezelfde meting als historie naar TDengine (idp.vps_host,
     idp.vps_container, idp.vps_cert), voor het Grafana-dashboard vps-health.

Draait op de host en niet in een container, zodat het dashboard ook werkt als de
demo-engine plat ligt. Valt TDengine weg, dan wordt alleen stats.json geschreven.
Wachtwoorden komen uit de .env van de stack en worden nooit gelogd.
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

OUT_DIR = Path(os.environ.get("HOSTSTATS_DIR", "/opt/hoststats"))
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
TD_CONTAINER = "vla-tdengine"
TD_DB = "idp"
CERT_HOSTS = ["enterprise-ai.techflow24.com", "mcp.techflow24.com", "sla.techflow24.com",
              "milkdemo.techflow24.com"]
BACKUP_DIR = Path("/root/backups/idp-os/nacht")
# Containers die bewust stilstaan; die tellen niet als "onverwacht gestopt".
EXPECTED_DOWN = re.compile(r"buzzws|opcua-init|bvip-migrate|portainer-old|emcp-embeddings")
SLOW_EVERY_S = 600          # OOM-telling en back-upleeftijd
CERT_EVERY_S = 3600


# ── metingen (puur, getest) ──────────────────────────────────────────────
def cpu_times(stat_text: str) -> list[int]:
    """Eerste regel van /proc/stat: user nice system idle iowait irq softirq steal."""
    parts = stat_text.splitlines()[0].split()
    return [int(x) for x in parts[1:9]]


def cpu_pct(prev: Optional[list[int]], cur: list[int]) -> dict:
    keys = ("cpu_user", "cpu_system", "cpu_idle", "cpu_iowait", "cpu_steal")
    if not prev or len(prev) != len(cur):
        return dict.fromkeys(keys)
    d = [c - p for c, p in zip(cur, prev)]
    total = sum(d)
    if total <= 0:
        return dict.fromkeys(keys)
    user, nice, system, idle, iowait, irq, softirq, steal = d
    pct = lambda v: round(100.0 * v / total, 2)  # noqa: E731
    return {"cpu_user": pct(user + nice), "cpu_system": pct(system + irq + softirq),
            "cpu_idle": pct(idle), "cpu_iowait": pct(iowait), "cpu_steal": pct(steal)}


def meminfo(text: str) -> dict:
    kb = {}
    for line in text.splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            kb[m.group(1)] = int(m.group(2))
    mb = lambda k: round(kb.get(k, 0) / 1024.0, 1)  # noqa: E731
    return {"mem_total_mb": mb("MemTotal"), "mem_avail_mb": mb("MemAvailable"),
            "swap_used_mb": round((kb.get("SwapTotal", 0) - kb.get("SwapFree", 0)) / 1024.0, 1)}


def disk(st) -> dict:
    """Zoals df: gebruikt / (gebruikt + beschikbaar voor gewone gebruikers)."""
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    return {"disk_used_pct": round(100.0 * used / (used + avail), 2) if used + avail else None,
            "disk_free_gb": round(avail / 1024 ** 3, 2)}


def mib(s: str) -> Optional[float]:
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?i?B)", s or "")
    if not m:
        return None
    f = {"B": 1 / 1024 ** 2, "KiB": 1 / 1024, "kB": 1 / 1024, "KB": 1 / 1024, "MiB": 1, "MB": 1,
         "GiB": 1024, "GB": 1024, "TiB": 1024 ** 2}.get(m.group(2))
    return None if f is None else round(float(m.group(1)) * f, 1)


def pct(s: str) -> Optional[float]:
    try:
        return float((s or "").strip().rstrip("%"))
    except ValueError:
        return None


def containers(stats_lines: list[dict], inspect_lines: list[str]) -> list[dict]:
    """docker stats (draaiend) + docker inspect (alles) -> een rij per container."""
    info = {}
    for line in inspect_lines:
        p = line.split("|")
        if len(p) < 5:
            continue
        name = p[0].lstrip("/")
        info[name] = {"restarts": int(p[1] or 0), "health": p[2] or None,
                      "running": p[3] == "true", "mem_limit_mb": round(int(p[4] or 0) / 1024 ** 2, 1) or None}
    out = []
    for s in stats_lines:
        name = s.get("Name", "")
        used, _, limit = (s.get("MemUsage") or "").partition("/")
        row = {**s, "kind": "container", "cpu_pct": pct(s.get("CPUPerc")), "mem_mb": mib(used),
               "mem_pct": pct(s.get("MemPerc")), **info.pop(name, {"restarts": None, "health": None,
                                                                   "running": True, "mem_limit_mb": None})}
        if row.get("mem_limit_mb") is None:
            row["mem_limit_mb"] = mib(limit)
        out.append(row)
    for name, i in info.items():   # gestopt: geen docker stats-regel
        out.append({"Name": name, "kind": "container", "cpu_pct": None, "mem_mb": None,
                    "mem_pct": None, **i})
    return out


def summary(rows: list[dict]) -> dict:
    running = [r for r in rows if r.get("running")]
    stopped = [r for r in rows if not r.get("running")]
    return {"containers_running": len(running),
            "containers_unhealthy": sum(1 for r in running if r.get("health") == "unhealthy"),
            "containers_stopped_unexpected": sum(1 for r in stopped
                                                 if not EXPECTED_DOWN.search(r.get("Name", "")))}


# ── TDengine ─────────────────────────────────────────────────────────────
SCHEMA = [
    f"CREATE STABLE IF NOT EXISTS {TD_DB}.vps_host (ts TIMESTAMP, load1 DOUBLE, load5 DOUBLE, "
    "load15 DOUBLE, cpu_user DOUBLE, cpu_system DOUBLE, cpu_idle DOUBLE, cpu_iowait DOUBLE, "
    "cpu_steal DOUBLE, mem_total_mb DOUBLE, mem_avail_mb DOUBLE, swap_used_mb DOUBLE, "
    "disk_used_pct DOUBLE, disk_free_gb DOUBLE, containers_running INT, containers_unhealthy INT, "
    "containers_stopped_unexpected INT, oom_24h INT, backup_age_h DOUBLE, uptime_h DOUBLE) "
    "TAGS (host_name VARCHAR(64))",
    f"CREATE STABLE IF NOT EXISTS {TD_DB}.vps_container (ts TIMESTAMP, cpu_pct DOUBLE, mem_mb DOUBLE, "
    "mem_limit_mb DOUBLE, mem_pct DOUBLE, restarts INT, healthy INT, running INT) "
    "TAGS (container_name VARCHAR(96))",
    f"CREATE STABLE IF NOT EXISTS {TD_DB}.vps_cert (ts TIMESTAMP, days_left DOUBLE) "
    "TAGS (cert_host VARCHAR(96))",
]
HOST_COLS = ["load1", "load5", "load15", "cpu_user", "cpu_system", "cpu_idle", "cpu_iowait",
             "cpu_steal", "mem_total_mb", "mem_avail_mb", "swap_used_mb", "disk_used_pct",
             "disk_free_gb", "containers_running", "containers_unhealthy",
             "containers_stopped_unexpected", "oom_24h", "backup_age_h", "uptime_h"]


def lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):          # INT-kolommen: nooit "0.0"
        return str(v)
    if isinstance(v, float):
        f = float(v)
        return "NULL" if f != f or f in (float("inf"), float("-inf")) else repr(round(f, 4))
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower())[:150]


def insert_sql(ts_ms: int, hostname: str, host: dict, rows: list[dict], certs: dict) -> str:
    parts = [f"{TD_DB}.vps_h_{slug(hostname)} USING {TD_DB}.vps_host TAGS ({lit(hostname)}) "
             f"VALUES ({ts_ms}, {', '.join(lit(host.get(c)) for c in HOST_COLS)})"]
    for r in rows:
        # Een gestopte container houdt zijn laatste healthstatus; die zegt niets meer.
        healthy = ({"healthy": 1, "unhealthy": 0}.get(r.get("health") or "")
                   if r.get("running") else None)
        parts.append(f"{TD_DB}.vps_c_{slug(r['Name'])} USING {TD_DB}.vps_container "
                     f"TAGS ({lit(r['Name'])}) VALUES ({ts_ms}, {lit(r.get('cpu_pct'))}, "
                     f"{lit(r.get('mem_mb'))}, {lit(r.get('mem_limit_mb'))}, {lit(r.get('mem_pct'))}, "
                     f"{lit(r.get('restarts'))}, {lit(healthy)}, {lit(1 if r.get('running') else 0)})")
    for h, days in certs.items():
        parts.append(f"{TD_DB}.vps_cert_{slug(h)} USING {TD_DB}.vps_cert TAGS ({lit(h)}) "
                     f"VALUES ({ts_ms}, {lit(days)})")
    return "INSERT INTO " + " ".join(parts)


def read_env(path: Path) -> dict:
    env = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def td_post(url: str, user: str, password: str, sql: str) -> None:
    auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(f"{url}/rest/sql", data=sql.encode("utf-8"), method="POST",
                                 headers={"Authorization": auth, "Content-Type": "text/plain"})
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.load(r)
    if body.get("code", 0) != 0:
        raise RuntimeError(f"TDengine {body.get('code')}: {body.get('desc')}")


# ── verzamelen op de host ────────────────────────────────────────────────
def run(cmd: list[str], timeout: float = 25) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL).stdout


def cached(name: str, every_s: float, fn):
    f = OUT_DIR / f".cache-{name}.json"
    try:
        c = json.loads(f.read_text())
        if time.time() - c["at"] < every_s:
            return c["value"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        value = fn()
    except Exception:  # noqa: BLE001  een trage meting mag de rest niet tegenhouden
        value = None
    f.write_text(json.dumps({"at": time.time(), "value": value}))
    return value


def oom_24h() -> int:
    out = run(["journalctl", "-k", "-q", "--no-pager", "--since", "24 hours ago"], timeout=20)
    return sum(1 for line in out.splitlines() if "out of memory" in line.lower())


def backup_age_h() -> Optional[float]:
    return round((time.time() - BACKUP_DIR.stat().st_mtime) / 3600.0, 2)


def cert_days(host: str) -> Optional[float]:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=8) as s, \
            ctx.wrap_socket(s, server_hostname=host) as t:
        end = ssl.cert_time_to_seconds(t.getpeercert()["notAfter"])
    return round((end - time.time()) / 86400.0, 2)


def all_certs() -> dict:
    out = {}
    for h in CERT_HOSTS:
        try:
            out[h] = cert_days(h)
        except Exception:  # noqa: BLE001  een onbereikbaar certificaat telt als NULL, niet als fout
            out[h] = None
    return out


def collect() -> tuple[dict, list[dict], dict]:
    prev_f = OUT_DIR / ".cpu_prev"
    cur = cpu_times(Path("/proc/stat").read_text())
    try:
        prev = json.loads(prev_f.read_text())
    except (OSError, ValueError):
        prev = None
    prev_f.write_text(json.dumps(cur))

    load = Path("/proc/loadavg").read_text().split()
    host = {"kind": "host", "measured_at": datetime.now(timezone.utc).isoformat(),
            "load1": float(load[0]), "load5": float(load[1]), "load15": float(load[2]),
            **cpu_pct(prev, cur), **meminfo(Path("/proc/meminfo").read_text()),
            **disk(os.statvfs("/")),
            "uptime_h": round(float(Path("/proc/uptime").read_text().split()[0]) / 3600.0, 2)}

    stats = [json.loads(x) for x in run(["docker", "stats", "--no-stream", "--format",
                                         "{{json .}}"]).splitlines() if x.strip()]
    ids = run(["docker", "ps", "-aq"]).split()
    inspect = run(["docker", "inspect", "-f",
                   "{{.Name}}|{{.RestartCount}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}"
                   "|{{.State.Running}}|{{.HostConfig.Memory}}", *ids]).splitlines() if ids else []
    rows = containers(stats, inspect)
    host.update(summary(rows))
    host["oom_24h"] = cached("oom", SLOW_EVERY_S, oom_24h)
    host["backup_age_h"] = cached("backup", SLOW_EVERY_S, backup_age_h)
    certs = cached("certs", CERT_EVERY_S, all_certs) or {}
    return host, rows, certs


def write_stats(host: dict, rows: list[dict]) -> None:
    tmp = OUT_DIR / ".stats.json.tmp"
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            if r.get("running"):
                f.write(json.dumps(r) + "\n")
        f.write(json.dumps(host) + "\n")
    os.replace(tmp, OUT_DIR / "stats.json")


def td_url() -> Optional[str]:
    ip = run(["docker", "inspect", "-f",
              "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", TD_CONTAINER]).split()
    return f"http://{ip[0]}:6041" if ip else None


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    host, rows, certs = collect()
    write_stats(host, rows)
    url = td_url()
    if not url:
        print("hoststats: TDengine niet gevonden, alleen stats.json geschreven", file=sys.stderr)
        return 0
    env = read_env(ENV_FILE)
    user, password = env.get("TD_USER", "root"), env.get("TD_PASS", "")
    ts_ms = int(time.time() * 1000)
    try:
        marker = OUT_DIR / ".schema-v1"
        if not marker.exists():
            for stmt in SCHEMA:
                td_post(url, user, password, stmt)
            marker.write_text("ok")
        td_post(url, user, password, insert_sql(ts_ms, socket.gethostname(), host, rows, certs))
    except Exception as e:  # noqa: BLE001  TDengine weg: volgende minuut opnieuw
        (OUT_DIR / ".schema-v1").unlink(missing_ok=True)
        print(f"hoststats: TDengine-schrijven mislukt: {type(e).__name__}: {str(e)[:200]}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
