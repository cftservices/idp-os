"""cornflour-sim entrypoint.

Runs the corn-flour mill simulation and serves a live dashboard with ZERO external
dependencies (stdlib http.server). Optionally also publishes ISA-95-shaped telemetry
to MQTT (MonsterMQ) for the canon 7-step demo, if paho-mqtt is installed and a broker
is reachable.

Usage:
    python sim.py                 # run + serve dashboard at http://localhost:8077
    python sim.py --selftest      # accelerated headless run, asserts Solve alarm fires
    PORT=9000 WEAR_RATE=400 python sim.py

Env / args:
    PORT             dashboard port (default 8077)
    WEAR_RATE        grinder wear fast-forward multiplier (default 300 for demo)
    SIM_STEP         seconds advanced per tick (default 0.2)
    AUTOSTART        "1" to start the factory immediately (default 1)
    MQTT_HOST        if set, publish telemetry to this broker (optional)
    MQTT_PORT        broker port (default 1883)
    SITE / LINE      topic prefix (default TechFlow / Mill1)
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

from machines import Factory, SPEC_THRESHOLD_PCT, RUNNING, STOPPED, ERROR

HERE = Path(__file__).parent
DASHBOARD = HERE / "dashboard.html"

PORT = int(os.environ.get("PORT", "8077"))
WEAR_RATE = float(os.environ.get("WEAR_RATE", "600"))
SIM_STEP = float(os.environ.get("SIM_STEP", "0.2"))
AUTOSTART = os.environ.get("AUTOSTART", "1") == "1"
SITE = os.environ.get("SITE", "TechFlow")
LINE = os.environ.get("LINE", "Mill1")
MQTT_HOST = os.environ.get("MQTT_HOST")          # optional
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

STATUS_STR = {STOPPED: "Stopped", RUNNING: "Running", ERROR: "Error"}

factory = Factory(wear_rate=WEAR_RATE)
_lock = threading.Lock()


def snapshot() -> dict:
    """Thread-safe view of the whole factory for the dashboard / API."""
    with _lock:
        g = factory.grinder
        return {
            "sim_time_s": round(factory.sim_time_s, 1),
            "state": factory.state,
            "master_running": factory.master_running,
            "spec_threshold_pct": SPEC_THRESHOLD_PCT,
            "buffers": {
                "raw_kg": round(factory.raw_buffer, 1),
                "washed_kg": round(factory.washed_buffer, 1),
                "dried_kg": round(factory.dried_buffer, 1),
                "flour_kg": round(factory.flour_buffer, 1),
            },
            "machines": {
                "Washer": {
                    "status": factory.washer.status,
                    "status_str": STATUS_STR[factory.washer.status],
                    "level_kg": round(factory.washer.level_kg, 1),
                    "running_hours": round(factory.washer.running_hours, 3),
                    "capacity_kg": factory.washer.capacity_kg,
                },
                "Dryer": {
                    "status": factory.dryer.status,
                    "status_str": STATUS_STR[factory.dryer.status],
                    "level_kg": round(factory.dryer.level_kg, 1),
                    "running_hours": round(factory.dryer.running_hours, 3),
                    "capacity_kg": factory.dryer.capacity_kg,
                    "temperature_c": round(factory.dryer.temperature_c, 1),
                },
                "Grinder": {
                    "status": g.status,
                    "status_str": STATUS_STR[g.status],
                    "level_kg": round(g.level_kg, 1),
                    "running_hours": round(g.running_hours, 3),
                    "capacity_kg": g.capacity_kg,
                    "speed_rpm": round(g.speed_rpm),
                    "blade_wear_pct": round(g.blade_wear_pct, 1),
                    "performance_pct": round(g.performance_pct, 1),
                    "throughput_kgph": round(g.throughput_kgph),
                    "out_of_spec": g.out_of_spec,
                },
                "BagFiller": {
                    "status": factory.bagfiller.status,
                    "status_str": STATUS_STR[factory.bagfiller.status],
                    "level_kg": round(factory.bagfiller.level_kg, 1),
                    "running_hours": round(factory.bagfiller.running_hours, 3),
                    "capacity_kg": factory.bagfiller.capacity_kg,
                    "bags_filled": factory.bagfiller.bags_filled,
                    "bags_this_batch": factory.batch_bags,
                },
            },
            "current_batch": {
                "batch_id": factory.batch_id,
                "bags_this_batch": factory.batch_bags,
            },
            "batches": list(reversed(factory.batches))[:8],
            "maintenance_alarm": factory.maintenance_alarm,
        }


# --- optional MQTT publisher (canon path, graceful if unavailable) ---
class MqttPublisher:
    def __init__(self):
        self.client = None
        if not MQTT_HOST:
            return
        try:
            import paho.mqtt.client as mqtt
        except Exception:
            print("[mqtt] paho-mqtt not installed; skipping MQTT publish.")
            return
        try:
            self.client = mqtt.Client()
            self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
            self.client.loop_start()
            print(f"[mqtt] publishing to {MQTT_HOST}:{MQTT_PORT} under {SITE}/{LINE}/...")
        except Exception as e:
            print(f"[mqtt] could not connect ({e}); continuing dashboard-only.")
            self.client = None

    def publish(self, snap: dict):
        if not self.client:
            return
        base = f"{SITE}/{LINE}"

        def pub(topic, value):
            try:
                self.client.publish(topic, json.dumps(value) if isinstance(value, (dict, list)) else str(value))
            except Exception:
                pass

        m = snap["machines"]
        pub(f"{base}/Milling/Washer/Status/status", m["Washer"]["status"])
        pub(f"{base}/Milling/Washer/Status/level_kg", m["Washer"]["level_kg"])
        pub(f"{base}/Milling/Dryer/Status/temperature_c", m["Dryer"]["temperature_c"])
        pub(f"{base}/Milling/Grinder/Status/speed_rpm", m["Grinder"]["speed_rpm"])
        pub(f"{base}/Milling/Grinder/Status/blade_wear_pct", m["Grinder"]["blade_wear_pct"])
        pub(f"{base}/Milling/Grinder/Status/performance_pct", m["Grinder"]["performance_pct"])
        pub(f"{base}/Milling/Grinder/Status/throughput_kgph", m["Grinder"]["throughput_kgph"])
        pub(f"{base}/Packaging/BagFiller/Status/bags_filled", m["BagFiller"]["bags_filled"])
        pub(f"{base}/Plant/Factory/Status/state", snap["state"])
        if snap["maintenance_alarm"]:
            pub(f"{base}/Plant/Factory/Event/maintenance", snap["maintenance_alarm"])


def run_loop(stop_evt: threading.Event, publisher: MqttPublisher | None):
    last_pub = 0.0
    while not stop_evt.is_set():
        with _lock:
            factory.tick(SIM_STEP)
        now = time.time()
        if publisher and now - last_pub >= 1.0:
            publisher.publish(snapshot())
            last_pub = now
        time.sleep(SIM_STEP)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.wfile.write(body)

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
            with _lock:
                if action == "start":
                    factory.start_factory()
                elif action == "stop":
                    factory.stop_factory()
                elif action == "reset_blades":
                    factory.reset_blades()
            self._send(200, json.dumps({"ok": True, "action": action}), "application/json")
        else:
            self._send(404, "not found", "text/plain")


def selftest():
    """Accelerated headless run: produce batches and assert the Solve alarm fires."""
    print("[selftest] running accelerated simulation...")
    factory.wear_rate = 400.0  # demo fast-forward: several batches, then blades wear out
    factory.start_factory()
    ticks = 0
    while ticks < 60000 and factory.maintenance_alarm is None:
        factory.tick(0.2)
        ticks += 1
    assert factory.batches, "no batch completed"
    assert factory.batches[0]["bags_out"] >= 1, "batch produced no bags"
    assert factory.maintenance_alarm is not None, "maintenance alarm never fired"
    g = factory.grinder
    print(f"[selftest] batches completed: {len(factory.batches)}")
    print(f"[selftest] first batch: {factory.batches[0]}")
    print(f"[selftest] grinder performance: {g.performance_pct:.1f}% (wear {g.blade_wear_pct:.1f}%)")
    print(f"[selftest] ALARM: {factory.maintenance_alarm['message']}")
    print("[selftest] PASS")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return

    if AUTOSTART:
        factory.start_factory()

    publisher = MqttPublisher()
    stop_evt = threading.Event()
    t = threading.Thread(target=run_loop, args=(stop_evt, publisher), daemon=True)
    t.start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n  cornflour-sim running")
    print(f"  dashboard:  http://localhost:{PORT}")
    print(f"  wear_rate:  {WEAR_RATE}x   (grinder wears fast so the Solve alarm fires on camera)")
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
