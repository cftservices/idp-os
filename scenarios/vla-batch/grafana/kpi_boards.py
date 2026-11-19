"""KPI dashboards for the 29 industrial BVIP KPIs, generated from the catalogue snapshot.

Nothing here is typed over: names, units, formulas, direction and thresholds come from
batch-engine/vla/bvip_catalogue.json (tools/sync-bvip-catalogue.py), sources and status from
batch-engine/vla/bvip_map.py. Data from the KPI history the batch engine writes to TDengine:

  idp.kpi        rolling value over the last 8 h, every 5 min       -> trend and current value
  idp.kpi_input  formula inputs per 5-minute bucket, at event time   -> exact value over the
                 chosen period: sum each input, then the catalogue formula, in SQL

A KPI without a source gets a text panel that says so and names the missing input. It never
gets a number.

Boards: fabriek-kpi (all 29) and fabriek-kpi-{productie,kwaliteit,onderhoud,energie,proces}.
Imported by build_dashboards.py.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE_VLA = HERE.parent / "batch-engine" / "vla"
MODEL = HERE.parent / "factory-model" / "isa95-vla.json"

DOMAINS = [("PRD", "productie", "Productie"), ("QUA", "kwaliteit", "Kwaliteit"),
           ("MNT", "onderhoud", "Onderhoud"), ("ENE", "energie", "Energie"),
           ("PAC", "proces", "Procesvoering")]
FASE = {2: "volgt in fase 2 (vastleggen in de MES)", 3: "volgt in fase 3 (fabriekssimulatie)",
        None: "niet in de demo"}
UNITS = {"%": "percent", "h": "h", "EUR": "currencyEUR", "kWh/unit": "kwatth", "kW": "kwatt",
         "bar": "pressurebar", "aantal": "none", "unit/h": "none", "EUR/unit": "currencyEUR",
         "l/unit": "none", "kg/unit": "none", "kW/m2/K": "none", "m2*K/kW": "none"}
VAR_LIJN_KPI = {
    "name": "lijn", "label": "Lijn", "type": "custom", "hide": 0,
    "query": "Fabriek : PLANT,L1 : L1,L2 : L2",
    "current": {"text": "Fabriek", "value": "PLANT"},
    "options": [{"text": "Fabriek", "value": "PLANT", "selected": True},
                {"text": "L1", "value": "L1", "selected": False},
                {"text": "L2", "value": "L2", "selected": False}],
}


def _load_map() -> dict:
    spec = importlib.util.spec_from_file_location("bvip_map", ENGINE_VLA / "bvip_map.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.BVIP_MAP


CATALOGUE = json.loads((ENGINE_VLA / "bvip_catalogue.json").read_text(encoding="utf-8"))["kpis"]
BVIP_MAP = _load_map()
KPI_TARGETS = {t["kpi_id"]: t for t in
               json.loads(MODEL.read_text(encoding="utf-8")).get("kpi_targets", [])}


# ── thresholds and texts ────────────────────────────────────────────────
def kpi_thresholds(k: dict) -> dict:
    """Colour steps from the catalogue (or the model norm), following the direction."""
    src = BVIP_MAP[k["id"]].get("drempels", "catalogus")
    if src == "geen":
        return {"mode": "absolute", "steps": [{"color": "blue", "value": None}]}
    if src.startswith("model:"):
        t = KPI_TARGETS[src.split(":", 1)[1]]
        higher = t["direction"] == "higher_is_better"
        doel, waarsch, krit = t["target"], t["warn"], t["critical"]
    else:
        higher = k["richting"] == "hoger"
        doel, waarsch, krit = k["doel"], k["waarsch"], k["krit"]
    if higher:
        steps = [("red", None), ("orange", krit), ("yellow", waarsch), ("green", doel)]
    else:
        steps = [("green", None), ("yellow", doel), ("orange", waarsch), ("red", krit)]
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def threshold_note(k: dict) -> str:
    src = BVIP_MAP[k["id"]].get("drempels", "catalogus")
    if src == "geen":
        return "Geen kleur: de catalogusdrempels gelden per jaar en zijn niet te vergelijken."
    if src.startswith("model:"):
        t = KPI_TARGETS[src.split(":", 1)[1]]
        return (f"Kleur volgt de lijnnorm uit het fabrieksmodel ({t['target']} {t['unit']}), "
                f"niet de generieke catalogusnorm ({k['doel']} {k['eenheid']}).")
    return f"Doel {k['doel']}, waarschuwing {k['waarsch']}, kritiek {k['krit']} {k['eenheid']} (catalogus)."


def description(k: dict) -> str:
    spec = BVIP_MAP[k["id"]]
    parts = [f"{k['id']} · {k['naam']}", f"Formule (catalogus): {k['formule']}",
             f"Bron in de demo: {spec['bron']}"]
    if spec.get("let_op"):
        parts.append(f"Let op: {spec['let_op']}")
    if spec.get("ontbreekt") and spec["status"] != "niet_aantoonbaar":
        parts.append("Ontbreekt (telt als 0): " + ", ".join(spec["ontbreekt"]))
    parts.append(threshold_note(k))
    parts.append("Uren zijn procesuren (demo draait 15x versneld).")
    return "\n".join(parts)


def not_demonstrable_md(k: dict) -> str:
    spec = BVIP_MAP[k["id"]]
    return (f"**{k['naam_nl'] or k['naam']}** · `{k['id']}`\n\n"
            f"**Niet aantoonbaar** · ontbreekt: {', '.join(f'`{x}`' for x in spec['ontbreekt'])}\n\n"
            f"{spec['bron']} {FASE.get(spec.get('fase'), '')}.")


def unit_of(k: dict) -> str:
    return UNITS.get(k["eenheid"], "none")


# ── SQL ─────────────────────────────────────────────────────────────────
def rolling_sql(kpi_id: str) -> str:
    return ("select ts, `value` as v from idp.kpi "
            f"where kpi_id='{kpi_id}' and `line`='$lijn' and ts >= $from and ts <= $to")


def period_sql(k: dict) -> str:
    """The catalogue formula over summed inputs: exact for any chosen period."""
    missing = set(BVIP_MAP[k["id"]].get("ontbreekt") or [])
    ast.parse(k["formule"], mode="eval")  # fail early on a formula that is not arithmetic

    def sub(m: re.Match) -> str:
        name = m.group(0)
        if name in missing:
            return "0.0"
        return f"sum(case when `input`='{name}' then `value` else 0.0 end)"

    expr = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", sub, k["formule"])
    return (f"select {expr} as v from idp.kpi_input "
            f"where kpi_id='{k['id']}' and `line`='$lijn' and ts >= $from and ts < $to")


def inputs_sql(k: dict) -> str:
    # plugin: a table with more than one row needs a timestamp as its first column
    return ("select last(ts) as tot, `input` as invoer, sum(`value`) as totaal from idp.kpi_input "
            f"where kpi_id='{k['id']}' and `line`='$lijn' and ts >= $from and ts < $to "
            "partition\nby `input`")   # plugin: "partition by" with a space -> group_by_column config is null


# ── boards ──────────────────────────────────────────────────────────────
def _title(k: dict) -> str:
    return f"{k['naam_nl'] or k['naam']} ({k['id'].replace('KPI-', '')})"


def kpi_overview(Board, td, stat_opts) -> "Board":
    b = Board("fabriek-kpi", "Fabriek — KPI's (alle 29)",
              "De 29 industriële KPI's uit de BVIP-catalogus: rollende waarde over 8 uur met "
              "het verloop als sparkline. Niet-aantoonbare KPI's tonen wat er ontbreekt.",
              time="now-24h", refresh="1m", variables=[VAR_LIJN_KPI])
    b.text("Waarden uit de KPI-historie (`idp.kpi`, elke 5 minuten, rollend venster 8 uur). "
           "Uren zijn procesuren. Per domein staat het detail, inclusief de exacte waarde over "
           "de gekozen periode, op de eigen pagina. Een grijs vak is **niet aantoonbaar**: de "
           "demo heeft die input (nog) niet, en dan staat er geen getal.", h=2)
    for dom, _slug, label in DOMAINS:
        b.row(label)
        for k in [x for x in CATALOGUE if x["domein"] == dom]:
            if BVIP_MAP[k["id"]]["status"] == "niet_aantoonbaar":
                b.add("text", "", 6, 5, [], options={"mode": "markdown",
                                                    "content": not_demonstrable_md(k)})
                continue
            opts = stat_opts(color="background")
            opts["graphMode"] = "area"
            opts["textMode"] = "value"
            b.add("stat", _title(k), 6, 5, [td(rolling_sql(k["id"]), ts=True)],
                  desc=description(k), options=opts,
                  field={"unit": unit_of(k), "decimals": 1, "thresholds": kpi_thresholds(k),
                         "noValue": "geen data"})
    return b


def kpi_domain(Board, td, stat_opts, dom: str, slug: str, label: str) -> "Board":
    b = Board(f"fabriek-kpi-{slug}", f"Fabriek — KPI {label.lower()}",
              f"KPI's {label.lower()} uit de BVIP-catalogus: verloop (rollend 8 uur), de exacte "
              "waarde over de gekozen periode en de inputs.",
              time="now-24h", refresh="1m", variables=[VAR_LIJN_KPI])
    b.text("Links het verloop van de rollende waarde (8 uur). Midden de waarde over de "
           "**gekozen periode**: de inputs opgeteld en dan de catalogusformule, dus exact, ook "
           "over een week. Rechts die inputs. Uren zijn procesuren.", h=2)
    for k in [x for x in CATALOGUE if x["domein"] == dom]:
        spec = BVIP_MAP[k["id"]]
        b.row(_title(k))
        if spec["status"] == "niet_aantoonbaar":
            b.text(not_demonstrable_md(k), h=3)
            continue
        field = {"unit": unit_of(k), "decimals": 2, "thresholds": kpi_thresholds(k),
                 "custom": {"lineWidth": 2, "fillOpacity": 8, "spanNulls": True,
                            "showPoints": "never",
                            "thresholdsStyle": {"mode": "dashed"}}}
        b.add("timeseries", "Verloop (rollend 8 uur)", 12, 7,
              [td(rolling_sql(k["id"]), ts=True)], desc=description(k), field=field,
              options={"legend": {"showLegend": False}, "tooltip": {"mode": "single"}})
        if spec.get("additief"):
            b.add("stat", "Over de gekozen periode", 5, 7, [td(period_sql(k))],
                  desc="Som van de inputs over de periode, dan de catalogusformule.\n"
                       + description(k),
                  options=stat_opts(color="background"),
                  field={"unit": unit_of(k), "decimals": 2, "thresholds": kpi_thresholds(k),
                         "noValue": "geen data"})
            b.add("table", "Inputs over de periode", 7, 7, [td(inputs_sql(k))],
                  options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
                  field={"decimals": 2})
        else:
            b.add("stat", "Laatste waarde", 12, 7, [td(rolling_sql(k["id"]), ts=True)],
                  desc=description(k), options=stat_opts(color="background"),
                  field={"unit": unit_of(k), "decimals": 2, "thresholds": kpi_thresholds(k),
                         "noValue": "geen data"})
    return b


def boards(Board, td, stat_opts) -> list:
    out = [lambda: kpi_overview(Board, td, stat_opts)]
    for dom, slug, label in DOMAINS:
        out.append(lambda d=dom, s=slug, lab=label: kpi_domain(Board, td, stat_opts, d, s, lab))
    return out
