"""Dashboard vps-health: de gezondheid van de VPS zelf, niet van de fabriek.

Data uit de host-meting (scripts/hoststats.py, elke minuut via cron), in TDengine:
  idp.vps_host       load, CPU (ook steal), geheugen, swap, schijf, containers, OOM, back-up
  idp.vps_container  CPU, geheugen, limiet, herstarts, health per container
  idp.vps_cert       dagen tot het certificaat verloopt, per hostnaam

De meting draait op de host, niet in de demo-engine, dus dit dashboard werkt ook als de
engine plat ligt. Drempels zijn afgestemd op deze machine: 2 vCPU, 8 GB RAM, 100 GB schijf.
Imported by build_dashboards.py.
"""
from __future__ import annotations

TAG = "vps"
H = "idp.vps_host"
C = "idp.vps_container"


def _ts(sql_cols: str, table: str = H, step: str = "1m", where: str = "") -> str:
    return (f"select _wstart as ts, {sql_cols} from {table} "
            f"where ts >= $from and ts <= $to{where} interval({step}) fill(null)")


def _per_container(td, agg: str, alias: str) -> dict:
    # Een serie per container via de group-by van de plugin zelf (colNameToGroup). Tabelvorm
    # met Grafana's partitionByValues werkt hier niet: panelen lopen via het frontend-pad van
    # de plugin en daar is de tijdkolom in tabelvorm geen tijd ("Data is missing a time field").
    t = td(f"select _wstart as ts, container_name, {agg} as {alias} from {C} "
           f"where ts >= $from and ts <= $to and running = 1 partition by container_name interval(2m)",
           ts=True)
    t.update({"colNameToGroup": "container_name", "colNameFormatStr": "{{container_name}}"})
    return t


def board(Board, td, stat_opts, thresholds, TS_OPTS, TS_FIELD, TABLE_OPTS):
    b = Board("vps-health", "VPS — gezondheid",
              "De machine onder alle stacks: load, CPU-steal, geheugen, schijf, containers, "
              "OOM-kills, back-up en certificaten. Elke minuut gemeten op de host.",
              time="now-6h", refresh="1m")
    b.d["tags"] = [TAG]
    b.d["links"] = []
    b.text("Gemeten op de host zelf (`scripts/hoststats.py`, elke minuut), dus ook zichtbaar als "
           "de demo-engine plat ligt. **CPU-steal** is rekentijd die de hostingprovider aan andere "
           "klanten geeft: hoog steal betekent traag zonder dat er bij ons iets mis is. "
           "Machine: 2 vCPU, 8 GB RAM, 100 GB schijf.", h=2)

    def stat(title, sql, w=4, unit="none", th=None, color=None, desc="", decimals=None):
        field = {"unit": unit, "color": {"mode": "thresholds"} if th else
                 {"mode": "fixed", "fixedColor": color or "blue"}}
        if th:
            field["thresholds"] = th
        if decimals is not None:
            field["decimals"] = decimals
        b.add("stat", title, w, 4, [td(sql)], options=stat_opts(), field=field, desc=desc)

    b.row("Nu")
    stat("Laatste meting", f"select cast((cast(now() as bigint) - cast(last(ts) as bigint)) / 1000 as bigint) "
         f"as sec_geleden from {H} where ts > now-1d", unit="s",
         th=thresholds(("green", None), ("orange", 180), ("red", 600)),
         desc="Hoe oud de laatste host-meting is. Rood: de cron-meting staat stil.")
    stat("Load (1 min)", f"select last(load1) as load from {H}", decimals=2,
         th=thresholds(("green", None), ("orange", 3), ("red", 6)),
         desc="2 vCPU: boven 2 staat er werk in de wachtrij.")
    stat("CPU-steal (5 min)", f"select avg(cpu_steal) as steal from {H} where ts > now-5m", unit="percent",
         decimals=1, th=thresholds(("green", None), ("orange", 15), ("red", 40)),
         desc="Rekentijd die de hostingprovider aan andere klanten geeft. Niet ons probleem, wel onze vertraging.")
    stat("CPU vrij (5 min)", f"select avg(cpu_idle) as vrij from {H} where ts > now-5m", unit="percent",
         decimals=0, th=thresholds(("red", None), ("orange", 15), ("green", 35)))
    stat("Geheugen beschikbaar", f"select last(mem_avail_mb) as beschikbaar from {H}", unit="decmbytes",
         decimals=0, th=thresholds(("red", None), ("orange", 700), ("green", 1500)))
    stat("Swap in gebruik", f"select last(swap_used_mb) as swap from {H}", unit="decmbytes", decimals=0,
         th=thresholds(("green", None), ("orange", 1500), ("red", 3000)))
    stat("Schijf gebruikt", f"select last(disk_used_pct) as schijf from {H}", unit="percent", decimals=1,
         th=thresholds(("green", None), ("orange", 80), ("red", 90)))
    stat("Containers draaiend", f"select last(containers_running) as draaiend from {H}", color="blue")
    stat("Unhealthy", f"select last(containers_unhealthy) as unhealthy from {H}",
         th=thresholds(("green", None), ("red", 1)))
    stat("Onverwacht gestopt", f"select last(containers_stopped_unexpected) as gestopt from {H}",
         th=thresholds(("green", None), ("red", 1)),
         desc="Gestopte containers, zonder de bewust uitgezette (buzzws, emcp-embeddings, init-jobs).")
    stat("OOM-kills (24 u)", f"select last(oom_24h) as oom from {H}",
         th=thresholds(("green", None), ("red", 1)), desc="Uit de kernel-log, elke 10 minuten geteld.")
    stat("Laatste back-up", f"select last(backup_age_h) as uur_geleden from {H}", unit="h", decimals=1,
         th=thresholds(("green", None), ("orange", 26), ("red", 50)),
         desc="Leeftijd van de nachtelijke back-up van idp-os (02:00).")

    b.row("Verloop")
    b.add("timeseries", "CPU", 12, 8, [td(_ts(
        "avg(cpu_user) as gebruiker, avg(cpu_system) as systeem, avg(cpu_iowait) as io_wacht, "
        "avg(cpu_steal) as steal"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "unit": "percent", "min": 0, "max": 100,
                                  "custom": {**TS_FIELD["custom"], "stacking": {"mode": "normal"},
                                             "fillOpacity": 40}},
          overrides=[{"matcher": {"id": "byName", "options": "steal"},
                      "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}],
          desc="Gestapeld: wat er boven ligt is vrij. Rood is steal.")
    b.add("timeseries", "Load", 12, 8, [td(_ts(
        "avg(load1) as `1_min`, avg(load5) as `5_min`, avg(load15) as `15_min`"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "decimals": 2, "min": 0})
    b.add("timeseries", "Geheugen en swap", 12, 8, [td(_ts(
        "avg(mem_avail_mb) as beschikbaar, avg(swap_used_mb) as swap"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "unit": "decmbytes", "min": 0})
    b.add("timeseries", "Schijf en containers", 12, 8, [
        td(_ts("avg(disk_used_pct) as schijf_pct"), ts=True),
        td(_ts("last(containers_running) as draaiend, last(containers_unhealthy) as unhealthy, "
               "last(containers_stopped_unexpected) as gestopt"), ts=True)],
          options=TS_OPTS, field=TS_FIELD,
          overrides=[{"matcher": {"id": "byName", "options": "schijf_pct"}, "properties": [
              {"id": "unit", "value": "percent"}, {"id": "custom.axisPlacement", "value": "right"}]}])

    b.row("Containers")
    b.add("timeseries", "CPU per container", 12, 9, [_per_container(td, "avg(cpu_pct)", "cpu")],
          options={**TS_OPTS, "legend": {"showLegend": True, "displayMode": "table", "placement": "right",
                                         "calcs": ["max", "mean"], "sortBy": "Max", "sortDesc": True}},
          field={**TS_FIELD, "unit": "percent", "min": 0},
          desc="100% is één volledige vCPU.")
    b.add("timeseries", "Geheugen per container", 12, 9, [_per_container(td, "avg(mem_mb)", "geheugen")],
          options={**TS_OPTS, "legend": {"showLegend": True, "displayMode": "table", "placement": "right",
                                         "calcs": ["lastNotNull"], "sortBy": "Last *", "sortDesc": True}},
          field={**TS_FIELD, "unit": "decmbytes", "min": 0})
    b.add("table", "Containers nu", 24, 12, [td(
        # plugin: a multi-row table must start with a timestamp column
        f"select last(ts) as gemeten, container_name as container, last(running) as draait, "
        f"last(healthy) as health, last(cpu_pct) as cpu, last(mem_mb) as geheugen, "
        f"last(mem_limit_mb) as limiet, last(mem_mb) / last(mem_limit_mb) * 100 as van_limiet, "
        f"last(restarts) as herstarts from {C} where ts > now-10m partition\nby container_name")],
          desc="Geheugen tegen de eigen limiet: boven 90% dreigt een OOM-kill van die container. "
               "Herstarts tellen sinds de container is aangemaakt.",
          options={**TABLE_OPTS, "sortBy": [{"displayName": "van_limiet", "desc": True}]},
          overrides=[
              {"matcher": {"id": "byName", "options": "gemeten"},
               "properties": [{"id": "custom.hidden", "value": True}]},
              {"matcher": {"id": "byName", "options": "cpu"}, "properties": [
                  {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 1}]},
              {"matcher": {"id": "byName", "options": "geheugen"}, "properties": [
                  {"id": "unit", "value": "decmbytes"}, {"id": "decimals", "value": 0}]},
              {"matcher": {"id": "byName", "options": "limiet"}, "properties": [
                  {"id": "unit", "value": "decmbytes"}, {"id": "decimals", "value": 0}]},
              {"matcher": {"id": "byName", "options": "van_limiet"}, "properties": [
                  {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 0},
                  {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                  {"id": "thresholds", "value": thresholds(("green", None), ("orange", 80), ("red", 90))}]},
              {"matcher": {"id": "byName", "options": "draait"}, "properties": [
                  {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                  {"id": "mappings", "value": [{"type": "value", "options": {
                      "1": {"text": "ja", "color": "green", "index": 0},
                      "0": {"text": "gestopt", "color": "text", "index": 1}}}]}]},
              {"matcher": {"id": "byName", "options": "health"}, "properties": [
                  {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                  {"id": "mappings", "value": [{"type": "value", "options": {
                      "1": {"text": "healthy", "color": "green", "index": 0},
                      "0": {"text": "unhealthy", "color": "red", "index": 1}}},
                      {"type": "special", "options": {"match": "null", "result": {"text": "-", "index": 2}}}]}]},
              {"matcher": {"id": "byName", "options": "herstarts"}, "properties": [
                  {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                  {"id": "thresholds", "value": thresholds(("text", None), ("orange", 1), ("red", 10))}]},
          ])

    b.row("Certificaten")
    b.add("table", "Dagen tot verlopen", 12, 6, [td(
        "select last(ts) as gemeten, cert_host as hostnaam, last(days_left) as dagen "
        "from idp.vps_cert where ts > now-2h partition\nby cert_host")],
          desc="Elk uur gemeten. Let's Encrypt vernieuwt zelf vanaf 30 dagen; onder 14 is er iets mis.",
          options={**TABLE_OPTS, "sortBy": [{"displayName": "dagen", "desc": False}]},
          overrides=[{"matcher": {"id": "byName", "options": "gemeten"},
                      "properties": [{"id": "custom.hidden", "value": True}]},
                     {"matcher": {"id": "byName", "options": "dagen"}, "properties": [
                         {"id": "decimals", "value": 0},
                         {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                         {"id": "thresholds", "value": thresholds(("red", None), ("orange", 14), ("green", 30))}]}])
    b.add("stat", "Uptime", 6, 6, [td(f"select last(uptime_h) as uptime from {H}")],
          options=stat_opts(), field={"unit": "h", "decimals": 1, "color": {"mode": "fixed", "fixedColor": "blue"}})
    b.add("stat", "Schijf vrij", 6, 6, [td(f"select last(disk_free_gb) as vrij from {H}")],
          options=stat_opts(), field={"unit": "decgbytes", "decimals": 1, "color": {"mode": "thresholds"},
                                      "thresholds": thresholds(("red", None), ("orange", 10), ("green", 20))})
    return b
