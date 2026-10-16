"""KPI-laag (fase 4) — norm, delta, status en verliesvertaling op één plek.

Dit is de enige plek waar een KPI wordt gedefinieerd. Scherm en PDF lezen
allebei hieruit, zodat ze nooit uiteen kunnen lopen. Een afwijkende formule in
de UI is per contract een bug, geen variant.

Contract: GET /api/v1/kpi/summary?window=shift|day|week|month&compare=true en
`losses[]` in GET /report/period.

Ontwerpbesluiten die uit de onderzoeksronde van 2026-07-30 komen
(10-Research/2026-07-30-dashboarding-operator-management.md):

* **Drietakkige statusregel.** OK vanaf target, WARNING vanaf warn, CRITICAL
  vanaf critical. De oude tweetakkige regel liet het critical-veld ongebruikt,
  waardoor 89 % en 40 % identiek lazen.
* **UNSET is nooit stilzwijgend OK.** Elke KPI die niet berekenbaar is levert
  status UNSET plus een `reason`, zodat het scherm een streepje met uitleg kan
  tonen in plaats van een verzonnen nul.
* **`yield_pct` is gesplitst in twee KPI's die echt iets anders meten.** De oude
  formule (packs / planned_L) deelde stuks door liters en gaf in de praktijk
  117 %. Dat is plan-realisatie en heet hier `plan_attainment_pct`. Yield is wat
  een zuivelfabriek eronder verstaat: `mass_yield_pct`, kg product uit gedeeld
  door kg grondstof in. Het verschil met 100 % is echt verlies (indamping,
  fase-overgangen, leidingrestanten).
* **Quality ratio is QR = GQ/PQ (ISO 22400-2), rework niet meegerekend**, niet
  de gunstiger quality buy rate QBR die rework als goed telt.
* **Verliezen zijn causaal of resulterend.** Alleen causale verliezen tellen in
  het kopbedrag (Manufacturing Cost Deployment, Yamashina en Kubo 2002). Een
  ontbrekende kostenparameter laat de categorie weg, nooit 0.0.

Bekende beperkingen van de huidige data, vastgesteld in
docs/2026-07-30-grafana-kpi-bevindingen.md:

* ~~`reject_count` wordt nooit opgehoogd~~ **opgelost 30-07**: de fabriek kent nu
  een deterministisch afkeurmodel (`factory/physics.py::_reject_rate`), een
  basispercentage plus een oplopende straf naarmate de viscositeit onder spec
  zakt. `scrap_ratio` en de afkeurkosten werken daarmee echt, en de Solve krijgt
  een tweede meetbaar gevolg.
* Orders hebben een optionele `due_date`. Zonder due-date is OTIF niet
  berekenbaar en levert deze module UNSET, nooit 100 %.
* Vensters worden in UTC afgekapt. Een lokale dienstgrens is een openstaand
  punt (zie SHIFT_HOURS).
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import model as M
from .alarms import alarm_load
from .equipment import EQUIPMENT_IDS, EquipmentMonitor

log = logging.getLogger("vla.kpi")

# Dienstlengte. Openstaand punt: de echte dienstgrens is lokale tijd en
# waarschijnlijk niet op middernacht. Tot dat besloten is, is een dienst een
# rollend venster van 8 uur in UTC.
SHIFT_HOURS = 8

WINDOWS = ("shift", "day", "week", "month")

# Minimaal aantal batches met een eind-viscositeit voordat Cpk iets betekent.
CAPABILITY_MIN_SAMPLES = 8

# De indeling van equipment-states staat in model.py, niet hier. Zie de noot
# daar: toen deze verzamelingen op twee plekken stonden, landde de
# Dirty-herclassificatie alleen in dit bestand en spraken /oee en /kpi/summary
# elkaar op het scherm tegen. De aliassen blijven bestaan zodat bestaande
# imports blijven werken.
_DOWNTIME_STATES = M.DOWNTIME_STATES
_PRODUCTIVE_STATES = M.PRODUCTIVE_STATES
_NEUTRAL_STATES = M.NEUTRAL_STATES


# --------------------------------------------------------------------- helpers

def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(ts) -> Optional[datetime]:
    """ISO string to an aware datetime. A value without offset (a bare date
    from an older client, say) counts as UTC: comparing it with the aware
    window bounds would otherwise raise and take the whole summary down."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _within(ts, start: datetime, end: datetime) -> bool:
    """Halfopen interval [start, end). Randpunten expliciet: de eindgrens hoort
    bij het volgende venster, anders telt een meting in twee vensters mee."""
    parsed = _parse(ts)
    return parsed is not None and start <= parsed < end


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ config


def _model_path() -> Optional[str]:
    path = os.environ.get("FACTORY_MODEL")
    return path if path and os.path.exists(path) else None


def load_factory_model(path: Optional[str] = None) -> dict:
    """Lees isa95-vla.json. Ontbreekt het bestand of is het onleesbaar, dan is
    het resultaat een leeg dict: gedefinieerd gedrag, geen crash. De engine
    draait bewust ook zonder dit bestand (zie vla/model.py)."""
    path = path or _model_path()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("factory model unreadable (%s) — continuing without it", e)
        return {}


def load_kpi_targets(model: Optional[dict] = None) -> dict[str, dict]:
    """`kpi_targets` uit het model, geïndexeerd op kpi_id. Ontbreekt het blok,
    dan krijgt elke KPI status UNSET met reden 'geen norm ingesteld'."""
    model = load_factory_model() if model is None else model
    out: dict[str, dict] = {}
    for row in model.get("kpi_targets") or []:
        kpi_id = row.get("kpi_id")
        if kpi_id:
            out[kpi_id] = row
    return out


def load_cost_model(model: Optional[dict] = None) -> Optional[dict]:
    """`cost_model` uit het model, of None. Zonder kostenmodel levert
    compute_losses een lege lijst plus een `omitted`-verantwoording, nooit
    bedragen van 0.0."""
    model = load_factory_model() if model is None else model
    cost = model.get("cost_model")
    return cost if isinstance(cost, dict) else None


def viscosity_spec(model: Optional[dict] = None) -> Optional[tuple[float, float]]:
    """(LSL, USL) van de eind-viscositeit uit het recept. Tweezijdig: te laag
    koken is het risico, maar een enkelzijdige grens maakt Cp en Cpk onmogelijk."""
    model = load_factory_model() if model is None else model
    for recipe in model.get("recipes") or []:
        spec = recipe.get("viscosity_spec_cP") or {}
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and hi is not None:
            return float(lo), float(hi)
    rule = model.get("verdict_rule") or {}
    lo, hi = rule.get("spec_min_cP"), rule.get("spec_max_cP")
    if lo is not None and hi is not None:
        return float(lo), float(hi)
    return None


# ------------------------------------------------------------------ vensters


def window_bounds(window: str, now: Optional[datetime] = None,
                  offset: int = 0) -> tuple[datetime, datetime]:
    """Grenzen van een venster in UTC, als halfopen interval [start, end).

    `offset` telt vensters terug: 0 is het huidige venster, 1 het vorige. Dat
    is wat `compare=true` nodig heeft, en het is de reden dat een enkele
    `days=`-parameter niet volstaat: een dienst is niet in dagen uit te drukken
    en week-op-week is geen 7 dagen vanaf nu.
    """
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}, expected one of {WINDOWS}")
    now = now or datetime.now(timezone.utc)

    if window == "shift":
        span = timedelta(hours=SHIFT_HOURS)
        end = now - span * offset
        return end - span, end

    if window == "day":
        anchor = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = anchor - timedelta(days=offset)
        return start, start + timedelta(days=1)

    if window == "week":
        anchor = now.replace(hour=0, minute=0, second=0, microsecond=0)
        anchor -= timedelta(days=anchor.weekday())
        start = anchor - timedelta(weeks=offset)
        return start, start + timedelta(weeks=1)

    # month: kalendermaand, niet 30 dagen
    anchor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year, month = anchor.year, anchor.month - offset
    while month <= 0:
        month += 12
        year -= 1
    start = anchor.replace(year=year, month=month)
    nxt_y, nxt_m = (year + 1, 1) if month == 12 else (year, month + 1)
    return start, start.replace(year=nxt_y, month=nxt_m)


# --------------------------------------------------------------- statusregel


def kpi_status(value: Optional[float], target: Optional[dict]) -> dict:
    """Drietakkige, richting-bewuste statusregel.

    Levert {"status", "beyond_critical", "reason"}. Ontbreekt de waarde of de
    norm, dan is de status UNSET met een reden. Nooit stilzwijgend OK.
    """
    if target is None:
        return {"status": "UNSET", "beyond_critical": False,
                "reason": "geen norm ingesteld"}
    if value is None:
        return {"status": "UNSET", "beyond_critical": False,
                "reason": "niet berekenbaar in dit venster"}

    direction = target.get("direction", "higher_is_better")
    t = target.get("target")
    w = target.get("warn")
    c = target.get("critical")
    if t is None:
        return {"status": "UNSET", "beyond_critical": False,
                "reason": "norm onvolledig"}

    if direction == "lower_is_better":
        if value <= t:
            return {"status": "OK", "beyond_critical": False, "reason": None}
        if w is not None and value <= w:
            return {"status": "WARNING", "beyond_critical": False, "reason": None}
        beyond = c is not None and value > c
        return {"status": "CRITICAL", "beyond_critical": beyond, "reason": None}

    if value >= t:
        return {"status": "OK", "beyond_critical": False, "reason": None}
    if w is not None and value >= w:
        return {"status": "WARNING", "beyond_critical": False, "reason": None}
    beyond = c is not None and value < c
    return {"status": "CRITICAL", "beyond_critical": beyond, "reason": None}


def _delta(value: Optional[float], previous: Optional[float],
           direction: str) -> dict:
    """delta is None als het vorige venster geen data heeft; delta_pct is None
    als de vorige waarde 0 is. `favourable` vertaalt de richting, zodat de UI
    geen kale pijl hoeft te interpreteren."""
    if value is None or previous is None:
        return {"previous_value": previous, "delta": None,
                "delta_pct": None, "favourable": None}
    delta = round(value - previous, 4)
    delta_pct = round(delta / abs(previous) * 100, 2) if previous != 0 else None
    if delta == 0:
        favourable = None
    elif direction == "lower_is_better":
        favourable = delta < 0
    else:
        favourable = delta > 0
    return {"previous_value": previous, "delta": delta,
            "delta_pct": delta_pct, "favourable": favourable}


# ------------------------------------------------------------------ metingen

def _completed_batches(db, start: datetime, end: datetime) -> list[dict]:
    return [b for b in db.dw_batches.find({})
            if _within(b.get("completed_at"), start, end)]


def _production_rows(db, start: datetime, end: datetime) -> list[dict]:
    return [p for p in db.dw_production.find({})
            if _within(p.get("ts"), start, end)]


def _throughput_rate(db, start, end) -> tuple[Optional[float], dict]:
    """Packs per uur over het venster. Bewust uit dw_production op `ts` en niet
    uit /inventory: die telt all-time en beweegt dus niet met het venster mee."""
    rows = _production_rows(db, start, end)
    if not rows:
        return None, {"reason": "geen productieboekingen in dit venster"}
    packs = sum(_num(r.get("packs")) for r in rows)
    hours = (end - start).total_seconds() / 3600.0
    if hours <= 0:
        return None, {"reason": "venster heeft geen lengte"}
    return round(packs / hours, 2), {"sample_n": len(rows), "packs": packs}


def _quality_ratio(db, start, end) -> tuple[Optional[float], dict]:
    """QR = GQ / PQ over batches die in het venster zijn afgerond. Rework telt
    NIET als goed: een batch die na herkoken alsnog APPROVED wordt, zit in de
    noemer maar niet in de teller zolang zijn verdict niet APPROVED is."""
    batches = _completed_batches(db, start, end)
    total = sum(_num(b.get("packs_total")) for b in batches)
    if total <= 0:
        return None, {"reason": "geen afgeronde batches in dit venster"}
    good = sum(_num(b.get("packs_total")) for b in batches
               if b.get("verdict") == M.APPROVED)
    return round(good / total * 100, 2), {"sample_n": len(batches),
                                          "good_qty": good, "produced_qty": total}


def _scrap_ratio(db, start, end) -> tuple[Optional[float], dict]:
    """SR = SQ / PQ uit dw_production. Let op: de fabrieksimulatie hoogt
    reject_count nooit op, dus dit leest 0.0 zolang dat zo is. Dat is een
    eigenschap van de simulatie, geen rekenfout."""
    rows = _production_rows(db, start, end)
    if not rows:
        return None, {"reason": "geen productieboekingen in dit venster"}
    packs = sum(_num(r.get("packs")) for r in rows)
    rejects = sum(_num(r.get("reject_count")) for r in rows)
    produced = packs + rejects
    if produced <= 0:
        return None, {"reason": "geen geproduceerde eenheden in dit venster"}
    return round(rejects / produced * 100, 2), {
        "sample_n": len(rows), "scrap_qty": rejects, "produced_qty": produced,
        "note": ("de fabriekssimulatie produceert geen afkeur; deze waarde "
                 "blijft 0 tot reject_count wordt gevoed") if rejects == 0 else None,
    }


def _utilization_efficiency(db, start, end) -> tuple[Optional[float], dict]:
    """UE = APT / AUBT: draaitijd gedeeld door draaitijd plus storingstijd,
    over de statushistorie. Idle en Allocated zijn neutraal en vallen buiten
    beide emmers, consistent met EquipmentMonitor."""
    rows = sorted(db.dw_equipment_state.find({}), key=lambda r: r.get("ts") or "")
    running = down = 0.0
    # Nooit voorbij nu rekenen: een lopend venster is nog niet voorbij, en
    # toekomstige tijd als stilstand tellen maakt elke KPI onbruikbaar zodra je
    # midden in een week of maand kijkt.
    horizon = min(end, datetime.now(timezone.utc))
    per_eq: dict[str, list[dict]] = {}
    for row in rows:
        per_eq.setdefault(row.get("equipment_id"), []).append(row)

    for eq, hist in per_eq.items():
        if eq not in EQUIPMENT_IDS:
            continue
        for i, row in enumerate(hist):
            t0 = _parse(row.get("ts"))
            if t0 is None:
                continue
            t1 = _parse(hist[i + 1].get("ts")) if i + 1 < len(hist) else horizon
            if t1 is None:
                t1 = horizon
            # knip het interval op de venstergrenzen en op nu
            lo, hi = max(t0, start), min(t1, horizon)
            dur = (hi - lo).total_seconds()
            if dur <= 0:
                continue
            state = row.get("state")
            if state in _PRODUCTIVE_STATES:
                running += dur
            elif state in _NEUTRAL_STATES:
                continue
            elif state is not None:
                down += dur

    denom = running + down
    if denom <= 0:
        return None, {"reason": "geen statushistorie in dit venster"}
    return round(running / denom * 100, 2), {
        "apt_sec": round(running, 1), "aubt_sec": round(denom, 1)}


def _plan_attainment(db, start, end) -> tuple[Optional[float], dict]:
    """Geproduceerde liters gedeeld door gepland volume, in dezelfde eenheid.

    Dit is bewust GEEN yield: de noemer is een planningsgetal en niet de
    materiaalinzet. De oude formule deelde packs (stuks) door planned_L
    (liters) en gaf daardoor waarden boven 100 %. Hier wordt met pack_size_L
    naar liters gerekend, en een uitkomst boven 100 % wordt gemeld in plaats
    van stilzwijgend getoond.
    """
    batches = _completed_batches(db, start, end)
    planned = sum(_num(b.get("planned_L")) for b in batches)
    if planned <= 0:
        return None, {"reason": "geen gepland volume in dit venster"}
    produced_L = 0.0
    for b in batches:
        pack_L = _num(b.get("pack_size_L")) or 1.0
        produced_L += _num(b.get("packs_total")) * pack_L
    value = round(produced_L / planned * 100, 2)
    meta = {"sample_n": len(batches), "planned_L": planned,
            "produced_L": round(produced_L, 1)}
    if value > 100.0:
        meta["note"] = ("boven 100 %: gepland volume is kleiner dan de "
                        "werkelijke opbrengst, controleer de noemer")
    return value, meta


def _mass_yield(db, start, end, density: Optional[float]) -> tuple[Optional[float], dict]:
    """Yield op massabalans: kg product uit gedeeld door kg grondstof in.

    Dit is wat een zuivelfabriek onder yield verstaat, en het is iets anders dan
    plan-realisatie: de noemer is de werkelijke materiaalinzet en niet een
    planningsgetal. Het verschil tussen 100 % en deze waarde is echt verlies
    (indamping tijdens koken, fase-overgangen, restanten in leidingen), en dat
    is precies de verliescategorie waar geen generieke OEE-tool voor bestaat.
    """
    if not density:
        return None, {"reason": "geen product_density_kg_L in het model"}
    batches = _completed_batches(db, start, end)
    if not batches:
        return None, {"reason": "geen afgeronde batches in dit venster"}
    batch_ids = {b.get("batch_id") for b in batches}

    kg_in = 0.0
    for d in db.dw_doses.find({}):
        if d.get("batch_id") in batch_ids and d.get("qty_actual") is not None:
            kg_in += _num(d.get("qty_actual"))
    if kg_in <= 0:
        return None, {"reason": "geen gedoseerde hoeveelheden geboekt"}

    kg_out = 0.0
    for b in batches:
        pack_L = _num(b.get("pack_size_L")) or 1.0
        kg_out += _num(b.get("packs_total")) * pack_L * density

    value = round(kg_out / kg_in * 100, 2)
    meta = {"sample_n": len(batches), "kg_in": round(kg_in, 1),
            "kg_out": round(kg_out, 1), "density_kg_L": density}
    if value > 100.0:
        meta["note"] = ("boven 100 %: er komt meer massa uit dan erin gaat, "
                        "controleer de dichtheid of de doseerboekingen")
    return value, meta


def _capability_cpk(db, start, end, spec) -> tuple[Optional[float], dict]:
    """Cpk op de eind-viscositeit. De bindende term is de ondergrens, want te
    laag koken is het risico; die wordt apart teruggegeven zodat het scherm hem
    los kan tonen."""
    if spec is None:
        return None, {"reason": "geen viscositeitsspecificatie in het model"}
    lsl, usl = spec
    values = [_num(b.get("end_viscosity_cP"))
              for b in _completed_batches(db, start, end)
              if b.get("end_viscosity_cP") is not None]
    if len(values) < CAPABILITY_MIN_SAMPLES:
        return None, {"reason": (f"te weinig metingen ({len(values)} van "
                                 f"{CAPABILITY_MIN_SAMPLES} nodig)"),
                      "sample_n": len(values)}
    mean = statistics.fmean(values)
    sigma = statistics.stdev(values)
    if sigma <= 0:
        return None, {"reason": "spreiding is nul, Cpk niet gedefinieerd",
                      "sample_n": len(values)}
    lower = (mean - lsl) / (3 * sigma)
    upper = (usl - mean) / (3 * sigma)
    return round(min(lower, upper), 2), {
        "sample_n": len(values), "mean": round(mean, 1),
        "sigma": round(sigma, 2), "lsl": lsl, "usl": usl,
        "cp": round((usl - lsl) / (6 * sigma), 2),
        "cpk_lower": round(lower, 2), "cpk_upper": round(upper, 2),
        "binding": "lower" if lower <= upper else "upper",
    }


def _otif(db, start, end) -> tuple[Optional[float], dict]:
    """On time in full: orders die op tijd EN volledig zijn geleverd, gedeeld
    door alle orders met een due-date in het venster. Te late en nog open
    orders zitten dus in de noemer; alleen afgesloten orders tellen zou een
    order die drie weken te laat is nooit laten meewegen."""
    orders = [o for o in db.dw_orders.find({})
              if o.get("due_date") and _within(o.get("due_date"), start, end)]
    if not orders:
        total = db.dw_orders.count_documents({})
        return None, {"reason": ("geen orders met leverdatum in dit venster"
                                 if total else "geen orders"),
                      "orders_without_due_date": total}
    ok = 0
    for o in orders:
        if o.get("status") != M.ORDER_DONE:
            continue
        progress = o.get("progress") or {}
        produced = _num(progress.get("produced_L"))
        target = _num(o.get("target_qty_L"))
        in_full = target > 0 and produced >= target
        closed = _parse(o.get("closed_at")) or _parse(o.get("updated_at"))
        due = _parse(o.get("due_date"))
        on_time = closed is not None and due is not None and closed <= due
        if in_full and on_time:
            ok += 1
    return round(ok / len(orders) * 100, 2), {"sample_n": len(orders),
                                              "on_time_in_full": ok}


# ------------------------------------------------------------------- KPI-set

# Metadata per KPI. Timing, Audience en Production methodology zijn velden uit
# ISO 22400-2 Table 1; ze staan hier omdat de norm ze per KPI voorschrijft en
# omdat ze tegelijk het argument zijn dat operator en management verschillende
# schermen horen te krijgen.
KPI_DEFS = [
    {
        "kpi_id": "throughput_rate", "name": "Throughput rate", "unit": "packs/h",
        "iso_ref": "ISO 22400-2 TR", "direction": "higher_is_better",
        "formula": "(GQ + RQ) / AOET", "timing": "periodic",
        "audience": "Management", "production_methodology": "Batch",
    },
    {
        "kpi_id": "quality_ratio", "name": "Quality ratio", "unit": "%",
        "iso_ref": "ISO 22400-2 QR", "direction": "higher_is_better",
        "formula": "GQ / PQ (rework niet meegerekend)", "timing": "periodic",
        "audience": "Management", "production_methodology": "Batch",
    },
    {
        "kpi_id": "scrap_ratio", "name": "Scrap ratio", "unit": "%",
        "iso_ref": "ISO 22400-2 SR", "direction": "lower_is_better",
        "formula": "SQ / PQ", "timing": "periodic",
        "audience": "Management", "production_methodology": "Batch",
    },
    {
        "kpi_id": "utilization_efficiency", "name": "Utilization efficiency",
        "unit": "%", "iso_ref": "ISO 22400-2 UE", "direction": "higher_is_better",
        "formula": "APT / AUBT", "timing": "periodic",
        "audience": "Supervisors", "production_methodology": "Batch",
    },
    {
        "kpi_id": "plan_attainment_pct", "name": "Plan attainment", "unit": "%",
        "iso_ref": "geen (niet de ISO yield-KPI)", "direction": "higher_is_better",
        "formula": "geproduceerde liters / gepland volume", "timing": "periodic",
        "audience": "Management", "production_methodology": "Batch",
    },
    {
        "kpi_id": "mass_yield_pct", "name": "Yield (massabalans)", "unit": "%",
        "iso_ref": "ISO 22400-2 verwant (finished goods ratio)",
        "direction": "higher_is_better",
        "formula": "kg product uit / kg grondstof in", "timing": "periodic",
        "audience": "Management", "production_methodology": "Batch",
    },
    {
        "kpi_id": "capability_cpk", "name": "Process capability", "unit": "",
        "iso_ref": "ISO 22400-2 Cpk", "direction": "higher_is_better",
        "formula": "min((mean - LSL), (USL - mean)) / 3 sigma",
        "timing": "periodic", "audience": "Management",
        "production_methodology": "Batch",
    },
    {
        "kpi_id": "otif_pct", "name": "On time in full", "unit": "%",
        "iso_ref": "geen", "direction": "higher_is_better",
        "formula": "orders op tijd en volledig / orders met leverdatum",
        "timing": "periodic", "audience": "Management",
        "production_methodology": "Batch",
    },
]


def _measure(db, kpi_id: str, start, end, spec, density=None):
    if kpi_id == "throughput_rate":
        return _throughput_rate(db, start, end)
    if kpi_id == "quality_ratio":
        return _quality_ratio(db, start, end)
    if kpi_id == "scrap_ratio":
        return _scrap_ratio(db, start, end)
    if kpi_id == "utilization_efficiency":
        return _utilization_efficiency(db, start, end)
    if kpi_id == "plan_attainment_pct":
        return _plan_attainment(db, start, end)
    if kpi_id == "mass_yield_pct":
        return _mass_yield(db, start, end, density)
    if kpi_id == "capability_cpk":
        return _capability_cpk(db, start, end, spec)
    if kpi_id == "otif_pct":
        return _otif(db, start, end)
    raise ValueError(f"unknown kpi_id {kpi_id!r}")


def compute_kpis(db, window: str = "week", compare: bool = True,
                 now: Optional[datetime] = None,
                 model: Optional[dict] = None) -> list[dict]:
    """De KPI-set over `window`, met status tegen de norm en optioneel de delta
    ten opzichte van het vorige venster van dezelfde soort."""
    model = load_factory_model() if model is None else model
    targets = load_kpi_targets(model)
    spec = viscosity_spec(model)
    density = model.get("product_density_kg_L")
    start, end = window_bounds(window, now=now, offset=0)
    prev_start, prev_end = window_bounds(window, now=now, offset=1)

    out = []
    for spec_def in KPI_DEFS:
        kpi_id = spec_def["kpi_id"]
        value, meta = _measure(db, kpi_id, start, end, spec, density)
        target = targets.get(kpi_id)
        status = kpi_status(value, target)

        previous = None
        if compare:
            previous, _ = _measure(db, kpi_id, prev_start, prev_end, spec,
                                   density)
        delta = _delta(value, previous, spec_def["direction"])

        row = {
            **spec_def,
            "value": value,
            "status": status["status"],
            "beyond_critical": status["beyond_critical"],
            "target": (target or {}).get("target"),
            "warn": (target or {}).get("warn"),
            "critical": (target or {}).get("critical"),
            "window": window,
            "from": _iso(start),
            "to": _iso(end),
            **delta,
        }
        reason = status["reason"] or meta.pop("reason", None)
        if reason:
            row["reason"] = reason
        row["detail"] = {k: v for k, v in meta.items() if v is not None}
        out.append(row)
    return out


# ---------------------------------------------------------------- verliezen

# Welke verliescategorie een gevolg is van welke oorzaak. Dit staat expliciet
# in configuratie en wordt niet afgeleid: een verkeerd geraden causaliteit is
# schadelijker dan geen causaliteit. Vergelijk matrix B uit Manufacturing Cost
# Deployment, waar causaal tegen resulterend een expliciete registratie is.
LOSS_TAXONOMY = {
    "material_overdose": {"kind": "causal", "caused_by": None},
    "scrap": {"kind": "causal", "caused_by": None},
    "downtime": {"kind": "causal", "caused_by": None},
    "rework": {"kind": "resultant", "caused_by": "downtime"},
}


def compute_losses(db, start: datetime, end: datetime,
                   cost_model: Optional[dict]) -> dict:
    """Verliezen in geld over [start, end).

    Levert {"items": [...], "total_causal", "currency", "omitted": [...]}.
    Een ontbrekende kostenparameter laat de categorie weg en verantwoordt dat
    in `omitted`; hij levert nooit 0.0, want "geen verlies" en "niet gemeten"
    mogen er niet hetzelfde uitzien.
    """
    if not cost_model:
        return {"items": [], "total_causal": None, "currency": None,
                "omitted": [{"category": "*",
                             "reason": "geen kostenmodel in factory-model.json"}]}

    currency = cost_model.get("currency", "EUR")
    items: list[dict] = []
    omitted: list[dict] = []

    batches = _completed_batches(db, start, end)
    batch_ids = {b.get("batch_id") for b in batches}

    # 1. Overdosering: gedoseerd boven target, per materiaal.
    cost_per_kg = cost_model.get("cost_per_kg_material") or {}
    if cost_per_kg:
        amount = 0.0
        materials: dict[str, float] = {}
        for d in db.dw_doses.find({}):
            if d.get("batch_id") not in batch_ids:
                continue
            actual, target = d.get("qty_actual"), d.get("qty_target")
            if actual is None or target is None:
                continue
            over = max(0.0, _num(actual) - _num(target))
            rate = cost_per_kg.get(d.get("material_id"))
            if over <= 0 or rate is None:
                continue
            value = over * float(rate)
            amount += value
            materials[d.get("material_id")] = \
                round(materials.get(d.get("material_id"), 0.0) + value, 2)
        if amount > 0:
            items.append(_loss_item("material_overdose", "Overdosering",
                                    amount, currency,
                                    cause=", ".join(sorted(materials)) or None,
                                    detail={"per_material": materials}))
    else:
        omitted.append({"category": "material_overdose",
                        "reason": "cost_per_kg_material ontbreekt"})

    # 2. Afkeur.
    value_per_pack = cost_model.get("value_per_pack")
    if value_per_pack is not None:
        rejects = sum(_num(r.get("reject_count"))
                      for r in _production_rows(db, start, end))
        if rejects > 0:
            items.append(_loss_item("scrap", "Afkeur",
                                    rejects * float(value_per_pack), currency,
                                    cause=f"{int(rejects)} pakken",
                                    detail={"units": rejects}))
    else:
        omitted.append({"category": "scrap", "reason": "value_per_pack ontbreekt"})

    # 3. Stilstand.
    cost_per_hour = cost_model.get("cost_per_downtime_hour")
    if cost_per_hour is not None:
        down_sec = _downtime_seconds(db, start, end)
        if down_sec > 0:
            hours = down_sec / 3600.0
            items.append(_loss_item("downtime", "Stilstand",
                                    hours * float(cost_per_hour), currency,
                                    cause=f"{round(hours, 1)} uur",
                                    detail={"hours": round(hours, 2)}))
    else:
        omitted.append({"category": "downtime",
                        "reason": "cost_per_downtime_hour ontbreekt"})

    # 4. Herwerk. Resulterend: telt niet mee in het kopbedrag.
    rework_cost = cost_model.get("rework_cost_per_batch")
    if rework_cost is not None:
        held = [b for b in batches if b.get("verdict") in (M.HOLD, M.REJECTED)]
        if held:
            items.append(_loss_item("rework", "Herwerk en HOLD",
                                    len(held) * float(rework_cost), currency,
                                    cause=f"{len(held)} batches",
                                    detail={"batch_ids": [b.get("batch_id")
                                                          for b in held]}))
    else:
        omitted.append({"category": "rework",
                        "reason": "rework_cost_per_batch ontbreekt"})

    # Een post die op 0,00 afrondt hoort niet in de lijst. Hij is technisch
    # gemeten maar visueel niet te onderscheiden van "niet gekalibreerd", en dat
    # is precies het onderscheid dat overeind moet blijven.
    items = [i for i in items if i["amount"] > 0]

    causal = [i for i in items if i["causal_or_resultant"] == "causal"]
    total = round(sum(i["amount"] for i in causal), 2)
    for item in items:
        item["share_pct"] = (round(item["amount"] / total * 100, 1)
                             if total > 0 and item["causal_or_resultant"] == "causal"
                             else None)
    items.sort(key=lambda i: (i["causal_or_resultant"] != "causal", -i["amount"]))
    return {"items": items, "total_causal": total, "currency": currency,
            "omitted": omitted}


def _loss_item(category: str, label: str, amount: float, currency: str,
               cause: Optional[str] = None,
               detail: Optional[dict] = None) -> dict:
    tax = LOSS_TAXONOMY.get(category, {"kind": "causal", "caused_by": None})
    return {
        "category": category,
        "label": label,
        "amount": round(amount, 2),
        "currency": currency,
        "causal_or_resultant": tax["kind"],
        "caused_by": tax["caused_by"],
        "cause": cause,
        "detail": detail or {},
    }


def _downtime_seconds(db, start: datetime, end: datetime) -> float:
    horizon = min(end, datetime.now(timezone.utc))
    rows = sorted(db.dw_equipment_state.find({}), key=lambda r: r.get("ts") or "")
    per_eq: dict[str, list[dict]] = {}
    for row in rows:
        per_eq.setdefault(row.get("equipment_id"), []).append(row)
    total = 0.0
    for eq, hist in per_eq.items():
        if eq not in EQUIPMENT_IDS:
            continue
        for i, row in enumerate(hist):
            if row.get("state") not in _DOWNTIME_STATES:
                continue
            t0 = _parse(row.get("ts"))
            if t0 is None:
                continue
            t1 = _parse(hist[i + 1].get("ts")) if i + 1 < len(hist) else horizon
            lo, hi = max(t0, start), min(t1 or horizon, horizon)
            total += max(0.0, (hi - lo).total_seconds())
    return total


# ------------------------------------------------------------------ samenvat


def summary(db, window: str = "week", compare: bool = True,
            now: Optional[datetime] = None) -> dict:
    """Het object achter GET /kpi/summary. Scherm 11 en 12 lezen hieruit."""
    model = load_factory_model()
    start, end = window_bounds(window, now=now, offset=0)
    kpis = compute_kpis(db, window=window, compare=compare, now=now, model=model)
    losses = compute_losses(db, start, end, load_cost_model(model))
    return {
        "window": window,
        "from": _iso(start),
        "to": _iso(end),
        "timezone": "UTC",
        "shift_hours": SHIFT_HOURS,
        "compare": compare,
        "kpis": kpis,
        "losses": losses,
        # De alarmbelasting hoort bij de KPI-set: het is een maat tegen een
        # norm, en hij staat als live getal op L1.
        "alarm_load": alarm_load(db, start, end),
        "generated_at": _iso(datetime.now(timezone.utc)),
    }
