"""dairy-sim demo entrypoint.

Runs the DairyPlant simulation and serves a live plant-mimic dashboard with ZERO
external dependencies (stdlib http.server). Three triggerable scenarios (cooling,
leak, fault). Optionally publishes ISA-95 MQTT topics to MonsterMQ.

Usage:
    python sim.py                 # run + serve dashboard at http://localhost:8078
    python sim.py --selftest      # headless: trigger all 3 scenarios, assert detection
    PORT=9000 COOL_RATE=8 python sim.py

Env:
    PORT          dashboard port (default 8078)
    COOL_RATE     cooling-scenario fast-forward (default 4 for demo)
    SIM_STEP      seconds advanced per tick (default 0.2)
    MQTT_HOST     if set, publish telemetry to this broker (optional)
    MQTT_PORT     broker port (default 1883)
    SITE/LINE     unused here; topics follow DairyPlant/<Area>/<Equipment>/...
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import machines as M

HERE = Path(__file__).parent
DASHBOARD = HERE / "dashboard.html"

PORT = int(os.environ.get("PORT", "8078"))
COOL_RATE = float(os.environ.get("COOL_RATE", "4"))
SIM_STEP = float(os.environ.get("SIM_STEP", "0.2"))
MQTT_HOST = os.environ.get("MQTT_HOST")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

STATUS_STR = {M.STOPPED: "Stopped", M.RUNNING: "Running", M.ERROR: "Error"}

plant = M.Plant(cool_rate=COOL_RATE)
_lock = threading.Lock()


def snapshot() -> dict:
    with _lock:
        t, sep, p = plant.tank, plant.separator, plant.pasteurizer
        h, b = plant.homogenizer, plant.bottler
        return {
            "sim_time_s": round(plant.sim_time_s, 1),
            "master_running": plant.master_running,
            "limits": {
                "tank_limit_c": M.TANK_TEMP_LIMIT,
                "tank_warn_c": M.TANK_TEMP_WARN,
                "htst_setpoint_c": M.HTST_SETPOINT,
                "htst_divert_c": M.HTST_DIVERT_BELOW,
                "balance_tol": M.BALANCE_TOLERANCE,
            },
            "scenarios": {
                "cooling": plant.scn_cooling,
                "leak": plant.scn_leak,
                "fault": plant.faulted_asset,
            },
            "equipment": {
                "Tank01": {
                    "status": t.status, "status_str": STATUS_STR[t.status],
                    "in_temp_c": round(t.in_temp_c, 2), "flow_in": round(t.flow_in),
                    "flow_out": round(t.flow_out), "level_pct": round(t.level_pct),
                    "cooling_health_pct": round(t.cooling_health_pct),
                },
                "Separator": {
                    "status": sep.status, "status_str": STATUS_STR[sep.status],
                    "rpm": round(sep.rpm), "fat_pct": round(sep.fat_pct, 2),
                },
                "Pasteurizer": {
                    "status": p.status, "status_str": STATUS_STR[p.status],
                    "htst_temp_c": round(p.htst_temp_c, 2), "hold_sec": p.hold_sec,
                    "divert_valve": p.divert_valve, "diverted_l": round(p.diverted_l),
                },
                "Homogenizer": {
                    "status": h.status, "status_str": STATUS_STR[h.status],
                    "pressure_bar": round(h.pressure_bar),
                },
                "Bottler": {
                    "status": b.status, "status_str": STATUS_STR[b.status],
                    "bottles_per_min": round(b.bottles_per_min), "bottles_total": b.bottles_total,
                    "fill_volume_ml": round(b.fill_volume_ml), "reject_count": b.reject_count,
                },
            },
            "balance_error": round(plant.balance_error),
            "alarms": list(plant.alarms.values()),
            "events": list(reversed(plant.events))[:8],
            "open_event": plant._open_event,
        }


# --- optional MQTT publisher (graceful) ------------------------------------
class MqttPublisher:
    def __init__(self):
        self.client = None
        if not MQTT_HOST:
            return
        try:
            import paho.mqtt.client as mqtt
        except Exception:
            print("[mqtt] paho-mqtt not installed; dashboard-only.")
            return
        try:
            self.client = mqtt.Client()
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
            self.client.loop_start()
            print(f"[mqtt] publishing DairyPlant/... to {MQTT_HOST}:{MQTT_PORT}")
        except Exception as e:
            print(f"[mqtt] connect failed ({e}); dashboard-only.")
            self.client = None

    def publish(self, snap: dict):
        if not self.client:
            return
        e = snap["equipment"]

        def pub(topic, value):
            try:
                self.client.publish(topic, value if isinstance(value, str) else json.dumps(value))
            except Exception:
                pass

        pub("DairyPlant/Receiving/Tank01/in_temp_C", e["Tank01"]["in_temp_c"])
        pub("DairyPlant/Receiving/Tank01/flow_in_L_min", e["Tank01"]["flow_in"])
        pub("DairyPlant/Receiving/Tank01/flow_out_L_min", e["Tank01"]["flow_out"])
        pub("DairyPlant/Process/Separator/RPM", e["Separator"]["rpm"])
        pub("DairyPlant/Process/Separator/fat_pct", e["Separator"]["fat_pct"])
        pub("DairyPlant/Process/Pasteurizer/HTST_temp_C", e["Pasteurizer"]["htst_temp_c"])
        pub("DairyPlant/Process/Pasteurizer/divert_valve_status", str(e["Pasteurizer"]["divert_valve"]))
        pub("DairyPlant/Process/Homogenizer/pressure_bar", e["Homogenizer"]["pressure_bar"])
        pub("DairyPlant/Packaging/Bottler/bottles_per_min", e["Bottler"]["bottles_per_min"])
        pub("DairyPlant/Packaging/Bottler/bottles_total", e["Bottler"]["bottles_total"])
        for a in snap["alarms"]:
            pub(f"DairyPlant/Alarms/{a['id']}", a)


def run_loop(stop_evt: threading.Event, publisher: MqttPublisher):
    last_pub = 0.0
    while not stop_evt.is_set():
        with _lock:
            plant.tick(SIM_STEP)
        now = time.time()
        if publisher and now - last_pub >= 1.0:
            publisher.publish(snapshot())
            last_pub = now
        time.sleep(SIM_STEP)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode("utf-8") if isinstance(body, str) else body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            if DASHBOARD.exists():
                self._send(200, DASHBOARD.read_text(encoding="utf-8"), "text/html; charset=utf-8")
            else:
                self._send(404, "dashboard.html not found", "text/plain")
        elif parsed.path == "/state":
            self._send(200, json.dumps(snapshot()), "application/json")
        elif parsed.path == "/command":
            q = parse_qs(parsed.query)
            action = (q.get("action") or [""])[0]
            typ = (q.get("type") or [""])[0]
            asset = (q.get("asset") or [None])[0]
            with _lock:
                if action == "start":
                    plant.start()
                elif action == "stop":
                    plant.stop()
                elif action == "scenario":
                    plant.trigger(typ, asset)
                elif action == "clear":
                    plant.clear(typ)
                elif action == "heal":
                    plant.heal_all()
                elif action == "ack":
                    plant.acknowledge_events()
            self._send(200, json.dumps({"ok": True, "action": action, "type": typ}), "application/json")
        else:
            self._send(404, "not found", "text/plain")


def _run_ticks(n, dt=0.2):
    for _ in range(n):
        plant.tick(dt)


def selftest():
    print("[selftest] dairy scenarios...")
    plant.cool_rate = 30.0
    plant.start()
    _run_ticks(50)
    assert plant.bottler.bottles_total > 0, "no bottles produced at baseline"
    base_bottles = plant.bottler.bottles_total

    # cooling -> predictive alarm before 6 C breach
    plant.trigger("cooling")
    fired_warn = False
    for _ in range(2000):
        plant.tick(0.2)
        if "cooling" in plant.alarms and plant.tank.in_temp_c <= M.TANK_TEMP_LIMIT:
            fired_warn = True
            break
    assert fired_warn, "cooling predictive alarm did not fire before the 6 C breach"
    plant.clear("cooling")
    print(f"[selftest] cooling: predictive alarm fired at {plant.tank.in_temp_c:.1f} C  OK")

    # leak -> mass-balance alarm; single tags still nominal
    plant.trigger("leak")
    _run_ticks(10)
    assert "leak" in plant.alarms, "leak not detected by mass balance"
    assert plant.balance_error > M.BALANCE_TOLERANCE, "balance error below tolerance"
    print(f"[selftest] leak: balance_error {plant.balance_error:.0f} L/min -> alarm  OK")
    plant.clear("leak")
    _run_ticks(5)
    assert "leak" not in plant.alarms, "leak alarm did not clear"

    # fault -> ERROR + event frame + downstream cascade
    rpm_before = plant.separator.rpm
    plant.trigger("fault", "Separator")
    _run_ticks(20)
    assert plant.separator.status == M.ERROR, "separator did not go to ERROR"
    assert plant._open_event and plant._open_event["asset"] == "Separator", "no event frame opened"
    assert plant.separator.rpm < rpm_before, "no downstream cascade (rpm unchanged)"
    plant.clear("fault")
    assert plant.events and plant.events[-1]["asset"] == "Separator", "event frame not logged on clear"
    print(f"[selftest] fault: ERROR + event frame + cascade (rpm {rpm_before:.0f}->{plant.separator.rpm:.0f})  OK")

    print("[selftest] PASS")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    publisher = MqttPublisher()
    stop_evt = threading.Event()
    threading.Thread(target=run_loop, args=(stop_evt, publisher), daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n  dairy-sim demo running")
    print(f"  dashboard:  http://localhost:{PORT}")
    print(f"  scenarios:  cooling / leak / fault (trigger from the operator panel)")
    print(f"  mqtt:       {'on -> ' + MQTT_HOST if MQTT_HOST else 'off (dashboard-only)'}")
    print(f"  Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        server.shutdown()


if __name__ == "__main__":
    main()
