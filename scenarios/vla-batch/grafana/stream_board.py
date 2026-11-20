"""Dashboard stream-factory: the plant in Grafana, for the YouTube livestream.

Made for one 1280x720 screen in the carousel (broadcast/README.md), in English, and shared as a
public Grafana dashboard. A public dashboard has no login, and that sets two rules for this board:
  - TDengine only. The batch-engine JSON datasource runs in the browser and shows "Plugin
    unavailable" on a public dashboard (tested 20-11-2026 on fabriek-overzicht).
  - No template variables: a public dashboard does not fill them in (fabriek-kpi came back empty).

Data: idp.telemetry (the UNS archive, per topic) and idp.kpi (rolling KPI history, every 5 min).
Imported by build_dashboards.py.
"""
from __future__ import annotations

TAG = "stream"
ROOT = "DairyWorks"
L1 = f"{ROOT}/Vla"
L2 = f"{ROOT}/Vla-L2"


def _kpi(kpi_id: str, line: str, alias: str) -> str:
    return (f"select ts, `value` as {alias} from idp.kpi "
            f"where kpi_id='{kpi_id}' and `line`='{line}' and ts >= $from and ts <= $to")


def board(Board, td, stat_opts, thresholds, TS_OPTS, TS_FIELD, last_str, series, like):
    b = Board("stream-factory", "DairyWorks — live process",
              "The vla plant as the historian sees it: cooking, quality and OEE for both lines. "
              "Shown in the YouTube livestream; shared publicly, so TDengine only and no variables.",
              time="now-3h", refresh="30s")
    b.d["tags"] = [TAG]
    b.d["links"] = []

    state_map = [{"type": "value", "options": {
        "IDLE": {"text": "idle", "index": 0}, "DOSING": {"text": "dosing", "index": 1},
        "COOKING": {"text": "cooking", "index": 2}, "COOLING": {"text": "cooling", "index": 3},
        "FILLING": {"text": "filling", "index": 4}, "COMPLETE": {"text": "complete", "index": 5}}}]

    def stat(title, sql, unit="none", decimals=None, th=None, color="blue", mappings=None, desc=""):
        field = {"unit": unit, "color": {"mode": "thresholds"} if th else {"mode": "fixed", "fixedColor": color}}
        if th:
            field["thresholds"] = th
        if decimals is not None:
            field["decimals"] = decimals
        if mappings:
            field["mappings"] = mappings
        b.add("stat", title, 4, 4, [td(sql)], options=stat_opts(), field=field, desc=desc)

    stat("Line 1 · batch step", last_str(f"{L1}/Batch/Status/state", "step"), mappings=state_map)
    stat("Line 2 · batch step", last_str(f"{L2}/Batch/Status/state", "step"), mappings=state_map)
    stat("Line 1 · cook temperature",
         f"select last(`value`) as temp from idp.telemetry where `topic`='{L1}/Cook/cook-unit-01/Status/temp_C'",
         unit="celsius", decimals=1, color="orange")
    stat("Line 2 · cook temperature",
         f"select last(`value`) as temp from idp.telemetry where `topic`='{L2}/Cook/cook-unit-02/Status/temp_C'",
         unit="celsius", decimals=1, color="orange")
    stat("Plant OEE", "select last(`value`) as oee from idp.kpi where kpi_id='KPI-PRD-001' and `line`='PLANT'",
         unit="percent", decimals=1, th=thresholds(("red", None), ("orange", 60), ("green", 85)),
         desc="ISO 22400 OEE over a rolling 8-hour window, recalculated every 5 minutes.")
    stat("Plant first pass yield", "select last(`value`) as fpy from idp.kpi where kpi_id='KPI-PRD-005' and `line`='PLANT'",
         unit="percent", decimals=1, th=thresholds(("red", None), ("orange", 95), ("green", 98)))

    b.add("timeseries", "Cook temperature (pasteurising)", 12, 7, [
        td(series(f"`topic`='{L1}/Cook/cook-unit-01/Status/temp_C'", "line_1"), ts=True),
        td(series(f"`topic`='{L2}/Cook/cook-unit-02/Status/temp_C'", "line_2"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "unit": "celsius"},
          desc="Heated to the recipe setpoint and held: the starch gelatinises and the product is pasteurised.")
    b.add("timeseries", "Viscosity (derived quality signal)", 12, 7, [
        td(series(f"`topic`='{L1}/Cook/cook-unit-01/Status/viscosity_cP'", "line_1"), ts=True),
        td(series(f"`topic`='{L2}/Cook/cook-unit-02/Status/viscosity_cP'", "line_2"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "unit": "none", "min": 0},
          desc="Centipoise. A cook that runs too cold leaves the custard thin.")
    b.add("timeseries", "OEE per line (rolling 8 h)", 12, 7, [
        td(_kpi("KPI-PRD-001", "L1", "line_1"), ts=True),
        td(_kpi("KPI-PRD-001", "L2", "line_2"), ts=True),
        td(_kpi("KPI-PRD-001", "PLANT", "plant"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "unit": "percent", "min": 0, "max": 100})
    b.add("timeseries", "Packs filled per batch", 12, 7, [
        td(series(f"`topic`='{L1}/Filling/filler-01/Status/packs_total'", "line_1", "last(`value`)", "1m"), ts=True),
        td(series(f"`topic`='{L2}/Filling/filler-02/Status/packs_total'", "line_2", "last(`value`)", "1m"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "unit": "none", "min": 0},
          desc="1-litre packs; the counter restarts with every batch.")
    return b
