"""De datalaag als object: wat sluit erop aan, welke context draagt elk signaal,
en kun je het vertrouwen.

Drie leesfuncties voor de twin en de MCP-tools:

- `layer_map`  — bronnen, de datalaag zelf, opslag en afnemers, met live status.
- `context`    — per machine het ISA-95-pad, de topics met quality en leeftijd,
                 en het verschil tussen ruw (OPC-UA node) en gemodelleerd (topic).
- `quality`    — per machine GOOD / STALE / BAD, inclusief een gesimuleerde
                 sensoruitval die je in een demo aan en uit zet.

Bewust alleen-lezen. Probes naar externe diensten zijn TCP-connects met een
korte timeout en een cache, zodat een trage historian de twin niet ophoudt.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import model as M
from . import kpi as K

STALE_DEFAULT_S = 90.0
PROBE_TTL_S = 30.0

# Opslag en afnemers. Host en poort zijn per omgeving te overschrijven; zonder
# netwerk (tests, laptop) meldt de probe eerlijk "onbereikbaar".
_SERVICES = [
    {"id": "historian", "kind": "store", "name": "Historian (TDengine)",
     "env": "PROBE_TDENGINE", "default": "vla-tdengine:6041",
     "role": "Tijdreeksen van elk topic, voor trends en rapporten"},
    {"id": "documents", "kind": "store", "name": "Documentopslag (MongoDB)",
     "env": None, "default": None,
     "role": "Batches, orders, pallets, werkorders en events"},
    {"id": "dashboards", "kind": "consumer", "name": "Dashboards (Grafana)",
     "env": "PROBE_GRAFANA", "default": "grafana:3000",
     "role": "Trends en overzichten op de historian"},
    {"id": "ai", "kind": "consumer", "name": "Lokale AI (vragen aan je fabriek)",
     "env": "PROBE_AI", "default": "vla-ai:8080",
     "role": "Lokale taalmodellen; geen data buiten de fabriek"},
    {"id": "twin", "kind": "consumer", "name": "3D-twin en MES-schermen",
     "env": None, "default": None,
     "role": "Leest de datalaag via de MES-API"},
    {"id": "cmms", "kind": "consumer", "name": "Onderhoud (CMMS)",
     "env": None, "default": None,
     "role": "Werkorders uit storingen en CBM-alerts; status terug als context"},
    {"id": "agent", "kind": "consumer", "name": "Agent (read-only MCP)",
     "env": None, "default": None,
     "role": "Een AI-agent leest mee, schrijft nooit"},
]

_probe_cache: dict[str, tuple[float, bool]] = {}
_probe_lock = threading.Lock()

# Gesimuleerde sensoruitval: equipment_id -> monotonic deadline.
_dropouts: dict[str, float] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stale_threshold_s(model: Optional[dict] = None) -> float:
    model = model if model is not None else K.load_factory_model()
    try:
        return float(model.get("stale_threshold_s") or STALE_DEFAULT_S)
    except (TypeError, ValueError):
        return STALE_DEFAULT_S


def _probe(target: Optional[str]) -> object:
    """True/False voor host:poort, None als er niets te proberen valt,
    "absent" als de hostnaam niet bestaat (dienst niet uitgerold)."""
    if not target or ":" not in target:
        return None
    now = time.monotonic()
    with _probe_lock:
        hit = _probe_cache.get(target)
        if hit and now - hit[0] < PROBE_TTL_S:
            return hit[1]
    host, port = target.rsplit(":", 1)
    ok: object = False
    try:
        with socket.create_connection((host, int(port)), timeout=0.6):
            ok = True
    except socket.gaierror:
        ok = "absent"
    except (OSError, ValueError):
        ok = False
    with _probe_lock:
        _probe_cache[target] = (now, ok)
    return ok


# ------------------------------------------------------------------ dropout

def set_dropout(equipment_id: str, seconds: float = 120.0) -> dict:
    if equipment_id not in M.ALL_EQUIPMENT_IDS:
        raise ValueError(f"unknown equipment: {equipment_id}")
    seconds = max(5.0, min(float(seconds), 1800.0))
    _dropouts[equipment_id] = time.monotonic() + seconds
    return {"equipment_id": equipment_id, "seconds": seconds, "simulated": True}


def clear_dropout(equipment_id: Optional[str] = None) -> dict:
    if equipment_id is None:
        n = len(_dropouts)
        _dropouts.clear()
        return {"cleared": n}
    return {"cleared": 1 if _dropouts.pop(equipment_id, None) else 0}


def active_dropouts() -> dict[str, float]:
    now = time.monotonic()
    for eq, until in list(_dropouts.items()):
        if until <= now:
            _dropouts.pop(eq, None)
    return {eq: round(until - now, 1) for eq, until in _dropouts.items()}


# ------------------------------------------------------------------ topics

def _verbose(buses: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for b in buses.values():
        if b is None:
            continue
        try:
            out.update(b.snapshot_verbose())
        except Exception:  # pragma: no cover - een kapotte bus mag de rest niet slopen
            continue
    return out


def _prefix(equipment_id: str) -> str:
    ln = M.get_line(M.line_of_equipment(equipment_id))
    return f"{ln.uns_root}/{M.area_of(equipment_id)}/{equipment_id}/Status/"


# Tags die alleen bij een wijziging publiceren: de retained waarde geldt tot de
# volgende wijziging en is dus niet "verouderd".
ON_CHANGE_TAGS = {"maintenance_state", "work_order"}


def topic_state(row: dict, threshold: float, tag: Optional[str] = None) -> str:
    """GOOD, STALE of BAD voor één topic. BAD wint: een slechte waarde die
    vers binnenkomt is erger dan een goede die oud is."""
    q = str(row.get("quality") or "UNKNOWN").upper()
    if q.startswith("BAD"):
        return "BAD"
    if tag in ON_CHANGE_TAGS and (row.get("retained") or row.get("age_s") is None):
        return "GOOD"
    if row.get("retained") or row.get("age_s") is None:
        return "STALE"
    if row["age_s"] > threshold:
        return "STALE"
    return "GOOD"


def _opcua_node(equipment_id: str, tag: str) -> str:
    # Beide fabrieken draaien hetzelfde image en dus dezelfde NodeIds; het
    # verschil tussen de lijnen zit in de endpoint, niet in de naam.
    return f"ns=2;s=DairyWorks.Vla.{M.area_of(equipment_id)}.{equipment_id}.{tag}"


def _engine_tags() -> set[str]:
    """Tags die de MES-laag zelf publiceert (geen OPC-UA-bron)."""
    return {"state", "running_hours", "work_order", "maintenance_state"}


# ------------------------------------------------------------------ map

def layer_map(buses: dict, db=None, extra: Optional[dict] = None) -> dict:
    """Bronnen → datalaag → opslag/afnemers, met live status."""
    threshold = stale_threshold_s()
    verbose = _verbose(buses)
    sources = []
    for lid, ln in M.LINES.items():
        # Signaalkwaliteit gaat over machinesignalen, met dezelfde prefixen als
        # quality(); retained order- en regelinformatie ververst niet live en
        # zou anders als "verouderd" meetellen.
        prefixes = tuple(_prefix(e) for e in ln.equipment_ids)
        rows = {t: r for t, r in verbose.items() if t.startswith(prefixes)}
        ages = [r["age_s"] for r in rows.values() if r.get("age_s") is not None]
        states = [topic_state(r, threshold, t.rsplit("/", 1)[-1]) for t, r in rows.items()]
        bus = buses.get(lid)
        sources.append({
            "id": f"opcua-{lid.lower()}",
            "kind": "source",
            "name": f"PLC {ln.hall} (OPC-UA)",
            "protocol": "OPC-UA",
            "endpoint_label": ln.opcua_default.split("//", 1)[-1].split(":", 1)[0],
            "line": lid,
            "root": ln.uns_root,
            "equipment": list(ln.equipment_ids),
            "topics": len(rows),
            "last_age_s": min(ages) if ages else None,
            "good": states.count("GOOD"),
            "stale": states.count("STALE"),
            "bad": states.count("BAD"),
            "connected": bool(getattr(bus, "connected", False)),
            "status": _status(bool(getattr(bus, "connected", False)), rows, ages,
                              threshold),
        })
    sources.append({
        "id": "mes", "kind": "source", "name": "MES (batches, orders, kwaliteit)",
        "protocol": "API", "line": None, "root": None, "equipment": [],
        "topics": None, "last_age_s": None, "good": None, "stale": None,
        "bad": None, "connected": True, "status": "ok",
    })
    sources.append({
        "id": "warehouse", "kind": "source",
        "name": "Magazijn en logistiek (heftrucks, trucks)",
        "protocol": "API", "line": None, "root": None, "equipment": [],
        "topics": None, "last_age_s": None, "good": None, "stale": None,
        "bad": None, "connected": bool((extra or {}).get("logistics")),
        "status": "ok" if (extra or {}).get("logistics") else "off",
    })

    services = []
    for svc in _SERVICES:
        target = os.environ.get(svc["env"], svc["default"]) if svc["env"] else None
        up = _probe(target)
        if svc["id"] == "documents":
            up = db is not None
            status = "ok" if up else "down"
            detail = getattr(db, "backend", None)
        elif svc["id"] == "twin":
            status, detail = "ok", None
        elif svc["id"] in ("cmms", "agent"):
            on = bool((extra or {}).get(svc["id"]))
            status, detail = ("ok" if on else "off"), None
        else:
            if up == "absent":
                status, detail = "off", "niet uitgerold"
            else:
                status = "unknown" if up is None else ("ok" if up else "down")
                detail = None
        services.append({"id": svc["id"], "kind": svc["kind"], "name": svc["name"],
                         "role": svc["role"], "status": status, "detail": detail})

    signals = sum(s["topics"] or 0 for s in sources)
    good = sum(s["good"] or 0 for s in sources)
    stale = sum(s["stale"] or 0 for s in sources)
    bad = sum(s["bad"] or 0 for s in sources)
    links = [{"from": s["id"], "to": "datalayer"} for s in sources] + \
            [{"from": "datalayer", "to": s["id"]} for s in services]
    return {
        "generated_at": _now_iso(),
        "stale_threshold_s": threshold,
        "datalayer": {
            "id": "datalayer",
            "name": "De datalaag",
            "convention": "{Site}/{Line}/{Area}/{Equipment}/Status/{tag}",
            "topics": len(verbose),
            "signals": signals,
            "good": good, "stale": stale, "bad": bad,
            "roots": [ln.uns_root for ln in M.LINES.values()],
        },
        "sources": sources,
        "services": services,
        "links": links,
    }


def _status(connected: bool, rows: dict, ages: list, threshold: float) -> str:
    if not connected and not rows:
        return "down"
    if not ages:
        return "stale"
    return "ok" if min(ages) <= threshold else "stale"


# ------------------------------------------------------------------ context

def isa95_path(equipment_id: str, model: Optional[dict] = None) -> list[dict]:
    model = model if model is not None else K.load_factory_model()
    ent = (model or {}).get("enterprise") or {}
    site = (ent.get("sites") or [{}])[0]
    ln = M.get_line(M.line_of_equipment(equipment_id))
    return [
        {"level": "Enterprise", "id": ent.get("id", "ENT-DW"),
         "name": ent.get("name", "DairyWorks")},
        {"level": "Site", "id": site.get("id", "SITE-DW"),
         "name": site.get("name", "DairyWorks Plant")},
        {"level": "Line", "id": ln.line_id, "name": ln.name},
        {"level": "Area", "id": M.area_of(equipment_id),
         "name": M.area_of(equipment_id)},
        {"level": "Equipment", "id": equipment_id, "name": equipment_id},
    ]


def context(equipment_id: str, buses: dict, db=None,
            equipment=None, cmms=None) -> dict:
    if equipment_id not in M.ALL_EQUIPMENT_IDS:
        raise ValueError(f"unknown equipment: {equipment_id}")
    threshold = stale_threshold_s()
    prefix = _prefix(equipment_id)
    verbose = _verbose(buses)
    dropped = equipment_id in active_dropouts()
    engine = _engine_tags()
    ln = M.get_line(M.line_of_equipment(equipment_id))
    topics = []
    for t in sorted(verbose):
        if not t.startswith(prefix):
            continue
        tag = t[len(prefix):]
        row = dict(verbose[t])
        state = topic_state(row, threshold, tag)
        from_engine = tag in engine
        if dropped and not from_engine:
            state, row["quality"], row["value"] = "BAD", "BAD_SENSOR_FAILURE", None
        topics.append({
            "tag": tag,
            "topic": t,
            "value": row.get("value"),
            "unit": row.get("unit"),
            "quality": row.get("quality"),
            "age_s": row.get("age_s"),
            "retained": row.get("retained"),
            "state": state,
            "source": "MES" if from_engine else "OPC-UA",
            "raw": None if from_engine else {
                "endpoint": ln.opcua_default,
                "node_id": _opcua_node(equipment_id, tag),
            },
        })
    snap = None
    if equipment is not None:
        snap = next((r for r in equipment.snapshot(ln.line_id)
                     if r["equipment_id"] == equipment_id), None)
    wos = cmms.list(equipment_id=equipment_id, open_only=True) if cmms else []
    alerts = equipment.open_alerts(equipment_id) if equipment is not None else []
    counts = {s: sum(1 for t in topics if t["state"] == s)
              for s in ("GOOD", "STALE", "BAD")}
    return {
        "equipment_id": equipment_id,
        "line": ln.line_id,
        "isa95_path": isa95_path(equipment_id),
        "topic_prefix": prefix.rstrip("/"),
        "opcua_endpoint": ln.opcua_default,
        "topics": topics,
        "counts": counts,
        "quality": _worst(counts),
        "dropout_simulated": dropped,
        "state": (snap or {}).get("state"),
        "down": (equipment.down_info(equipment_id) if equipment is not None
                 and hasattr(equipment, "down_info") else None),
        "open_cbm_alerts": [{"alert_id": a.get("alert_id"), "ts": a.get("ts"),
                             "message": a.get("message")} for a in alerts],
        "open_work_orders": wos,
        "stale_threshold_s": threshold,
        "generated_at": _now_iso(),
    }


def _worst(counts: dict) -> str:
    if counts.get("BAD"):
        return "BAD"
    if counts.get("STALE") and not counts.get("GOOD"):
        return "STALE"
    if counts.get("STALE"):
        return "PARTIAL"
    if counts.get("GOOD"):
        return "GOOD"
    return "NO_DATA"


# ------------------------------------------------------------------ quality

def quality(buses: dict) -> dict:
    threshold = stale_threshold_s()
    verbose = _verbose(buses)
    dropped = active_dropouts()
    engine = _engine_tags()
    out = []
    for eq in M.ALL_EQUIPMENT_IDS:
        prefix = _prefix(eq)
        counts = {"GOOD": 0, "STALE": 0, "BAD": 0}
        oldest = None
        for t, row in verbose.items():
            if not t.startswith(prefix):
                continue
            tag = t[len(prefix):]
            state = topic_state(row, threshold, tag)
            if eq in dropped and tag not in engine:
                state = "BAD"
            counts[state] += 1
            if row.get("age_s") is not None:
                oldest = row["age_s"] if oldest is None else max(oldest, row["age_s"])
        out.append({"equipment_id": eq, "line": M.line_of_equipment(eq),
                    "counts": counts, "quality": _worst(counts),
                    "oldest_age_s": oldest,
                    "dropout_simulated": eq in dropped,
                    "dropout_remaining_s": dropped.get(eq)})
    return {"generated_at": _now_iso(), "stale_threshold_s": threshold,
            "equipment": out}


def recent_window(hours: float) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    return end - timedelta(hours=float(hours)), end


__all__ = ["layer_map", "context", "quality", "set_dropout", "clear_dropout",
           "active_dropouts", "isa95_path", "topic_state", "stale_threshold_s",
           "recent_window"]
