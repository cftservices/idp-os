"""Dashboard fabriek-fouling: vervuiling van de intakepasteur en de reinigingscycli.

Data uit de Fouling-schrijver van de batch engine (vla/fouling_history.py), in TDengine:
  idp.fouling    per lijn per vak van 0,25 procesdraaiuur van een reinigingscyclus: Rf, U,
                 warmteterugwinning (debietgewogen), genormaliseerde drukval, debiet.
                 run_h is het midden van het vak, cycle_start (ms) is de cyclus.
  idp.cip_cycle  per afgeronde CIP: draaiuren van de cyclus, kas (chemie, water, energie),
                 arbeid en het verbruik.

Dit is de meting waar BVIP ADR-030 het reinigingsinterval op rekent: het profiel is het
gemiddelde per vak over de gemeten cycli, met minstens 5 cycli per vak. Drempels komen uit
de catalogus (PAC-102/103/104/106) en de HACCP-grens uit het fabrieksmodel
(food_safety.haccp_max_run_h, synthetisch). Niets hier is overgetypt.
Imported by build_dashboards.py.
"""
from __future__ import annotations

import json

import kpi_boards as K

F = "idp.fouling"
C = "idp.cip_cycle"
LINES = ("L1", "L2")
MIN_CYCLES_PER_BUCKET = 5   # ADR-030: minder cycli in een vak = geen profielpunt
HACCP = (json.loads(K.MODEL.read_text(encoding="utf-8")).get("food_safety") or {}).get(
    "haccp_max_run_h") or {}
VAR_LIJN = {
    "name": "lijn", "label": "Lijn", "type": "custom", "hide": 0,
    "query": "L1 : L1,L2 : L2",
    "current": {"text": "L1", "value": "L1"},
    "options": [{"text": "L1", "value": "L1", "selected": True},
                {"text": "L2", "value": "L2", "selected": False}],
}
WHERE = "`line`='$lijn' and ts >= $from and ts <= $to"


def _kpi(kpi_id: str) -> dict:
    return next(k for k in K.CATALOGUE if k["id"] == kpi_id)


def _haccp_steps() -> dict:
    """Green below the HACCP limit, red from it. The limit is the same for both lines in the
    model; a board variable cannot pick a threshold, so a difference would be refused here."""
    limits = {HACCP.get(ln) for ln in LINES}
    if len(limits) != 1 or None in limits:
        raise SystemExit(f"food_safety.haccp_max_run_h moet voor {LINES} gelijk zijn: {HACCP}")
    return {"mode": "absolute", "steps": [{"color": "green", "value": None},
                                          {"color": "red", "value": limits.pop()}]}


def _field(unit: str, thr: dict, decimals: int = 3, bars: bool = False) -> dict:
    custom = {"lineWidth": 2, "fillOpacity": 8, "spanNulls": True, "showPoints": "never",
              "thresholdsStyle": {"mode": "dashed"}}
    if bars:
        custom.update({"drawStyle": "bars", "fillOpacity": 70, "barAlignment": 0})
    return {"unit": unit, "decimals": decimals, "thresholds": thr, "custom": custom}


def profile_sql() -> str:
    # plugin: a multi-row table needs a timestamp first; "partition by" with a space breaks it
    return ("select * from (select last(ts) as tot, run_h, avg(rf) as rf, "
            "avg(recovery_pct) as terugwinning, count(*) as cycli from " + F +
            f" where {WHERE} partition\nby run_h) order by run_h")


def board(Board, td, stat_opts):
    rf, dp, rec, cost = (_kpi(x) for x in ("KPI-PAC-102", "KPI-PAC-103", "KPI-PAC-104",
                                            "KPI-PAC-106"))
    haccp = _haccp_steps()
    b = Board("fabriek-fouling", "Fabriek — vervuiling en reiniging",
              "Vervuiling van de intakepasteur per reinigingscyclus, het gemeten profiel waar "
              "het reinigingsinterval op rekent, en wat elke CIP kost.",
              time="now-24h", refresh="1m", variables=[VAR_LIJN])
    b.text("Gemeten door de batch engine uit temperaturen en debiet (`idp.fouling`, per vak van "
           "0,25 procesdraaiuur) en per afgeronde CIP (`idp.cip_cycle`). Het **profiel** is per "
           "vak het gemiddelde over de cycli in de gekozen periode; ADR-030 telt een vak pas mee "
           f"bij minstens {MIN_CYCLES_PER_BUCKET} cycli. De rode lijn bij draaiuren is de "
           "HACCP-grens uit het fabrieksmodel (**synthetisch**, gekozen bij de versnelde "
           "vervuiling van de demo). Kosten zijn demo-aannames uit `cost_model`. Uren zijn "
           "procesuren; de demo vervuilt veel sneller dan een echte pasteur.", h=3)

    b.row("Nu")
    o = stat_opts(color="background")
    b.add("stat", "Vervuilingsweerstand Rf", 6, 4,
          [td(f"select last(rf) as rf from {F} where {WHERE}")],
          desc=K.description(rf), options=o,
          field={"unit": "none", "decimals": 4, "thresholds": K.kpi_thresholds(rf),
                 "noValue": "geen data"})
    b.add("stat", "Draaiuren in deze cyclus", 6, 4,
          [td(f"select last(run_h) as run_h from {F} where {WHERE}")],
          desc="Midden van het laatst gevulde vak van 0,25 draaiuur. Rood vanaf de HACCP-grens.",
          options=o, field={"unit": "h", "decimals": 2, "thresholds": haccp,
                            "noValue": "geen data"})
    b.add("stat", "Warmteterugwinning", 6, 4,
          [td(f"select last(recovery_pct) as terugwinning from {F} where {WHERE}")],
          desc=K.description(rec), options=o,
          field={"unit": "percent", "decimals": 1, "thresholds": K.kpi_thresholds(rec),
                 "noValue": "geen data"})
    b.add("stat", "Reinigingen in de periode", 6, 4,
          [td(f"select count(*) as cips from {C} where {WHERE}")],
          options=stat_opts(color="none"), field={"decimals": 0, "noValue": "0"})

    b.row("Vervuilingsprofiel")
    b.add("timeseries", "Rf in de tijd (zaagtand: elke CIP zet hem terug)", 14, 8,
          [td(f"select ts, rf from {F} where {WHERE}", ts=True)],
          desc=K.description(rf), field=_field("none", K.kpi_thresholds(rf), 4),
          options={"legend": {"showLegend": False}, "tooltip": {"mode": "single"}})
    b.add("xychart", "Rf tegen draaiuren (gemeten profiel)", 10, 8, [td(profile_sql())],
          desc="Per vak van 0,25 draaiuur het gemiddelde Rf over de cycli in de periode. Dit is "
               "de curve waar ADR-030 het reinigingsinterval op rekent.",
          transforms=[{"id": "organize", "options": {
              "excludeByName": {"tot": True, "terugwinning": True, "cycli": True}}}],
          options={"mapping": "auto", "series": [{}],
                   "legend": {"showLegend": False}, "tooltip": {"mode": "single"}},
          field={"unit": "none", "decimals": 4, "thresholds": K.kpi_thresholds(rf),
                 "custom": {"show": "points+lines", "pointSize": {"fixed": 6},
                            "lineWidth": 2}})
    b.add("table", "Profiel per vak", 24, 7, [td(profile_sql())],
          desc=f"Een vak met minder dan {MIN_CYCLES_PER_BUCKET} cycli telt in ADR-030 niet mee.",
          transforms=[{"id": "organize", "options": {
              "excludeByName": {"tot": True},
              "renameByName": {"run_h": "draaiuur (midden vak)", "rf": "Rf gemiddeld",
                               "terugwinning": "terugwinning %", "cycli": "cycli"}}}],
          options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
          field={"decimals": 4},
          overrides=[{"matcher": {"id": "byName", "options": "cycli"}, "properties": [
              {"id": "decimals", "value": 0},
              {"id": "custom.cellOptions", "value": {"type": "color-background"}},
              {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                  {"color": "orange", "value": None},
                  {"color": "transparent", "value": MIN_CYCLES_PER_BUCKET}]}}]}])

    b.row("Reinigingscycli")
    b.add("timeseries", "Draaiuren per cyclus", 12, 7,
          [td(f"select ts, run_h from {C} where {WHERE}", ts=True)],
          desc="Draaiuren van de pasteur tussen twee reinigingen, geboekt bij het einde van de "
               "CIP. Rode lijn: de HACCP-grens.",
          field=_field("h", haccp, 2, bars=True),
          options={"legend": {"showLegend": False}, "tooltip": {"mode": "single"}})
    b.add("timeseries", "Kosten per reiniging", 12, 7,
          [td(f"select ts, cash_eur as kas, labour_eur as arbeid from {C} where {WHERE}",
              ts=True)],
          desc="Kas: chemie, water en energie. Arbeid: 0,5 uur. Demo-aannames uit cost_model.",
          field={**_field("currencyEUR", K.kpi_thresholds(cost), 2, bars=True),
                 "custom": {"drawStyle": "bars", "fillOpacity": 70, "stacking":
                            {"mode": "normal", "group": "A"}, "lineWidth": 1}},
          options={"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                   "tooltip": {"mode": "multi"}})
    b.add("stat", "Gemiddelde kosten per reiniging", 8, 4,
          [td(f"select avg(cash_eur + labour_eur) as kosten from {C} where {WHERE}")],
          desc=K.description(cost), options=o,
          field={"unit": "currencyEUR", "decimals": 2, "thresholds": K.kpi_thresholds(cost),
                 "noValue": "geen data"})
    b.add("stat", "Gemiddelde draaiuren per cyclus", 8, 4,
          [td(f"select avg(run_h) as run_h from {C} where {WHERE}")],
          desc="Het huidige reinigingsschema, zoals ADR-030 het meet.",
          options=o, field={"unit": "h", "decimals": 2, "thresholds": haccp,
                            "noValue": "geen data"})
    b.add("stat", "Kosten in de periode", 8, 4,
          [td(f"select sum(cash_eur + labour_eur) as totaal from {C} where {WHERE}")],
          options=stat_opts(color="none"),
          field={"unit": "currencyEUR", "decimals": 0, "noValue": "0"})
    b.add("table", "Laatste reinigingen", 24, 8,
          [td(f"select ts as einde, run_h as draaiuren, cash_eur as kas, labour_eur as arbeid, "
              f"water_l as water_l, caustic_kg as loog_kg, acid_kg as zuur_kg, "
              f"heat_kwh as warmte_kwh, electricity_kwh as stroom_kwh from {C} "
              f"where {WHERE} order by ts desc limit 50")],
          options={"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
          field={"decimals": 2})

    b.row("Warmtewisselaar")
    b.add("timeseries", "Warmteterugwinning", 12, 7,
          [td(f"select ts, recovery_pct as terugwinning from {F} where {WHERE}", ts=True)],
          desc=K.description(rec), field=_field("percent", K.kpi_thresholds(rec), 2),
          options={"legend": {"showLegend": False}, "tooltip": {"mode": "single"}})
    b.add("timeseries", "Genormaliseerde drukval", 12, 7,
          [td(f"select ts, dp_norm as drukval from {F} where {WHERE}", ts=True)],
          desc=K.description(dp), field=_field("pressurebar", K.kpi_thresholds(dp), 3),
          options={"legend": {"showLegend": False}, "tooltip": {"mode": "single"}})
    return b
