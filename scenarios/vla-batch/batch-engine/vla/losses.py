"""Verlies in euro's per machine.

Dezelfde kostenparameters en dezelfde regel als `kpi.compute_losses`: een
ontbrekende parameter laat de post weg en verantwoordt dat in `omitted`, hij
wordt nooit 0,00. De toewijzing aan een machine:

    stilstand     -> de machine die Down stond (eigen toestandshistorie)
    overdosering  -> de procestank van de lijn van de batch (daar wordt gedoseerd)
    afkeur        -> de vuller van de lijn van de batch
    herwerk/HOLD  -> de kookunit (resulterend: telt niet mee in het kopbedrag)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from . import model as M
from .cmms import reliability
from .kpi import _completed_batches, _num, _production_rows


def losses_per_equipment(db, equipment, start: datetime, end: datetime,
                         cost_model: Optional[dict],
                         line: Optional[str] = None) -> dict:
    ids = M.LINES[line].equipment_ids if line in M.LINES else M.ALL_EQUIPMENT_IDS
    if not cost_model:
        return {"equipment": [], "currency": None, "total_causal": None,
                "omitted": [{"category": "*",
                             "reason": "geen kostenmodel in factory-model.json"}]}
    currency = cost_model.get("currency", "EUR")
    rows = {eq: {"equipment_id": eq, "line": M.line_of_equipment(eq),
                 "role": next((r for r, e in M.get_line(M.line_of_equipment(eq))
                               .equipment.items() if e == eq), None),
                 "items": []} for eq in ids}
    omitted: list[dict] = []

    batches = _completed_batches(db, start, end)
    line_of_batch = {b.get("batch_id"): M.line_of_doc(b) for b in batches}

    def add(eq: str, category: str, label: str, amount: float, kind: str,
            cause: Optional[str] = None) -> None:
        if eq in rows and round(amount, 2) > 0:
            rows[eq]["items"].append({"category": category, "label": label,
                                      "amount": round(amount, 2), "kind": kind,
                                      "cause": cause})

    # Stilstand
    rate = cost_model.get("cost_per_downtime_hour")
    if rate is not None:
        hours = max(0.0, (end - start).total_seconds() / 3600.0)
        rel = reliability(db, equipment, line=line, hours=hours, now=end)
        for r in rel["equipment"]:
            if r["adet_h"] > 0:
                add(r["equipment_id"], "downtime", "Stilstand",
                    r["adet_h"] * float(rate), "causal",
                    f"{round(r['adet_h'] * 60)} min, {r['failures']} storing(en)")
    else:
        omitted.append({"category": "downtime",
                        "reason": "cost_per_downtime_hour ontbreekt"})

    # Overdosering -> procestank
    per_kg = cost_model.get("cost_per_kg_material") or {}
    if per_kg:
        acc: dict[str, float] = {}
        for d in db.dw_doses.find({}):
            ln = line_of_batch.get(d.get("batch_id"))
            if ln is None:
                continue
            a, t = d.get("qty_actual"), d.get("qty_target")
            r = per_kg.get(d.get("material_id"))
            if a is None or t is None or r is None:
                continue
            over = max(0.0, _num(a) - _num(t))
            eq = M.LINES[ln].eq("process")
            acc[eq] = acc.get(eq, 0.0) + over * float(r)
        for eq, amount in acc.items():
            add(eq, "material_overdose", "Overdosering", amount, "causal")
    else:
        omitted.append({"category": "material_overdose",
                        "reason": "cost_per_kg_material ontbreekt"})

    # Afkeur -> vuller
    vpp = cost_model.get("value_per_pack")
    if vpp is not None:
        acc = {}
        for p in _production_rows(db, start, end):
            ln = line_of_batch.get(p.get("batch_id")) or M.line_of_doc(p)
            eq = M.LINES[ln].eq("filler")
            acc[eq] = acc.get(eq, 0.0) + _num(p.get("reject_count"))
        for eq, n in acc.items():
            add(eq, "scrap", "Afkeur", n * float(vpp), "causal", f"{int(n)} pakken")
    else:
        omitted.append({"category": "scrap", "reason": "value_per_pack ontbreekt"})

    # Herwerk -> kookunit (resulterend)
    rw = cost_model.get("rework_cost_per_batch")
    if rw is not None:
        acc = {}
        for b in batches:
            if b.get("verdict") in (M.HOLD, M.REJECTED):
                eq = M.LINES[line_of_batch[b.get("batch_id")]].eq("cook")
                acc[eq] = acc.get(eq, 0) + 1
        for eq, n in acc.items():
            add(eq, "rework", "Herwerk en HOLD", n * float(rw), "resultant",
                f"{n} batches")
    else:
        omitted.append({"category": "rework",
                        "reason": "rework_cost_per_batch ontbreekt"})

    out = []
    for r in rows.values():
        r["total_causal"] = round(sum(i["amount"] for i in r["items"]
                                      if i["kind"] == "causal"), 2)
        r["total_resultant"] = round(sum(i["amount"] for i in r["items"]
                                         if i["kind"] == "resultant"), 2)
        out.append(r)
    out.sort(key=lambda r: -r["total_causal"])
    return {"equipment": out, "currency": currency,
            "total_causal": round(sum(r["total_causal"] for r in out), 2),
            "omitted": omitted}
