"""Generate the factory dashboards for Grafana.

    python build_dashboards.py          # writes grafana/dashboards/fabriek-*.json

The JSON is output, this script is the source. Edit here, regenerate, commit both.
Two data sources:
  - vla-tdengine      time series from the historian (idp.telemetry)
  - vla-batch-engine  MES/logistics/CMMS state via the batch-engine REST API

Wording rule: dashboards speak of "de datalaag", "topics" and "ISA-95-context".
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
TD = {"type": "tdengine-datasource", "uid": "vla-tdengine"}
JS = {"type": "marcusolsson-json-datasource", "uid": "vla-batch-engine"}
TAG = "fabriek"
ROOT = "DairyWorks"


# ── targets ─────────────────────────────────────────────────────────────
def td(sql: str, ts: bool = False, ref: str = "A") -> dict:
    return {"refId": ref, "datasource": TD, "queryType": "SQL",
            "formatType": "Time series" if ts else "Table", "sql": sql}


def js(path: str, fields: list[tuple], params: str = "", ref: str = "A") -> dict:
    # marcusolsson-json-datasource 1.4: only `params` ([[key, value]]) reaches the URL; a per-query
    # `queryParams` string is silently ignored. Filter expressions [?()] are refused ("eval prevented"),
    # so a field tuple may carry a 4th element "jsonata".
    return {"refId": ref, "datasource": JS, "method": "GET", "urlPath": path, "cacheDurationSeconds": 0,
            "params": [kv.split("=", 1) for kv in params.split("&") if kv],
            "fields": [{"jsonPath": f[0], "name": f[1], "type": f[2], "language": f[3] if len(f) > 3 else "jsonpath"}
                       for f in fields]}


def refs(targets: list[dict]) -> list[dict]:
    for i, t in enumerate(targets):
        t["refId"] = chr(65 + i)
    return targets


def last_str(topic: str, alias: str) -> str:
    return f"select last(`valuestr`) as {alias} from idp.telemetry where `topic`='{topic}'"


def series(topic_expr: str, alias: str, agg: str = "avg(`value`)", step: str = "30s") -> str:
    return (f"select _wstart as ts, {agg} as {alias} from idp.telemetry "
            f"where {topic_expr} and _ts >= $from and _ts <= $to interval({step}) fill(prev)")


def like(pattern: str) -> str:
    return f"`topic` like '{pattern}'"


# ── panels ──────────────────────────────────────────────────────────────
class Board:
    def __init__(self, uid: str, title: str, desc: str, *, time: str = "now-6h",
                 refresh: str = "30s", variables: list | None = None):
        self.d = {
            "uid": uid, "title": title, "description": desc, "tags": [TAG],
            "editable": True, "schemaVersion": 39, "version": 1,
            "time": {"from": time, "to": "now"}, "refresh": refresh,
            "timezone": "browser", "graphTooltip": 1,
            "templating": {"list": variables or []},
            "links": [{"type": "dashboards", "tags": [TAG], "asDropdown": True,
                       "title": "Fabrieksdashboards", "includeVars": False,
                       "keepTime": True, "targetBlank": False, "icon": "external link"}],
            "annotations": {"list": []}, "panels": [],
        }
        self.y = 0
        self.x = 0
        self.row_h = 0

    def add(self, ptype: str, title: str, w: int, h: int, targets: list[dict], *,
            desc: str = "", options: dict | None = None, field: dict | None = None,
            overrides: list | None = None, transforms: list | None = None,
            ds: dict | None = None) -> dict:
        if self.x + w > 24:
            self.newline()
        p = {"id": len(self.d["panels"]) + 1, "type": ptype, "title": title,
             "gridPos": {"x": self.x, "y": self.y, "w": w, "h": h},
             "targets": refs(targets),
             "fieldConfig": {"defaults": field or {}, "overrides": overrides or []},
             "options": options or {}}
        if targets:
            p["datasource"] = ds or targets[0]["datasource"]
        if desc:
            p["description"] = desc
        if transforms:
            p["transformations"] = transforms
        self.d["panels"].append(p)
        self.x += w
        self.row_h = max(self.row_h, h)
        return p

    def newline(self):
        if self.x:
            self.y += self.row_h
        self.x, self.row_h = 0, 0

    def text(self, md: str, h: int = 3):
        self.newline()
        self.add("text", "", 24, h, [], options={"mode": "markdown", "content": md})
        self.newline()

    def row(self, title: str):
        self.newline()
        self.d["panels"].append({"id": len(self.d["panels"]) + 1, "type": "row",
                                 "title": title, "collapsed": False, "panels": [],
                                 "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}})
        self.y += 1

    def write(self):
        OUT.mkdir(exist_ok=True)
        f = OUT / f"{self.d['uid']}.json"
        f.write_text(json.dumps(self.d, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8", newline="\n")
        return f


# ── shared option sets ──────────────────────────────────────────────────
def stat_opts(calc: str = "lastNotNull", fields: str = "", color: str = "background") -> dict:
    return {"colorMode": color, "graphMode": "none", "textMode": "value_and_name",
            "justifyMode": "center", "wideLayout": True,
            "reduceOptions": {"calcs": [calc], "fields": fields, "values": False}}


def thresholds(*steps) -> dict:
    """steps: (color, value) — first value is None (base)."""
    return {"mode": "absolute",
            "steps": [{"color": c, "value": v} for c, v in steps]}


STATE_MAP = [{"type": "value", "options": {
    "IDLE": {"color": "text", "index": 0}, "Idle": {"color": "text", "index": 1},
    "RECEIVING": {"color": "blue", "index": 2}, "MIXING": {"color": "purple", "index": 3},
    "COOKING": {"color": "orange", "index": 4}, "COOLING": {"color": "light-blue", "index": 5},
    "FILLING": {"color": "green", "index": 6}, "COMPLETE": {"color": "dark-green", "index": 7},
    "CIP": {"color": "yellow", "index": 8}, "FAULT": {"color": "red", "index": 9},
    "Running": {"color": "green", "index": 10}, "Stopped": {"color": "red", "index": 11},
    "vrij": {"color": "text", "index": 12}, "bezig": {"color": "green", "index": 13},
    "storing": {"color": "red", "index": 14}, "ok": {"color": "green", "index": 15},
    "due": {"color": "yellow", "index": 16}, "overdue": {"color": "red", "index": 17},
}}]
TIMELINE_OPTS = {"mergeValues": True, "showValue": "auto", "rowHeight": 0.8,
                 "alignValue": "center", "legend": {"showLegend": False},
                 "tooltip": {"mode": "single", "sort": "none"}}
TABLE_OPTS = {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}
TS_OPTS = {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
           "tooltip": {"mode": "multi", "sort": "none"}}
TS_FIELD = {"custom": {"lineWidth": 2, "fillOpacity": 8, "spanNulls": True,
                       "showPoints": "never"}}

VAR_LIJN_TOPIC = {   # value = topic root segment, text = line id for the REST API
    "name": "lijn", "label": "Lijn", "type": "custom", "hide": 0,
    "query": "L1 : Vla,L2 : Vla-L2",
    "current": {"text": "L1", "value": "Vla"},
    "options": [{"text": "L1", "value": "Vla", "selected": True},
                {"text": "L2", "value": "Vla-L2", "selected": False}],
}
VAR_LIJN_ID = {
    "name": "lijn", "label": "Lijn", "type": "custom", "hide": 0,
    "query": "L1,L2", "current": {"text": "L1", "value": "L1"},
    "options": [{"text": "L1", "value": "L1", "selected": True},
                {"text": "L2", "value": "L2", "selected": False}],
}


def var_custom(name: str, label: str, values: list[str], current: str) -> dict:
    return {"name": name, "label": label, "type": "custom", "hide": 0,
            "query": ",".join(values), "current": {"text": current, "value": current},
            "options": [{"text": v, "value": v, "selected": v == current} for v in values]}


def kpi(kpi_id: str, name: str) -> dict:
    return js("/kpi/summary", [(f"kpis[kpi_id='{kpi_id}'].value", name, "number", "jsonata")])


def count_of(path: str, jp: str, name: str, params: str = "") -> dict:
    return js(path, [(jp, name, "string")], params)


def count_where(path: str, arr: str, key: str, val: str, name: str, params: str = "") -> dict:
    """Number of items in `arr` with key == val; use with stat_opts(calc="lastNotNull")."""
    return js(path, [(f"$count({arr}[{key}='{val}'])", name, "number", "jsonata")], params)


AREAS = [("Receiving", "receiving-tank"), ("Mixing", "process-tank"), ("Cook", "cook-unit"),
         ("Cooling", "cooler"), ("Filling", "filler")]


# ════════════════════════════════════════════════════════════════════════
# 1. Fabriek-overzicht
# ════════════════════════════════════════════════════════════════════════
def fabriek_overzicht() -> Board:
    b = Board("fabriek-overzicht", "Fabriek — overzicht",
              "Beide lijnen, voorbereiding, magazijn en logistiek op één scherm.")
    b.text("## DairyWorks — fabriek in één oogopslag\n"
           "Live toestand komt uit de historian (TDengine), KPI's en logistiek uit de batch-engine. "
           "Elke KPI wordt op precies één plek berekend: `GET /api/v1/kpi/summary`. "
           "Doorklikken via **Fabrieksdashboards** rechtsboven.", h=3)
    pct = {"unit": "percent", "decimals": 1, "color": {"mode": "thresholds"},
           "thresholds": thresholds(("red", None), ("orange", 85), ("green", 95))}
    b.add("stat", "Doorzet (pakken/uur)", 4, 4, [kpi("throughput_rate", "Doorzet")],
          options=stat_opts(), field={"decimals": 0, "color": {"mode": "fixed", "fixedColor": "blue"}})
    b.add("stat", "Kwaliteitsratio", 4, 4, [kpi("quality_ratio", "Kwaliteit")], options=stat_opts(), field=pct)
    b.add("stat", "Benutting", 4, 4, [kpi("utilization_efficiency", "Benutting")], options=stat_opts(), field=pct)
    b.add("stat", "OTIF", 4, 4, [kpi("otif_pct", "OTIF")], options=stat_opts(), field=pct)
    b.add("stat", "Massarendement", 4, 4, [kpi("mass_yield_pct", "Rendement")], options=stat_opts(),
          field={**pct, "thresholds": thresholds(("red", None), ("orange", 80), ("green", 90))})
    b.add("stat", "Verlies deze week", 4, 4,
          [js("/kpi/summary", [("$.losses.total_causal", "Verlies", "number")])],
          options=stat_opts(), field={"unit": "currencyEUR", "decimals": 0,
                                      "color": {"mode": "fixed", "fixedColor": "orange"}})
    b.newline()
    state_field = {"mappings": STATE_MAP, "color": {"mode": "fixed", "fixedColor": "text"}}
    b.add("stat", "Lijn 1", 4, 4, [td(last_str(f"{ROOT}/Vla/Batch/Status/state", "L1"))],
          options=stat_opts(fields="/.*/"), field=state_field)
    b.add("stat", "Lijn 2", 4, 4, [td(last_str(f"{ROOT}/Vla-L2/Batch/Status/state", "L2"))],
          options=stat_opts(fields="/.*/"), field=state_field)
    b.add("stat", "Voorbereiding", 6, 4,
          [td(last_str(f"{ROOT}/Prep/{s}/Status/state", s.replace("-", "_")))
           for s in ("weigh-station-01", "premix-tank-01", "weigh-station-02", "premix-tank-02")],
          options={**stat_opts(fields="/.*/"), "textMode": "value_and_name"}, field=state_field)
    b.add("stat", "Open alarmen", 3, 4, [count_of("/alarms", "$[*].alarm_id", "Alarmen", "state=open")],
          options=stat_opts(calc="count", fields="/.*/"),
          field={"color": {"mode": "thresholds"}, "thresholds": thresholds(("green", None), ("orange", 1), ("red", 5))})
    b.add("stat", "Vrachtwagens onderweg", 3, 4,
          [count_where("/logistics", "shipments", "stage", "en_route", "Onderweg")],
          options=stat_opts(calc="lastNotNull", fields="/.*/"), field={"color": {"mode": "fixed", "fixedColor": "blue"}})
    b.add("stat", "Open werkorders", 4, 4,
          [count_of("/cmms/work-orders", "$.work_orders[*].wo_id", "Werkorders", "open_only=true")],
          options=stat_opts(calc="count", fields="/.*/"),
          field={"noValue": "0", "color": {"mode": "thresholds"},
                 "thresholds": thresholds(("green", None), ("orange", 1), ("red", 3))})
    b.newline()
    b.add("state-timeline", "Batchverloop L1 en L2", 24, 5, [
        td(series(f"`topic`='{ROOT}/Vla/Batch/Status/state'", "lijn_1", "last(`valuestr`)", "10s"), ts=True),
        td(series(f"`topic`='{ROOT}/Vla-L2/Batch/Status/state'", "lijn_2", "last(`valuestr`)", "10s"), ts=True),
    ], desc="L2 wordt pas sinds 22-10-2026 in de historian weggeschreven; daarvoor is die rij leeg.",
          options=TIMELINE_OPTS, field={"mappings": STATE_MAP})
    b.add("table", "Heftrucks", 12, 7, [js("/logistics", [
        ("$.forklifts[*].id", "Heftruck", "string"), ("$.forklifts[*].role", "Taak", "string"),
        ("$.forklifts[*].line", "Lijn", "string"), ("$.forklifts[*].state", "Toestand", "string"),
        ("$.forklifts[*].battery_pct", "Accu %", "number"), ("$.forklifts[*].trips_today", "Ritten vandaag", "number"),
        ("$.forklifts[*].last_task", "Laatste taak", "string")])], options=TABLE_OPTS,
          overrides=[{"matcher": {"id": "byName", "options": "Accu %"}, "properties": [
              {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic"}},
              {"id": "max", "value": 100}, {"id": "min", "value": 0},
              {"id": "thresholds", "value": thresholds(("red", None), ("orange", 25), ("green", 50))}]}])
    b.add("table", "Open alarmen", 12, 7, [js("/alarms", [
        ("$[*].ts", "Tijd", "time"), ("$[*].line", "Lijn", "string"), ("$[*].equipment_id", "Machine", "string"),
        ("$[*].priority", "Prioriteit", "string"), ("$[*].message", "Melding", "string")], "state=open")],
          options=TABLE_OPTS)
    b.add("bargauge", "OEE per machine", 12, 9, [js("/oee", [
        ("$[*].equipment_id", "Machine", "string"), ("$[*].oee", "OEE", "number")])],
          options={"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                   "valueMode": "color", "reduceOptions": {"calcs": [], "fields": "/OEE/", "values": True}},
          field={"unit": "percentunit", "min": 0, "max": 1, "decimals": 1, "color": {"mode": "thresholds"},
                 "thresholds": thresholds(("red", None), ("orange", 0.65), ("green", 0.85))})
    b.add("table", "Verliezen in euro's (deze week)", 12, 9, [js("/kpi/summary", [
        ("$.losses.items[*].label", "Verlies", "string"), ("$.losses.items[*].cause", "Oorzaak", "string"),
        ("$.losses.items[*].causal_or_resultant", "Soort", "string"),
        ("$.losses.items[*].amount", "Bedrag", "number"), ("$.losses.items[*].share_pct", "Aandeel %", "number")])],
          options=TABLE_OPTS, overrides=[{"matcher": {"id": "byName", "options": "Bedrag"},
                                          "properties": [{"id": "unit", "value": "currencyEUR"}]}])
    return b


# ════════════════════════════════════════════════════════════════════════
# 2. Lijn live (L1/L2)
# ════════════════════════════════════════════════════════════════════════
def lijn_live() -> Board:
    b = Board("fabriek-lijn", "Fabriek — lijn live", "Eén lijn live uit de historian; kies L1 of L2.",
              time="now-30m", refresh="10s", variables=[VAR_LIJN_TOPIC])
    L = f"{ROOT}/$lijn"
    state_field = {"mappings": STATE_MAP, "color": {"mode": "fixed", "fixedColor": "text"}}
    b.add("stat", "Batchtoestand", 4, 4, [td(last_str(f"{L}/Batch/Status/state", "toestand"))],
          options=stat_opts(fields="/.*/"), field=state_field)
    b.add("stat", "Batch", 5, 4, [js("/line/live", [("$.batch.batch_id", "Batch", "string")], "line=${lijn:text}")],
          options=stat_opts(fields="/.*/", color="none"))
    b.add("stat", "Recept", 5, 4, [js("/line/live", [("$.batch.recipe_id", "Recept", "string")], "line=${lijn:text}")],
          options=stat_opts(fields="/.*/", color="none"))
    b.add("gauge", "Dosering", 3, 4, [js("/line/live", [("$.dose_totals.pct", "Dosering", "number")], "line=${lijn:text}")],
          options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
          field={"unit": "percent", "min": 0, "max": 100})
    b.add("gauge", "Vulvoortgang", 3, 4,
          [js("/line/live", [("$.filling.packs_progress_pct", "Vullen", "number")], "line=${lijn:text}")],
          options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
          field={"unit": "percent", "min": 0, "max": 100})
    b.add("stat", "Kooktemperatuur", 4, 4,
          [td(f"select last(`value`) as kook_c from idp.telemetry where {like(L + '/Cook/%/Status/temp_C')}")],
          options=stat_opts(), field={"unit": "celsius", "decimals": 1, "color": {"mode": "thresholds"},
                                      "thresholds": thresholds(("blue", None), ("green", 80), ("red", 95))})
    b.newline()
    b.add("state-timeline", "Batchverloop", 24, 5, [
        td(series(f"`topic`='{L}/Batch/Status/state'", "lijn", "last(`valuestr`)", "10s"), ts=True),
        td(series(like(L + "/Mixing/%/Status/phase"), "mengfase", "last(`valuestr`)", "10s"), ts=True),
    ] + [td(series(like(f"{L}/{a}/%/Status/state"), a.lower(), "last(`valuestr`)", "10s"), ts=True)
         for a, _ in AREAS], options=TIMELINE_OPTS, field={"mappings": STATE_MAP})
    b.add("timeseries", "Temperaturen", 12, 8, [
        td(series(like(L + "/Cook/%/Status/temp_C"), "koken"), ts=True),
        td(series(like(L + "/Mixing/%/Status/temp_C"), "mengen"), ts=True),
        td(series(like(L + "/Cooling/%/Status/temp_C"), "koelen"), ts=True),
        td(series(like(L + "/Receiving/%/Status/temp_C"), "ontvangst"), ts=True),
    ], options=TS_OPTS, field={**TS_FIELD, "unit": "celsius"})
    b.add("timeseries", "Viscositeit", 12, 8, [
        td(series(like(L + "/Cook/%/Status/viscosity_cP"), "viscositeit_cP"), ts=True),
    ], desc="Specificatie 150–300 cP (groene band).", options=TS_OPTS,
          field={**TS_FIELD, "unit": "none", "color": {"mode": "fixed", "fixedColor": "purple"},
                 "thresholds": thresholds(("transparent", None), ("green", 150), ("transparent", 300)),
                 "custom": {**TS_FIELD["custom"], "thresholdsStyle": {"mode": "area"}}})
    b.add("timeseries", "Tankniveaus", 12, 8, [
        td(series(like(L + "/Receiving/%/Status/level_L"), "ontvangsttank_L"), ts=True),
        td(series(like(L + "/Mixing/%/Status/level_L"), "procestank_L"), ts=True),
    ], options=TS_OPTS, field={**TS_FIELD, "unit": "litre"})
    b.add("table", "Dosering — werkelijk tegen recept", 12, 8, [js("/line/live", [
        ("$.doses[*].material_id", "Grondstof", "string"), ("$.doses[*].qty_target", "Recept", "number"),
        ("$.doses[*].qty_actual", "Werkelijk", "number"), ("$.doses[*].tol_min", "Min", "number"),
        ("$.doses[*].tol_max", "Max", "number"), ("$.doses[*].in_tolerance", "Binnen tolerantie", "string")],
        "line=${lijn:text}")], options=TABLE_OPTS, field={"unit": "kg"})
    b.add("table", "Gebeurtenissen", 24, 9, [td(
        "select _ts as tijd, `topic` as `topic`, `valuestr` as waarde from idp.telemetry where ("
        f"`topic` in ('{L}/Batch/Status/state','{L}/Batch/Status/operation') or "
        f"{like(L + '/%/Status/state')} or {like(L + '/Mixing/%/Status/phase')}) "
        "and _ts >= $from and _ts <= $to order by _ts desc limit 300")], options=TABLE_OPTS)
    return b


# ════════════════════════════════════════════════════════════════════════
# 3. Voorbereiding
# ════════════════════════════════════════════════════════════════════════
def voorbereiding() -> Board:
    b = Board("fabriek-voorbereiding", "Fabriek — voorbereiding",
              "Weegstations en premixtanks van de voorbereidingshal, plus de voorbereidingssets per order.",
              time="now-1h", refresh="15s")
    st = ("weigh-station-01", "premix-tank-01", "weigh-station-02", "premix-tank-02")
    sf = {"mappings": STATE_MAP, "color": {"mode": "fixed", "fixedColor": "text"}}
    for s in st:
        b.add("stat", s, 6, 3, [td(last_str(f"{ROOT}/Prep/{s}/Status/state", "toestand"))],
              options=stat_opts(fields="/.*/"), field=sf)
    b.add("state-timeline", "Bezetting stations", 24, 6,
          [td(series(f"`topic`='{ROOT}/Prep/{s}/Status/state'", s.replace("-", "_"), "last(`valuestr`)", "10s"), ts=True)
           for s in st],
          desc="Historie vanaf 22-10-2026, het moment dat de voorbereidingshal in de historian kwam.",
          options=TIMELINE_OPTS, field={"mappings": STATE_MAP})
    for state, label, color in (("afwegen", "Afwegen", "blue"), ("premix", "Premix", "purple"),
                                ("gereed", "Gereed", "green"), ("verbruikt", "Verbruikt", "text")):
        b.add("stat", label, 6, 3,
              [count_where("/prep-orders", "sets", "state", state, label, "limit=200")],
              options=stat_opts(calc="lastNotNull", fields="/.*/"),
              field={"noValue": "0", "color": {"mode": "fixed", "fixedColor": color}})
    b.add("table", "Voorbereidingssets", 24, 10, [js("/prep-orders", [
        ("$.sets[*].prep_id", "Set", "string"), ("$.sets[*].line", "Lijn", "string"),
        ("$.sets[*].recipe_id", "Recept", "string"), ("$.sets[*].order_id", "Order", "string"),
        ("$.sets[*].weigh_station", "Weegstation", "string"), ("$.sets[*].premix_tank", "Premixtank", "string"),
        ("$.sets[*].state", "Toestand", "string"), ("$.sets[*].created_at", "Aangemaakt", "time"),
        ("$.sets[*].state_changed_at", "Laatste wijziging", "time"), ("$.sets[*].batch_id", "Batch", "string")],
        "limit=100")], options=TABLE_OPTS)
    return b


# ════════════════════════════════════════════════════════════════════════
# 4. OEE en verliezen
# ════════════════════════════════════════════════════════════════════════
def oee_verliezen() -> Board:
    b = Board("fabriek-oee", "Fabriek — OEE en verliezen",
              "OEE per machine, verliezen in euro's en betrouwbaarheid per lijn.", refresh="1m",
              variables=[VAR_LIJN_ID, var_custom("uren", "Venster (uren)", ["2", "8", "24", "168"], "24"),
                         var_custom("dagen", "Rapportperiode (dagen)", ["1", "7", "30"], "7")])
    b.add("stat", "Batches", 4, 4, [js("/report/period", [("$.batches_total", "Batches", "number")], "days=$dagen")],
          options=stat_opts(), field={"color": {"mode": "fixed", "fixedColor": "blue"}})
    b.add("stat", "Rendement", 4, 4, [js("/report/period", [("$.yield_pct", "Rendement", "number")], "days=$dagen")],
          options=stat_opts(), field={"unit": "percent", "color": {"mode": "thresholds"},
                                      "thresholds": thresholds(("red", None), ("orange", 95), ("green", 98))})
    b.add("stat", "Afgekeurd", 4, 4,
          [js("/report/period", [("$.batches_by_verdict.REJECTED", "Afgekeurd", "number")], "days=$dagen")],
          options=stat_opts(), field={"color": {"mode": "thresholds"},
                                      "thresholds": thresholds(("green", None), ("orange", 1), ("red", 10))})
    b.add("stat", "Stilstandmeldingen", 4, 4,
          [js("/report/period", [("$.downtime_events", "Stilstand", "number")], "days=$dagen")],
          options=stat_opts(), field={"color": {"mode": "fixed", "fixedColor": "orange"}})
    b.add("stat", "Verlies lijn (venster)", 8, 4,
          [js("/losses/equipment", [("$.total_causal", "Verlies", "number")], "line=$lijn&hours=$uren")],
          options=stat_opts(), field={"unit": "currencyEUR", "decimals": 0,
                                      "color": {"mode": "fixed", "fixedColor": "red"}})
    b.add("bargauge", "OEE per machine — $lijn", 12, 9, [js("/oee", [
        ("$[*].equipment_id", "Machine", "string"), ("$[*].oee", "OEE", "number")], "line=$lijn")],
          options={"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                   "valueMode": "color", "reduceOptions": {"calcs": [], "fields": "/OEE/", "values": True}},
          field={"unit": "percentunit", "min": 0, "max": 1, "decimals": 1, "color": {"mode": "thresholds"},
                 "thresholds": thresholds(("red", None), ("orange", 0.65), ("green", 0.85))})
    b.add("table", "OEE-factoren", 12, 9, [js("/oee", [
        ("$[*].equipment_id", "Machine", "string"), ("$[*].availability", "Beschikbaarheid", "number"),
        ("$[*].performance", "Prestatie", "number"), ("$[*].quality", "Kwaliteit", "number"),
        ("$[*].oee", "OEE", "number")], "line=$lijn")], options=TABLE_OPTS,
          field={"unit": "percentunit", "decimals": 1,
                 "custom": {"cellOptions": {"type": "color-text"}}, "color": {"mode": "thresholds"},
                 "thresholds": thresholds(("red", None), ("orange", 0.65), ("green", 0.85))})
    b.add("bargauge", "Verlies per machine (venster)", 12, 9, [js("/losses/equipment", [
        ("$.equipment[*].equipment_id", "Machine", "string"), ("$.equipment[*].total_causal", "Verlies", "number")],
        "line=$lijn&hours=$uren")],
          options={"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True,
                   "reduceOptions": {"calcs": [], "fields": "/Verlies/", "values": True}},
          field={"unit": "currencyEUR", "decimals": 0, "color": {"mode": "fixed", "fixedColor": "red"}})
    b.add("table", "Betrouwbaarheid (MTBF / MTTR)", 12, 9, [js("/reliability", [
        ("$.equipment[*].equipment_id", "Machine", "string"), ("$.equipment[*].availability", "Beschikbaarheid", "number"),
        ("$.equipment[*].failures", "Storingen", "number"), ("$.equipment[*].mtbf_h", "MTBF (u)", "number"),
        ("$.equipment[*].mttr_min", "MTTR (min)", "number"), ("$.equipment[*].downtime_eur", "Stilstand €", "number")],
        "line=$lijn&hours=$uren")], options=TABLE_OPTS,
          overrides=[{"matcher": {"id": "byName", "options": "Beschikbaarheid"},
                      "properties": [{"id": "unit", "value": "percentunit"}]},
                     {"matcher": {"id": "byName", "options": "Stilstand €"},
                      "properties": [{"id": "unit", "value": "currencyEUR"}]}])
    b.add("table", "Verliesoorzaken fabriek (deze week)", 24, 6, [js("/kpi/summary", [
        ("$.losses.items[*].label", "Verlies", "string"), ("$.losses.items[*].cause", "Oorzaak", "string"),
        ("$.losses.items[*].causal_or_resultant", "Soort", "string"),
        ("$.losses.items[*].amount", "Bedrag", "number"), ("$.losses.items[*].share_pct", "Aandeel %", "number")])],
          options=TABLE_OPTS, overrides=[{"matcher": {"id": "byName", "options": "Bedrag"},
                                          "properties": [{"id": "unit", "value": "currencyEUR"}]}])
    return b


# ════════════════════════════════════════════════════════════════════════
# 5. Logistiek en track & trace
# ════════════════════════════════════════════════════════════════════════
def logistiek_trace() -> Board:
    order_var = {
        "name": "order", "label": "Order", "type": "query", "hide": 0, "refresh": 2, "sort": 2,
        "datasource": JS,
        "query": {"method": "GET", "urlPath": "/orders", "queryParams": "", "cacheDurationSeconds": 0,
                  "fields": [{"jsonPath": "$[*].order_id", "name": "order", "type": "string"}]},
        "current": {}, "options": [], "includeAll": False, "multi": False,
    }
    b = Board("fabriek-logistiek", "Fabriek — logistiek en track & trace",
              "Inkomende melk, uitgaande vrachtwagens, heftrucks en het volledige spoor per order.",
              refresh="30s", variables=[var_custom("heftruck", "Heftruck", ["HT-01", "HT-02", "HT-03", "HT-04"], "HT-01"),
                                         order_var])
    b.row("Magazijn en transport")
    b.add("stat", "Melktankwagen", 6, 4, [js("/logistics", [("$.inbound.id", "Wagen", "string"),
                                                          ("$.inbound.stage", "Fase", "string")])],
          options=stat_opts(fields="/.*/", color="none"))
    b.add("gauge", "Lossen", 4, 4, [js("/logistics", [("$.inbound.unloaded_pct", "Gelost", "number")])],
          options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
          field={"unit": "percent", "min": 0, "max": 100})
    for stage, label, color in (("order", "Te laden", "yellow"), ("en_route", "Onderweg", "blue"),
                                ("delivered", "Afgeleverd", "green")):
        b.add("stat", label, 4 if stage != "delivered" else 6, 4,
              [count_where("/logistics", "shipments", "stage", stage, label)],
              options=stat_opts(calc="lastNotNull", fields="/.*/"),
              field={"noValue": "0", "color": {"mode": "fixed", "fixedColor": color}})
    b.add("table", "Zendingen", 14, 9, [js("/logistics", [
        ("$.shipments[*].shipment_id", "Zending", "string"), ("$.shipments[*].truck_id", "Wagen", "string"),
        ("$.shipments[*].order_id", "Order", "string"), ("$.shipments[*].line", "Lijn", "string"),
        ("$.shipments[*].customer", "Klant", "string"), ("$.shipments[*].destination", "Bestemming", "string"),
        ("$.shipments[*].stage", "Fase", "string"), ("$.shipments[*].pallets_loaded", "Geladen", "number"),
        ("$.shipments[*].pallets", "Pallets", "number")])], options=TABLE_OPTS)
    b.add("table", "Heftrucks", 10, 9, [js("/logistics", [
        ("$.forklifts[*].id", "Heftruck", "string"), ("$.forklifts[*].role", "Taak", "string"),
        ("$.forklifts[*].line", "Lijn", "string"), ("$.forklifts[*].state", "Toestand", "string"),
        ("$.forklifts[*].battery_pct", "Accu %", "number"), ("$.forklifts[*].trips_today", "Ritten", "number")])],
          options=TABLE_OPTS)
    b.row("Per heftruck: $heftruck")
    for key, label in (("moves", "Bewegingen"), ("putaway", "Weggezet"), ("loaded", "Geladen"), ("orders", "Orders")):
        b.add("stat", label, 6, 3, [js("/forklifts/$heftruck/history", [(f"$.totals.{key}", label, "number")])],
              options=stat_opts(), field={"color": {"mode": "fixed", "fixedColor": "blue"}})
    b.add("table", "Orders behandeld door $heftruck", 12, 9, [js("/forklifts/$heftruck/history", [
        ("$.orders[*].order_id", "Order", "string"), ("$.orders[*].customer", "Klant", "string"),
        ("$.orders[*].recipe_id", "Recept", "string"), ("$.orders[*].putaway", "Weggezet", "number"),
        ("$.orders[*].loaded", "Geladen", "number"), ("$.orders[*].first_ts", "Eerste", "time"),
        ("$.orders[*].last_ts", "Laatste", "time")])], options=TABLE_OPTS)
    b.add("table", "Laatste bewegingen $heftruck", 12, 9, [js("/forklifts/$heftruck/history", [
        ("$.recent[*].ts", "Tijd", "time"), ("$.recent[*].task", "Taak", "string"),
        ("$.recent[*].hu_id", "Pallet (SSCC)", "string"), ("$.recent[*].order_id", "Order", "string"),
        ("$.recent[*].line", "Lijn", "string"), ("$.recent[*].slot", "Vak", "number"),
        ("$.recent[*].truck_id", "Wagen", "string")])], options=TABLE_OPTS)
    b.row("Track & trace order: $order")
    T = "/orders/$order/trace"
    for key, label in (("batches", "Batches"), ("pallets", "Pallets"), ("packs", "Pakken"),
                       ("trucks_en_route", "Wagens onderweg"), ("trucks_delivered", "Wagens afgeleverd"),
                       ("packs_delivered", "Pakken afgeleverd")):
        b.add("stat", label, 4, 3, [js(T, [(f"$.totals.{key}", label, "number")])],
              options=stat_opts(), field={"color": {"mode": "fixed", "fixedColor": "green"}})
    b.add("table", "Batches van de order", 24, 8, [js(T, [
        ("$.batches[*].batch_id", "Batch", "string"), ("$.batches[*].line", "Lijn", "string"),
        ("$.batches[*].state", "Toestand", "string"), ("$.batches[*].verdict", "Oordeel", "string"),
        ("$.batches[*].packs", "Pakken", "number"), ("$.batches[*].pallets", "Pallets", "number"),
        ("$.batches[*].step", "Stap", "string"), ("$.batches[*].started_at", "Start", "time"),
        ("$.batches[*].completed_at", "Klaar", "time")])], options=TABLE_OPTS)
    b.add("table", "Gebeurtenissen van de order", 24, 10, [js(T, [
        ("$.events[*].ts", "Tijd", "time"), ("$.events[*].batch_id", "Batch", "string"),
        ("$.events[*].type", "Gebeurtenis", "string")])],
          desc="Maximaal 600 gebeurtenissen per order (de API kapt af en meldt dat met events_truncated).",
          options={**TABLE_OPTS, "sortBy": [{"displayName": "Tijd", "desc": True}]})
    return b


# ════════════════════════════════════════════════════════════════════════
# 6. Onderhoud en CBM
# ════════════════════════════════════════════════════════════════════════
def onderhoud_cbm() -> Board:
    b = Board("fabriek-onderhoud", "Fabriek — onderhoud en CBM",
              "Conditiebewaking (vervuiling kookunit), draaiuren, onderhoudstoestand en werkorders.",
              time="now-24h", refresh="1m", variables=[VAR_LIJN_TOPIC])
    L = f"{ROOT}/$lijn"
    b.add("state-timeline", "Onderhoudstoestand per machine", 24, 6,
          [td(series(like(f"{L}/{a}/%/Status/maintenance_state"), a.lower(), "last(`valuestr`)", "1m"), ts=True)
           for a, _ in AREAS], options=TIMELINE_OPTS, field={"mappings": STATE_MAP})
    b.add("timeseries", "Draaiuren", 12, 8,
          [td(series(like(f"{L}/{a}/%/Status/running_hours"), a.lower(), "last(`value`)", "5m"), ts=True)
           for a, _ in AREAS], options=TS_OPTS, field={**TS_FIELD, "unit": "h"})
    b.add("timeseries", "Vervuilingsalarm kookunit", 12, 8, [
        td(f"select _wstart as ts, count(*) as alarmen from idp.telemetry where {like(L + '/Cook/%/Alert/fouling_heatup')} "
           "and _ts >= $from and _ts <= $to interval(1h)", ts=True)],
          desc="Aantal vervuilingsmeldingen per uur: de opwarmtijd overschrijdt de CBM-grens, CIP plannen.",
          options=TS_OPTS, field={"custom": {"drawStyle": "bars", "fillOpacity": 60},
                                  "color": {"mode": "fixed", "fixedColor": "orange"}})
    b.add("table", "Gezondheid per machine", 24, 8, [js("/equipment/health", [
        ("$[*].equipment_id", "Machine", "string"), ("$[*].line", "Lijn", "string"),
        ("$[*].area", "Gebied", "string"), ("$[*].state", "Toestand", "string"),
        ("$[*].running_hours", "Draaiuren", "number"), ("$[*].batches_since_cip", "Batches sinds CIP", "number"),
        ("$[*].dirty", "Vuil", "string"), ("$[*].last_cip_at", "Laatste CIP", "time"),
        ("$[*].heatup_threshold_sec", "CBM-grens (s)", "number")])], options=TABLE_OPTS,
          overrides=[{"matcher": {"id": "byName", "options": "Draaiuren"},
                      "properties": [{"id": "decimals", "value": 1}]}])
    b.add("table", "Werkorders", 24, 10, [js("/cmms/work-orders", [
        ("$.work_orders[*].wo_id", "Werkorder", "string"), ("$.work_orders[*].equipment_id", "Machine", "string"),
        ("$.work_orders[*].line", "Lijn", "string"), ("$.work_orders[*].kind", "Soort", "string"),
        ("$.work_orders[*].priority", "Prio", "string"), ("$.work_orders[*].cause", "Oorzaak", "string"),
        ("$.work_orders[*].status", "Status", "string"), ("$.work_orders[*].technician.name", "Monteur", "string"),
        ("$.work_orders[*].reported_at", "Gemeld", "time"), ("$.work_orders[*].done_at", "Gereed", "time")])],
          options={**TABLE_OPTS, "sortBy": [{"displayName": "Gemeld", "desc": True}]})
    return b


# ════════════════════════════════════════════════════════════════════════
# 7. Historian en DataOps (het TDengine-dashboard)
# ════════════════════════════════════════════════════════════════════════
def historian_dataops() -> Board:
    b = Board("fabriek-historian", "Fabriek — historian en datakwaliteit",
              "Wat de historian (TDengine) binnenkrijgt, hoe vers elk topic is en of de datalaag gezond is.",
              time="now-24h", refresh="1m")
    b.add("stat", "Rijen totaal", 6, 4, [td("select count(*) as rijen from idp.telemetry")],
          options=stat_opts(), field={"unit": "short", "decimals": 1, "color": {"mode": "fixed", "fixedColor": "blue"}})
    b.add("stat", "Topics", 6, 4, [td(
        "select count(*) as `aantal` from information_schema.ins_tables where db_name='idp' and stable_name='telemetry'")],
          options=stat_opts(), field={"color": {"mode": "fixed", "fixedColor": "purple"}})
    b.add("stat", "Rijen laatste uur", 6, 4, [td("select count(*) as rijen from idp.telemetry where _ts > now-1h")],
          options=stat_opts(), field={"unit": "short", "color": {"mode": "fixed", "fixedColor": "green"}})
    b.add("stat", "Laatste waarde", 6, 4, [td(
        "select cast((cast(now() as bigint) - cast(last(_ts) as bigint)) / 1000 as bigint) as sec_geleden from idp.telemetry where _ts > now-1d")],
          options=stat_opts(), field={"unit": "s", "color": {"mode": "thresholds"},
                                      "thresholds": thresholds(("green", None), ("orange", 30), ("red", 120))})
    b.add("timeseries", "Instroom per gebied (rijen per 10 min)", 24, 8, [
        td(f"select _wstart as ts, count(*) as lijn_1 from idp.telemetry where {like(ROOT + '/Vla/%')} and _ts >= $from and _ts <= $to interval(10m)", ts=True),
        td(f"select _wstart as ts, count(*) as lijn_2 from idp.telemetry where {like(ROOT + '/Vla-L2/%')} and _ts >= $from and _ts <= $to interval(10m)", ts=True),
        td(f"select _wstart as ts, count(*) as voorbereiding from idp.telemetry where {like(ROOT + '/Prep/%')} and _ts >= $from and _ts <= $to interval(10m)", ts=True),
        td(f"select _wstart as ts, count(*) as datakwaliteit from idp.telemetry where {like(ROOT + '/Vla-B/%')} and _ts >= $from and _ts <= $to interval(10m)", ts=True),
    ], options=TS_OPTS, field={**TS_FIELD, "custom": {**TS_FIELD["custom"], "drawStyle": "bars", "fillOpacity": 70,
                                                       "stacking": {"mode": "normal"}}})
    b.add("table", "Versheid per topic", 14, 12, [td(
        # Plugin quirks (tdengine-datasource 3.7): a multi-row table must start with a timestamp column,
        # and the literal "partition by"/"group by" demands an unused group-by config, so use a newline.
        "select now() as gemeten, `topic` as `topic`, "
        "cast((cast(now() as bigint) - cast(last(_ts) as bigint)) / 1000 as bigint) as sec_geleden, "
        "count(*) as rijen_24u from idp.telemetry where _ts > now-1d partition\nby `topic` order by sec_geleden desc")],
          desc="Topics die het afgelopen etmaal iets stuurden. Stilgevallen topics staan bovenaan.",
          options={**TABLE_OPTS, "footer": {"show": True, "reducer": ["count"], "fields": ["topic"]}},
          overrides=[{"matcher": {"id": "byName", "options": "gemeten"}, "properties": [
              {"id": "custom.hidden", "value": True}]},
                     {"matcher": {"id": "byName", "options": "sec_geleden"}, "properties": [
              {"id": "unit", "value": "s"}, {"id": "custom.cellOptions", "value": {"type": "color-background"}},
              {"id": "thresholds", "value": thresholds(("green", None), ("orange", 120), ("red", 900))}]}])
    b.add("table", "Datakwaliteit per machine", 10, 12, [js("/datalayer/quality", [
        ("$.equipment[*].equipment_id", "Machine", "string"), ("$.equipment[*].line", "Lijn", "string"),
        ("$.equipment[*].quality", "Kwaliteit", "string"), ("$.equipment[*].oldest_age_s", "Oudste (s)", "number")])],
          options=TABLE_OPTS, overrides=[{"matcher": {"id": "byName", "options": "Kwaliteit"}, "properties": [
              {"id": "custom.cellOptions", "value": {"type": "color-background"}},
              {"id": "mappings", "value": [{"type": "value", "options": {
                  "GOOD": {"color": "green", "index": 0}, "UNCERTAIN": {"color": "orange", "index": 1},
                  "BAD": {"color": "red", "index": 2}}}]}]}])
    b.add("table", "DataOps-regels", 12, 7, [js("/dataops", [
        ("$.rules[*].id", "Regel", "string"), ("$.rules[*].rule", "Omschrijving", "string"),
        ("$.rules[*].ok", "In orde", "string"), ("$.rules[*].detail", "Detail", "string")])], options=TABLE_OPTS)
    b.add("table", "Databases in de historian", 12, 7, [td(
        "select create_time as aangemaakt, name as db, ntables as tabellen, `keep` as bewaartermijn, `vgroups` as vgroepen "
        "from information_schema.ins_databases where name not in ('information_schema','performance_schema','log')")],
          options=TABLE_OPTS)
    b.add("table", "Containers (geheugen en CPU)", 24, 9, [js("/system/resources", [
        ("$.containers[*].name", "Container", "string"), ("$.containers[*].mem_mib", "Geheugen (MiB)", "number"),
        ("$.containers[*].cpu_pct", "CPU %", "number"), ("$.containers[*].stack", "Eigen stack", "string")])],
          options={**TABLE_OPTS, "sortBy": [{"displayName": "Geheugen (MiB)", "desc": True}]})
    return b


BOARDS = [fabriek_overzicht, lijn_live, voorbereiding, oee_verliezen, logistiek_trace, onderhoud_cbm,
          historian_dataops]

if __name__ == "__main__":
    for fn in BOARDS:
        f = fn().write()
        print(f"{f.name}: {len(json.loads(f.read_text(encoding='utf-8'))['panels'])} panelen")
