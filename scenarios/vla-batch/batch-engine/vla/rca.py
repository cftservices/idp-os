"""Van alarm naar oorzaak: correleren via de datalaag.

Een alarm zegt wát er gebeurde; de oorzaak staat in andere signalen rond
hetzelfde tijdstip. Omdat alles in één datalaag met dezelfde equipment-id's
zit, is dat een opzoeking en geen speurtocht: toestandshistorie, opwarmtrend,
batchgebeurtenissen, werkorders en het eindoordeel.

De keten is deterministisch, afgeleid uit de data. De AI-tekst is optioneel
(`ai=True`) en vat alleen samen wat de keten al aantoont; hij voegt geen
feiten toe en valt bij een time-out stil terug.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

from . import model as M

log = logging.getLogger("vla.rca")

WINDOW_MIN = 10


def _parse(ts) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except (TypeError, ValueError):
        return None


def _near(ts, t: datetime, minutes: float) -> bool:
    d = _parse(ts)
    return d is not None and abs((d - t).total_seconds()) <= minutes * 60


def find_alarm(db, alarm_id: str) -> Optional[dict]:
    a = db.dw_alarms.find_one({"alarm_id": alarm_id})
    if a:
        return dict(a, source="alarm")
    c = db.dw_cbm_alerts.find_one({"alert_id": alarm_id})
    if c:
        return {"alarm_id": c["alert_id"], "equipment_id": c["equipment_id"],
                "alarm_type": c.get("alert_type"), "severity": "WARNING",
                "message": c.get("message"), "ts": c.get("ts"),
                "batch_id": None, "line": M.line_of_equipment(c["equipment_id"]),
                "source": "cbm"}
    return None


def cause(db, alarm_id: str, cmms=None, cost_model: Optional[dict] = None,
          ai: bool = False, ollama_url: Optional[str] = None,
          ai_model: str = "qwen2.5:3b", ai_timeout_s: float = 20.0) -> Optional[dict]:
    alarm = find_alarm(db, alarm_id)
    if alarm is None:
        return None
    eq = alarm.get("equipment_id")
    t = _parse(alarm.get("ts"))
    if t is None:
        return None
    ln = M.get_line(M.line_of_equipment(eq))
    cook = ln.eq("cook")

    states = sorted([s for s in db.dw_equipment_state.find({"equipment_id": eq})
                     if _near(s.get("ts"), t, WINDOW_MIN)],
                    key=lambda r: r.get("ts") or "")
    meta = db.dw_equipment_meta.find_one({"equipment_id": cook}) or {}
    heat = list(meta.get("heatup_history") or [])[-8:]
    base = None
    try:
        from .equipment import BASE_HEATUP_SEC, CBM_ALERT_FACTOR
        base = (BASE_HEATUP_SEC, BASE_HEATUP_SEC * CBM_ALERT_FACTOR)
    except ImportError:  # pragma: no cover
        pass

    batch = db.dw_batches.find_one({"batch_id": alarm.get("batch_id")}) \
        if alarm.get("batch_id") else None
    events = sorted([e for e in db.dw_batch_events.find(
        {"batch_id": alarm["batch_id"]})] if alarm.get("batch_id") else [],
        key=lambda e: e.get("ts") or "")[-12:]
    wos = []
    if cmms is not None:
        wos = [w for w in cmms.list(equipment_id=eq)
               if _near(w.get("reported_at"), t, 120)][:5]
    cbm_open = [a for a in db.dw_cbm_alerts.find({"equipment_id": cook})
                if not a.get("resolved")]

    chain: list[dict] = []
    rising = len(heat) >= 3 and heat[-1]["heatup_sec"] > heat[0]["heatup_sec"]
    if rising:
        chain.append({"step": "fouling", "label": "Vervuiling kookunit",
                      "evidence": f"opwarmtijd {heat[0]['heatup_sec']}s -> "
                                  f"{heat[-1]['heatup_sec']}s over {len(heat)} batches",
                      "equipment_id": cook})
    if base and heat and heat[-1]["heatup_sec"] >= base[1]:
        chain.append({"step": "heatup", "label": "Opwarmtijd boven CBM-grens",
                      "evidence": f"{heat[-1]['heatup_sec']}s >= {round(base[1])}s",
                      "equipment_id": cook})
    down = [s for s in states if s.get("state") in M.DOWNTIME_STATES]
    if down:
        chain.append({"step": "downtime", "label": "Stilstand",
                      "evidence": f"{len(down)}x Down rond het alarm"
                                  + (f": {down[0].get('reason')}" if down[0].get("reason") else ""),
                      "equipment_id": eq})
    if batch and batch.get("verdict") in (M.HOLD, M.REJECTED):
        chain.append({"step": "verdict", "label": f"Batch {batch.get('verdict')}",
                      "evidence": f"{batch['batch_id']}: viscositeit "
                                  f"{batch.get('end_viscosity_cP', '?')} cP",
                      "equipment_id": eq})
        if cost_model and cost_model.get("rework_cost_per_batch") is not None:
            chain.append({"step": "euro", "label": "Kosten herwerk",
                          "evidence": f"{cost_model.get('currency', 'EUR')} "
                                      f"{cost_model['rework_cost_per_batch']:.0f} per batch",
                          "equipment_id": cook})
    if cbm_open or rising:
        chain.append({"step": "action", "label": "Actie: CIP plannen",
                      "evidence": "open CBM-alert" if cbm_open else "trend stijgt",
                      "equipment_id": cook})
    for w in wos:
        chain.append({"step": "work_order", "label": f"Werkorder {w['wo_id']}",
                      "evidence": f"{w['kind']}, {w['status']}: {w.get('cause')}",
                      "equipment_id": w["equipment_id"]})

    if not chain:
        verdict = "Geen samenhangend signaal gevonden rond dit alarm."
    else:
        verdict = " -> ".join(c["label"] for c in chain)

    out = {
        "alarm": {k: alarm.get(k) for k in ("alarm_id", "equipment_id", "line",
                                             "alarm_type", "severity", "message",
                                             "ts", "batch_id", "source")},
        "window_min": WINDOW_MIN,
        "signals": {
            "state_history": [{"state": s.get("state"), "ts": s.get("ts"),
                               "reason": s.get("reason")} for s in states][-20:],
            "heatup_trend": heat,
            "heatup_base_s": base[0] if base else None,
            "heatup_alert_s": round(base[1], 1) if base else None,
            "batch_events": [{"event_type": e.get("event_type"), "ts": e.get("ts")}
                             for e in events],
            "work_orders": [{"wo_id": w["wo_id"], "kind": w["kind"],
                             "status": w["status"], "cause": w.get("cause")}
                            for w in wos],
            "batch": ({"batch_id": batch.get("batch_id"),
                       "verdict": batch.get("verdict"),
                       "recipe_id": batch.get("recipe_id")} if batch else None),
        },
        "chain": chain,
        "verdict": verdict,
        "focus_equipment": chain[0]["equipment_id"] if chain else eq,
        "ai": None,
    }
    if ai and ollama_url and chain:
        out["ai"] = _ai_summary(out, ollama_url, ai_model, ai_timeout_s)
    return out


def _ai_summary(rca: dict, url: str, model: str, timeout: float) -> Optional[dict]:
    prompt = ("Vat in maximaal drie Nederlandse zinnen samen waarom dit alarm "
              "optrad. Gebruik alleen deze feiten, voeg niets toe.\n"
              + json.dumps({"alarm": rca["alarm"], "keten": rca["chain"]},
                           ensure_ascii=False))
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = json.loads(r.read().decode()).get("response", "").strip()
        return {"model": model, "text": text} if text else None
    except Exception as e:
        log.info("AI-samenvatting overgeslagen: %s", e)
        return {"model": model, "text": None, "error": "niet beschikbaar"}
