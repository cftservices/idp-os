"""Dashboard pi5-health: de Raspberry Pi 5 die de livestream met de 3D-twin maakt.

Data uit de Pi zelf (broadcast/pi5/pi-health.sh, elke minuut via cron), via een ssh-sleutel die
alleen scripts/pi_ingest.py mag voeden, in TDengine:
  idp.pi_host   temperatuur, throttling en onderspanning, ventilator, CPU-klok, load, CPU,
                geheugen, schijf, containers, en de stream (unit, herstarts, encoder)

Waar het om draait is warmte. De Pi 5 schakelt bij 85 °C zijn klok terug (soft limit vanaf
80 °C); dat zie je hier als "throttling" en als een lagere CPU-klok. Onderspanning komt van een
te zwakke voeding en geeft hetzelfde effect. Bankproef 20-11-2026: 66 à 69 °C met de stream.
Imported by build_dashboards.py.
"""
from __future__ import annotations

TAG = "pi5"
P = "idp.pi_host"


def _ts(cols: str, step: str = "1m") -> str:
    return (f"select _wstart as ts, {cols} from {P} "
            f"where ts >= $from and ts <= $to interval({step}) fill(null)")


def board(Board, td, stat_opts, thresholds, TS_OPTS, TS_FIELD):
    b = Board("pi5-health", "Pi 5 — gezondheid",
              "De Raspberry Pi 5 achter de livestream: temperatuur, throttling, voeding, ventilator, "
              "CPU, geheugen en de stream zelf. Elke minuut gemeten op de Pi.",
              time="now-6h", refresh="1m")
    b.d["tags"] = [TAG]
    b.d["links"] = []
    b.text("Gemeten op de Pi zelf (`broadcast/pi5/pi-health.sh`, elke minuut). **Warmte:** boven "
           "80 °C begint de Pi zachtjes terug te schakelen, bij 85 °C echt; dan zakt de CPU-klok onder "
           "2400 MHz en haalt de twin geen 15 fps meer. **Onderspanning** betekent een te zwakke "
           "voeding (de Pi 5 wil 5 V / 5 A). Pi 5, 8 GB, actieve koeler.", h=2)

    def stat(title, sql, w=4, unit="none", th=None, color=None, desc="", decimals=None, mappings=None):
        field = {"unit": unit, "color": {"mode": "thresholds"} if th else
                 {"mode": "fixed", "fixedColor": color or "blue"}}
        if th:
            field["thresholds"] = th
        if decimals is not None:
            field["decimals"] = decimals
        if mappings:
            field["mappings"] = mappings
        b.add("stat", title, w, 4, [td(sql)], options=stat_opts(), field=field, desc=desc)

    ja_nee = [{"type": "value", "options": {
        "0": {"text": "nee", "index": 0}, "1": {"text": "JA", "index": 1}}}]

    b.row("Nu")
    stat("Laatste meting", f"select cast((cast(now() as bigint) - cast(last(ts) as bigint)) / 1000 as bigint) "
         f"as sec_geleden from {P} where ts > now-1d", unit="s",
         th=thresholds(("green", None), ("orange", 180), ("red", 600)),
         desc="Hoe oud de laatste meting van de Pi is. Rood: de Pi is uit, offline, of de cron staat stil.")
    stat("Temperatuur", f"select last(temp_c) as temp from {P}", unit="celsius", decimals=1,
         th=thresholds(("green", None), ("orange", 70), ("red", 80)),
         desc="SoC-temperatuur. Vanaf 80 °C schakelt de Pi zachtjes terug, bij 85 °C hard.")
    stat("Max. temperatuur (1 u)", f"select max(temp_c) as max_temp from {P} where ts > now-1h",
         unit="celsius", decimals=1, th=thresholds(("green", None), ("orange", 70), ("red", 80)))
    stat("Throttling nu", f"select last(throttled_now) + last(soft_temp_now) + last(freq_capped_now) "
         f"as throttle from {P}", th=thresholds(("green", None), ("red", 1)),
         mappings=[{"type": "value", "options": {"0": {"text": "nee", "index": 0}}},
                   {"type": "range", "options": {"from": 1, "to": 10, "result": {"text": "JA", "index": 1}}}],
         desc="Klok teruggeschakeld door warmte of door de voeding, op dit moment.")
    stat("Ooit gethrottled", f"select last(throttled_ever) + last(soft_temp_ever) + last(freq_capped_ever) "
         f"as sinds_boot from {P}", th=thresholds(("green", None), ("orange", 1)),
         mappings=[{"type": "value", "options": {"0": {"text": "nee", "index": 0}}},
                   {"type": "range", "options": {"from": 1, "to": 10, "result": {"text": "ja", "index": 1}}}],
         desc="Sinds de laatste herstart van de Pi. Wordt pas weer 'nee' na een reboot.")
    stat("Onderspanning", f"select last(undervolt_now) + last(undervolt_ever) as voeding from {P}",
         th=thresholds(("green", None), ("red", 1)),
         mappings=[{"type": "value", "options": {"0": {"text": "nee", "index": 0}}},
                   {"type": "range", "options": {"from": 1, "to": 2, "result": {"text": "JA", "index": 1}}}],
         desc="Te lage spanning, nu of sinds de boot. Oorzaak: voeding of kabel.")
    stat("Ventilator", f"select last(fan_rpm) as rpm from {P}", unit="rotrpm", color="blue",
         desc="Actieve koeler. 0 rpm bij een koele Pi is normaal; 0 rpm boven 70 °C niet.")
    stat("CPU-klok", f"select last(arm_mhz) as klok from {P}", unit="MHz", decimals=0,
         th=thresholds(("red", None), ("orange", 1800), ("green", 2300)),
         desc="2400 MHz is vol vermogen. Lager terwijl de CPU bezig is: throttling.")
    stat("CPU vrij (5 min)", f"select avg(cpu_idle) as vrij from {P} where ts > now-5m", unit="percent",
         decimals=0, th=thresholds(("red", None), ("orange", 15), ("green", 30)))
    stat("Geheugen beschikbaar", f"select last(mem_avail_mb) as beschikbaar from {P}", unit="decmbytes",
         decimals=0, th=thresholds(("red", None), ("orange", 1000), ("green", 2000)))
    stat("Schijf gebruikt", f"select last(disk_used_pct) as schijf from {P}", unit="percent", decimals=0,
         th=thresholds(("green", None), ("orange", 80), ("red", 90)))
    stat("Stream", f"select last(stream_active) as status from {P}",
         th=thresholds(("text", None), ("green", 1)),
         mappings=[{"type": "value", "options": {"0": {"text": "uit", "index": 0},
                                                  "1": {"text": "LIVE", "index": 1}}}],
         desc="systemd-unit vla-stream op de Pi.")
    stat("Encoder speed", f"select last(stream_speed) as speed from {P} where ts > now-5m", decimals=2,
         th=thresholds(("red", None), ("orange", 0.95), ("green", 0.98)),
         desc="ffmpeg houdt realtime bij bij 1x. Leeg: de stream staat uit.")
    stat("Stream-fps", f"select last(stream_fps) as fps from {P} where ts > now-5m", decimals=1,
         th=thresholds(("red", None), ("orange", 13.5), ("green", 14.5)))
    stat("Herstarts stream", f"select last(stream_restarts) as herstarts from {P} where ts > now-5m",
         th=thresholds(("green", None), ("orange", 1), ("red", 3)),
         desc="Hoe vaak systemd de stream sinds de start opnieuw heeft opgestart.")
    stat("Uptime", f"select last(uptime_h) as uptime from {P}", unit="h", decimals=1, color="blue")
    stat("Containers", f"select last(containers_running) as draaiend from {P}", color="blue",
         desc="Docker-containers op de Pi (n8n, dokploy en zo); die tellen mee in de belasting.")

    def lijn(waarde, kleur):
        return {"matcher": {"id": "byName", "options": waarde},
                "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": kleur}}]}

    b.row("Warmte")
    b.add("timeseries", "Temperatuur en ventilator", 12, 9, [
        td(_ts("max(temp_c) as temperatuur"), ts=True),
        td(_ts("avg(fan_rpm) as ventilator"), ts=True, ref="B")],
          options=TS_OPTS,
          field={**TS_FIELD, "unit": "celsius", "min": 30, "max": 90,
                 "thresholds": thresholds(("green", None), ("orange", 80), ("red", 85)),
                 "custom": {**TS_FIELD["custom"], "thresholdsStyle": {"mode": "line+area"}}},
          overrides=[lijn("temperatuur", "orange"),
                     {"matcher": {"id": "byName", "options": "ventilator"}, "properties": [
                         {"id": "unit", "value": "rotrpm"}, {"id": "min", "value": 0},
                         {"id": "max", "value": None}, {"id": "custom.axisPlacement", "value": "right"},
                         {"id": "custom.thresholdsStyle", "value": {"mode": "off"}},
                         {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}]}],
          desc="Oranje vlak vanaf 80 °C (zacht terugschakelen), rood vanaf 85 °C (hard).")
    b.add("timeseries", "CPU-klok en throttling", 12, 9, [
        td(_ts("min(arm_mhz) as klok"), ts=True),
        td(_ts("max(throttled_now + soft_temp_now + freq_capped_now) as throttling, "
               "max(undervolt_now) as onderspanning"), ts=True, ref="B")],
          options=TS_OPTS, field={**TS_FIELD, "unit": "MHz", "min": 0},
          overrides=[lijn("klok", "green"),
                     {"matcher": {"id": "byRegexp", "options": "throttling|onderspanning"}, "properties": [
                         {"id": "unit", "value": "none"}, {"id": "min", "value": 0}, {"id": "max", "value": 3},
                         {"id": "custom.axisPlacement", "value": "right"},
                         {"id": "custom.drawStyle", "value": "bars"}, {"id": "custom.fillOpacity", "value": 60},
                         {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}],
          desc="Rode staven: er was die minuut throttling of onderspanning.")

    b.row("Belasting en stream")
    b.add("timeseries", "CPU", 12, 8, [td(_ts(
        "avg(cpu_user) as gebruiker, avg(cpu_system) as systeem, avg(cpu_iowait) as io_wacht"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "unit": "percent", "min": 0, "max": 100,
                                  "custom": {**TS_FIELD["custom"], "stacking": {"mode": "normal"},
                                             "fillOpacity": 40}},
          desc="Gestapeld over vier kernen: wat erboven ligt is vrij.")
    b.add("timeseries", "Stream: encoder en beeld", 12, 8, [
        td(_ts("avg(stream_fps) as fps, avg(stream_speed) * 15 as `speed_x15`"), ts=True),
        td(_ts("avg(stream_kbps) as kbit_s"), ts=True, ref="B")],
          options=TS_OPTS, field={**TS_FIELD, "min": 0},
          overrides=[{"matcher": {"id": "byName", "options": "kbit_s"}, "properties": [
              {"id": "unit", "value": "Kbits"}, {"id": "custom.axisPlacement", "value": "right"}]}],
          desc="fps en speed×15 horen samen op 15 te liggen. kbit/s is wat er naar YouTube gaat.")
    b.add("timeseries", "Load", 12, 7, [td(_ts(
        "avg(load1) as `1_min`, avg(load5) as `5_min`, avg(load15) as `15_min`"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "decimals": 2, "min": 0}, desc="4 kernen.")
    b.add("timeseries", "Geheugen en schijf", 12, 7, [
        td(_ts("avg(mem_avail_mb) as beschikbaar, avg(swap_used_mb) as swap"), ts=True),
        td(_ts("avg(disk_used_pct) as schijf_pct"), ts=True, ref="B")],
          options=TS_OPTS, field={**TS_FIELD, "unit": "decmbytes", "min": 0},
          overrides=[{"matcher": {"id": "byName", "options": "schijf_pct"}, "properties": [
              {"id": "unit", "value": "percent"}, {"id": "custom.axisPlacement", "value": "right"}]}])
    return b
