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
    b = Board("pi5-health", "Pi 5 — health",
              "The Raspberry Pi 5 behind the livestream: temperature, throttling, power, fan, "
              "CPU, memory and the stream itself. Measured on the Pi every minute.",
              time="now-6h", refresh="1m")
    b.d["tags"] = [TAG]
    b.d["links"] = []
    b.text("This Raspberry Pi 5 renders the 3D twin and encodes the stream, measured on the Pi every "
           "minute. **Heat:** above 80 °C the Pi starts to slow its clock down, at 85 °C for real; the "
           "clock then drops below 2400 MHz and the picture stutters. **Undervoltage** means the power "
           "supply is too weak (a Pi 5 wants 5 V / 5 A). Pi 5, 8 GB, active cooler.", h=2)

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

    b.row("Now")
    stat("Last reading", f"select cast((cast(now() as bigint) - cast(last(ts) as bigint)) / 1000 as bigint) "
         f"as `seconds_ago` from {P} where ts > now-1d", unit="s",
         th=thresholds(("green", None), ("orange", 180), ("red", 600)),
         desc="Age of the last reading from the Pi. Red: the Pi is off, offline, or its cron job stopped.")
    stat("Temperature", f"select last(temp_c) as `temp` from {P}", unit="celsius", decimals=1,
         th=thresholds(("green", None), ("orange", 70), ("red", 80)),
         desc="SoC temperature. From 80 °C the Pi slows down softly, at 85 °C hard.")
    stat("Max temperature (1 h)", f"select max(temp_c) as `max_temp` from {P} where ts > now-1h",
         unit="celsius", decimals=1, th=thresholds(("green", None), ("orange", 70), ("red", 80)))
    stat("Throttling now", f"select last(throttled_now) + last(soft_temp_now) + last(freq_capped_now) "
         f"as `throttle` from {P}", th=thresholds(("green", None), ("red", 1)),
         mappings=[{"type": "value", "options": {"0": {"text": "no", "index": 0}}},
                   {"type": "range", "options": {"from": 1, "to": 10, "result": {"text": "YES", "index": 1}}}],
         desc="Clock slowed down by heat or by the power supply, right now.")
    stat("Throttled since boot", f"select last(throttled_ever) + last(soft_temp_ever) + last(freq_capped_ever) "
         f"as `since_boot` from {P}", th=thresholds(("green", None), ("orange", 1)),
         mappings=[{"type": "value", "options": {"0": {"text": "no", "index": 0}}},
                   {"type": "range", "options": {"from": 1, "to": 10, "result": {"text": "yes", "index": 1}}}],
         desc="Since the last restart of the Pi. Only returns to 'no' after a reboot.")
    stat("Undervoltage", f"select last(undervolt_now) + last(undervolt_ever) as `supply` from {P}",
         th=thresholds(("green", None), ("red", 1)),
         mappings=[{"type": "value", "options": {"0": {"text": "no", "index": 0}}},
                   {"type": "range", "options": {"from": 1, "to": 2, "result": {"text": "YES", "index": 1}}}],
         desc="Voltage too low, now or since boot. Cause: power supply or cable.")
    stat("Fan", f"select last(fan_rpm) as `rpm` from {P}", unit="rotrpm", color="blue",
         desc="Active cooler. 0 rpm on a cool Pi is normal; 0 rpm above 70 °C is not.")
    stat("CPU clock", f"select last(arm_mhz) as `clock` from {P}", unit="MHz", decimals=0,
         th=thresholds(("red", None), ("orange", 1800), ("green", 2300)),
         desc="2400 MHz is full speed. Lower while the CPU is busy: throttling.")
    stat("CPU idle (5 min)", f"select avg(cpu_idle) as `idle` from {P} where ts > now-5m", unit="percent",
         decimals=0, th=thresholds(("red", None), ("orange", 15), ("green", 30)))
    stat("Memory available", f"select last(mem_avail_mb) as `available` from {P}", unit="decmbytes",
         decimals=0, th=thresholds(("red", None), ("orange", 1000), ("green", 2000)))
    stat("Disk used", f"select last(disk_used_pct) as `disk` from {P}", unit="percent", decimals=0,
         th=thresholds(("green", None), ("orange", 80), ("red", 90)))
    stat("Stream", f"select last(stream_active) as status from {P}",
         th=thresholds(("text", None), ("green", 1)),
         mappings=[{"type": "value", "options": {"0": {"text": "off", "index": 0},
                                                  "1": {"text": "LIVE", "index": 1}}}],
         desc="The systemd unit vla-stream on the Pi.")
    stat("Encoder speed", f"select last(stream_speed) as `speed` from {P} where ts > now-5m", decimals=2,
         th=thresholds(("red", None), ("orange", 0.95), ("green", 0.98)),
         desc="ffmpeg keeps up with real time at 1x. Empty: the stream is off.")
    stat("Stream fps", f"select last(stream_fps) as `fps` from {P} where ts > now-5m", decimals=1,
         th=thresholds(("red", None), ("orange", 27), ("green", 29)),
         desc="Frames per second the encoder sends. The stream runs at 30 fps.")
    stat("Stream restarts", f"select last(stream_restarts) as `restarts` from {P} where ts > now-5m",
         th=thresholds(("green", None), ("orange", 1), ("red", 3)),
         desc="How often systemd has restarted the stream since it was started.")
    stat("Uptime", f"select last(uptime_h) as `uptime` from {P}", unit="h", decimals=1, color="blue")
    stat("Containers", f"select last(containers_running) as `running` from {P}", color="blue",
         desc="Docker containers on the Pi; they add to the load.")

    def lijn(waarde, kleur):
        return {"matcher": {"id": "byName", "options": waarde},
                "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": kleur}}]}

    b.row("Heat")
    b.add("timeseries", "Temperature and fan", 12, 9, [
        td(_ts("max(temp_c) as `temperature`"), ts=True),
        td(_ts("avg(fan_rpm) as `fan`"), ts=True, ref="B")],
          options=TS_OPTS,
          field={**TS_FIELD, "unit": "celsius", "min": 30, "max": 90,
                 "thresholds": thresholds(("green", None), ("orange", 80), ("red", 85)),
                 "custom": {**TS_FIELD["custom"], "thresholdsStyle": {"mode": "line+area"}}},
          overrides=[lijn("temperature", "orange"),
                     {"matcher": {"id": "byName", "options": "fan"}, "properties": [
                         {"id": "unit", "value": "rotrpm"}, {"id": "min", "value": 0},
                         {"id": "max", "value": None}, {"id": "custom.axisPlacement", "value": "right"},
                         {"id": "custom.thresholdsStyle", "value": {"mode": "off"}},
                         {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}]}],
          desc="Orange band from 80 °C (soft slowdown), red from 85 °C (hard).")
    b.add("timeseries", "CPU clock and throttling", 12, 9, [
        td(_ts("min(arm_mhz) as `clock`"), ts=True),
        td(_ts("max(throttled_now + soft_temp_now + freq_capped_now) as `throttling`, "
               "max(undervolt_now) as `undervoltage`"), ts=True, ref="B")],
          options=TS_OPTS, field={**TS_FIELD, "unit": "MHz", "min": 0},
          overrides=[lijn("clock", "green"),
                     {"matcher": {"id": "byRegexp", "options": "throttling|undervoltage"}, "properties": [
                         {"id": "unit", "value": "none"}, {"id": "min", "value": 0}, {"id": "max", "value": 3},
                         {"id": "custom.axisPlacement", "value": "right"},
                         {"id": "custom.drawStyle", "value": "bars"}, {"id": "custom.fillOpacity", "value": 60},
                         {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}],
          desc="Red bars: throttling or undervoltage in that minute.")

    b.row("Load and stream")
    b.add("timeseries", "CPU", 12, 8, [td(_ts(
        "avg(cpu_user) as `user_pct`, avg(cpu_system) as `system_pct`, avg(cpu_iowait) as `io_wait_pct`"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "unit": "percent", "min": 0, "max": 100,
                                  "custom": {**TS_FIELD["custom"], "stacking": {"mode": "normal"},
                                             "fillOpacity": 40}},
          desc="Stacked over four cores: everything above is idle.")
    b.add("timeseries", "Stream: encoder and picture", 12, 8, [
        td(_ts("avg(stream_fps) as `fps`, avg(stream_speed) * 30 as `speed_x30`"), ts=True),
        td(_ts("avg(stream_kbps) as `kbit_s`"), ts=True, ref="B")],
          options=TS_OPTS, field={**TS_FIELD, "min": 0},
          overrides=[{"matcher": {"id": "byName", "options": "kbit_s"}, "properties": [
              {"id": "unit", "value": "Kbits"}, {"id": "custom.axisPlacement", "value": "right"}]}],
          desc="fps and speed×30 should both sit at 30. kbit/s is what goes to YouTube.")
    b.add("timeseries", "Load", 12, 7, [td(_ts(
        "avg(load1) as `1_min`, avg(load5) as `5_min`, avg(load15) as `15_min`"), ts=True)],
          options=TS_OPTS, field={**TS_FIELD, "decimals": 2, "min": 0}, desc="4 cores.")
    b.add("timeseries", "Memory and disk", 12, 7, [
        td(_ts("avg(mem_avail_mb) as `available`, avg(swap_used_mb) as `swap`"), ts=True),
        td(_ts("avg(disk_used_pct) as `disk_pct`"), ts=True, ref="B")],
          options=TS_OPTS, field={**TS_FIELD, "unit": "decmbytes", "min": 0},
          overrides=[{"matcher": {"id": "byName", "options": "disk_pct"}, "properties": [
              {"id": "unit", "value": "percent"}, {"id": "custom.axisPlacement", "value": "right"}]}])
    return b
