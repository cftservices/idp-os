"""Aspen IP.21 REST historian stub.

Simuleert een Aspen IP.21 historian REST endpoint. Bedoeld om in workshop 1
CONNECT te tonen hoe je een REST-bron aansluit op MonsterMQ via polling.

Endpoints:
  GET /health                       -> liveness probe
  GET /tags                         -> lijst alle beschikbare tag-namen
  GET /tags/{name}/current          -> {name, value, timestamp}
  GET /tags/{name}/history?hours=N  -> [{timestamp, value}, ...]
"""
import math
import random
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException


app = FastAPI(title="IP.21 Stub", version="0.1.0")


# Aggregate "historian" tag-set — bewust een subset van DairyPlant met
# 60-min aggregates (lijkt op echte IP.21 use-case: reporting-data).
TAGS = {
    "HTST_temp_C_60min_avg": {"base": 72.0, "amp": 0.3, "noise": 0.05, "unit": "C"},
    "flow_L_min_60min_avg": {"base": 1000.0, "amp": 50.0, "noise": 5.0, "unit": "L/min"},
    "fat_pct_60min_avg": {"base": 3.5, "amp": 0.05, "noise": 0.02, "unit": "%"},
    "homog_pressure_bar_60min_avg": {"base": 180.0, "amp": 3.0, "noise": 0.5, "unit": "bar"},
    "bottles_per_hour_total": {"base": 7200.0, "amp": 200.0, "noise": 30.0, "unit": "units/h"},
}


def _generate_value(spec: dict, t: int) -> float:
    """Deterministic-ish sinusoidal value with noise, like a real historian feed."""
    return round(spec["base"] + spec["amp"] * math.sin(t * 0.02) + random.gauss(0, spec["noise"]), 3)


@app.get("/health")
def health():
    return {"status": "ok", "service": "ip21-stub"}


@app.get("/tags")
def list_tags():
    return {
        "count": len(TAGS),
        "tags": [{"name": name, "unit": spec["unit"]} for name, spec in TAGS.items()],
    }


@app.get("/tags/{name}/current")
def tag_current(name: str):
    spec = TAGS.get(name)
    if not spec:
        raise HTTPException(status_code=404, detail=f"tag '{name}' not found")
    now = datetime.now(timezone.utc)
    return {
        "name": name,
        "value": _generate_value(spec, int(now.timestamp() // 60)),
        "unit": spec["unit"],
        "timestamp": now.isoformat(),
    }


@app.get("/tags/{name}/history")
def tag_history(name: str, hours: int = 24):
    spec = TAGS.get(name)
    if not spec:
        raise HTTPException(status_code=404, detail=f"tag '{name}' not found")
    if not 1 <= hours <= 168:
        raise HTTPException(status_code=400, detail="hours must be between 1 and 168")

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    samples = []
    for i in range(hours, 0, -1):
        ts = now - timedelta(hours=i)
        t = int(ts.timestamp() // 60)
        samples.append({"timestamp": ts.isoformat(), "value": _generate_value(spec, t)})
    return {"name": name, "unit": spec["unit"], "samples": samples}
