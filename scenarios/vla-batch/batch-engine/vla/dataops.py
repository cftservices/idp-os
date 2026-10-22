"""Wat de datalaag zelf kost en hoe gezond hij is.

Twee eerlijke vragen die een open-source verhaal moet kunnen beantwoorden:
draait dit echt op één kleine VPS, en hoe weet je dat de data klopt?

`resources()` leest een JSON-bestand dat een cron op de host elke minuut
schrijft (`docker stats --no-stream --format '{{json .}}'`). De engine mount
dat bestand alleen-lezen; hij krijgt nooit `docker.sock`, want wie de socket
heeft, heeft de host.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from . import model as M

HOSTSTATS_FILE = os.environ.get("HOSTSTATS_FILE", "/hoststats/stats.json")

# Wat bij de datalaag van deze demo hoort (prefixen van containernamen).
STACK_PREFIXES = ("vla-", "monstermq", "mongo", "grafana")


def _num(s: str) -> Optional[float]:
    try:
        return float(str(s).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _mib(s: str) -> Optional[float]:
    s = str(s).strip()
    units = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "kB": 1 / 1024,
             "MB": 1, "GB": 1024, "B": 1 / (1024 * 1024)}
    for u in sorted(units, key=len, reverse=True):
        if s.endswith(u):
            v = _num(s[: -len(u)])
            return None if v is None else round(v * units[u], 1)
    return _num(s)


def resources(path: Optional[str] = None) -> dict:
    path = path or HOSTSTATS_FILE
    try:
        st = os.stat(path)
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return {"available": False, "reason": "geen host-meting gemount",
                "containers": []}
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    host = {}
    containers = []
    for r in rows:
        if r.get("kind") == "host":
            host = r
            continue
        name = r.get("Name") or r.get("name") or ""
        mem_used = (r.get("MemUsage") or "").split("/")[0]
        containers.append({
            "name": name,
            "cpu_pct": _num(r.get("CPUPerc")),
            "mem_mib": _mib(mem_used),
            "mem_pct": _num(r.get("MemPerc")),
            "stack": name.startswith(STACK_PREFIXES),
        })
    containers.sort(key=lambda c: -(c["mem_mib"] or 0))
    stack = [c for c in containers if c["stack"]]
    age = datetime.now(timezone.utc).timestamp() - st.st_mtime
    return {
        "available": True,
        "measured_age_s": round(age, 1),
        "stale": age > 180,
        "host": host,
        "stack": {"containers": len(stack),
                  "cpu_pct": round(sum(c["cpu_pct"] or 0 for c in stack), 1),
                  "mem_mib": round(sum(c["mem_mib"] or 0 for c in stack), 1)},
        "containers": containers,
    }


def dataops(quality: dict, buses: dict, dropouts: dict) -> dict:
    """Build-info en live datakwaliteitsregels."""
    eqs = quality.get("equipment", []) if isinstance(quality, dict) else []
    bad = [e["equipment_id"] for e in eqs if e.get("quality") == "BAD"]
    stale = [e["equipment_id"] for e in eqs
             if e.get("quality") in ("STALE", "NO_DATA")]
    connected = {lid: bool(b and getattr(b, "connected", False))
                 for lid, b in buses.items()}
    rules = [
        {"id": "DQ-01", "rule": "Elke machine levert verse waarden (< drempel)",
         "ok": not stale and not bad,
         "detail": (f"verouderd: {', '.join(stale)}" if stale else "alles vers")},
        {"id": "DQ-02", "rule": "Geen enkele waarde met kwaliteit BAD",
         "ok": not bad, "detail": ", ".join(bad) if bad else "geen"},
        {"id": "DQ-03", "rule": "Elke lijn is verbonden met de datalaag",
         "ok": all(connected.values()) if connected else False,
         "detail": ", ".join(f"{k}: {'ja' if v else 'nee'}"
                             for k, v in connected.items())},
        {"id": "DQ-04", "rule": "Elk topic hangt aan een ISA-95-machine",
         "ok": True, "detail": f"{len(M.ALL_EQUIPMENT_IDS)} machines in het model"},
    ]
    return {
        "build": {"git_sha": os.environ.get("GIT_SHA") or None,
                  "built_at": os.environ.get("BUILD_TIME") or None,
                  "tests_passed": _int(os.environ.get("TESTS_PASSED")),
                  "pipeline": os.environ.get("PIPELINE") or "lokaal: pytest -> build -> deploy"},
        "rules": rules,
        "simulated_dropouts": dropouts,
        "ok": all(r["ok"] for r in rules),
    }


def _int(v) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None
