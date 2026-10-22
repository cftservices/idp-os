"""De L1-payload: alles wat het operatoroverzicht toont, kant-en-klaar.

Bestaansreden: de oude client leidde een reeks dingen zelf af, en die hoorden
daar niet. Het scherpste voorbeeld is de lijnsnelheid, die werd berekend uit
twee opeenvolgende metingen van de packs-teller met state die tussen polls
bleef staan. Precies daarom ging hij fout bij een re-render.

Wat hier vandaan komt in plaats van uit de browser:
  * packs_rate_per_min      was: (packs2 - packs1) / dt, met state over polls
  * packs_target / progress  was: planned_L / pack_size_L in de client
  * dose_totals              was: de "Total charge"-kaart, client-side som
  * pallets                  was: packs / 1200, met de 1200 hardgecodeerd
  * phase_started_at         was: een lokaal bijgehouden fasetimer

De fase-timer is de enige met een nuance. Een tikkende seconde tussen twee
polls is presentatie en mag de UI zelf doen, maar dan vanaf `phase_started_at`
als server-anker; niet vanaf "de state veranderde toen ik het zag".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import model as M

# Rollend venster waarover de vulsnelheid wordt gemeten. Kort genoeg om
# responsief te zijn, lang genoeg om niet op één poll te wiebelen.
RATE_WINDOW_S = 120


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _active_batch(db, line: Optional[str] = None) -> Optional[dict]:
    """De lopende batch, en anders de meest recente.

    Dit is de regressie die eerder één keer is gefixt: targets alleen laden bij
    een actieve batch lijkt logisch en is fout, want de lijn staat het grootste
    deel van de tijd op COMPLETE. Dan toonden de doseerkaarten "target -".
    """
    batches = [b for b in db.dw_batches.find({})
               if line is None or M.line_of_doc(b) == line]
    if not batches:
        return None
    active = [b for b in batches if b.get("state") not in (None, M.COMPLETE, "IDLE")]
    if active:
        return sorted(active, key=lambda b: b.get("started_at") or "", reverse=True)[0]
    return sorted(batches, key=lambda b: b.get("started_at") or "", reverse=True)[0]


def live(db, model: Optional[dict] = None, line: str = M.DEFAULT_LINE) -> dict:
    """De volledige payload van één lijn (standaard L1)."""
    model = model or {}
    recipe = (model.get("recipes") or [{}])[0]
    pack_size_L = _num(recipe.get("pack_size_L")) or 1.0
    packs_per_pallet = model.get("packs_per_pallet")
    fill_limits = model.get("fill_limits_ml")
    phase_nominal = model.get("phase_nominal_sec") or {}

    batch = _active_batch(db, line)
    if batch is None:
        return {
            "available": False,
            "line": line,
            "reason": "nog geen batch gedraaid",
            "generated_at": _iso(),
        }

    batch_id = batch.get("batch_id")
    planned_L = _num(batch.get("planned_L"))
    packs_total = _num(batch.get("packs_total"))

    # Doseringen: targets uit de MES, actuals ook. De setpoint-nodes van de
    # factory bereiken de UNS niet, dus /tags is hier niet de bron.
    doses = [d for d in db.dw_doses.find({}) if d.get("batch_id") == batch_id]
    dose_rows = []
    tgt_sum = act_sum = 0.0
    for d in doses:
        target = _num(d.get("qty_target"))
        actual = None if d.get("qty_actual") is None else _num(d.get("qty_actual"))
        tgt_sum += target
        act_sum += actual or 0.0
        dose_rows.append({
            "material_id": d.get("material_id"),
            "qty_target": target,
            "qty_actual": actual,
            "uom": d.get("uom"),
            "tol_min": d.get("tol_min"),
            "tol_max": d.get("tol_max"),
            "in_tolerance": d.get("in_tolerance"),
        })

    # Vulsnelheid over een servervenster, uit de productieboekingen.
    now = datetime.now(timezone.utc)
    recent = []
    line_batches = {b.get("batch_id") for b in db.dw_batches.find({})
                    if M.line_of_doc(b) == line}
    for p in db.dw_production.find({}):
        if p.get("batch_id") not in line_batches:
            continue
        ts = _parse(p.get("ts"))
        if ts and (now - ts).total_seconds() <= RATE_WINDOW_S:
            recent.append((ts, _num(p.get("packs"))))
    if len(recent) >= 1:
        span = max(1.0, (now - min(t for t, _ in recent)).total_seconds())
        rate = sum(p for _, p in recent) / span * 60.0
    else:
        rate = 0.0

    packs_target = planned_L / pack_size_L if pack_size_L else None

    return {
        "available": True,
        "line": line,
        "batch": {
            "batch_id": batch_id,
            "state": batch.get("state"),
            "verdict": batch.get("verdict"),
            "recipe_id": batch.get("recipe_id"),
            "started_at": batch.get("started_at"),
            "completed_at": batch.get("completed_at"),
            # Server-anker voor de fasetimer. De UI mag hiervandaan tikken,
            # niet vanaf het moment dat hij de wijziging zag.
            "phase_started_at": batch.get("phase_started_at") or batch.get("started_at"),
            "phase_nominal_sec": phase_nominal.get(str(batch.get("state"))),
        },
        "doses": dose_rows,
        "dose_totals": {
            "target_kg": round(tgt_sum, 1),
            "actual_kg": round(act_sum, 1),
            "pct": round(act_sum / tgt_sum * 100, 1) if tgt_sum else None,
        },
        "filling": {
            "packs_total": packs_total,
            "packs_target": None if packs_target is None else round(packs_target),
            "packs_progress_pct": (
                round(packs_total / packs_target * 100, 1) if packs_target else None
            ),
            "packs_rate_per_min": round(rate, 1),
            "rate_window_s": RATE_WINDOW_S,
            # Zonder packs_per_pallet in het model geen schatting, in plaats van
            # een magische 1200 in de client.
            "pallets": (
                None if not packs_per_pallet else int(packs_total // float(packs_per_pallet))
            ),
            "fill_limits_ml": fill_limits,
        },
        "quality": {
            "end_viscosity_cP": batch.get("end_viscosity_cP"),
            "peak_cook_temp_C": batch.get("peak_cook_temp_C"),
            "hold_elapsed_sec": batch.get("hold_elapsed_sec"),
            "spec_cP": recipe.get("viscosity_spec_cP"),
        },
        "generated_at": _iso(),
    }
